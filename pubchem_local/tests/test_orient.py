"""Standard orientation of CDK layouts (skipped without Java/CDK unless REQUIRE_CDK is set)."""
import math
import os
import unittest

from pubchem_local import cdk

ANALOGUES = {  # PubChem SMILES of the HCl salts (CID 76631, 151318): different atom order and one extra Br
    "2,5-dimethoxyphenethylamine HCl": "COC1=CC(=C(C=C1)OC)CCN.Cl",
    "4-bromo-2,5-dimethoxyphenethylamine HCl": "COC1=CC(=C(C=C1CCN)OC)Br.Cl",
}


def setUpModule():
    if not cdk.available():
        if os.environ.get("REQUIRE_CDK"):
            raise RuntimeError("CDK required but unavailable: %s" % cdk.unavailable_reason())
        raise unittest.SkipTest("CDK (Java) not available: %s" % cdk.unavailable_reason())


def _parse(smiles):
    j = cdk._java
    return j["SmilesParser"](j["SilentChemObjectBuilder"].getInstance()).parseSmiles(smiles)


class TestOrientation(unittest.TestCase):
    def _pose(self, smiles):
        """Directions (degrees) from the ring centroid to the amine N and to the ortho (2-) methoxy O."""
        from pubchem_local import orient
        mol = _parse(smiles)
        orient.layout(cdk._java, mol)
        atoms = list(mol.atoms())
        ring = [a for a in atoms if a.isInRing()]
        cx = sum(a.getPoint2d().x for a in ring) / len(ring)
        cy = sum(a.getPoint2d().y for a in ring) / len(ring)
        n = next(a for a in atoms if a.getSymbol() == "N")
        # the methoxy O attached to the ring carbon next to the ethylamine chain
        chain_c = next(a for a in ring if any(nb.getSymbol() == "C" and not nb.isInRing()
                                              and any(x.getSymbol() == "C" and not x.isInRing()
                                                      for x in mol.getConnectedAtomsList(nb))
                                              for nb in mol.getConnectedAtomsList(a)))
        ortho_o = next(nb2 for nb in mol.getConnectedAtomsList(chain_c) if nb.isInRing()
                       for nb2 in mol.getConnectedAtomsList(nb) if nb2.getSymbol() == "O")

        def angle(atom):
            return round(math.degrees(math.atan2(atom.getPoint2d().y - cy, atom.getPoint2d().x - cx)))
        return angle(n), angle(ortho_o)

    def test_analogues_share_orientation(self):
        poses = [self._pose(s) for s in ANALOGUES.values()]
        self.assertEqual(poses[0], poses[1])
        n_angle, o_angle = poses[0]
        self.assertTrue(-60 < n_angle < 60, "amine should point right")
        self.assertTrue(60 < o_angle < 120, "2-methoxy should point up")

    def test_stereo_preserved(self):
        """Render, write a molfile with CDK's coordinates and wedges, read it back with RDKit, compare."""
        import jpype
        from rdkit import Chem
        from pubchem_local import orient
        writer_cls = jpype.JClass("org.openscience.cdk.io.MDLV2000Writer")
        string_writer = jpype.JClass("java.io.StringWriter")
        for smiles in ("CC(C)[C@@H]1CC[C@@H](C)C[C@H]1O",  # menthol
                       "CC1=CC[C@@H](CC1=O)C(=C)C",  # (R)-carvone (mirrored by the standard orientation)
                       "C1=CC(=C(C=C1C[C@@H](C(=O)O)N)O)O.Cl",  # L-DOPA HCl (mirrored)
                       "CC/C(=C(\\C1=CC=CC=C1)/C2=CC=C(C=C2)OCCN(C)C)/C3=CC=CC=C3"):  # (Z)-tamoxifen (mirrored)
            mol = _parse(smiles)
            orient.layout(cdk._java, mol)
            sw = string_writer()
            w = writer_cls(sw)
            w.write(mol)
            w.close()
            rd = Chem.MolFromMolBlock(str(sw.toString()))
            Chem.AssignChiralTypesFromBondDirs(rd)
            Chem.AssignStereochemistry(rd, cleanIt=True, force=True)
            self.assertEqual(Chem.MolToSmiles(Chem.MolFromSmiles(smiles)), Chem.MolToSmiles(rd), smiles)

    def test_polycyclic_kept(self):
        from pubchem_local import orient
        mol = _parse("C[C@H](CCCC(C)C)[C@H]1CC[C@@H]2[C@@]1(CC[C@H]3[C@H]2CC=C4[C@@]3(CC[C@@H](C4)O)C)C")
        self.assertIsNone(orient.layout(cdk._java, mol))  # cholesterol: CDK's template orientation is kept

    def test_api_option(self):
        a = cdk.depict(ANALOGUES["2,5-dimethoxyphenethylamine HCl"], "svg", 300, 200, orient="standard")[0]
        b = cdk.depict(ANALOGUES["2,5-dimethoxyphenethylamine HCl"], "svg", 300, 200, orient="cdk")[0]
        self.assertNotEqual(a, b)
        with self.assertRaises(cdk.DepictError):
            cdk.depict("CCO", "svg", orient="sideways")


if __name__ == "__main__":
    unittest.main()
