"""
Best-effort parsing of the free text experimental property values found in PubChem's Laboratory Chemical Safety
Summaries (e.g. "313 to 316 °F at 758 mmHg (NTP, 1992)") into numbers with normalised units.

Every parsed value keeps a reference to its raw text and source, so callers can always show where a number came from.
Values measured at non-standard pressure (boiling points) or that describe vapours (density) are marked as not
'standard' and are left out of the summary values.
"""
import re
import statistics

_NUM = r"[-−–]?\s?\d+(?:[.,]\d+)?"
_DEG = r"(?:°|º|˚|deg(?:rees?)?\.?|DEG)"
_TEMP_RE = re.compile(
    r"(?<![\w.])(?P<a>%s)(?:\s*(?:to|-|–|—|and)\s*(?P<b>%s))?\s*%s\s*(?P<u>[CFK])(?![a-zA-Z])" % (_NUM, _NUM, _DEG),
    re.I)
_PRESSURE_RE = re.compile(
    r"(?P<v>\d+(?:\.\d+)?)\s*(?P<u>mm\s*hg|mmhg|torr|kpa|hpa|mbar|atm|bar|pa)\b", re.I)
_DENSITY_UNIT_RE = re.compile(
    r"(?P<v>\d*\.?\d+)\s*(?P<u>g\s*/\s*(?:cm\s*3|cm³|cu\s*cm|cc|ml|mL)|g/cm\^3|kg\s*/\s*(?:m\s*3|m³|cu\s*m)|kg\s*/\s*l|g\s*/\s*l)\b",
    re.I)
_FIRST_NUMBER_RE = re.compile(r"(?<![\w.])(\d*\.\d+|\d+)(?![\d])")
_VAPOUR_RE = re.compile(r"vapou?r|air\s*=\s*1|\(air\)|gas|molten|melt|solution|aqueous|\d\s*%", re.I)

# LCSS heading -> property key
HEADINGS = {
    "Boiling Point": "boiling_point",
    "Melting Point": "melting_point",
    "Flash Point": "flash_point",
    "Autoignition Temperature": "autoignition_temperature",
    "Density": "density",
    "Refractive Index": "refractive_index",
}

UNITS = {
    "boiling_point": "°C",
    "melting_point": "°C",
    "flash_point": "°C",
    "autoignition_temperature": "°C",
    "density": "g/cm³",
    "refractive_index": "",
}


def _num(text):
    text = text.replace(" ", "").replace("−", "-").replace("–", "-").replace(",", ".")
    return float(text)


def _to_celsius(value, unit):
    unit = unit.upper()
    if unit == "F":
        return (value - 32.0) * 5.0 / 9.0
    if unit == "K":
        return value - 273.15
    return value


def _pressure_mmhg(value, unit):
    unit = re.sub(r"\s+", "", unit.lower())
    factor = {"mmhg": 1.0, "torr": 1.0, "kpa": 7.50062, "hpa": 0.750062, "mbar": 0.750062, "atm": 760.0,
              "bar": 750.062, "pa": 0.00750062}[unit]
    return value * factor


def parse_temperature(text, check_pressure=False):
    """
    :return: dict(value, low, high, standard) in °C, or None
    """
    m = _TEMP_RE.search(text)
    if not m:
        return None
    try:
        low = _to_celsius(_num(m.group("a")), m.group("u"))
        high = _to_celsius(_num(m.group("b")), m.group("u")) if m.group("b") else low
    except ValueError:
        return None
    if high < low:
        # "-94 - 90 °C" style ranges are ambiguous, treat as single value
        high = low
    standard = True
    if check_pressure:
        p = _PRESSURE_RE.search(text[m.end():]) or _PRESSURE_RE.search(text)
        if p:
            mmhg = _pressure_mmhg(float(p.group("v")), p.group("u"))
            standard = 700.0 <= mmhg <= 800.0
    return {"value": (low + high) / 2.0, "low": low, "high": high, "standard": standard}


def parse_density(text):
    """
    :return: dict(value, low, high, standard) in g/cm³ (specific gravity is treated as g/cm³), or None
    """
    standard = not _VAPOUR_RE.search(text)
    m = _DENSITY_UNIT_RE.search(text)
    if m:
        value = float(m.group("v"))
        unit = re.sub(r"\s+", "", m.group("u").lower())
        if unit.startswith("kg/m") or unit.startswith("kg/cu") or unit == "g/l":
            value /= 1000.0
    else:
        # Specific gravity / relative density without unit, e.g. "0.961 at 68 °F" or "Relative density: 0.79".
        # Use the first number that is not part of a temperature.
        temps = [(t.start(), t.end()) for t in _TEMP_RE.finditer(text)]
        value = None
        for n in _FIRST_NUMBER_RE.finditer(text):
            if any(s <= n.start() < e for s, e in temps):
                continue
            # skip reference values like "(water = 1)"
            before = text[max(0, n.start() - 3):n.start()]
            if "=" in before:
                continue
            value = float(n.group(1))
            break
        if value is None:
            return None
    if not (0.01 < value < 25.0):
        return None
    # Densities measured far from room temperature are not useful as a lab reference value
    temp = parse_temperature(text)
    if temp is not None and not (0.0 <= temp["value"] <= 40.0):
        standard = False
    return {"value": value, "low": value, "high": value, "standard": standard}


def parse_refractive_index(text):
    for n in _FIRST_NUMBER_RE.finditer(text):
        value = float(n.group(1))
        if 1.2 <= value <= 2.5:
            return {"value": value, "low": value, "high": value, "standard": True}
    return None


def parse_property(prop, text):
    if not text:
        return None
    if prop == "boiling_point":
        return parse_temperature(text, check_pressure=True)
    if prop in ("melting_point", "flash_point", "autoignition_temperature"):
        return parse_temperature(text)
    if prop == "density":
        return parse_density(text)
    if prop == "refractive_index":
        return parse_refractive_index(text)
    return None


def _round(value, prop):
    if prop in ("density", "refractive_index"):
        return round(value, 4)
    return round(value, 1)


def summarize(prop, values):
    """
    :param values: list of dicts with keys 'value' and 'standard'
    :return: dict(value, unit, min, max, n) using the median of all standard values, or None
    """
    nums = [v["value"] for v in values if v.get("standard") and v.get("value") is not None]
    if not nums:
        return None
    return {
        "value": _round(statistics.median(nums), prop),
        "unit": UNITS.get(prop, ""),
        "min": _round(min(nums), prop),
        "max": _round(max(nums), prop),
        "n": len(nums),
    }
