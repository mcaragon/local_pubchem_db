import re
import unittest

from rdkit import Chem

from pubchem_local.depict import depict_svg, HASH_MARKS


class TestDepict(unittest.TestCase):
    def test_bonds_black_labels_coloured(self):
        svg = depict_svg(Chem.MolFromSmiles("CC(C(=O)C1=CC(=CC=C1)Cl)NC(C)(C)C"))  # bupropion
        bond_colours = set(re.findall(r"<path class='bond-[^>]*stroke:(#[0-9A-Fa-f]{6})", svg))
        self.assertEqual({"#000000"}, bond_colours)
        self.assertIn("#3050F8", svg.upper())  # N label in CDK blue
        self.assertIn("stroke-linecap:round", svg)

    def test_hashed_wedges_thinned(self):
        svg = depict_svg(Chem.MolFromSmiles("CC(C)[C@@H]1CC[C@@H](C)C[C@H]1O"))  # menthol
        counts = {}
        for bond in re.findall(r"<path class='(bond-\d+) ", svg):
            counts[bond] = counts.get(bond, 0) + 1
        self.assertTrue(counts)
        self.assertLessEqual(max(counts.values()), HASH_MARKS)

    def test_no_bonds_and_dark(self):
        self.assertIn("<svg", depict_svg(Chem.MolFromSmiles("[BH4-].[Na+]")))
        dark = depict_svg(Chem.MolFromSmiles("CCO"), dark=True)
        self.assertNotIn("stroke:#000000", dark)

    def test_no_chiral_h_labels(self):
        svg = depict_svg(Chem.MolFromSmiles("CN1CC[C@]23C4=C5C=CC(O)=C4O[C@H]2[C@@H](O)C=C[C@H]3[C@H]1C5"))
        self.assertNotIn("class='atom-21'", svg)  # no added explicit H atoms beyond morphine's 21 heavy atoms


if __name__ == "__main__":
    unittest.main()
