# PubChem Local API

Base URL: `http://<server>:8080`. All responses are JSON. Interactive docs (try requests in the browser):
`/docs` (Swagger UI) and `/redoc`; machine-readable schema: `/openapi.json` (use it to generate a client).

CORS is enabled (configure with `CORS_ORIGINS`), so browser apps on your network can call the API directly.
Search endpoints need no authentication; only build endpoints can be protected with `ADMIN_TOKEN`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/search?q=…&limit=20` | Ranked search by name, CAS, CID, InChIKey, InChI, formula or SMILES |
| GET | `/api/autocomplete?q=…&limit=10` | Name suggestions for a prefix (lab chemicals first) |
| GET | `/api/lookup?q=…` | Best single match → lab summary (add `&full=true` for the full record) |
| POST | `/api/lab/batch` | Resolve many identifiers at once → lab summaries |
| GET | `/api/compound/{cid}` | Full record: identifiers, computed + physical properties, safety, synonyms |
| GET | `/api/compound/{cid}/lab` | Compact lab notebook record |
| GET | `/api/compound/{cid}/safety-summary` | Full Laboratory Chemical Safety Summary (all sections, with sources) |
| GET | `/api/compound/{cid}/synonyms` | Synonyms |
| GET | `/api/depict?cid=…` or `?smi=…` | Structure drawing (CDK Depict): SVG or PNG, styles, R/S labels (see below) |
| GET | `/api/compound/{cid}/structure.svg?width=300&height=240&theme=light` | 2D structure image (same CDK renderer) |
| GET | `/api/health` | Available datasets and counts |
| GET | `/api/build/status` | Dataset and build status |
| POST | `/api/build/start` | Start a build: `{"datasets": ["lcss", "names", "compounds"]}` |
| POST | `/api/build/cancel` | Cancel the running build |

`search` results carry `match_type` (`cid`, `inchikey`, `inchi`, `cas`, `name`, `formula`, `smiles`, `name_prefix`)
and `has_lab_data`.

## Structure drawings: `/api/depict`

Drawn with the [Chemistry Development Kit](https://cdk.github.io/) (the engine behind CDK Depict). Give either a
`cid` (uses PubChem's SMILES, or one derived from the InChI when the compounds dataset isn't built) or a `smi`.

| Parameter | Values | Default |
| --- | --- | --- |
| `smi` / `cid` | SMILES string / PubChem CID (exactly one) | |
| `format` | `svg`, `png` | `svg` |
| `w`, `h` | size in px (both or neither; omitted = automatic size) | automatic |
| `style` | `cow` colour on white, `bow` black on white, `wob` white on black, `cob` colour on black | `cow` |
| `abbr` | `on` contracts common groups (Ph, OMe, OtBu, Boc, ...) | `off` |
| `annotate` | `none`, `number` (atom numbers), `cip` (R/S and E/Z labels) | `none` |
| `zoom` | scale factor; with `w`/`h` set, overrides fit-to-size | |
| `title` | `true` prints the compound name under the drawing (with `cid`) | `false` |

Examples: `/api/depict?cid=2244&w=400&h=300`, `/api/depict?smi=CC(C)[C@@H]1CC[C@@H](C)C[C@H]1O&annotate=cip&format=png`.

Invalid SMILES returns 400. If the server has no Java/CDK, SVGs are drawn with RDKit instead and PNG returns 501; the
`X-Depiction-Engine` response header (and `depiction_engine` in `/api/health`) says which engine was used.

## Lab summary record

`/api/lookup`, `/api/compound/{cid}/lab` and each `found` entry of `/api/lab/batch` return:

```json
{
  "cid": 180,
  "name": "Acetone",
  "iupac_name": "propan-2-one",
  "cas": "67-64-1",
  "molecular_formula": "C3H6O",
  "molecular_weight": 58.08,
  "smiles": "CC(=O)C",
  "boiling_point_c": 56.1,
  "melting_point_c": -94.9,
  "density_g_cm3": 0.791,
  "flash_point_c": -17.8,
  "refractive_index": null,
  "signal_word": "Danger",
  "pictograms": ["GHS02", "GHS07"],
  "hazard_statements": ["H225: Highly Flammable liquid and vapor [Danger Flammable liquids]", "..."],
  "pubchem_url": "https://pubchem.ncbi.nlm.nih.gov/compound/180"
}
```

Values are `null` when unknown. `molecular_weight` and `smiles` require the `compounds` or `lcss` dataset (without
`compounds`, `smiles` is derived from the InChI with RDKit: same structure, possibly different notation than PubChem's);
physical properties and safety data require `lcss`. Each physical property value is the median of all source values at
standard conditions. The sources are in `physical_properties.<property>.sources` of `/api/compound/{cid}`:

```json
"boiling_point": {
  "value": 56.1, "unit": "°C", "min": 56.0, "max": 56.5, "n": 8,
  "sources": [
    {"value": 56.05, "low": 56.05, "high": 56.05, "standard": true, "raw": "56.05 °C", "source": "CAMEO Chemicals"},
    "..."
  ]
}
```

Properties: `boiling_point`, `melting_point`, `flash_point`, `autoignition_temperature` (°C), `density` (g/cm³,
specific gravity is treated as g/cm³), `refractive_index`.

## Examples

### Python

```python
import requests

BASE = "http://unraid:8080"

reagents = ["benzaldehyde", "sodium borohydride", "methanol", "75-09-2"]
resp = requests.post(f"{BASE}/api/lab/batch", json={"identifiers": reagents}).json()
for r in resp["results"]:
    if not r["found"]:
        print(f"{r['query']}: not found")
        continue
    print(f"{r['name']:<22} {r['molecular_weight']:>8} g/mol  d={r['density_g_cm3']}  bp={r['boiling_point_c']} °C")

# Moles from a mass, volume of a liquid reagent
acetone = requests.get(f"{BASE}/api/lookup", params={"q": "acetone"}).json()
grams = 5.0
print("mmol:", grams / acetone["molecular_weight"] * 1000)
print("mL:", grams / acetone["density_g_cm3"])
```

### JavaScript (browser or Node)

```js
const BASE = "http://unraid:8080";
const res = await fetch(`${BASE}/api/lab/batch`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ identifiers: ["acetic anhydride", "salicylic acid"] }),
});
const { results } = await res.json();

// Autocomplete for a reagent input field
const { suggestions } = await (await fetch(`${BASE}/api/autocomplete?q=${encodeURIComponent("acet")}`)).json();

// Structure image for a report (PNG for documents, SVG for web pages)
const img = `<img src="${BASE}/api/depict?cid=${results[0].cid}&w=400&h=300&format=png">`;
```

## Reading the SQLite files directly

The databases in `/data/db` are plain SQLite and can be opened read-only by other tools (for example with
`sqlite3 'file:compounds.sqlite?mode=ro'`). Prefer the REST API for apps: the files are replaced when a dataset is
rebuilt, and the API combines all three databases.

* `compounds.sqlite`: `compounds` (one row per CID, see `pubchem_local/compound_layout.json`), `sdf_file`
* `names.sqlite`: `titles(cid, title)`, `synonyms(cid, rank, name)`
* `lcss.sqlite`: `lcss_records`, `lcss_info` (every value of every section), `lcss_refs`, `lcss_props`
  (parsed numbers), `lcss_synonyms`
