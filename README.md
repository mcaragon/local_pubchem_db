# PubChem Local

A self-hosted, **offline copy of the [PubChem](https://pubchem.ncbi.nlm.nih.gov/) chemistry database** with a web UI
and a REST API, packaged as a Docker container (with an Unraid template).

* Search by **name, CAS number, molecular formula, SMILES, InChI/InChIKey or CID** (with autocomplete).
* Compound pages with 2D structure, identifiers, computed properties, **experimental physical properties**
  (boiling point, melting point, density, flash point, refractive index, solubility, ...) and **GHS safety data**
  (pictograms, signal word, H/P statements) plus PubChem's full Laboratory Chemical Safety Summary.
* Structure drawings rendered with the **Chemistry Development Kit** (CDK Depict), as SVG or PNG, with optional
  R/S labels and group abbreviations (`/api/depict`).
* A documented **REST API** (`/docs`) designed for other apps, e.g. a lab notebook that auto-fills molar masses,
  densities and boiling points: `POST /api/lab/batch` resolves a list of reagents in one call.
* Builds from PubChem's public bulk downloads; the database files are plain SQLite.

Based on [bachi55/local_pubchem_db](https://github.com/bachi55/local_pubchem_db) (the `pubchem2sqlite` library,
still included and documented [below](#the-pubchem2sqlite-library)).

## Datasets

The data is split into three independent SQLite databases that you can build (and rebuild) separately from the
web UI ("Databases" page):

| Dataset | Contents | Download | Disk | Build time* |
| --- | --- | --- | --- | --- |
| `lcss` — Lab safety & experimental data | ~257,000 compounds that matter in a lab: experimental properties, GHS hazards, handling, storage, first aid, disposal, with sources (`CID-LCSS.xml.gz`) | 0.5 GB | 2 GB | ~5 min |
| `names` — Names & synonyms | Preferred names and ~100M synonyms (trade names, CAS numbers, ...) for all compounds; enables name search across all of PubChem | 3 GB | ~22 GB | ~30 min |
| `compounds` — All compounds | ~120 million compounds: formula, masses, SMILES, InChI/InChIKey, IUPAC name, XLogP3, TPSA, H-bond donors/acceptors, ... (`CURRENT-Full/SDF`) | 110 GB | ~70–100 GB (+ ~15 GB temporary while building) | several hours |

\* on a 4-core machine with a fast connection; the compound build is resumable and parses files in parallel.

**Start with `lcss`**: it builds in minutes and on its own already gives a useful lab-chemical database (name/CAS
search, structures, properties, safety). Add `names` and `compounds` for the complete PubChem compound collection.

Experimental values are compiled by PubChem from many sources and are given as free text (e.g.
*"313 to 316 °F at 758 mmHg (NTP, 1992)"*). PubChem Local parses them to numbers in °C / g/cm³ and reports the
**median of all values at standard conditions** (boiling points at reduced pressure, vapour densities, values for
molten material or solutions are excluded). Every individual source value is always shown/returned as well, so verify
critical values before relying on them.

## Installation on Unraid

The databases need up to ~125 GB (all datasets), so give them a **dedicated share** instead of `appdata`:

1. **Shares → Add Share**, name it `pubchem-local`:
   * *Primary storage*: your SSD **cache pool**. SQLite builds on the parity-protected array are very slow.
   * *Secondary storage*: **Array**, *Mover action*: **Cache → Array** if your cache can't hold everything
     permanently. Builds then run at SSD speed and the mover moves the finished databases to the array.
     Searching from the array works fine (the first query after the disks spin down takes a few seconds).
     Otherwise *Secondary storage: None* (cache only).
   * The cache still needs room for the dataset that is being built: ~25 GB for names, ~100–120 GB for all
     compounds (database + temporary sort space).
2. **Docker → Add Container**, or download [`unraid/pubchem-local.xml`](unraid/pubchem-local.xml) to
   `/boot/config/plugins/dockerMan/templates-user/my-pubchem-local.xml` and pick it under *Template → User templates*:
   * Repository: `ghcr.io/mcaragon/local_pubchem_db:latest`
   * Port: `8080` (container) → any free host port
   * Path: `/mnt/user/pubchem-local` → `/data`
3. Start the container and open the WebUI. Go to **Databases**, select the datasets and click
   **Download & build selected**. Progress and logs are shown on that page.

Everything lives under `/data` (one mapping): `db/` (the SQLite databases), `downloads/`, `tmp/` (temporary files,
including SQLite's sort files), `state/` and `logs/`. Downloaded SDF files are deleted after import unless
`KEEP_SDF_FILES=true`.

### Moving an existing installation to a new share

1. On the **Databases** page, cancel a running build (imported compound files are kept; the build resumes later).
2. Stop the container.
3. Copy the data, check it, then delete the old folder:
   ```bash
   rsync -a --info=progress2 /mnt/user/appdata/pubchem-local/ /mnt/user/pubchem-local/
   ```
   ```bash
   rm -rf /mnt/user/appdata/pubchem-local
   ```
4. Edit the container, change the host path of `/data` to `/mnt/user/pubchem-local`, click **Apply**, and restart any
   build from the **Databases** page.

## Installation with Docker / Docker Compose

```bash
docker run -d --name pubchem-local -p 8080:8080 -v /path/to/pubchem-data:/data \
  -e AUTO_BUILD=lcss,names ghcr.io/mcaragon/local_pubchem_db:latest
```

or use the included [`docker-compose.yml`](docker-compose.yml). To build the image yourself: `docker build -t pubchem-local .`

### Configuration (environment variables)

| Variable | Default | Description |
| --- | --- | --- |
| `AUTO_BUILD` | *(empty)* | Datasets to build automatically on start if they don't exist yet: `lcss`, `names`, `compounds` or `all` |
| `WORKERS` | CPU cores − 1 (max 8) | Parallel processes for parsing SDF files |
| `DOWNLOAD_CONCURRENCY` | `3` | Parallel downloads from PubChem |
| `KEEP_SDF_FILES` | `false` | Keep the ~110 GB of SDF files after import |
| `ADMIN_TOKEN` | *(empty)* | If set, starting/cancelling builds requires this token (`X-Admin-Token` header). Searching is always open |
| `CORS_ORIGINS` | `*` | Origins allowed to call the API from browser apps (comma separated) |
| `PUID` / `PGID` | `99` / `100` | User/group the app runs as (Unraid's `nobody:users`) |
| `PORT` | `8080` | Port inside the container |
| `SDF_LIMIT` | `0` | Only import the first N SDF files (for testing) |

Builds can also be started from the command line:

```bash
docker exec pubchem-local pubchem-local build --datasets lcss,names,compounds
docker exec pubchem-local pubchem-local status
```

## Using the API from other apps

Interactive documentation is at `http://<server>:8080/docs`. See **[docs/API.md](docs/API.md)** for an overview and
examples. A quick taste:

```bash
curl "http://unraid:8080/api/lookup?q=acetone"
curl -X POST http://unraid:8080/api/lab/batch -H 'Content-Type: application/json' \
     -d '{"identifiers": ["benzaldehyde", "75-09-2", "NaBH4", "CCO"]}'
```

## Updating the data

PubChem updates its files weekly. Rebuilding a dataset from the **Databases** page downloads the current files
(unchanged files are skipped via their md5 checksum) and builds a new database next to the old one, which stays online
until the new one is complete.

## Development

```bash
pip install -e ".[server]" httpx
mvn -f java/pom.xml org.apache.maven.plugins:maven-dependency-plugin:3.8.1:copy-dependencies -DoutputDirectory=lib   # CDK jars (needs Java + Maven; optional)
python -m unittest pubchem2sqlite/tests/unittests_utils.py
python -m unittest discover -s pubchem_local/tests -t .
PUBCHEM_DATA_DIR=./pubchem-data pubchem-local build --datasets lcss
PUBCHEM_DATA_DIR=./pubchem-data pubchem-local serve --port 8080
```

Code layout: `pubchem2sqlite/` (SDF → SQLite library), `pubchem_local/` (downloads, importers, API, web UI in
`static/`), `java/` (CDK dependency list), `docker/`, `unraid/`. Without Java/CDK, structures are drawn with RDKit.

---

## The pubchem2sqlite library

This library can be used to build an [SQLite](https://www.sqlite.org/index.html) database (DB) containing all compounds in [PubChem](https://pubchem.ncbi.nlm.nih.gov/). A simple [json-file](default_db_layout.json) is used to specify the layout of the SQLite DB allowing to controll which PubChem [SD-tags](https://ftp.ncbi.nlm.nih.gov/pubchem/specifications/pubchem_sdtags.pdf) should be included.

### Installation 
 
The library libraries only requirement is Python (tested on 3.11 - 3.13), no external libraries need to be installed. Simply run:
```bash
git clone https://github.com/mcaragon/local_pubchem_db
cd local_pubchem_db
pip install .
```

### Usage

#### (1) Download PubChem SDF-files

PubChem allows to [download a snapshot](https://ftp.ncbi.nlm.nih.gov/pubchem/Compound/CURRENT-Full/SDF/) of their current database (~110GB). You can use the following command to retrieve the sdf-files: 
```bash
wget -e robots=off --recursive --no-parent https://ftp.ncbi.nlm.nih.gov/pubchem/Compound/CURRENT-Full/SDF/
mkdir -p pubchem/sdf
mv ftp.ncbi.nlm.nih.gov/pubchem/Compound/CURRENT-Full/SDF/*.sdf.gz pubchem/sdf
rm -r ftp.ncbi.nlm.nih.gov/pubchem/Compound/CURRENT-Full/SDF
```

#### (2) Specifiy the DB Layout

You can specify the DB layout using a simple json-file (see also this [example](default_db_layout.json)):
```json
{
    "columns": {
        "InChI": {
            "SD_TAG": ["PUBCHEM_IUPAC_INCHI"],
            "DTYPE": "varchar",
            "NOT_NULL": true,
            "PRIMARY_KEY": true
        },
        "InChIKey": {
            "SD_TAG": ["PUBCHEM_IUPAC_INCHIKEY"],
            "DTYPE": "varchar",
            "NOT_NULL": true
        },
        "InChIKey_1": {
            "SD_TAG": ["PUBCHEM_IUPAC_INCHIKEY"],
            "DTYPE": "varchar",
            "NOT_NULL": true,
            "CREATE_LIKE": "lambda __x: __x.split('-')[0]"
        },     
        "xlogp3": {
            "SD_TAG": ["PUBCHEM_XLOGP3", "PUBCHEM_XLOGP3_AA"],
            "DTYPE": "real",
            "NOT_NULL": false
        },
        "cid": {
            "SD_TAG": ["PUBCHEM_COMPOUND_CID"],
            "DTYPE": "integer",
            "NOT_NULL": true
        }
    }
}
```
The json-file contains consists of nested dictionaries. The one containing the DB layout is accessed via ```columns```. This dictionary contains a key for each column name in the [*compounds* table](https://github.com/bachi55/local_pubchem_db/blob/bd339a19ffd8b442eda54f0b8684270eabf4c357/pubchem2sqlite/utils.py#L162) and its properites:

| Key | Description | Optional |
| --- | --- | --- | 
| [SD_TAG](https://ftp.ncbi.nlm.nih.gov/pubchem/specifications/pubchem_sdtags.pdf) | List of SD-Tags correponding to the column. For example ```[PUBCHEM_IUPAC_INCHI]``` refering to the InChI of a compound, or, ```["PUBCHEM_XLOGP3", "PUBCHEM_XLOGP3_AA"]``` to its predicted XLogP3 value (here more than one tag can occure in the sdf-files). | No | 
| [DTYPE](https://github.com/bachi55/local_pubchem_db/blob/bd339a19ffd8b442eda54f0b8684270eabf4c357/pubchem2sqlite/utils.py#L9) | Type of the information behind the sd-tag. Must be a valid SQLite type. | No |
| NOT_NULL | If true, the column cannot contain null entries. If a compound does not have the requested property and the corresponding column should not be null, *it will not be added* to tha DB. | Yes |
| PRIMARY_KEY | If true, the column is used as primary key for the *compounds* table. Currently, only one column can be specified as primary key. | Yes |
| WITH_INDEX | If true, an index is created for the column. This allows faster queries with constraints on this column. | Yes |
| CREATE_LIKE | Value of the SD_TAG can be transformed using a Python lambda function defined as string. For example ```"lambda __x: __x.split('-')[0]"``` applied to the InChIKey tag can be used to only return the first InChIKey part. The lambda function is applied after the value has been converted into the target DTYPE. | Yes |

#### (3) Build the Database

The DB is build using: 
```bash
cd pubchem
mkdir db
# Create your db-layout
touch db/db_layout.json
# Edit it ... 
# vim db/db_layout.json
python /path/to/local_pubchem_db/build_pubchem_db.py pubchem --gzip --db_layout_fn=db/db_layout.json --workers 4
```
Please note: PubChem contains *many* compound and the resulting SQLite file can be very large. Especially when the indices are created sqlite might require twice the memory of the DB for some time. Ensure your drive has enough memory. If you face the problem, that your systems temp-directory runs out of memory, when creating the indices, have a look [here](https://sqlite.org/tempfiles.html#temporary_file_storage_locations). You can temporarily change sqlite's temp-directory by setting ```SQLITE_TMPDIR```:
```bash
SQLITE_TMPDIR=/my/large/disk/temp python /path/to/local_pubchem_db/build_pubchem_db.py pubchem --gzip --db_layout_fn=db/db_layout.json
```
## Version History

#### 0.4:
- Docker image with web UI and REST API (`pubchem_local` package), Unraid template
- Imports lab safety / experimental property data (`CID-LCSS.xml.gz`) and compound names/synonyms
- Fixed default layout for PubChem's current SD-tags (`PUBCHEM_SMILES`, `PUBCHEM_CONNECTIVITY_SMILES`; the
  `PUBCHEM_OPENEYE_*_SMILES` tags no longer exist and made every compound be skipped) and a syntax error in its
  `CREATE_LIKE` lambda
- Much faster, streaming SDF parser; parallel parsing of SDF files (`--workers`); duplicate keys no longer abort a build

#### 0.3:
- Add support for data-transformations of SD_TAG values using lambda functions

#### 0.2:
- Add flexibility for the DB-layout using a json-file
- Add tests
- Publish code as package
- Change license to MIT

#### 0.1:
- Initial Version
