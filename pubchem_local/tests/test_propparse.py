import unittest

from pubchem_local.propparse import parse_property, summarize


class TestTemperature(unittest.TestCase):
    def check(self, prop, text, value, standard=True, places=1):
        res = parse_property(prop, text)
        self.assertIsNotNone(res, text)
        self.assertAlmostEqual(value, res["value"], places=places, msg=text)
        self.assertEqual(standard, res["standard"], text)

    def test_celsius(self):
        self.check("boiling_point", "159.46 °C @ 760 MM HG", 159.46)
        self.check("boiling_point", "159.5 °C", 159.5)
        self.check("melting_point", "-94.7 °C", -94.7)
        self.check("melting_point", "−94.7 °C", -94.7)
        self.check("boiling_point", "160.00 to 161.00 °C. @ 760.00 mm Hg", 160.5)
        self.check("melting_point", "56-57 °C", 56.5)
        self.check("melting_point", "-95 to -93 °C", -94.0)
        self.check("boiling_point", "56.05 DEG C", 56.05)

    def test_fahrenheit(self):
        self.check("boiling_point", "313 to 316 °F at 758 mmHg (NTP, 1992)", (314.5 - 32) * 5 / 9)
        self.check("flash_point", "171 °F (77 °C) (closed cup)", (171 - 32) * 5 / 9)

    def test_reduced_pressure(self):
        self.check("boiling_point", "85 °C at 15 mm Hg", 85.0, standard=False)
        self.check("boiling_point", "120 °C at 2 kPa", 120.0, standard=False)
        self.check("boiling_point", "100 °C at 101.3 kPa", 100.0, standard=True)

    def test_no_value(self):
        self.assertIsNone(parse_property("melting_point", "Decomposes"))
        self.assertIsNone(parse_property("boiling_point", "Sublimes"))


class TestDensity(unittest.TestCase):
    def check(self, text, value, standard=True):
        res = parse_property("density", text)
        self.assertIsNotNone(res, text)
        self.assertAlmostEqual(value, res["value"], places=4, msg=text)
        self.assertEqual(standard, res["standard"], text)

    def test_units(self):
        self.check("0.96 g/cm³", 0.96)
        self.check("0.7845 g/cu cm at 25 °C", 0.7845)
        self.check("1.11 g/mL", 1.11)
        self.check("1250 kg/m3", 1.25)

    def test_specific_gravity(self):
        self.check("0.961 at 68 °F (USCG, 1999) - Less dense than water; will float", 0.961)
        self.check("0.9611 @ 20 °C/4 °C", 0.9611)
        self.check("Relative density (water = 1): 0.79", 0.79)

    def test_vapour(self):
        self.check("Relative density of the vapour/air-mixture at 20 °C (air = 1): 1.00", 1.0, standard=False)
        self.check("Relative vapor density (air = 1): 2.0", 2.0, standard=False)

    def test_non_standard_conditions(self):
        self.check("Density of molten sodium chloride at 850 °C: 1.549 g/cu cm", 1.549, standard=False)
        self.check("0.9 g/cm3 at 150 °C", 0.9, standard=False)
        self.check("1.05 (10% solution)", 1.05, standard=False)
        self.check("2.17 at 25 °C/4 °C", 2.17)


class TestSummary(unittest.TestCase):
    def test_median(self):
        values = [{"value": 56.0, "standard": True}, {"value": 56.5, "standard": True},
                  {"value": 20.0, "standard": False}, {"value": 57.0, "standard": True}]
        s = summarize("boiling_point", values)
        self.assertEqual(56.5, s["value"])
        self.assertEqual(3, s["n"])
        self.assertEqual("°C", s["unit"])
        self.assertIsNone(summarize("boiling_point", [{"value": 1.0, "standard": False}]))

    def test_refractive_index(self):
        self.assertAlmostEqual(1.3611, parse_property("refractive_index", "Index of refraction: 1.3611 @ 20 °C/D")["value"])


if __name__ == "__main__":
    unittest.main()
