"""
Runtime configuration, read from environment variables (so that it can be set from docker / Unraid).
All values are read lazily, which keeps the package importable for tests that change the environment.
"""
import os
from pathlib import Path

DATASETS = ("lcss", "names", "compounds")


def _env(name, default=None):
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_bool(name, default=False):
    value = _env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def data_dir():
    return Path(_env("PUBCHEM_DATA_DIR", "/data"))


def db_dir():
    return data_dir() / "db"


def download_dir():
    return data_dir() / "downloads"


def tmp_dir():
    return data_dir() / "tmp"


def state_dir():
    return data_dir() / "state"


def log_dir():
    return data_dir() / "logs"


def db_path(dataset):
    return db_dir() / ("%s.sqlite" % dataset)


def building_path(dataset):
    return db_dir() / ("%s.sqlite.building" % dataset)


def pubchem_base_url():
    return _env("PUBCHEM_BASE_URL", "https://ftp.ncbi.nlm.nih.gov/pubchem").rstrip("/")


def workers():
    default = max(1, min(8, (os.cpu_count() or 2) - 1))
    return max(1, int(_env("WORKERS", default)))


def download_concurrency():
    return max(1, int(_env("DOWNLOAD_CONCURRENCY", 3)))


def keep_sdf_files():
    return _env_bool("KEEP_SDF_FILES", False)


def auto_build():
    """Datasets to build automatically on start-up if they do not exist yet, e.g. 'lcss,names,compounds'."""
    value = _env("AUTO_BUILD", "")
    if value.lower() in ("all", "full"):
        return list(DATASETS)
    return [v.strip() for v in value.split(",") if v.strip() in DATASETS]


def admin_token():
    return _env("ADMIN_TOKEN")


def cors_origins():
    return [o.strip() for o in _env("CORS_ORIGINS", "*").split(",") if o.strip()]


def sdf_limit():
    """Only import the first N SDF files (useful for testing a full pipeline quickly). 0 = all."""
    return int(_env("SDF_LIMIT", 0))


def ensure_dirs():
    for d in (db_dir(), download_dir(), tmp_dir(), state_dir(), log_dir()):
        d.mkdir(parents=True, exist_ok=True)
