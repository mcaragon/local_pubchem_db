"""
Build pipeline: download the PubChem files and build the SQLite databases.

Every dataset is built into '<name>.sqlite.building' and atomically renamed to '<name>.sqlite' when complete, so the
web server keeps serving the previous version during a rebuild. The compounds build is resumable: SDF files that
were already imported are skipped when the build is restarted.
"""
import json
import os
import shutil
import sqlite3
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from pathlib import Path

from pubchem2sqlite.utils import (load_db_specifications, initialize_db, connect_for_build, merge_shard,
                                  parse_sdf_file_to_shard, create_indices, CompiledSpecs)

from pubchem_local import config, download, ingest_lcss, ingest_names
from pubchem_local.status import StatusReporter, now_iso

COMPOUND_LAYOUT = Path(__file__).with_name("compound_layout.json")
SDF_PATTERN = r"Compound_\d+_\d+\.sdf\.gz"


def _extras_url(name):
    return "%s/Compound/Extras/%s" % (config.pubchem_base_url(), name)


def _sdf_dir_url():
    return "%s/Compound/CURRENT-Full/SDF" % config.pubchem_base_url()


def _fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def _write_meta(db_file, items):
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [(k, str(v)) for k, v in items.items()])
        conn.commit()
    finally:
        conn.close()


def _finalize(dataset):
    """Checkpoint the WAL, then move the finished '.building' database into place."""
    building = config.building_path(dataset)
    conn = sqlite3.connect(str(building))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    target = config.db_path(dataset)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(str(target) + suffix):
            os.remove(str(target) + suffix)
    os.replace(str(building), str(target))


def _remove_building(dataset):
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = str(config.building_path(dataset)) + suffix
        if os.path.exists(path):
            os.remove(path)


def _download_extra(name, reporter, dataset):
    dest = config.download_dir() / "extras" / name
    reporter.step(dataset, "Downloading %s" % name)
    download.download(_extras_url(name), dest, log=reporter.log,
                      progress=lambda done, total: reporter.progress(done, total, "bytes", name))
    return dest


def build_lcss(reporter):
    dataset = "lcss"
    xml_gz = _download_extra("CID-LCSS.xml.gz", reporter, dataset)
    _remove_building(dataset)
    reporter.step(dataset, "Importing laboratory chemical safety summaries")
    n = ingest_lcss.build(xml_gz, config.building_path(dataset), reporter, dataset)
    _write_meta(config.building_path(dataset), {"built_at": now_iso(), "records": n,
                                               "source_md5": _read_md5(xml_gz)})
    _finalize(dataset)
    reporter.log("[lcss] Done: %d compounds with lab safety data" % n)


def build_names(reporter):
    dataset = "names"
    title_gz = _download_extra("CID-Title.gz", reporter, dataset)
    syn_gz = _download_extra("CID-Synonym-filtered.gz", reporter, dataset)
    _remove_building(dataset)
    n_titles, n_syn = ingest_names.build(title_gz, syn_gz, config.building_path(dataset), reporter, dataset)
    _write_meta(config.building_path(dataset), {"built_at": now_iso(), "titles": n_titles, "synonyms": n_syn,
                                               "title_md5": _read_md5(title_gz), "synonym_md5": _read_md5(syn_gz)})
    _finalize(dataset)
    reporter.log("[names] Done: %d titles, %d synonyms" % (n_titles, n_syn))


def _read_md5(path):
    try:
        with open(str(path) + ".md5") as fh:
            return fh.read().strip()
    except OSError:
        return None


def build_compounds(reporter, fresh=False):
    """
    Download all SDF files and import them. Downloading, parsing (in worker processes) and merging into the database
    run concurrently. Already imported files are skipped (resume).
    """
    dataset = "compounds"
    if fresh:
        _remove_building(dataset)
    specs = load_db_specifications(str(COMPOUND_LAYOUT))
    columns = CompiledSpecs(specs).columns

    reporter.step(dataset, "Listing SDF files on the PubChem server")
    names = download.list_directory(_sdf_dir_url(), SDF_PATTERN)
    if config.sdf_limit():
        names = names[:config.sdf_limit()]
    if not names:
        raise RuntimeError("No SDF files found at %s" % _sdf_dir_url())

    building = config.building_path(dataset)
    conn = connect_for_build(str(building))
    lock = threading.Lock()
    try:
        with conn:
            initialize_db(conn, specs)
        done_files = set(r[0] for r in conn.execute("SELECT filename FROM sdf_file"))
        todo = [n for n in names if n not in done_files]
        n_total = len(names)
        n_done = len(names) - len(todo)
        reporter.step(dataset, "%d SDF files on server, %d already imported, %d to go" % (n_total, n_done, len(todo)))

        sdf_dir = config.download_dir() / "sdf"
        shard_dir = config.tmp_dir() / "shards"
        shard_dir.mkdir(parents=True, exist_ok=True)
        byte_progress = {}

        def report():
            with lock:
                active = ", ".join("%s %s" % (k.split(".")[0], v) for k, v in sorted(byte_progress.items()))
            reporter.progress(n_done, n_total, "files", ("downloading: " + active) if active else None)

        def fetch(name):
            def _p(done, total):
                with lock:
                    byte_progress[name] = _fmt_bytes(done) + (" / " + _fmt_bytes(total) if total else "")
                report()
            path = download.download("%s/%s" % (_sdf_dir_url(), name), sdf_dir / name, log=reporter.log, progress=_p)
            with lock:
                byte_progress.pop(name, None)
            return path

        n_workers = config.workers()
        with ThreadPoolExecutor(max_workers=config.download_concurrency()) as dl_pool, \
                ProcessPoolExecutor(max_workers=n_workers) as parse_pool:
            # Limit how far downloads run ahead of parsing to bound the disk space needed for not yet imported files.
            pending_names = list(todo)
            max_ahead = n_workers + config.download_concurrency() + 2
            downloads = {}
            parses = {}

            def top_up():
                while pending_names and len(downloads) + len(parses) < max_ahead:
                    name = pending_names.pop(0)
                    downloads[dl_pool.submit(fetch, name)] = name

            top_up()
            while downloads or parses:
                finished, _ = wait(list(downloads) + list(parses), return_when=FIRST_COMPLETED)
                for fut in finished:
                    if fut in downloads:
                        name = downloads.pop(fut)
                        path = fut.result()
                        shard = str(shard_dir / ("%s.sqlite" % name.split(".")[0]))
                        parses[parse_pool.submit(parse_sdf_file_to_shard, str(path), specs, shard, True)] = name
                    else:
                        name = parses.pop(fut)
                        sdf_fn, shard_fn, _ = fut.result()
                        try:
                            n_inserted = merge_shard(conn, sdf_fn, shard_fn, columns)
                        finally:
                            if os.path.exists(shard_fn):
                                os.remove(shard_fn)
                        if not config.keep_sdf_files():
                            for p in (sdf_fn, sdf_fn + ".md5"):
                                if os.path.exists(p):
                                    os.remove(p)
                        n_done += 1
                        reporter.log("[compounds] Imported %s: %d compounds (%d/%d files)"
                                     % (name, n_inserted, n_done, n_total))
                        report()
                top_up()

        total = conn.execute("SELECT COALESCE(SUM(n_compounds), 0) FROM sdf_file").fetchone()[0]
        reporter.step(dataset, "All files imported (%d compounds). Creating indices, this can take hours "
                               "for the full database" % total)
        create_indices(conn, specs, log=lambda m: reporter.step(dataset, m))
        reporter.step(dataset, "Analyzing database")
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()

    _write_meta(building, {"built_at": now_iso(), "compounds": total, "sdf_files": n_total})
    _finalize(dataset)
    shutil.rmtree(str(config.tmp_dir() / "shards"), ignore_errors=True)
    reporter.log("[compounds] Done: %d compounds" % total)


BUILDERS = {
    "lcss": build_lcss,
    "names": build_names,
    "compounds": build_compounds,
}


def run(datasets, fresh=False):
    """Build the given datasets in a fixed order (fast and most useful first)."""
    config.ensure_dirs()
    datasets = [d for d in config.DATASETS if d in datasets]
    reporter = StatusReporter(datasets)
    try:
        for dataset in datasets:
            if dataset == "compounds":
                build_compounds(reporter, fresh=fresh)
            else:
                BUILDERS[dataset](reporter)
        reporter.status["current"] = None
        reporter.log("Build finished: %s" % ", ".join(datasets))
        reporter.finish("done")
        return 0
    except KeyboardInterrupt:
        reporter.log("Build cancelled")
        reporter.finish("cancelled")
        return 130
    except Exception as err:  # noqa: B902 - report every error to the UI
        import traceback
        reporter.log("Build failed: %s" % err)
        reporter.log(traceback.format_exc())
        reporter.finish("failed", error=str(err))
        return 1


def dataset_info(dataset):
    """Information about a built dataset (for the status page)."""
    path = config.db_path(dataset)
    info = {"name": dataset, "built": path.exists(), "size_bytes": None, "meta": {},
            "building": config.building_path(dataset).exists()}
    if path.exists():
        info["size_bytes"] = path.stat().st_size
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
            try:
                info["meta"] = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    return info


def describe():
    return json.dumps([dataset_info(d) for d in config.DATASETS], indent=1)
