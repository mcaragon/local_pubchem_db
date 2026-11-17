"""
Import PubChem's Laboratory Chemical Safety Summaries (Compound/Extras/CID-LCSS.xml.gz).

This single bulk file contains, for the compounds most relevant in a laboratory, experimental physical properties
(boiling point, melting point, density, flash point, solubility, ...), GHS hazard classification, handling, storage,
first aid and disposal information - each with the original source reference.

Resulting tables (lcss.sqlite):
    lcss_records(cid, title, cas, molecular_formula, molecular_weight, iupac_name, inchi, inchikey,
                 signal_word, pictograms)
    lcss_info(cid, seq, path, heading, name, value, ref_num, note)  -- every value of every section, in order
    lcss_refs(cid, ref_num, source_name, name, url)
    lcss_props(cid, prop, value, low, high, standard, info_seq)      -- parsed numeric properties
    lcss_synonyms(cid, rank, name)
"""
import gzip
import re
import sqlite3
import xml.etree.ElementTree as ET

from pubchem_local import propparse

NS = "{http://pubchem.ncbi.nlm.nih.gov/pug_view}"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS lcss_records(
    cid INTEGER PRIMARY KEY, title TEXT, cas TEXT, molecular_formula TEXT, molecular_weight REAL,
    iupac_name TEXT, inchi TEXT, inchikey TEXT, signal_word TEXT, pictograms TEXT);
CREATE TABLE IF NOT EXISTS lcss_info(
    cid INTEGER, seq INTEGER, path TEXT, heading TEXT, name TEXT, value TEXT, ref_num INTEGER, note TEXT,
    PRIMARY KEY(cid, seq)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS lcss_refs(
    cid INTEGER, ref_num INTEGER, source_name TEXT, name TEXT, url TEXT,
    PRIMARY KEY(cid, ref_num)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS lcss_props(
    cid INTEGER, prop TEXT, value REAL, low REAL, high REAL, standard INTEGER, info_seq INTEGER);
CREATE TABLE IF NOT EXISTS lcss_synonyms(
    cid INTEGER, rank INTEGER, name TEXT COLLATE NOCASE, PRIMARY KEY(cid, rank)) WITHOUT ROWID;
"""

INDICES = """
CREATE INDEX IF NOT EXISTS idx_lcss_props_cid ON lcss_props(cid, prop);
CREATE INDEX IF NOT EXISTS idx_lcss_syn_name ON lcss_synonyms(name, rank, cid);
CREATE INDEX IF NOT EXISTS idx_lcss_records_inchikey ON lcss_records(inchikey);
CREATE INDEX IF NOT EXISTS idx_lcss_records_formula ON lcss_records(molecular_formula);
CREATE INDEX IF NOT EXISTS idx_lcss_records_cas ON lcss_records(cas);
"""

_GHS_CODE_RE = re.compile(r"/(GHS\d\d)\.svg", re.I)


def _text(elem, tag):
    child = elem.find(NS + tag)
    return child.text if child is not None and child.text is not None else None


def _string_with_markup_value(swm):
    s = (_text(swm, "String") or "").strip()
    if s:
        return s
    # Pictograms are encoded as blank strings with icon markup
    icons = []
    for markup in swm.findall(NS + "Markup"):
        if _text(markup, "Type") == "Icon":
            name = _text(markup, "Extra") or ""
            code = _GHS_CODE_RE.search(_text(markup, "URL") or "")
            icons.append("%s (%s)" % (name, code.group(1).upper()) if code else name)
    return "; ".join(i for i in icons if i) or None


def extract_values(info):
    """All values of an <Information> element as a list of strings."""
    value = info.find(NS + "Value")
    if value is None:
        return []
    values = []
    for swm in value.findall(NS + "StringWithMarkup"):
        s = _string_with_markup_value(swm)
        if s:
            values.append(s)
    numbers = [n.text for n in value.findall(NS + "Number") if n.text]
    if numbers:
        unit = _text(value, "Unit")
        values.append(", ".join(numbers) + (" " + unit if unit else ""))
    for tag in ("ExternalDataURL", "Binary"):
        for e in value.findall(NS + tag):
            if e.text and tag == "ExternalDataURL":
                values.append(e.text.strip())
    return values


def parse_record(rec):
    """
    Convert a <Record> element into python structures.

    :return: dict(cid, title, refs, info) where info is a list of (path, heading, name, value, ref_num, note)
    """
    cid = int(_text(rec, "RecordNumber"))
    refs = {}
    for ref in rec.findall(NS + "Reference"):
        try:
            num = int(_text(ref, "ReferenceNumber"))
        except (TypeError, ValueError):
            continue
        refs[num] = (_text(ref, "SourceName"), _text(ref, "Name"), _text(ref, "URL"))

    info_rows = []

    def walk(section, path):
        heading = _text(section, "TOCHeading") or ""
        for info in section.findall(NS + "Information"):
            try:
                ref_num = int(_text(info, "ReferenceNumber"))
            except (TypeError, ValueError):
                ref_num = None
            name = _text(info, "Name") or None
            note = _text(info, "Description") or None
            for value in extract_values(info):
                info_rows.append((" > ".join(path), heading, name, value, ref_num, note))
        for sub in section.findall(NS + "Section"):
            walk(sub, path + [heading])

    for section in rec.findall(NS + "Section"):
        walk(section, [])

    return {"cid": cid, "title": _text(rec, "RecordTitle"), "refs": refs, "info": info_rows}


def summarize_record(parsed):
    """Pull the identifiers and GHS summary out of the parsed sections."""
    first = {}
    for path, heading, name, value, ref_num, note in parsed["info"]:
        first.setdefault(heading, value)

    signal_word = None
    pictograms = None
    for path, heading, name, value, ref_num, note in parsed["info"]:
        if heading != "GHS Classification":
            continue
        if name == "Signal" and signal_word is None:
            signal_word = value
        elif name == "Pictogram(s)" and pictograms is None:
            pictograms = value
        if signal_word and pictograms:
            break

    try:
        mw = float(re.findall(r"\d+(?:\.\d+)?", first.get("Molecular Weight") or "")[0])
    except IndexError:
        mw = None

    return (parsed["cid"], parsed["title"], first.get("CAS"), first.get("Molecular Formula"), mw,
            first.get("IUPAC Name"), first.get("InChI"), first.get("InChIKey"), signal_word, pictograms)


def iter_records(stream):
    """Stream <Record> elements from the (decompressed) XML without keeping the whole tree in memory."""
    context = ET.iterparse(stream, events=("start", "end"))
    root = None
    for event, elem in context:
        if event == "start":
            if root is None:
                root = elem
            continue
        if elem.tag == NS + "Record":
            yield elem
            elem.clear()
            root.clear()


def create_schema(conn):
    conn.executescript(SCHEMA)


def insert_record(conn, parsed):
    cid = parsed["cid"]
    conn.execute("INSERT OR REPLACE INTO lcss_records VALUES (?,?,?,?,?,?,?,?,?,?)", summarize_record(parsed))
    conn.executemany("INSERT OR REPLACE INTO lcss_refs VALUES (?,?,?,?,?)",
                     [(cid, num) + ref for num, ref in parsed["refs"].items()])
    rows = []
    props = []
    # The record title (e.g. "Acetone") is always the preferred name (rank 0)
    synonyms = [parsed["title"]] if parsed["title"] else []
    seen_syn = set(n.lower() for n in synonyms)
    for seq, (path, heading, name, value, ref_num, note) in enumerate(parsed["info"]):
        rows.append((cid, seq, path, heading, name, value, ref_num, note))
        prop = propparse.HEADINGS.get(heading)
        if prop:
            res = propparse.parse_property(prop, value)
            if res:
                props.append((cid, prop, res["value"], res["low"], res["high"], int(res["standard"]), seq))
        if heading == "Depositor-Supplied Synonyms" or heading == "CAS":
            key = value.lower()
            if key not in seen_syn:
                seen_syn.add(key)
                synonyms.append(value)
    conn.executemany("INSERT OR REPLACE INTO lcss_info VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO lcss_props VALUES (?,?,?,?,?,?,?)", props)
    conn.executemany("INSERT OR REPLACE INTO lcss_synonyms VALUES (?,?,?)",
                     [(cid, rank, name) for rank, name in enumerate(synonyms)])


def build(xml_gz_path, db_path, reporter=None, dataset="lcss"):
    """
    Build lcss.sqlite from CID-LCSS.xml.gz.

    :return: number of imported records
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    create_schema(conn)
    n = 0
    try:
        with gzip.open(str(xml_gz_path), "rb") as raw:
            conn.execute("BEGIN")
            for rec in iter_records(raw):
                insert_record(conn, parse_record(rec))
                n += 1
                if n % 2000 == 0:
                    conn.execute("COMMIT")
                    conn.execute("BEGIN")
                    if reporter:
                        reporter.progress(n, None, "records")
            conn.execute("COMMIT")
        if reporter:
            reporter.step(dataset, "Creating indices (%d records imported)" % n)
        conn.executescript(INDICES)
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()
    return n
