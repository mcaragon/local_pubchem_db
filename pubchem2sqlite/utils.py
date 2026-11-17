#####
# MIT License
#
# Copyright (c) 2017-2020 Eric Bach (eric.bach@aalto.fi)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#####


import re
import os
import json
import gzip
import glob
import sqlite3
import tempfile
import traceback

from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, as_completed
from timeit import default_timer as timer


# Matches an SD-tag header line ("> <TAG>" possibly followed by extra text such as "(1)") and captures the first
# line of its value. Only the first line is used, which is the behaviour of the original line based parser.
_SD_TAG_RE = re.compile(r"^> +<([^>\n]+)>[^\n]*\n([^\n]*)", re.M)


def _as_dtype(val, dtype):
    """
    Convert a value represented using a string variable to the desired output data-type.

    :param val: string, representation of the value to convert

    :param dtype: string, desired output data-type

    :return: input values converted to the output data-type
    """
    if dtype in ["integer", "int"]:
        oval = int(val)
    elif dtype in ["real", "float", "double"]:
        oval = float(val)
    elif dtype in ["varchar", "character", "text"]:
        oval = val
    else:
        raise ValueError("Invalid dtype: %s." % dtype)

    return oval


class CompiledSpecs(object):
    """
    Pre-processed version of a DB layout specification. Building this once (instead of once per molecule) avoids
    re-evaluating the 'CREATE_LIKE' lambda functions for every compound.
    """
    def __init__(self, db_specs):
        self.columns = list(db_specs["columns"].keys())
        self.dtypes = []
        self.sd_tags = []
        self.create_likes = []
        self.not_null = []

        for name, specs in db_specs["columns"].items():
            self.dtypes.append(specs["DTYPE"].lower())
            self.sd_tags.append(list(specs["SD_TAG"]))
            self.create_likes.append(eval(specs["CREATE_LIKE"]) if "CREATE_LIKE" in specs else None)
            self.not_null.append(bool(specs.get("NOT_NULL", False)))

    def values_from_tags(self, tags):
        """
        :param tags: dictionary, SD-tag name -> (string) value

        :return: list of values (one per column, None if not available)
        """
        values = []
        for dtype, sd_tags, create_like in zip(self.dtypes, self.sd_tags, self.create_likes):
            val = None
            for sd_tag in sd_tags:
                if sd_tag in tags:
                    val = _as_dtype(tags[sd_tag], dtype)
                    if create_like is not None:
                        val = create_like(val)
                    break
            values.append(val)
        return values

    def row_from_tags(self, tags):
        """
        :return: tuple of values, or None if a NOT NULL column has no value.
        """
        values = self.values_from_tags(tags)
        for val, not_null in zip(values, self.not_null):
            if not_null and val is None:
                return None
        return tuple(values)


def parse_sd_tags(sdf):
    """
    Extract all SD-tags (first value line only) from a single molecule's sdf-string.

    :param sdf: string, containing the molecule's sdf

    :return: dictionary, SD-tag name -> string value
    """
    return dict(_SD_TAG_RE.findall(sdf))


def extract_info_from_sdf(sdf, db_specs):
    """
    Extract the information for a given molecules from its sdf-string.

    :param sdf: string, containing the molecule's sdf

    :param db_specs: dictionary, containing the database specifications (see 'default_db_layout.json')

    :return: OrderedDict, containing the extracted information.
    """
    compiled = db_specs if isinstance(db_specs, CompiledSpecs) else CompiledSpecs(db_specs)
    return OrderedDict(zip(compiled.columns, compiled.values_from_tags(parse_sd_tags(sdf))))


def insert_info_from_sdf_strings(db_connection, db_specs, iter_cid_sdf):
    """
    Insert the basic information of each molecule in the DB (represented using
    its sdf-string) and insert those information into the 'info' table.

    :param db_connection: sqlite3.Connection, database connection

    :param db_specs: dictionary, containing the database specifications (see 'default_db_layout.json')

    :param iter_cid_sdf: list of (cid, sdf-string)-tuples

    :return: scalar, number of inserted rows (compounds)
    """
    start = timer()

    compiled = CompiledSpecs(db_specs)
    rows = (compiled.row_from_tags(parse_sd_tags(sdf)) for _, sdf in iter_cid_sdf)
    n_inserted = _insert_rows(db_connection, compiled, (row for row in rows if row is not None))

    end = timer()
    print("Extraction and insertion of the information took %.3fsec" % (end - start))

    return n_inserted


def _insert_rows(db_connection, compiled, rows, table="compounds", batch_size=10000):
    """
    Insert rows using 'INSERT OR IGNORE', i.e. duplicates of the primary key are skipped instead of aborting the
    whole build.

    :return: number of inserted rows
    """
    stmt = "INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (
        table, ",".join(compiled.columns), ",".join(["?"] * len(compiled.columns)))

    n_inserted = 0
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            n_inserted += _executemany_count(db_connection, stmt, batch)
            batch = []
    if batch:
        n_inserted += _executemany_count(db_connection, stmt, batch)
    return n_inserted


def _executemany_count(db_connection, stmt, batch):
    before = db_connection.total_changes
    db_connection.executemany(stmt, batch)
    return db_connection.total_changes - before


def load_db_specifications(fn):
    """
    Loads a database layout specified in a json-file into a dictionary.

    :param fn: string, path to the json-file specifying the database layout. (see 'default_db_layout.json')

    :return: dictionary
    """
    with open(fn, "r") as json_file:
        specs = json.loads(json_file.read(), object_pairs_hook=OrderedDict)
    return specs


def get_column_stmt(column_specs):
    stmt_columns = []

    has_primary_key = False  # Primary keys must (if) be defined on a single column.

    for name, spec in column_specs.items():
        new_col = [name, spec["DTYPE"]]

        if spec.get("NOT_NULL", False) or spec.get("PRIMARY_KEY", False):
            new_col.append("not null")

        if spec.get("PRIMARY_KEY", False):
            if has_primary_key:
                raise ValueError("Primary keys must be defined on a single column.")

            new_col.append("primary key")
            has_primary_key = True

        stmt_columns.append(" ".join(new_col))

    return ",".join(stmt_columns)


def initialize_db(db_connection, specs, reset=False):
    """
    Initialization of the DB:
        - Remove existing tables if desired.
        - Create not present tables.

    :param db_connection: sqlite3.Connection, connection to the database to modify.

    :param specs: dictionary, specifying the database layout, e.g. columns, primary key and indices.
        See: 'load_db_specifications' for details.

    :param reset: boolean, If 'True' tables in the database are dropped and recreated.
    """
    if reset:
        db_connection.execute("DROP TABLE IF EXISTS sdf_file")
        db_connection.execute("DROP TABLE IF EXISTS compounds")

    # Table to keep track about the sdf-chucks (files) included
    db_connection.execute("CREATE TABLE IF NOT EXISTS sdf_file("
                          "filename          VARCHAR NOT NULL PRIMARY KEY,"
                          "lowest_cid        INTEGER NOT NULL,"
                          "highest_cid       INTEGER NOT NULL,"
                          "date_added        VARCHAR NOT NULL,"
                          "n_compounds       INTEGER NOT NULL)")

    # Table containing the compounds
    db_connection.execute("CREATE TABLE IF NOT EXISTS compounds(%s)" % get_column_stmt(specs["columns"]))


def split_sdf_file(sdf_file_stream):
    """
    Split a sdf-file containing the information for several molecules into a
    list of (cid, sdf-string).

    :param sdf_file_stream: file stream, pointing to the sdf-file. Result of 'open'

    :return: list of (cid, sdf-string)-tuples
    """
    return list(iter_sdf_file(sdf_file_stream))


def iter_sdf_records(sdf_file_stream, chunk_size=1 << 24):
    """
    Iterate over the molecule records ('$$$$' separated) of an sdf-file without loading the whole file into memory.

    :param sdf_file_stream: file stream (text mode), pointing to the sdf-file.

    :param chunk_size: scalar, number of characters read at once.

    :yields: string, sdf-string of a single molecule (without the '$$$$' terminator)
    """
    buffer = ""
    while True:
        data = sdf_file_stream.read(chunk_size)
        if not data:
            break
        buffer += data
        records = buffer.split("$$$$\n")
        buffer = records.pop()
        for record in records:
            yield record
    # A file not ending with a newline after the last '$$$$'
    if buffer.rstrip().endswith("$$$$"):
        yield buffer.rstrip()[:-4]


def iter_sdf_file(sdf_file_stream):
    """
    Split a sdf-file containing the information for several molecules. Returns an iterator over the list
    of (cid, sdf-string)-tuples.

    :param sdf_file_stream: file stream, pointing to the sdf-file. Result of 'open'

    :yields: (cid, sdf-string)-tuple
    """
    for sdf_str in iter_sdf_records(sdf_file_stream):
        sdf_str = sdf_str.rstrip("\n")
        cid = int(re.findall("<PUBCHEM_COMPOUND_CID>\n([0-9]+)", sdf_str)[0])
        yield cid, sdf_str


def get_sdf_files_not_in_db(db_connection, sdf_fn_in_folder):
    """
    Returns the sdf_file names (full path) which are not already in the DB.

    :param db_connection: sqlite3.Connection, database connection
    :param sdf_fn_in_folder: list of string, filenames of the sdf-files.
    """
    sdf_files_in_db = db_connection.execute("SELECT filename FROM sdf_file").fetchall()
    sdf_files_in_db = [str(x[0]) for x in sdf_files_in_db]

    return sorted(list(filter(lambda x: os.path.basename(x) not in sdf_files_in_db, sdf_fn_in_folder)))


def opensdf(fn, use_gzip=None):
    if use_gzip is None:
        use_gzip = fn.endswith(".gz")
    if use_gzip:
        return gzip.open(fn, "rt", encoding="utf-8", errors="replace")
    else:
        return open(fn, "r", encoding="utf-8", errors="replace")


def cid_range_from_filename(sdf_fn):
    """
    PubChem files are named like 'Compound_000000001_000500000.sdf.gz'.

    :return: (lowest_cid, highest_cid) or (0, 0) if the name does not follow that pattern
    """
    parts = os.path.basename(sdf_fn).split(".")[0].split("_")
    try:
        return int(parts[1]), int(parts[2])
    except (IndexError, ValueError):
        return 0, 0


def parse_sdf_file_to_shard(sdf_fn, db_specs, shard_fn, use_gzip=None):
    """
    Parse a complete sdf-file and write the extracted rows into a separate (temporary) SQLite file. This function is
    executed in worker processes, so that several sdf-files can be parsed in parallel while the main process merges
    finished shards into the target database.

    :return: (sdf_fn, shard_fn, number of parsed compounds)
    """
    compiled = CompiledSpecs(db_specs)
    if os.path.exists(shard_fn):
        os.remove(shard_fn)
    conn = sqlite3.connect(shard_fn)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        # No constraints in the shard, they are enforced when merging.
        conn.execute("CREATE TABLE compounds(%s)" % ",".join(
            "%s %s" % (name, dtype) for name, dtype in zip(compiled.columns, compiled.dtypes)))
        with opensdf(sdf_fn, use_gzip) as sdf_file:
            rows = (compiled.row_from_tags(parse_sd_tags(rec)) for rec in iter_sdf_records(sdf_file))
            n = _insert_rows(conn, compiled, (row for row in rows if row is not None))
        conn.commit()
    finally:
        conn.close()
    return sdf_fn, shard_fn, n


def merge_shard(db_connection, sdf_fn, shard_fn, columns):
    """
    Copy all rows of a shard into the 'compounds' table and register the sdf-file as processed (single transaction).

    :return: number of inserted rows
    """
    cols = ",".join(columns)
    lowest_cid, highest_cid = cid_range_from_filename(sdf_fn)
    db_connection.execute("ATTACH DATABASE ? AS shard", (shard_fn,))
    try:
        with db_connection:
            before = db_connection.total_changes
            db_connection.execute("INSERT OR IGNORE INTO main.compounds (%s) SELECT %s FROM shard.compounds" %
                                  (cols, cols))
            n_inserted = db_connection.total_changes - before
            db_connection.execute("INSERT OR REPLACE INTO sdf_file "
                                  "(filename, lowest_cid, highest_cid, date_added, n_compounds) "
                                  "VALUES(?, ?, ?, DATE('now'), ?)",
                                  (os.path.basename(sdf_fn), lowest_cid, highest_cid, n_inserted))
    finally:
        db_connection.execute("DETACH DATABASE shard")
    return n_inserted


def create_indices(db_connection, db_specs, log=print):
    for colname, specs in db_specs["columns"].items():
        if specs.get("WITH_INDEX", False):
            idx_name = "idx_%s" % colname
            log("Create index on '%s'." % colname)
            with db_connection:
                db_connection.execute("DROP INDEX IF EXISTS %s" % idx_name)
                db_connection.execute("CREATE INDEX %s ON compounds(%s)" % (idx_name, colname))


def import_sdf_files(db_connection, db_specs, sdf_files, n_workers=1, tmp_dir=None, use_gzip=None,
                     on_file_done=None, log=print):
    """
    Import a list of sdf-files into an initialized database. With 'n_workers > 1' the files are parsed in parallel.

    :param on_file_done: optional callable(sdf_fn, n_inserted, n_done, n_total), called after each merged file.

    :return: total number of inserted compounds
    """
    n_total = len(sdf_files)
    n_done = 0
    n_inserted_total = 0
    columns = CompiledSpecs(db_specs).columns

    if n_total == 0:
        return 0

    tmp_dir = tmp_dir or tempfile.gettempdir()
    os.makedirs(tmp_dir, exist_ok=True)

    def _shard_fn(sdf_fn):
        return os.path.join(tmp_dir, "shard_%s.sqlite" % os.path.basename(sdf_fn).split(".")[0])

    def _merge(sdf_fn, shard_fn):
        try:
            return merge_shard(db_connection, sdf_fn, shard_fn, columns)
        finally:
            if os.path.exists(shard_fn):
                os.remove(shard_fn)

    if n_workers <= 1:
        for sdf_fn in sdf_files:
            log("Process sdf-file: %s (%d/%d)" % (os.path.basename(sdf_fn), n_done + 1, n_total))
            _, shard_fn, _ = parse_sdf_file_to_shard(sdf_fn, db_specs, _shard_fn(sdf_fn), use_gzip)
            n_inserted = _merge(sdf_fn, shard_fn)
            n_done += 1
            n_inserted_total += n_inserted
            log("Number of inserted compounds: %d" % n_inserted)
            if on_file_done:
                on_file_done(sdf_fn, n_inserted, n_done, n_total)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(parse_sdf_file_to_shard, sdf_fn, db_specs, _shard_fn(sdf_fn), use_gzip)
                       for sdf_fn in sdf_files]
            for future in as_completed(futures):
                sdf_fn, shard_fn, _ = future.result()
                n_inserted = _merge(sdf_fn, shard_fn)
                n_done += 1
                n_inserted_total += n_inserted
                log("Imported %s: %d compounds (%d/%d)" % (os.path.basename(sdf_fn), n_inserted, n_done, n_total))
                if on_file_done:
                    on_file_done(sdf_fn, n_inserted, n_done, n_total)

    return n_inserted_total


def connect_for_build(db_fn):
    conn = sqlite3.connect(db_fn)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-262144")  # 256 MB
    return conn


def build_db(base_dir, use_gzip, reset, db_specs, n_workers=1, db_fn=None, tmp_dir=None):
    # Directory paths to the SDF and output DB file
    sdf_dir = os.path.join(base_dir, "sdf")
    db_dir = os.path.join(base_dir, "db")

    # Connect to the 'pubchem' database.
    conn = connect_for_build(db_fn or os.path.join(db_dir, "pubchem.sqlite"))

    try:
        # Initialize the database
        with conn:
            initialize_db(conn, db_specs, reset=reset)

        # Get a list of all sdf-files available and reduce it to the ones still
        # needed to be processed.
        fn_patter = "*.sdf.gz" if use_gzip else "*.sdf"
        sdf_files = glob.glob(os.path.join(sdf_dir, fn_patter))
        n_sdf_files = len(sdf_files)
        print("Sdf-files to process (before filtering): %d" % n_sdf_files)

        sdf_files = get_sdf_files_not_in_db(conn, sdf_files)
        n_sdf_files = len(sdf_files)
        print("Sdf-files to process (after filtering): %d" % n_sdf_files)

        import_sdf_files(conn, db_specs, sdf_files, n_workers=n_workers, use_gzip=use_gzip,
                         tmp_dir=tmp_dir or os.path.join(db_dir, "tmp"))

        # Create indices after all sdf-files have been parsed and imported to the database.
        create_indices(conn, db_specs)

        return_code = 0

    except sqlite3.ProgrammingError as err:
        print("Programming error: '" + err.args[0] + "'.")
        traceback.print_exc()
        return_code = 1
    except sqlite3.DatabaseError as err:
        print("Database error: '" + err.args[0] + "'.")
        traceback.print_exc()
        return_code = 1
    except IOError as err:
        print("An IOError occurred: '%s'." % err)
        traceback.print_exc()
        return_code = 1
    except Exception as err:
        print(err)
        traceback.print_exc()
        return_code = 1

    finally:
        conn.close()

    return return_code
