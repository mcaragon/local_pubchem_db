"""
Import compound names from PubChem's Extras folder:

    CID-Title.gz              one preferred name ("title") per compound, e.g. "Acetone"
    CID-Synonym-filtered.gz   all filtered synonyms per compound (trade names, CAS numbers, ...),
                              most relevant first

Resulting tables (names.sqlite):
    titles(cid, title)
    synonyms(cid, rank, name)     with a case-insensitive index on name, used for name/CAS search and autocomplete
"""
import gzip
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS titles(cid INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE IF NOT EXISTS synonyms(
    cid INTEGER, rank INTEGER, name TEXT COLLATE NOCASE, PRIMARY KEY(cid, rank)) WITHOUT ROWID;
"""

BATCH = 50000


def _iter_tsv(path):
    with gzip.open(str(path), "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            cid, sep, name = line.rstrip("\n").partition("\t")
            if not sep or not name:
                continue
            try:
                yield int(cid), name
            except ValueError:
                continue


def _insert(conn, stmt, rows, reporter, label, total_hint=None):
    n = 0
    batch = []
    conn.execute("BEGIN")
    for row in rows:
        batch.append(row)
        if len(batch) >= BATCH:
            conn.executemany(stmt, batch)
            n += len(batch)
            batch = []
            if n % (BATCH * 20) == 0:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
                if reporter:
                    reporter.progress(n, total_hint, label)
    if batch:
        conn.executemany(stmt, batch)
        n += len(batch)
    conn.execute("COMMIT")
    return n


def _ranked(rows):
    last_cid = None
    rank = 0
    for cid, name in rows:
        if cid != last_cid:
            last_cid = cid
            rank = 0
        else:
            rank += 1
        yield cid, rank, name


def build(title_gz, synonym_gz, db_path, reporter=None, dataset="names"):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-262144")
    conn.executescript(SCHEMA)
    try:
        if reporter:
            reporter.step(dataset, "Importing compound titles")
        n_titles = _insert(conn, "INSERT OR REPLACE INTO titles VALUES (?,?)", _iter_tsv(title_gz), reporter, "titles")
        if reporter:
            reporter.step(dataset, "Imported %d titles. Importing synonyms" % n_titles)
        n_syn = _insert(conn, "INSERT OR IGNORE INTO synonyms VALUES (?,?,?)", _ranked(_iter_tsv(synonym_gz)),
                        reporter, "synonyms")
        if reporter:
            reporter.step(dataset, "Imported %d synonyms. Creating name index (this can take a while)" % n_syn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_synonyms_name ON synonyms(name, rank, cid)")
        conn.execute("ANALYZE")
    finally:
        conn.close()
    return n_titles, n_syn
