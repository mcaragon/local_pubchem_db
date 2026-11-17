"""
Read-only query layer over the three databases (compounds, names, lcss). Every database is optional: the API works
with whatever has been built so far.
"""
import re
import sqlite3
from collections import OrderedDict

from pubchem_local import config, propparse

try:  # RDKit is optional; it enables structure images and SMILES/InChI search
    from rdkit import Chem, RDLogger
    from rdkit.Chem.Draw import rdMolDraw2D
    RDLogger.DisableLog("rdApp.*")
except ImportError:  # pragma: no cover
    Chem = None

CID_RE = re.compile(r"^(?:cid[:\s]*)?(\d{1,10})$", re.I)
INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
INCHIKEY1_RE = re.compile(r"^[A-Z]{14}$")
CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")
FORMULA_RE = re.compile(r"^(?:[A-Z][a-z]?\d*)+$")
SMILES_HINT_RE = re.compile(r"[=#\[\]()@/\\]|^[BCNOSPFIcnosp]{1,3}$|[cnos]\d|^C+$")

PUBCHEM_URL = "https://pubchem.ncbi.nlm.nih.gov/compound/%d"


def _connect(dataset):
    path = config.db_path(dataset)
    if not path.exists():
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _smiles_from_inchi(inchi):
    """
    SMILES derived with RDKit from an InChI. Used when the 'compounds' dataset (which has PubChem's own SMILES) is not
    built, so that lab records still carry a structure string. RDKit's canonical form can differ in notation from
    PubChem's SMILES but describes the same structure.
    """
    if Chem is None or not inchi:
        return None
    try:
        mol = Chem.MolFromInchi(inchi)
        return Chem.MolToSmiles(mol) if mol is not None else None
    except Exception:
        return None


class Store(object):
    """Open connections to all available databases. Cheap to create; create one per request."""

    def __init__(self):
        self.compounds = _connect("compounds")
        self.names = _connect("names")
        self.lcss = _connect("lcss")

    def close(self):
        for conn in (self.compounds, self.names, self.lcss):
            if conn is not None:
                conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def available(self):
        return {"compounds": self.compounds is not None, "names": self.names is not None,
                "lcss": self.lcss is not None}

    # ------------------------------------------------------------------ basic lookups

    def compound_row(self, cid):
        if self.compounds is None:
            return None
        row = self.compounds.execute("SELECT * FROM compounds WHERE cid = ?", (cid,)).fetchone()
        return dict(row) if row else None

    def lcss_row(self, cid):
        if self.lcss is None:
            return None
        row = self.lcss.execute("SELECT * FROM lcss_records WHERE cid = ?", (cid,)).fetchone()
        return dict(row) if row else None

    def title(self, cid):
        if self.names is not None:
            row = self.names.execute("SELECT title FROM titles WHERE cid = ?", (cid,)).fetchone()
            if row:
                return row[0]
        lrow = self.lcss_row(cid)
        if lrow:
            return lrow["title"]
        crow = self.compound_row(cid)
        if crow:
            return crow.get("iupac_name")
        return None

    def exists(self, cid):
        if self.compound_row(cid) or self.lcss_row(cid):
            return True
        if self.names is not None:
            return self.names.execute("SELECT 1 FROM titles WHERE cid = ?", (cid,)).fetchone() is not None
        return False

    def synonyms(self, cid, limit=50):
        conn, table = (self.names, "synonyms") if self.names is not None else (self.lcss, "lcss_synonyms")
        if conn is None:
            return []
        rows = conn.execute("SELECT name FROM %s WHERE cid = ? ORDER BY rank LIMIT ?" % table, (cid, limit))
        return [r[0] for r in rows]

    def has_lcss(self, cids):
        if self.lcss is None or not cids:
            return set()
        q = "SELECT cid FROM lcss_records WHERE cid IN (%s)" % ",".join("?" * len(cids))
        return set(r[0] for r in self.lcss.execute(q, list(cids)))

    # ------------------------------------------------------------------ search

    def _cids_by_inchikey(self, inchikey, limit):
        cids = []
        col = "inchikey" if len(inchikey) > 14 else "inchikey_1"
        if self.compounds is not None:
            cids += [r[0] for r in self.compounds.execute(
                "SELECT cid FROM compounds WHERE %s = ? ORDER BY cid LIMIT ?" % col, (inchikey, limit))]
        if self.lcss is not None:
            if col == "inchikey":
                q, arg = "SELECT cid FROM lcss_records WHERE inchikey = ? ORDER BY cid LIMIT ?", inchikey
            else:
                q, arg = "SELECT cid FROM lcss_records WHERE inchikey LIKE ? ORDER BY cid LIMIT ?", inchikey + "-%"
            cids += [r[0] for r in self.lcss.execute(q, (arg, limit))]
        return cids

    def _cids_by_formula(self, formula, limit):
        cids = []
        if self.lcss is not None:
            cids += [r[0] for r in self.lcss.execute(
                "SELECT cid FROM lcss_records WHERE molecular_formula = ? ORDER BY cid LIMIT ?", (formula, limit))]
        if self.compounds is not None:
            cids += [r[0] for r in self.compounds.execute(
                "SELECT cid FROM compounds WHERE molecular_formula = ? ORDER BY cid LIMIT ?", (formula, limit))]
        return cids

    def _name_matches(self, name, limit, prefix=False):
        """:return: list of (cid, matched name, rank)"""
        results = []
        sources = []
        if self.names is not None:
            sources.append((self.names, "synonyms"))
        if self.lcss is not None:
            sources.append((self.lcss, "lcss_synonyms"))
        for conn, table in sources:
            if prefix:
                rows = conn.execute(
                    "SELECT cid, name, rank FROM %s WHERE name >= ? AND name < ? LIMIT ?" % table,
                    (name, name + "\U0010ffff", limit * 20))
            else:
                rows = conn.execute("SELECT cid, name, rank FROM %s WHERE name = ? LIMIT ?" % table,
                                    (name, limit * 20))
            results += [(r[0], r[1], r[2]) for r in rows]
        return results

    def _structure_to_inchikey(self, text):
        if Chem is None:
            return None
        try:
            if text.startswith("InChI="):
                return Chem.InchiToInchiKey(text)
            mol = Chem.MolFromSmiles(text)
            return Chem.MolToInchiKey(mol) if mol is not None else None
        except Exception:
            return None

    def search(self, query, limit=20):
        """
        Search by CID, name/synonym, CAS number, InChIKey, InChI, molecular formula or SMILES.

        :return: list of hits (dicts), best first
        """
        q = query.strip()
        if not q:
            return []
        hits = OrderedDict()

        def add(cid, match_type, matched=None, score=0.0):
            if cid not in hits or hits[cid]["score"] < score:
                hits[cid] = {"cid": cid, "match_type": match_type, "matched": matched or q, "score": score}

        m = CID_RE.match(q)
        if m and self.exists(int(m.group(1))):
            add(int(m.group(1)), "cid", score=100)

        if INCHIKEY_RE.match(q) or INCHIKEY1_RE.match(q):
            for cid in self._cids_by_inchikey(q, limit):
                add(cid, "inchikey", score=90)

        if q.startswith("InChI="):
            key = self._structure_to_inchikey(q)
            if key:
                for cid in self._cids_by_inchikey(key, limit):
                    add(cid, "inchi", score=90)

        looks_like_formula = bool(FORMULA_RE.match(q)) and any(ch.isdigit() for ch in q)

        # Exact name / synonym / CAS matches (case-insensitive)
        for cid, name, rank in self._name_matches(q, limit):
            score = (55 if looks_like_formula else 80) - min(rank, 50) * 0.1
            add(cid, "cas" if CAS_RE.match(q) else "name", name, score=score)

        if looks_like_formula or (FORMULA_RE.match(q) and len(q) <= 4):
            for cid in self._cids_by_formula(q, limit):
                add(cid, "formula", q, score=70 if looks_like_formula else 60)

        if " " not in q and (SMILES_HINT_RE.search(q) or not hits) and not q.startswith("InChI="):
            key = self._structure_to_inchikey(q)
            if key:
                for cid in self._cids_by_inchikey(key, limit):
                    add(cid, "smiles", q, score=85)

        # Prefix matches to fill up the list
        if len(hits) < limit and len(q) >= 3 and not q.isdigit():
            for cid, name, rank in self._name_matches(q, limit, prefix=True):
                add(cid, "name_prefix", name, score=40 - min(rank, 50) * 0.1 - len(name) * 0.01)

        ranked = list(hits.values())
        lab = self.has_lcss([h["cid"] for h in ranked])
        # Within the same score band prefer compounds with lab data, then lower CIDs (usually the common compound).
        ranked.sort(key=lambda h: (-int(h["score"]), h["cid"] not in lab, -h["score"], h["cid"]))
        return [self.summary(h["cid"], extra={"match_type": h["match_type"], "matched": h["matched"]},
                             has_lab_data=h["cid"] in lab) for h in ranked[:limit]]

    def _prefix(self, conn, table, prefix, n, titles_only):
        rank_filter = " AND rank = 0" if titles_only else " AND rank > 0"
        rows = conn.execute("SELECT cid, name, rank FROM %s WHERE name >= ? AND name < ?%s LIMIT ?"
                            % (table, rank_filter), (prefix, prefix + "\U0010ffff", n))
        return [(r[0], r[1], r[2]) for r in rows]

    def autocomplete(self, prefix, limit=10):
        """
        Name suggestions. Preferred names of compounds with lab safety data come first (these are the chemicals
        commonly found in a lab), then their synonyms, then names of all other compounds. Within each group lower
        CIDs (usually the more common compounds) come first.
        """
        prefix = prefix.strip()
        if len(prefix) < 2:
            return []
        groups = []
        if self.lcss is not None:
            groups.append(sorted(self._prefix(self.lcss, "lcss_synonyms", prefix, 2000, True),
                                 key=lambda m: (len(m[1]) > 30, m[0])))
            groups.append(sorted(self._prefix(self.lcss, "lcss_synonyms", prefix, 300, False),
                                 key=lambda m: (len(m[1]), m[0])))
        if self.names is not None:
            groups.append(sorted(self._prefix(self.names, "synonyms", prefix, 300, True),
                                 key=lambda m: (len(m[1]) > 30, m[0])))
            groups.append(sorted(self._prefix(self.names, "synonyms", prefix, 300, False),
                                 key=lambda m: (len(m[1]), m[0])))
        out, seen_names, seen_cids = [], set(), set()
        for group in groups:
            for cid, name, rank in group:
                key = name.lower()
                if key in seen_names or cid in seen_cids:
                    continue
                seen_names.add(key)
                seen_cids.add(cid)
                out.append({"name": name, "cid": cid})
                if len(out) >= limit:
                    return out
        return out

    # ------------------------------------------------------------------ compound details

    def summary(self, cid, extra=None, has_lab_data=None):
        crow = self.compound_row(cid) or {}
        lrow = self.lcss_row(cid) or {}
        cas = lrow.get("cas")
        if not cas:
            found = self.cas_numbers(cid, limit=1)
            cas = found[0] if found else None
        out = {
            "cid": cid,
            "title": self.title(cid),
            "molecular_formula": crow.get("molecular_formula") or lrow.get("molecular_formula"),
            "molecular_weight": crow.get("molecular_weight") or lrow.get("molecular_weight"),
            "cas": cas,
            "has_lab_data": bool(lrow) if has_lab_data is None else has_lab_data,
        }
        if extra:
            out.update(extra)
        return out

    def cas_numbers(self, cid, limit=5):
        return [s for s in self.synonyms(cid, limit=200) if CAS_RE.match(s)][:limit]

    def physical_properties(self, cid):
        """Parsed experimental properties with per-source values and a median summary."""
        if self.lcss is None:
            return {}
        rows = self.lcss.execute(
            "SELECT p.prop, p.value, p.low, p.high, p.standard, i.value AS raw, i.ref_num, r.source_name "
            "FROM lcss_props p JOIN lcss_info i ON i.cid = p.cid AND i.seq = p.info_seq "
            "LEFT JOIN lcss_refs r ON r.cid = p.cid AND r.ref_num = i.ref_num "
            "WHERE p.cid = ? ORDER BY p.prop, p.info_seq", (cid,)).fetchall()
        grouped = OrderedDict()
        for r in rows:
            grouped.setdefault(r["prop"], []).append({
                "value": r["value"], "low": r["low"], "high": r["high"], "standard": bool(r["standard"]),
                "raw": r["raw"], "source": r["source_name"]})
        out = OrderedDict()
        for prop in propparse.UNITS:
            if prop in grouped:
                summary = propparse.summarize(prop, grouped[prop]) or {"value": None, "unit": propparse.UNITS[prop]}
                summary["sources"] = grouped[prop]
                out[prop] = summary
        return out

    def text_properties(self, cid, headings=("Physical Description", "Color/Form", "Odor", "Solubility",
                                             "Vapor Pressure", "Vapor Density", "Viscosity", "Stability/Shelf Life",
                                             "Decomposition", "pH", "Dissociation Constants", "LogP")):
        """Free-text experimental properties (first few values per heading)."""
        if self.lcss is None:
            return {}
        out = OrderedDict()
        q = ("SELECT i.heading, i.value, r.source_name FROM lcss_info i "
             "LEFT JOIN lcss_refs r ON r.cid = i.cid AND r.ref_num = i.ref_num "
             "WHERE i.cid = ? AND i.heading IN (%s) ORDER BY i.seq" % ",".join("?" * len(headings)))
        for r in self.lcss.execute(q, (cid,) + tuple(headings)):
            lst = out.setdefault(r["heading"], [])
            if len(lst) < 8:
                lst.append({"value": r["value"], "source": r["source_name"]})
        return out

    def safety(self, cid):
        """GHS summary: signal word, pictograms, hazard and precautionary statements."""
        lrow = self.lcss_row(cid)
        if not lrow:
            return None
        rows = self.lcss.execute(
            "SELECT name, value, ref_num FROM lcss_info WHERE cid = ? AND heading = 'GHS Classification' "
            "ORDER BY seq", (cid,)).fetchall()
        first_ref = rows[0]["ref_num"] if rows else None
        hazards, precautions = [], []
        for r in rows:
            if r["ref_num"] != first_ref:
                continue
            if r["name"] == "GHS Hazard Statements":
                hazards.append(r["value"])
            elif r["name"] == "Precautionary Statement Codes":
                precautions.append(re.sub(r"\s*\(click each P-code.*\)$", "", r["value"]))
        pictograms = []
        for part in (lrow.get("pictograms") or "").split(";"):
            m = re.match(r"\s*(.*?)\s*\((GHS\d\d)\)", part)
            if m:
                pictograms.append({"code": m.group(2), "name": m.group(1)})
        nfpa = self.lcss.execute(
            "SELECT name, value FROM lcss_info WHERE cid = ? AND heading = 'NFPA Hazard Classification' "
            "ORDER BY seq LIMIT 6", (cid,)).fetchall()
        return {
            "signal_word": lrow.get("signal_word"),
            "pictograms": pictograms,
            "hazard_statements": hazards,
            "precautionary_statement_codes": ", ".join(precautions) or None,
            "nfpa": [{"name": r["name"], "value": r["value"]} for r in nfpa],
        }

    def lcss_sections(self, cid):
        """The complete laboratory chemical safety summary as a nested list of sections."""
        if self.lcss is None:
            return None
        rows = self.lcss.execute(
            "SELECT i.seq, i.path, i.heading, i.name, i.value, i.note, r.source_name, r.url "
            "FROM lcss_info i LEFT JOIN lcss_refs r ON r.cid = i.cid AND r.ref_num = i.ref_num "
            "WHERE i.cid = ? ORDER BY i.seq", (cid,)).fetchall()
        if not rows:
            return None
        sections = OrderedDict()
        for r in rows:
            top = (r["path"].split(" > ")[0] if r["path"] else r["heading"]) or "Other"
            sub = r["heading"]
            entries = sections.setdefault(top, OrderedDict()).setdefault(sub, [])
            entries.append({"name": r["name"], "value": r["value"], "note": r["note"],
                            "source": r["source_name"], "url": r["url"]})
        return [{"heading": top, "sections": [{"heading": h, "entries": e} for h, e in subs.items()]}
                for top, subs in sections.items()]

    def compound(self, cid):
        """Everything we know about a compound, or None if the CID is unknown."""
        crow = self.compound_row(cid)
        lrow = self.lcss_row(cid)
        title = self.title(cid)
        if crow is None and lrow is None and title is None:
            return None
        crow = crow or {}
        lrow = lrow or {}
        synonyms = self.synonyms(cid)
        identifiers = OrderedDict([
            ("iupac_name", crow.get("iupac_name") or lrow.get("iupac_name")),
            ("cas", lrow.get("cas") or next((s for s in synonyms if CAS_RE.match(s)), None)),
            ("inchi", crow.get("inchi") or lrow.get("inchi")),
            ("inchikey", crow.get("inchikey") or lrow.get("inchikey")),
            ("smiles", crow.get("smiles") or _smiles_from_inchi(lrow.get("inchi"))),
            ("connectivity_smiles", crow.get("connectivity_smiles")),
        ])
        computed = OrderedDict((k, v) for k, v in crow.items()
                               if k not in identifiers and k not in ("cid", "inchikey_1"))
        if not computed and lrow:
            computed = OrderedDict([("molecular_formula", lrow.get("molecular_formula")),
                                    ("molecular_weight", lrow.get("molecular_weight"))])
        return OrderedDict([
            ("cid", cid),
            ("title", title),
            ("pubchem_url", PUBCHEM_URL % cid),
            ("identifiers", identifiers),
            ("computed_properties", computed),
            ("physical_properties", self.physical_properties(cid)),
            ("other_properties", self.text_properties(cid)),
            ("safety", self.safety(cid)),
            ("synonyms", synonyms),
            ("has_lab_data", bool(lrow)),
        ])

    def lab_summary(self, cid):
        """A compact record with the values typically needed in a lab notebook / report."""
        c = self.compound(cid)
        if c is None:
            return None
        phys = c["physical_properties"]
        safety = c["safety"] or {}

        def val(prop):
            p = phys.get(prop)
            return p["value"] if p else None

        return OrderedDict([
            ("cid", cid),
            ("name", c["title"]),
            ("iupac_name", c["identifiers"]["iupac_name"]),
            ("cas", c["identifiers"]["cas"]),
            ("molecular_formula", c["computed_properties"].get("molecular_formula")),
            ("molecular_weight", c["computed_properties"].get("molecular_weight")),
            ("smiles", c["identifiers"]["smiles"]),
            ("boiling_point_c", val("boiling_point")),
            ("melting_point_c", val("melting_point")),
            ("density_g_cm3", val("density")),
            ("flash_point_c", val("flash_point")),
            ("refractive_index", val("refractive_index")),
            ("signal_word", safety.get("signal_word")),
            ("pictograms", [p["code"] for p in safety.get("pictograms", [])]),
            ("hazard_statements", safety.get("hazard_statements", [])),
            ("pubchem_url", c["pubchem_url"]),
        ])

    def resolve(self, identifier):
        """Best single compound for an identifier (name, CAS, CID, InChIKey, SMILES, formula)."""
        hits = self.search(identifier, limit=1)
        return hits[0]["cid"] if hits else None

    # ------------------------------------------------------------------ structure depiction

    def structure_svg(self, cid, width=300, height=240, dark=False):
        if Chem is None:
            return None
        crow = self.compound_row(cid) or {}
        mol = None
        if crow.get("smiles"):
            mol = Chem.MolFromSmiles(crow["smiles"])
        if mol is None:
            inchi = crow.get("inchi") or (self.lcss_row(cid) or {}).get("inchi")
            if inchi:
                mol = Chem.MolFromInchi(inchi)
        if mol is None:
            return None
        drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
        opts = drawer.drawOptions()
        opts.clearBackground = False
        opts.bondLineWidth = 1.5
        if dark:
            opts.setAtomPalette({-1: (0.9, 0.9, 0.9), 6: (0.9, 0.9, 0.9), 1: (0.9, 0.9, 0.9),
                                 7: (0.45, 0.6, 1.0), 8: (1.0, 0.4, 0.4), 16: (0.95, 0.85, 0.3),
                                 9: (0.4, 0.85, 0.4), 17: (0.4, 0.85, 0.4), 35: (0.85, 0.5, 0.35),
                                 15: (1.0, 0.6, 0.2), 53: (0.75, 0.45, 0.95)})
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
        drawer.FinishDrawing()
        return drawer.GetDrawingText()

    # ------------------------------------------------------------------ statistics

    def stats(self):
        out = {}
        if self.compounds is not None:
            out["compounds"] = self.compounds.execute(
                "SELECT COALESCE(SUM(n_compounds),0) FROM sdf_file").fetchone()[0]
        if self.names is not None:
            meta = dict(self.names.execute("SELECT key, value FROM meta").fetchall())
            out["titles"] = int(meta.get("titles", 0))
            out["synonyms"] = int(meta.get("synonyms", 0))
        if self.lcss is not None:
            out["lab_safety_records"] = self.lcss.execute("SELECT COUNT(*) FROM lcss_records").fetchone()[0]
        return out
