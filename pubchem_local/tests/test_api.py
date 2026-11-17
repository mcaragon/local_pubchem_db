"""
End-to-end tests of the build steps and the REST API using small fixtures:
  - tests/data/lcss_sample.xml.gz: three real records from PubChem's CID-LCSS.xml.gz (CIDs 4, 6, 11)
  - pubchem2sqlite/tests/sdf/*.sdf.gz: compound SDF chunks
  - generated title / synonym files
"""
import gzip
import os
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SDF_DIR = HERE.parent.parent / "pubchem2sqlite" / "tests" / "sdf"


def _write_gz(path, lines):
    with gzip.open(str(path), "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["PUBCHEM_DATA_DIR"] = cls.tmp
        os.environ.pop("ADMIN_TOKEN", None)

        from pubchem_local import config, ingest_lcss, ingest_names
        from pubchem2sqlite.utils import (load_db_specifications, initialize_db, connect_for_build, import_sdf_files,
                                          create_indices)
        from pubchem_local.pipeline import COMPOUND_LAYOUT
        config.ensure_dirs()

        ingest_lcss.build(HERE / "data" / "lcss_sample.xml.gz", config.db_path("lcss"))

        _write_gz(Path(cls.tmp) / "titles.gz", ["4\t1-Amino-2-propanol", "11\t1,2-Dichloroethane",
                                                 "31038\t4-Tetradecylpyridine 1-oxide"])
        _write_gz(Path(cls.tmp) / "syn.gz", ["4\t1-Amino-2-propanol", "4\tIsopropanolamine", "4\t78-96-6",
                                              "11\t1,2-Dichloroethane", "11\tEthylene dichloride", "11\t107-06-2",
                                              "31038\t4-Tetradecylpyridine 1-oxide"])
        ingest_names.build(Path(cls.tmp) / "titles.gz", Path(cls.tmp) / "syn.gz", config.db_path("names"))

        specs = load_db_specifications(str(COMPOUND_LAYOUT))
        conn = connect_for_build(str(config.db_path("compounds")))
        with conn:
            initialize_db(conn, specs)
        import_sdf_files(conn, specs, sorted(str(p) for p in SDF_DIR.glob("*.sdf.gz")), n_workers=2,
                         tmp_dir=str(config.tmp_dir()), log=lambda m: None)
        create_indices(conn, specs, log=lambda m: None)
        conn.close()

        from fastapi.testclient import TestClient
        from pubchem_local.api import app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, url, status=200):
        res = self.client.get(url)
        self.assertEqual(status, res.status_code, res.text)
        return res.json()

    def test_health(self):
        data = self.get("/api/health")
        self.assertEqual({"compounds": True, "names": True, "lcss": True}, data["datasets"])
        self.assertEqual(8, data["counts"]["compounds"])
        self.assertEqual(3, data["counts"]["lab_safety_records"])

    def test_compound_from_sdf(self):
        data = self.get("/api/compound/31038")
        self.assertEqual("4-Tetradecylpyridine 1-oxide", data["title"])
        self.assertEqual("JGUZOCJCNMVJHU-UHFFFAOYSA-N", data["identifiers"]["inchikey"])
        self.assertEqual(6.6, data["computed_properties"]["xlogp3"])
        self.assertTrue(data["identifiers"]["smiles"])  # legacy OPENEYE tag fallback
        self.assertFalse(data["has_lab_data"])

    def test_compound_with_lab_data(self):
        data = self.get("/api/compound/11")
        self.assertEqual("1,2-Dichloroethane", data["title"])
        self.assertEqual("107-06-2", data["identifiers"]["cas"])
        bp = data["physical_properties"]["boiling_point"]
        self.assertAlmostEqual(83.5, bp["value"], delta=1.0)
        self.assertEqual("°C", bp["unit"])
        self.assertTrue(len(bp["sources"]) >= 2)
        self.assertAlmostEqual(1.25, data["physical_properties"]["density"]["value"], delta=0.03)
        self.assertEqual("Danger", data["safety"]["signal_word"])
        codes = [p["code"] for p in data["safety"]["pictograms"]]
        self.assertIn("GHS02", codes)
        self.assertTrue(any(h.startswith("H225") for h in data["safety"]["hazard_statements"]))

    def test_lab_summary(self):
        data = self.get("/api/compound/4/lab")
        self.assertEqual("78-96-6", data["cas"])
        self.assertAlmostEqual(159.5, data["boiling_point_c"], delta=1.5)
        self.assertAlmostEqual(0.96, data["density_g_cm3"], delta=0.01)
        self.assertIn("GHS05", data["pictograms"])

    def test_search(self):
        results = self.get("/api/search?q=ethylene dichloride")["results"]
        self.assertEqual(11, results[0]["cid"])
        self.assertEqual("name", results[0]["match_type"])
        self.assertEqual(11, self.get("/api/search?q=107-06-2")["results"][0]["cid"])
        self.assertEqual(11, self.get("/api/search?q=11")["results"][0]["cid"])
        self.assertEqual(31038, self.get("/api/search?q=JGUZOCJCNMVJHU-UHFFFAOYSA-N")["results"][0]["cid"])
        self.assertEqual(31038, self.get("/api/search?q=JGUZOCJCNMVJHU")["results"][0]["cid"])
        self.assertIn(11, [r["cid"] for r in self.get("/api/search?q=C2H4Cl2")["results"]])
        self.assertEqual(11, self.get("/api/search?q=ClCCCl")["results"][0]["cid"])  # SMILES via RDKit
        self.assertEqual([], self.get("/api/search?q=unobtainium")["results"])

    def test_autocomplete(self):
        suggestions = self.get("/api/autocomplete?q=ethyl")["suggestions"]
        self.assertIn(11, [s["cid"] for s in suggestions])
        names = [s["name"] for s in self.get("/api/autocomplete?q=1,2-dic")["suggestions"]]
        self.assertEqual("1,2-Dichloroethane", names[0])  # preferred name of a lab compound first

    def test_lookup_and_batch(self):
        self.assertEqual(4, self.get("/api/lookup?q=isopropanolamine")["cid"])
        self.get("/api/lookup?q=unobtainium", status=404)
        res = self.client.post("/api/lab/batch", json={"identifiers": ["107-06-2", "nothing-here", "4"]}).json()
        self.assertEqual([True, False, True], [r["found"] for r in res["results"]])
        self.assertEqual(11, res["results"][0]["cid"])

    def test_safety_summary_and_structure(self):
        data = self.get("/api/compound/6/safety-summary")
        headings = [s["heading"] for s in data["sections"]]
        self.assertIn("First Aid", headings)
        res = self.client.get("/api/compound/11/structure.svg")
        self.assertEqual(200, res.status_code)
        self.assertIn("<svg", res.text)
        self.get("/api/compound/999999999", status=404)

    def test_ui_and_build_status(self):
        self.assertIn("PubChem", self.client.get("/").text)
        self.assertEqual(200, self.client.get("/static/app.js").status_code)
        data = self.get("/api/build/status")
        self.assertEqual(["lcss", "names", "compounds"], [d["name"] for d in data["datasets"]])

    def test_admin_token(self):
        os.environ["ADMIN_TOKEN"] = "secret"
        try:
            self.assertEqual(401, self.client.post("/api/build/cancel").status_code)
            res = self.client.post("/api/build/cancel", headers={"X-Admin-Token": "secret"})
            self.assertEqual(200, res.status_code)
        finally:
            os.environ.pop("ADMIN_TOKEN")


if __name__ == "__main__":
    unittest.main()
