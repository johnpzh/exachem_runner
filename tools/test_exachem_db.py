#!/usr/bin/env python3
"""Tests for tools/exachem_db.py. Run: python3 tools/test_exachem_db.py"""

import json
import sqlite3
import sys
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exachem_db  # noqa: E402

EXACHEM_HASH = "8b9019ed3ee2a47238633acb3b25bf7c83f5e1ab"
TAMM_HASH = "c00148cb8eb9a282939c6227de5f4631680c5370"

INPUT = {
    "geometry": {"coordinates": ["O 0 0 0", "O 0 -2.0473 -1.2595", "O 0 2.0473 -1.2595"], "units": "bohr"},
    "basis": {"basisset": "sto-3g"},
    "SCF": {"restart": False},
    "CD": {"diagtol": 1e-06},
    "CC": {"threshold": 1e-08},
    "TASK": {"scf": False, "mp2": False, "cd_2e": False, "ccsd": True, "ccsd_t": False},
}


def make_log(date: str, inp: dict, body: str, nproc: int = 2) -> str:
    """Mimic the header ExaChem prints at the top of its printout."""
    return (
        "\nExaChem Git Information\n{\n Branch: main\n"
        f" Commit Hash: 8b9019e [{EXACHEM_HASH}]\n Commit Date: 2025-10-02 13:07:40 -0700\n"
        " Commit Message: add basisset files from BSE, update docs\n}\n\n\n"
        "TAMM Git Information\n{\n Branch: main\n"
        f" Commit Hash: c00148c [{TAMM_HASH}]\n Commit Date: 2025-09-30 12:38:26 -0700\n"
        " Commit Message: utility routine to copy dense tensors\n}\n\n\n"
        f"date: {date}\n"
        'program: "/home/u/install/tamm/bin/ExaChem"\n'
        f"nnodes: 1, nproc_per_node: {nproc}, nproc_total: {nproc}, \n"
        "Memory information\n{\n[AMD EPYC 7502 32-Core Processor] : \n  CPU memory per node (GiB): 251\n}\n\n"
        "------------------------------------------------------------\n\n"
        "Input file provided: /home/u/workspace/ozone.json\n\n"
        + json.dumps(inp, indent=2) + "\n\n"
        "Output folder & files prefix: ozone.sto-3g\n\n" + body
    )


COMPLETED_BODY = (
    "Number of basis functions = 15\n\nTotal number of shells = 9\n\nTotal number of electrons = 24\n\n"
    " Iterations converged\n CCSD correlation energy / hartree =        -0.238015205899041\n"
    " CCSD total energy / hartree       =      -221.527104787865426\n\n"
    "Time taken for Closed Shell Cholesky CCSD: 0.23 secs\n\n"
)


def make_result(date: str, inp: dict) -> dict:
    return {
        "output": {
            "machine_info": {"date": date, "nnodes": 1, "nproc_per_node": 2, "nproc_total": 2,
                             "cpu": {"name": "AMD EPYC 7502 32-Core Processor      ",
                                     "cpu_memory_per_node_gib": 251}},
            "system_info": {"nbf": 15, "nshells": 9, "nelectrons_total": 24,
                            "nelectrons_alpha": 12, "nelectrons_beta": 12},
            "SCF": {"final_energy": -221.28908958196638, "n_iterations": 12,
                    "iter": {"1": {"energy": -221.08, "performance": {"total_time": 0.08}}},
                    "performance": {"total_time": 1.864865233}},
            "CD": {"n_cholesky_vectors": 76, "diagtol": 1e-06},
            "CCSD": {"iter": {"1": {"residual": 0.285, "correlation": 0.0,
                                    "performance": {"total_time": 0.006097363}}},
                     "n_iterations": 31,
                     "final_energy": {"correlation": -0.23801520589904082, "total": -221.52710478786543},
                     "performance": {"total_time": 0.198753243}},
        },
        "molecule": {"name": "ozone", "basis": {"basisset": "sto-3g"}, "nbf": 15, "nshells": 9,
                     "nelectrons": 24, "nelectrons_alpha": 12, "nelectrons_beta": 12},
        "input": inp,
    }


def write_printout(d: Path, name: str, text: str) -> None:
    (d / name).write_text(text)


FIRST, RESTART, FAILED = "2026-08-18T23:54:49", "2026-08-18T23:56:04", "2026-08-19T08:00:01"


def make_result_dir(root: Path) -> Path:
    """A run-and-restart session plus a failed run, with printouts under arbitrary file names."""
    d = root / "output.workspace.remote.2026-08-18T23:54:23"
    (d / "json").mkdir(parents=True)
    first_date, restart_date, failed_date = ("Tue Aug 18 23:54:49 2026",
                                             "Tue Aug 18 23:56:04 2026",
                                             "Wed Aug  19 08:00:01 2026")
    restart_input = dict(INPUT, SCF={"restart": True}, common={"file_prefix": "ozone"})
    write_printout(d, "first.txt", make_log(first_date, INPUT, COMPLETED_BODY))          # finished, JSON overwritten
    write_printout(d, "restart.txt", make_log(restart_date, restart_input, COMPLETED_BODY))  # finished, JSON present
    write_printout(d, "restart copy.log", make_log(restart_date, restart_input, COMPLETED_BODY))  # same run, twice
    write_printout(d, "failed.txt", make_log(failed_date, INPUT,                            # died on a bad input
                                             "[INPUT FILE ERROR] only a single task can be enabled at once!\n"))
    write_printout(d, "slurm.out", "/usr/bin/python\nPython 3.9.21\n")                    # not a printout
    (d / "json" / "ozone.sto-3g.ccsd.json").write_text(
        json.dumps(make_result(restart_date, restart_input), indent=2))
    return d


class PushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.result_dir = make_result_dir(root)
        self.db = str(root / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def push(self, db=None, on_conflict="skip"):
        return exachem_db.main(["--db", db or self.db, "push", "--cluster", "deception",
                                "--user", "tester", "--on-conflict", on_conflict, str(self.result_dir)])

    def rows(self, db=None):
        with closing(exachem_db.connect(db or self.db)) as conn:
            return {r["run_date"]: dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY run_date")}

    def scalar(self, sql, params=(), db=None):
        with closing(exachem_db.connect(db or self.db)) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def test_one_row_per_run_without_using_file_names(self):
        self.assertEqual(self.push(), 0)
        rows = self.rows()
        self.assertEqual(sorted(rows), [FIRST, RESTART, FAILED])          # the duplicate printout collapsed
        self.assertEqual(rows[FIRST]["status"], "no_result")
        self.assertEqual(rows[RESTART]["status"], "success")
        self.assertEqual(rows[FAILED]["status"], "failed")
        self.assertIsNone(rows[FIRST]["result"])
        self.assertIsNotNone(rows[RESTART]["result"])
        fingerprints = {r["fingerprint"] for r in rows.values()}
        self.assertEqual(len(fingerprints), 3)
        self.assertTrue(all(len(f) == 64 for f in fingerprints))

    def test_columns_of_successful_run(self):
        self.push()
        r = self.rows()[RESTART]
        self.assertEqual((r["nnodes"], r["nproc_total"]), (1, 2))              # generated from the JSON
        self.assertNotIn("task", r)
        self.assertNotIn("molecule", r)
        self.assertEqual(r["exachem_commit"], EXACHEM_HASH)
        self.assertEqual(r["tamm_commit"], TAMM_HASH)
        self.assertEqual(r["run_date"], "2026-08-18T23:56:04")
        self.assertEqual(r["submitted_by"], "tester")
        self.assertTrue(json.loads(r["input"])["SCF"]["restart"])
        self.assertEqual(json.loads(r["result"])["molecule"]["name"], "ozone")
        # anything else is a JSON path away
        self.assertAlmostEqual(self.scalar("SELECT result ->> '$.output.CCSD.final_energy.total' FROM runs "
                                           "WHERE status = 'success'"), -221.52710478786543)
        self.assertAlmostEqual(self.scalar("SELECT result ->> '$.output.CCSD.performance.total_time' FROM runs "
                                           "WHERE status = 'success'"), 0.198753243)

    def test_failed_run_keeps_input_but_no_logs_are_stored(self):
        self.push()
        r = self.rows()[FAILED]
        self.assertEqual(json.loads(r["input"])["basis"]["basisset"], "sto-3g")   # the input survives
        self.assertTrue(json.loads(r["input"])["TASK"]["ccsd"])
        self.assertEqual(r["run_date"], "2026-08-19T08:00:01")   # single-digit day, double space
        self.assertEqual(self.scalar("SELECT count(*) FROM sqlite_master WHERE name = 'run_logs'"), 0)

    def test_old_database_files_are_upgraded(self):
        old_ddl = """
            CREATE TABLE runs (id INTEGER PRIMARY KEY, status TEXT NOT NULL, task TEXT NOT NULL, molecule TEXT,
                basisset TEXT, scf_type TEXT, nbf INTEGER, natoms INTEGER, nelectrons INTEGER, nnodes INTEGER,
                nproc_total INTEGER, cpu_name TEXT, cluster TEXT NOT NULL, slurm_job_id INTEGER NOT NULL,
                exachem_commit TEXT, tamm_commit TEXT, run_date TEXT, scf_total_energy REAL,
                ccsd_correlation_energy REAL, ccsd_total_energy REAL, total_time_s REAL, submitted_by TEXT,
                ingested_at TEXT NOT NULL, input TEXT NOT NULL, result TEXT, UNIQUE (cluster, slurm_job_id));
            CREATE INDEX runs_task_nbf ON runs (task, nbf);
            CREATE TABLE run_logs (run_id INTEGER, kind TEXT, content TEXT);
            INSERT INTO runs (status, task, cluster, slurm_job_id, run_date, ingested_at, input, result)
                VALUES ('success', 'ccsd', 'deception', 42, '2026-08-27T15:20:34', 'x', '{}', '{"output": {}}'),
                       ('failed',  'ccsd', 'deception', 43, '2026-08-28T15:20:34', 'x', '{}', NULL);
            PRAGMA user_version = 1;
        """
        db = str(Path(self.tmp.name) / "old.db")
        with closing(sqlite3.connect(db)) as conn:
            conn.executescript(old_ddl)
        with closing(exachem_db.connect(db)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], exachem_db.SCHEMA_VERSION)
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertEqual(names, {"runs"})
            rows = conn.execute("SELECT status, fingerprint, result FROM runs ORDER BY id").fetchall()
        self.assertEqual([r[0] for r in rows], ["success", "failed"])
        self.assertEqual(rows[0][1], exachem_db.sha256_text('{"output": {}}'))
        self.assertEqual(len(rows[1][1]), 64)

    def test_push_is_idempotent(self):
        self.push()
        before = self.rows()
        self.push()
        self.assertEqual(self.rows(), before)
        self.push(on_conflict="replace")
        after = self.rows()
        self.assertEqual(sorted(after), sorted(before))

    def test_json_path_query_works_in_sqlite(self):
        self.push()
        val = self.scalar("SELECT result ->> '$.output.CCSD.iter.1.performance.total_time' FROM runs "
                          "WHERE status = 'success'")
        self.assertAlmostEqual(val, 0.006097363)

    def test_export_import_roundtrip(self):
        self.push()
        root = Path(self.tmp.name)
        dump, db2 = root / "rows.jsonl", str(root / "copy.db")
        exachem_db.main(["--db", self.db, "export", "--out", str(dump)])
        self.assertEqual(len(dump.read_text().splitlines()), 3)
        exachem_db.main(["--db", db2, "import", str(dump)])
        self.assertEqual(self.rows(db2), self.rows())

    def test_merge_skips_existing_rows(self):
        self.push()
        root = Path(self.tmp.name)
        db2 = str(root / "colleague.db")
        exachem_db.main(["--db", db2, "push", "--cluster", "perlmutter", "--user", "colleague",
                         str(self.result_dir)])
        # The same runs under another cluster label are the same runs: the fingerprints match
        exachem_db.main(["--db", db2, "merge", self.db])
        self.assertEqual(self.scalar("SELECT count(*) FROM runs", db=db2), 3)
        self.assertEqual(self.scalar("SELECT count(DISTINCT cluster) FROM runs", db=db2), 1)
        # Rows that only exist on one side do get copied
        other = make_result_dir(root / "other")
        (other / "json" / "ozone.sto-3g.ccsd.json").write_text(
            json.dumps(make_result("Fri Sep  4 09:00:00 2026", INPUT)))
        exachem_db.main(["--db", self.db, "push", "--cluster", "deception", "--user", "tester", str(other)])
        self.assertEqual(self.scalar("SELECT count(*) FROM runs"), 4)   # +1 result; its printouts are known runs
        exachem_db.main(["--db", db2, "merge", self.db])
        self.assertEqual(self.scalar("SELECT count(*) FROM runs", db=db2), 4)


class RestartFlowTests(unittest.TestCase):
    """The pipeline pushes after the first run and again after the restart, whose fetched directory
    holds the first run's printout again but no longer its JSON. One row per run must survive."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = str(self.root / "flow.db")
        self.first_date, self.restart_date = "Tue Aug 18 23:54:49 2026", "Tue Aug 18 23:56:04 2026"
        self.restart_input = dict(INPUT, SCF={"restart": True})

    def tearDown(self):
        self.tmp.cleanup()

    def push(self, d):
        exachem_db.main(["--db", self.db, "push", "--cluster", "deception", "--user", "t", str(d)])

    def rows(self):
        with closing(exachem_db.connect(self.db)) as conn:
            return [(r["run_date"], r["status"]) for r in conn.execute("SELECT * FROM runs ORDER BY run_date")]

    def test_first_run_then_restart(self):
        after_first = self.root / "fetched"
        (after_first / "json").mkdir(parents=True)
        write_printout(after_first, "a.log", make_log(self.first_date, INPUT, COMPLETED_BODY))
        (after_first / "json" / "r.json").write_text(json.dumps(make_result(self.first_date, INPUT)))
        self.push(after_first)
        # the restart's fetch overwrote the local directory: both printouts, only the restart's JSON
        (after_first / "json" / "r.json").write_text(json.dumps(make_result(self.restart_date, self.restart_input)))
        write_printout(after_first, "b.log", make_log(self.restart_date, self.restart_input, COMPLETED_BODY))
        self.push(after_first)
        self.assertEqual(self.rows(), [(FIRST, "success"), (RESTART, "success")])

    def test_result_without_printout_gets_its_date_from_the_json(self):
        d = self.root / "d"
        d.mkdir()
        (d / "r.json").write_text(json.dumps(make_result(self.first_date, INPUT)))
        self.push(d)
        self.assertEqual(self.rows(), [(FIRST, "success")])

    def test_printout_first_then_its_result(self):
        d = self.root / "d"
        d.mkdir()
        write_printout(d, "a.log", make_log(self.first_date, INPUT, COMPLETED_BODY))
        self.push(d)
        self.assertEqual(self.rows(), [(FIRST, "no_result")])
        (d / "r.json").write_text(json.dumps(make_result(self.first_date, INPUT)))
        self.push(d)
        self.assertEqual(self.rows(), [(FIRST, "success")])          # upgraded, not duplicated


class ExitCodeHintTests(unittest.TestCase):
    """A run whose printout stops after the header: unknown by itself, failed when the pipeline says so."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.result_dir = self.root / "output.workspace.remote.2026-09-01T10:00:00"
        self.result_dir.mkdir()
        write_printout(self.result_dir, "printout.txt", make_log("Tue Sep  1 10:00:05 2026", INPUT, ""))

    def tearDown(self):
        self.tmp.cleanup()

    def status(self, *extra):
        db = str(self.root / ("db_" + "_".join(extra).replace("-", "") + ".db"))   # one file per case
        exachem_db.main(["--db", db, "push", "--cluster", "c", "--user", "t", *extra, str(self.result_dir)])
        with closing(exachem_db.connect(db)) as conn:
            return conn.execute("SELECT status FROM runs").fetchone()[0]

    def test_exit_code_hint(self):
        self.assertEqual(self.status(), "unknown")
        self.assertEqual(self.status("--exit-code", "0"), "unknown")
        self.assertEqual(self.status("--exit-code", "1"), "failed")


class ParserTests(unittest.TestCase):
    def test_parse_exachem_date(self):
        self.assertEqual(exachem_db.parse_exachem_date("Thu Aug 27 15:20:34 2026"), "2026-08-27T15:20:34")
        self.assertEqual(exachem_db.parse_exachem_date("Thu Aug  7 05:02:03 2026"), "2026-08-07T05:02:03")
        self.assertIsNone(exachem_db.parse_exachem_date("not a date"))

    def test_header_without_input_echo(self):
        info = exachem_db.parse_log_header("date: Thu Aug 27 15:20:34 2026\nnothing else\n")
        self.assertEqual(info["run_date"], "2026-08-27T15:20:34")
        self.assertIsNone(info["input"])
        self.assertIsNone(info["exachem_commit"])


if __name__ == "__main__":
    unittest.main()
