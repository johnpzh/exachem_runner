#!/usr/bin/env python3
"""ExaChem results database, version 0 (SQLite, standard library only).

Stores ExaChem results in a SQLite database, following notes/database_design.md.

`push` takes any directory (typically one fetched by this pipeline) and stores one row per
ExaChem result JSON found below it, plus one row per ExaChem printout that belongs to a run
without a result, so that failed runs are kept too. Nothing is inferred from file names:
result files are recognized as JSON objects with an "output" section, printouts by the header
ExaChem writes at the top of its output, and a printout is matched to a result by the run
date that ExaChem writes into both. The printouts supply the ExaChem and TAMM commits and,
for runs without a result, the echoed input file; their contents are not stored.

Rows are identified by a fingerprint, the SHA-256 of the result JSON text (or of the printout
text for a run without one), which makes re-pushing and merging idempotent.

Commands (run with -h for options):

    exachem_db.py --db FILE push --cluster NAME RESULT_DIR...
    exachem_db.py --db FILE list
    exachem_db.py --db FILE export > rows.jsonl
    exachem_db.py --db FILE import rows.jsonl
    exachem_db.py --db FILE merge OTHER.db
    exachem_db.py --db FILE sql "SELECT ..."

The database file defaults to $EXACHEM_DB (a path, or sqlite:///path) and then to
./exachem_results.db.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 5
DEFAULT_DB = "exachem_results.db"

DDL = """
CREATE TABLE IF NOT EXISTS runs (
    -- Identity and provenance: known for every run, not (reliably) inside the JSON documents
    id              INTEGER PRIMARY KEY,
    fingerprint     TEXT NOT NULL UNIQUE,   -- sha256 of the result JSON text (or of the printout when there
                                            -- is none); what makes re-pushing and merging idempotent
    status          TEXT NOT NULL,          -- success | no_result | failed | timeout | unknown
    cluster         TEXT NOT NULL,          -- where the run happened: the pipeline's remote_host, or --cluster
    run_date        TEXT,                   -- when ExaChem started, ISO 8601 in the cluster's local time
                                            -- (machine_info.date in the JSON, or the printout's "date:" line)
    exachem_commit  TEXT,                   -- ExaChem git commit, from the printout header (the JSON has no version)
    tamm_commit     TEXT,                   -- TAMM git commit, same source
    submitted_by    TEXT,                   -- who pushed the row: login name, or --user
    ingested_at     TEXT NOT NULL,          -- when the row was pushed, ISO 8601 UTC
    -- The documents themselves
    input           TEXT NOT NULL,          -- the ExaChem input file, as JSON text
    result          TEXT,                   -- the ExaChem result file, as JSON text; NULL when the run wrote none
    -- Query helpers computed from the documents on read (VIRTUAL: nothing is stored, so they can never
    -- disagree with the JSON); add or drop them with ALTER TABLE, no re-ingest needed
    nnodes          INTEGER GENERATED ALWAYS AS (json_extract(result, '$.output.machine_info.nnodes')) VIRTUAL,
    nproc_total     INTEGER GENERATED ALWAYS AS (json_extract(result, '$.output.machine_info.nproc_total')) VIRTUAL
);
CREATE INDEX IF NOT EXISTS runs_cluster_date ON runs (cluster, run_date);
"""

# The stored columns, in insert order (the generated ones are computed by SQLite)
RUN_COLUMNS = (
    "fingerprint", "status", "cluster", "run_date", "exachem_commit", "tamm_commit",
    "submitted_by", "ingested_at", "input", "result",
)

PRINTOUT_MARKERS = (b"ExaChem Git Information", b"Input file provided:")
PRINTOUT_HEAD_BYTES = 8192           # a printout shows one of the markers within this much
MAX_PRINTOUT_BYTES = 256 * 1024 * 1024
GIT_BLOCK_RE = re.compile(r"(?P<name>ExaChem|TAMM) Git Information\s*\{(?P<body>.*?)\}", re.S)
COMMIT_RE = re.compile(r"Commit Hash:\s*\S+\s*\[(?P<full>[0-9a-fA-F]+)\]")
DATE_RE = re.compile(r"^date:\s*(?P<date>.+?)\s*$", re.M)
PROGRAM_RE = re.compile(r'^program:\s*"?(?P<program>[^"\n]+?)"?\s*$', re.M)
NPROC_RE = re.compile(
    r"^nnodes:\s*(?P<nnodes>\d+),\s*nproc_per_node:\s*(?P<ppn>\d+),\s*nproc_total:\s*(?P<total>\d+)", re.M)
INPUT_FILE_RE = re.compile(r"^Input file provided:\s*(?P<path>.+?)\s*$", re.M)
COMPLETED_RE = re.compile(r"^Time taken for .+:\s*[\d.]+\s*secs", re.M)
TIMEOUT_RE = re.compile(r"DUE TO TIME LIMIT|CANCELLED AT", re.I)
ERROR_RE = re.compile(
    r"\bERROR\b|tamm_terminate|Segmentation fault|MPI_ABORT|Aborted|Traceback \(most recent call last\)")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def resolve_db_path(arg: str | None) -> str:
    """CLI --db, else $EXACHEM_DB (path or sqlite:///path), else ./exachem_results.db."""
    value = arg or os.environ.get("EXACHEM_DB") or os.environ.get("EXACHEM_DB_URL") or DEFAULT_DB
    if value.startswith("sqlite:///"):
        value = value[len("sqlite:///"):]
    return value


def connect(path: str) -> sqlite3.Connection:
    """Open (and if needed create) the database, returning a connection with the schema in place."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    elif version < SCHEMA_VERSION:
        migrate(conn, version)
    elif version != SCHEMA_VERSION:
        raise SystemExit(f"{path}: schema version {version} is not supported by this tool ({SCHEMA_VERSION})")
    return conn


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def migrate(conn: sqlite3.Connection, version: int) -> None:
    """Bring an older database file to the current schema, in place.

    v1 stored log contents (dropped); v3 replaced the (cluster, job id) key by a fingerprint;
    v4 and v5 stopped storing columns that are derivable from the documents. The table is rebuilt
    from the stored documents, so nothing is lost.
    """
    if version == 1:
        conn.execute("DROP TABLE IF EXISTS run_logs")
    rows = conn.execute("SELECT * FROM runs").fetchall()
    for index in ("runs_task_nbf", "runs_molecule", "runs_cluster_date"):
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.execute("ALTER TABLE runs RENAME TO runs_old")
    conn.executescript(DDL)
    for r in rows:
        row = {col: r[col] for col in RUN_COLUMNS if col in r.keys()}
        if "fingerprint" not in r.keys():
            row["fingerprint"] = (sha256_text(r["result"]) if r["result"]
                                  else sha256_text(f"{r['cluster']}|{r['slurm_job_id']}|{r['run_date']}"))
        insert_run(conn, row, "skip")
    conn.execute("DROP TABLE runs_old")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def insert_run(conn: sqlite3.Connection, row: dict[str, Any], on_conflict: str) -> str:
    """Insert one run. Returns 'inserted', 'skipped', or 'replaced'.

    Identity is the content fingerprint. On top of that, a run is recognized by its cluster and
    run date, so that a printout-only row (no result) is skipped when the same run is already
    stored with its result, and replaced when that result arrives later. This is what keeps a
    run-and-restart, whose directory carries the first run's printout twice, at one row per run.
    """
    with conn:
        existing = conn.execute("SELECT id FROM runs WHERE fingerprint = ?", (row["fingerprint"],)).fetchone()
        if existing:
            if on_conflict == "skip":
                return "skipped"
            conn.execute("DELETE FROM runs WHERE id = ?", (existing["id"],))
            outcome = "replaced"
        else:
            outcome = "inserted"
            if row.get("run_date"):
                same_run = conn.execute(
                    "SELECT id, result IS NOT NULL AS has_result FROM runs WHERE cluster = ? AND run_date = ?",
                    (row["cluster"], row["run_date"])).fetchall()
                if row.get("result") is None and any(r["has_result"] for r in same_run):
                    return "skipped"                       # already stored together with its result
                partial = [r["id"] for r in same_run if not r["has_result"]]
                if row.get("result") is not None and partial:
                    conn.execute(f"DELETE FROM runs WHERE id IN ({','.join('?' * len(partial))})", partial)
                    outcome = "replaced"                   # the printout-only row is upgraded
        placeholders = ", ".join("?" for _ in RUN_COLUMNS)
        conn.execute(
            f"INSERT INTO runs ({', '.join(RUN_COLUMNS)}) VALUES ({placeholders})",
            [row.get(col) for col in RUN_COLUMNS])
    return outcome


def iter_records(conn: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    """Yield every run as a column dict, in id order. JSON columns stay as text."""
    for run in conn.execute("SELECT * FROM runs ORDER BY id"):
        yield {col: run[col] for col in RUN_COLUMNS}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_exachem_date(text: str) -> str | None:
    """'Thu Aug 27 15:20:34 2026' -> '2026-08-27T15:20:34' (no offset: cluster local time)."""
    try:
        return datetime.strptime(text.strip(), "%a %b %d %H:%M:%S %Y").isoformat(timespec="seconds")
    except ValueError:
        return None


def parse_log_header(text: str) -> dict[str, Any]:
    """Extract provenance and the echoed input file from an ExaChem printout log."""
    info: dict[str, Any] = {
        "exachem_commit": None, "tamm_commit": None, "run_date_raw": None, "run_date": None,
        "program": None, "nnodes": None, "nproc_per_node": None, "nproc_total": None,
        "input_path": None, "input": None,
    }
    for m in GIT_BLOCK_RE.finditer(text):
        c = COMMIT_RE.search(m.group("body"))
        if c:
            info[m.group("name").lower() + "_commit"] = c.group("full")
    m = DATE_RE.search(text)
    if m:
        info["run_date_raw"] = m.group("date")
        info["run_date"] = parse_exachem_date(m.group("date"))
    m = PROGRAM_RE.search(text)
    if m:
        info["program"] = m.group("program")
    m = NPROC_RE.search(text)
    if m:
        info["nnodes"] = int(m.group("nnodes"))
        info["nproc_per_node"] = int(m.group("ppn"))
        info["nproc_total"] = int(m.group("total"))
    m = INPUT_FILE_RE.search(text)
    if m:
        info["input_path"] = m.group("path")
        start = text.find("{", m.end())
        if start != -1:
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[start:])
                if isinstance(obj, dict):
                    info["input"] = obj
            except json.JSONDecodeError:
                pass
    return info


def classify(pure_out: str, err: str, has_result: bool) -> str:
    """Status heuristic; see notes/database_design.md, section 6."""
    if has_result:
        return "success"
    combined = (pure_out or "") + "\n" + (err or "")
    if TIMEOUT_RE.search(combined):
        return "timeout"
    if ERROR_RE.search(combined):
        return "failed"
    if COMPLETED_RE.search(pure_out or ""):
        return "no_result"   # finished normally, but its JSON was overwritten by a later run
    return "unknown"


def dig(obj: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def derive_columns(result: dict[str, Any] | None, header: dict[str, Any]) -> dict[str, Any]:
    """The stored columns that come from the result JSON and the printout header.

    Everything a query may want about the calculation itself (task, molecule, basis set, problem
    size, machine, energies, timings) lives in the stored documents, a JSON path away.
    """
    return {
        "exachem_commit": header.get("exachem_commit"),
        "tamm_commit": header.get("tamm_commit"),
        "run_date": header.get("run_date")
                    or parse_exachem_date(dig(result, "output", "machine_info", "date") or ""),
    }


# ---------------------------------------------------------------------------
# Result directories
# ---------------------------------------------------------------------------

def find_results(result_dir: Path, warn=print) -> list[tuple[Path, dict[str, Any], str]]:
    """ExaChem result files below result_dir: JSON objects with an 'output' section, any name."""
    found = []
    for path in sorted(p for p in result_dir.rglob("*.json") if p.is_file()):
        try:
            text = path.read_text()
            obj = json.loads(text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            warn(f"warning: skipping unreadable JSON {path}: {exc}")
            continue
        if isinstance(obj, dict) and isinstance(obj.get("output"), dict):
            found.append((path, obj, text))
    return found


def looks_like_printout(path: Path) -> bool:
    """True if the file starts like an ExaChem printout (checked by content, never by name)."""
    try:
        if path.suffix == ".json" or path.stat().st_size > MAX_PRINTOUT_BYTES:
            return False
        with open(path, "rb") as fh:
            head = fh.read(PRINTOUT_HEAD_BYTES)
    except OSError:
        return False
    return any(marker in head for marker in PRINTOUT_MARKERS)


def find_printouts(result_dir: Path, warn=print) -> dict[str, tuple[Path, str, dict[str, Any]]]:
    """ExaChem printouts below result_dir, keyed by the run date in their header.

    Copies of the same printout (the pipeline fetches it as both err.log and pure_out.log) share
    a date and collapse into one entry; the longest copy is kept.
    """
    printouts: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    for path in sorted(p for p in result_dir.rglob("*") if p.is_file()):
        if not looks_like_printout(path):
            continue
        text = path.read_text(errors="replace")
        header = parse_log_header(text)
        date = header["run_date_raw"]
        if not date:
            warn(f"warning: {path}: ExaChem printout without a date line; ignored")
            continue
        if date not in printouts or len(text) > len(printouts[date][1]):
            printouts[date] = (path, text, header)
    return printouts


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def push_dir(conn: sqlite3.Connection, result_dir: Path, cluster: str, user: str,
             on_conflict: str, out=print, exit_code: int | None = None) -> dict[str, int]:
    """Ingest one directory: one row per result JSON, plus one per printout of a run without one.

    exit_code is the exit status of the pipeline step that produced the directory's newest run.
    A non-zero value turns an otherwise `unknown` status (no result, no marker in the printout)
    into `failed`; it never overrides what the printout says.
    """
    counts = {"inserted": 0, "skipped": 0, "replaced": 0}
    results = find_results(result_dir, warn=out)
    printouts = find_printouts(result_dir, warn=out)
    if not results and not printouts:
        out(f"warning: {result_dir}: no ExaChem result JSON or printout found, nothing to do")
        return counts
    blank_header = parse_log_header("")

    def store(row: dict[str, Any], source: Path, molecule: Any, basisset: Any) -> None:
        row.update({"cluster": cluster, "submitted_by": user, "ingested_at": now_iso()})
        outcome = insert_run(conn, row, on_conflict)
        counts[outcome] += 1
        out(f"{outcome:9s} {cluster} {row['status']:10s} {molecule}/{basisset} "
            f"run {row['run_date']} ({source.name})")

    # Runs with a result: the JSON is the record; a printout with the same date adds the commits
    for path, obj, text in results:
        date = dig(obj, "output", "machine_info", "date")
        printout = printouts.pop(date, None) if date else None
        header = printout[2] if printout else blank_header
        inp = obj.get("input") if isinstance(obj.get("input"), dict) else (header["input"] or {})
        row = derive_columns(obj, header)
        row.update({"status": "success", "fingerprint": sha256_text(text),
                    "input": json.dumps(inp), "result": text})
        store(row, path, dig(obj, "molecule", "name"),
              dig(obj, "molecule", "basis", "basisset") or dig(inp, "basis", "basisset"))

    # Runs without a result: the printout is all there is
    for date, (path, text, header) in sorted(printouts.items(), key=lambda kv: kv[1][2]["run_date"] or ""):
        status = classify(text, text, False)
        if exit_code and status == "unknown":
            status = "failed"
        if header["input"] is None:
            out(f"warning: {path}: no input echo in the printout; input stored as {{}}")
        inp = header["input"] or {}
        row = derive_columns(None, header)
        row.update({"status": status, "fingerprint": sha256_text(text),
                    "input": json.dumps(inp), "result": None})
        molecule = Path(header["input_path"]).stem if header.get("input_path") else dig(inp, "common", "file_prefix")
        store(row, path, molecule, dig(inp, "basis", "basisset"))
    return counts


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_push(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    total = {"inserted": 0, "skipped": 0, "replaced": 0}
    for d in args.result_dir:
        path = Path(d)
        if not path.is_dir():
            print(f"warning: {path} is not a directory, skipped")
            continue
        for key, val in push_dir(conn, path, args.cluster, args.user, args.on_conflict,
                                 exit_code=args.exit_code).items():
            total[key] += val
    print(f"done: {total['inserted']} inserted, {total['replaced']} replaced, "
          f"{total['skipped']} skipped -> {args.db}")
    return 0


# What `list` shows: stored columns plus a few values read from the documents on the fly
LIST_SELECT = """
    SELECT id, status, cluster,
           coalesce(json_extract(result, '$.molecule.name'), json_extract(input, '$.common.file_prefix')) AS molecule,
           coalesce(json_extract(result, '$.molecule.basis.basisset'), json_extract(input, '$.basis.basisset')) AS basisset,
           json_extract(result, '$.output.system_info.nbf') AS nbf,
           nnodes, nproc_total, run_date
    FROM runs
"""
LIST_COLUMNS = ("id", "status", "cluster", "molecule", "basisset", "nbf", "nnodes", "nproc_total", "run_date")


def cmd_list(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    where, params = [], []
    for col in ("status", "cluster"):
        val = getattr(args, col)
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    sql = LIST_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    print_table(LIST_COLUMNS, [[row[c] for c in LIST_COLUMNS] for row in rows])
    return 0


def print_table(columns: tuple[str, ...], rows: list[list[Any]]) -> None:
    def fmt(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.6f}" if abs(v) < 1e6 else f"{v:.6g}"
        return str(v)
    text = [[fmt(v) for v in row] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in text)) if text else len(c) for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    for r in text:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))
    print(f"({len(rows)} rows)")


def cmd_export(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    out = open(args.out, "w") if args.out else sys.stdout
    n = 0
    try:
        for row in iter_records(conn):
            # input/result stay as the stored JSON text, so an import is byte-for-byte faithful
            out.write(json.dumps(row) + "\n")
            n += 1
    finally:
        if out is not sys.stdout:
            out.close()
    print(f"exported {n} runs", file=sys.stderr)
    return 0


def cmd_import(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    counts = {"inserted": 0, "skipped": 0, "replaced": 0}
    with open(args.file) as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                row = dict(record.get("run", record))      # older exports wrapped the row in {"run": ...}
                for col in ("input", "result"):        # tolerate objects as well as text
                    if isinstance(row.get(col), (dict, list)):
                        row[col] = json.dumps(row[col])
            except (AttributeError, TypeError, json.JSONDecodeError) as exc:
                print(f"warning: line {line_no}: not an exported record ({exc}); skipped")
                continue
            counts[insert_run(conn, row, args.on_conflict)] += 1
    print(f"done: {counts['inserted']} inserted, {counts['replaced']} replaced, "
          f"{counts['skipped']} skipped -> {args.db}")
    return 0


def cmd_merge(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if Path(args.other).resolve() == Path(args.db).resolve():
        raise SystemExit("merge: source and destination are the same file")
    if not Path(args.other).is_file():
        raise SystemExit(f"merge: {args.other} does not exist")
    other = connect(args.other)
    counts = {"inserted": 0, "skipped": 0, "replaced": 0}
    try:
        for row in iter_records(other):
            counts[insert_run(conn, row, args.on_conflict)] += 1
    finally:
        other.close()
    print(f"done: {counts['inserted']} inserted, {counts['replaced']} replaced, "
          f"{counts['skipped']} skipped from {args.other} -> {args.db}")
    return 0


def cmd_sql(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    cur = conn.execute(args.query)
    if cur.description:
        columns = tuple(d[0] for d in cur.description)
        print_table(columns, [list(row) for row in cur.fetchall()])
    else:
        conn.commit()
        print(f"{cur.rowcount} rows affected")
    return 0


def cmd_init(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    print(f"ready: {args.db}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="exachem_db.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help=f"SQLite file (default: $EXACHEM_DB, else ./{DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    def conflict_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--on-conflict", choices=("skip", "replace"), default="skip",
                       help="what to do when (cluster, job id) already exists (default: skip)")

    p = sub.add_parser("push", help="ingest fetched result directories")
    p.add_argument("--cluster", required=True, help="cluster name the jobs ran on, e.g. deception")
    p.add_argument("--user", default=getpass.getuser(), help="value for submitted_by (default: login name)")
    p.add_argument("--exit-code", type=int, default=None,
                   help="exit status of the pipeline step that produced the newest run; non-zero marks "
                        "runs without a result and without any marker in their printout as failed")
    conflict_arg(p)
    p.add_argument("result_dir", nargs="+",
                   help="directories holding ExaChem result JSON files and printouts, searched "
                        "recursively, e.g. pg00_submit_job/output.workspace.remote.*")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("list", help="show stored runs")
    for col in ("status", "cluster"):
        p.add_argument(f"--{col}", help=f"filter on {col}")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("export", help="write all runs as JSON lines")
    p.add_argument("--out", help="output file (default: stdout)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="read runs from an exported JSON lines file")
    p.add_argument("file")
    conflict_arg(p)
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("merge", help="copy the runs of another database file into this one")
    p.add_argument("other", help="the other SQLite file")
    conflict_arg(p)
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("sql", help="run one SQL statement and print the rows")
    p.add_argument("query")
    p.set_defaults(func=cmd_sql)

    p = sub.add_parser("init", help="create an empty database")
    p.set_defaults(func=cmd_init)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.db = resolve_db_path(args.db)
    conn = connect(args.db)
    try:
        return args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
