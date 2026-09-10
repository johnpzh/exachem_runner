# ExaChem Results Database: Design Notes

Status: design proposal, not implemented yet. Last updated 2026-09-09.

## 1. Goal

Store the results of every ExaChem run, including failed runs, in one central database, so that

- runs can be queried across each other on common properties (molecule, basis set, method, problem size, machine, version), and
- the complete output of any run is still available for detailed analysis.

The first step is single-user: the pipeline in this repository fetches results from the cluster and stores them. Multi-user and public access are a stretch goal (section 8); the schema is prepared for them from the start so that nothing has to be redesigned later.

## 2. What an ExaChem run produces

These facts drive the design. They were checked against ExaChem's source code and against results fetched by this pipeline.

- **One task per run.** ExaChem rejects an input with more than one task enabled, so every run has exactly one task. The valid task names are `sinfo`, `scf`, `fci`, `fcidump`, `mp2`, `gw`, `cd_2e`, `cc2`, `ducc`, `ccsd`, `ccsd_t`, `ccsd_lambda`, `eom_ccsd`, `gfccsd`, `rteom_cc2`, `rteom_ccsd`, `dlpno_ccsd`, and `dlpno_ccsd_t`.
- **One JSON result file per run**, written to `<file_prefix>.<basisset>_files/<scf_type>/json/<file_prefix>.<basisset>.<task>.json`, for example `ozone.sto-3g_files/restricted/json/ozone.sto-3g.ccsd.json`. It has three top-level parts:
  - `output`: `machine_info` (date, node and rank counts, CPU name and memory), `system_info` (basis functions, shells, electrons), then one section per stage that ran, e.g. `SCF`, `CD`, `CCSD`, with per-iteration energies, residuals and timings, final energies, and a `performance.total_time` per section. Which sections exist depends on the task. Iterations are objects keyed by the iteration number as a string (`"1"`, `"2"`, ...), not arrays.
  - `molecule`: name, basis set, basis-function and electron counts.
  - `input`: a verbatim echo of the input file.
- **Small files.** A small-molecule CCSD result is about 10 KB. Even large runs stay in the tens or hundreds of KB, because only iteration summaries are written, not tensors.
- **Provenance is not in the JSON.** The JSON contains no version information (ExaChem's CMake version is 0.0.0). The ExaChem and TAMM git commits, the executable path and the input path appear only in the header of the printout log. The cluster name is known only to the pipeline.
- **A restart overwrites the result file.** A restart run writes the same JSON path again and the pipeline's fetch step overwrites the local result directory, so after a run-and-restart only the restart's result survives on disk. Results must therefore be stored right after each job.
- **A failed run writes no JSON**, only logs. Note the trap in the restart case: the previous run's JSON is still present in the same directory, so "a JSON file exists" does not mean "this job succeeded". The ingest step must check the job's exit status and compare the JSON's `machine_info.date` with the job's time.

## 3. Storage choice

**Decision: one relational table with the whole result file stored in a JSON column, plus ordinary columns for the properties that are common to all runs. Phase 1 uses SQLite (one file, no server) because no shared machine is available yet; the target is PostgreSQL with JSONB once one exists. Section 7 explains why the move is cheap and how to do it.**

Why this shape:

- The output is document-like and its structure varies by task and by ExaChem version, which a rigid relational schema cannot follow. The JSONB column absorbs that variation.
- The common columns (molecule, basis, task, problem size, machine, version, energies, timings) give a stable, readable schema for the queries that matter, and let ordinary SQL do joins, aggregates and window functions.
- JSONB can be indexed (GIN index, or expression indexes on individual paths) and queried with path operators, so the common columns are a convenience for clarity, not a workaround for slow JSON access.
- One system for both structured metadata and documents, easy to host, back up and connect from any language.

Alternatives considered:

- **MongoDB** would work: it stores JSON natively and can index and query nested numeric fields just as well. PostgreSQL is preferred for having relational and document data in one place with plain SQL, and for being more commonly available as an institutional service. At the expected scale (at most a few hundred thousand runs over several years, i.e. a few GB) the performance of either engine is not a concern.
- **SQLite** is the phase 1 store: a single file, no server, and JSON functions plus the `->>` operator built in since SQLite 3.38 (2022), so the same style of query works. Its limits: only one machine can safely use the file (SQLite's own documentation warns that network filesystems can corrupt it, and sync folders such as OneDrive are worse), and colleagues cannot insert into it remotely. Section 6 describes how results are shared meanwhile.
- **Vector databases** are for similarity search over embeddings. They cannot answer exact queries such as "iteration-1 time for all CCSD runs with 500 to 1000 basis functions", so they are not applicable.
- **MolSSI QCArchive** is the community platform for quantum chemistry results. It would require converting every result to QCSchema, its import path for externally computed records is new and limited, and ExaChem-specific data would survive only in an "extras" field. It remains a possible export target later; the extracted energy columns below use QCSchema field names to keep that easy.

A note on infrastructure: the PostgreSQL version needs *a PostgreSQL server that everyone can reach*, nothing more. Running one in a Docker container is merely a convenient way to get one onto a machine; a package-manager installation or a server provided by the institution works the same. Section 7 gives the container setup and the user roles for when such a machine is available.

## 4. Data model

Principles:

1. **The raw result file is stored unchanged.** All columns are derived from it (or from the log and the pipeline) at insert time and can be recomputed later if the extraction logic improves or ExaChem's format changes.
2. **Failed runs are kept.** A `status` column records the outcome, `result` is NULL when no JSON was produced, and the logs are read for provenance and status but not stored. Failures and timeouts are often the most informative rows in a performance database. A run that finished normally but whose JSON was overwritten by a later run in the same directory gets the status `no_result`, so it is not mistaken for a failure.
3. **Provenance comes from three sources**: the result JSON, the printout log header, and the pipeline's own parameters.
4. **One row per result document**, made unique by a content fingerprint (SHA-256 of the result JSON text, or of the printout for a run without one), so re-ingesting or merging the same results is harmless. A run is additionally recognized by `(cluster, run_date)`: a printout-only row is skipped when that run is already stored with its result and replaced when the result arrives later, which keeps a run-and-restart, whose fetched directory carries the first run's printout twice, at one row per run. Nothing is inferred from file names: result files and printouts are recognized by their content, and matched to each other by the run date ExaChem writes into both.
5. **Ready for multiple users.** A `submitted_by` column exists from day one; authentication is added later without changing the table.

### Schema

Only what is not inside the documents is stored. The query helpers are generated columns, computed from the JSON, so they can never disagree with it and can be added or dropped later without re-ingesting.

```sql
CREATE TABLE runs (
  -- Identity and provenance: known for every run, not (reliably) inside the JSON documents
  id              BIGSERIAL PRIMARY KEY,
  fingerprint     TEXT NOT NULL UNIQUE,          -- sha256 of the result JSON text (or of the printout when there is none)
  status          TEXT NOT NULL,                 -- 'success', 'no_result', 'failed', 'timeout', 'unknown'
  cluster         TEXT NOT NULL,                 -- where the run happened: the pipeline's remote_host
  run_date        TIMESTAMPTZ,                   -- when ExaChem started (machine_info.date, or the printout's date line)
  exachem_commit  TEXT,                          -- from the printout header (the JSON carries no version)
  tamm_commit     TEXT,                          -- same source
  submitted_by    TEXT,                          -- who pushed the row
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- The documents themselves
  input           JSONB NOT NULL,                -- the input file as submitted (available even for failed runs)
  result          JSONB,                         -- the complete result JSON; NULL if the run wrote none
  -- Query helpers computed from the documents (PostgreSQL stores generated columns; SQLite keeps them virtual)
  nnodes          INTEGER GENERATED ALWAYS AS ((result #>> '{output,machine_info,nnodes}')::int) STORED,
  nproc_total     INTEGER GENERATED ALWAYS AS ((result #>> '{output,machine_info,nproc_total}')::int) STORED
);

CREATE INDEX runs_result_gin   ON runs USING GIN (result jsonb_path_ops);
CREATE INDEX runs_cluster_date ON runs (cluster, run_date);
```

Notes on the columns:

- Nothing about the calculation itself is a column: task, molecule, basis set, problem size (number of basis functions, `output.system_info.nbf`), energies and timings are all read from the documents by JSON path. Any of them can be promoted to a generated column, or given an expression index, when a query needs it often; the two machine-size helpers `nnodes` and `nproc_total` are there as the pattern to copy.
- Energies are in Hartree and times in seconds, as ExaChem writes them. Their location in the JSON depends on the task (`output.CCSD.final_energy.total`, `output.CCSD(T)...`, and so on), which is one more reason not to fix them as columns.
- Log files are not stored. They are read at ingest time for the git commits, the run date, the echoed input file, and the status; the fetched result directories keep the originals.
- **SQLite variant.** The ingest tool creates the schema for whichever backend the connection string names. In SQLite the differences are: `INTEGER PRIMARY KEY` instead of `BIGSERIAL`, the two JSON columns stored as `TEXT` holding valid JSON, timestamps stored as ISO 8601 text (`ingested_at` in UTC, `run_date` as the cluster's local time because ExaChem prints no offset), generated columns declared `VIRTUAL` with `json_extract(...)` expressions, and no GIN index (an expression index on any JSON path can be added when needed).

## 5. Example queries

Iteration-1 CCSD time as a function of problem size:

```sql
SELECT (result #>> '{output,system_info,nbf}')::int AS nbf,
       (result #>> '{output,CCSD,iter,1,performance,total_time}')::float AS iter1_time_s
FROM runs
WHERE status = 'success' AND (input #>> '{TASK,ccsd}')::bool
  AND (result #>> '{output,system_info,nbf}')::int BETWEEN 500 AND 1000
ORDER BY nbf;
```

Strong scaling of one calculation across rank counts:

```sql
SELECT nproc_total, nnodes,
       (result #>> '{output,CCSD,performance,total_time}')::float AS total_time_s
FROM runs
WHERE status = 'success' AND result #>> '{molecule,name}' = 'uracil'
  AND result #>> '{molecule,basis,basisset}' = 'cc-pvdz' AND (input #>> '{TASK,ccsd}')::bool
ORDER BY nproc_total;
```

Failure rate per cluster and ExaChem version:

```sql
SELECT cluster, exachem_commit,
       count(*) FILTER (WHERE status <> 'success') AS failed,
       count(*) AS total
FROM runs
GROUP BY cluster, exachem_commit
ORDER BY total DESC;
```

The SQLite spelling of the same path expression differs only in the operator's right-hand side, which is a JSON path:

```sql
SELECT result ->> '$.output.system_info.nbf' AS nbf,
       result ->> '$.output.CCSD.iter.1.performance.total_time' AS iter1_time_s
FROM runs
WHERE status = 'success' AND input ->> '$.TASK.ccsd' = 1
  AND result ->> '$.output.system_info.nbf' BETWEEN 500 AND 1000
ORDER BY nbf;
```

Any value inside the document is reachable the same way, e.g. the number of SCF iterations is `(result #>> '{output,SCF,n_iterations}')::int` in PostgreSQL and `result ->> '$.output.SCF.n_iterations'` in SQLite. The task is the one key of the input's `TASK` section that is true, hence the `TASK,ccsd` test above. Queries on the ordinary columns are identical in both.

## 6. Ingestion

### Phase 1: a standalone ingest script writing to a SQLite file (implemented)

Implemented as `tools/exachem_db.py`, a standard-library-only Python tool whose usage is in the README. Its `push <result_dir>` command

1. finds the result JSON files and any ExaChem printouts below the directory, recognized by content rather than by name;
2. parses the printout header for the ExaChem and TAMM commits, the run date, and the echoed input file;
3. matches a result JSON to a job by the run date that ExaChem writes both into the log header and into `machine_info.date`, then sets `status`: `success` when a JSON matched, `no_result` when the log shows a normal end but the JSON was overwritten by a later run, `failed` or `timeout` when the logs contain error or time-limit markers, and `unknown` otherwise (the job's real exit status will replace this heuristic once the pipeline prerequisites below are done);
4. extracts the columns listed above (with the basis-function and electron counts read from the log when there is no JSON) and inserts one row, skipping rows that already exist, or replacing them with `--on-conflict replace`.

Because it works on any directory, it can also backfill previously fetched results and ingest runs made outside the pipeline.

The tool takes the database path from `--db`, else `$EXACHEM_DB` (a path or `sqlite:///path`), else `./exachem_results.db`. The PostgreSQL backend will be selected the same way, by a `postgresql://` URL; nothing else changes for the user.

Sharing while on SQLite: each person keeps their own file and runs their own pushes. To combine them, the `merge` subcommand copies the rows of another file into yours, skipping rows whose fingerprint already exists, so merging is idempotent and can be repeated. Alternatively colleagues send their fetched result directories and one person ingests them. Do not place a live SQLite file on a network filesystem or in a sync folder such as OneDrive; copying a closed file to share or back it up is fine.

### Prerequisites in the pipeline (done)

Three changes were needed before failed runs could be stored reliably:

1. **Capture ExaChem's exit status.** The templates run `mpirun ... | tee ...`; without `set -o pipefail` the pipe's status was `tee`'s, so a failed ExaChem looked like a successful job. The templates now set `pipefail`, and the Slurm logs and the printout are written straight into the remote workspace directory instead of being copied there at the end, so they exist even when the run dies.
2. **Fetch logs even when the job fails.** The submit step now records the remote exit status as a process output instead of failing, the fetch step copes with a missing `json/` directory, and the exit status is passed to the tool as `--exit-code`, which turns an otherwise `unknown` status into `failed`.
3. **Store right after each job**, both the first run and the restart, because the restart overwrites the result file. The publish step runs after every fetch, and the run-and-restart driver fetches after both runs.

A side finding: command-line values such as `--do_fetch_results FALSE` reach the pipeline as strings, which Groovy treats as true, so that switch had never disabled the fetch. The pipelines now coerce true/false strings explicitly.

### Phase 2: pipeline hook (implemented)

A `publish_results` process in `nf01` and `nf02` runs after the fetch step and calls the tool on the fetched directory, with the cluster label taken from `remote_host` and the remote exit status passed as `--exit-code`. It is controlled by three config parameters: `do_publish_results`, `results_db` (the SQLite file, by default `exachem_results.db` in the directory the pipeline is launched from, i.e. `pg00_submit_job/`; a relative path is resolved against that directory, never against a task's work directory), and `exachem_db_tool`. The driver `run04.run_nextflow_exachem_remote.v4.single_database.sh` makes the database explicit through the `EXACHEM_DB` environment variable, so several people can point their runs at one file on a shared machine. Once the store is PostgreSQL, the connection string will live in an environment variable rather than in the config file.

## 7. Moving to a shared PostgreSQL server

### Why the move is cheap

The move costs little if these rules are followed from the first day on SQLite:

- **The ingest tool owns the schema.** It creates the tables for either backend from one definition (SQLAlchemy Core, or two short DDL strings). Nobody hand-edits the SQLite file's schema.
- **Only portable SQL in the tool**: plain column types, no SQLite-only or PostgreSQL-only features in the code paths that insert and read. The nested-JSON path syntax is the one place the two differ, and it is confined to helper functions.
- **The raw input and result JSON are stored unchanged**, so every derived column can be recomputed on the target. The database file is never the only copy of anything: the fetched result directories remain the source of truth.
- **Timestamps are stored in UTC** as ISO 8601 text on SQLite, so they load into `TIMESTAMPTZ` without guessing time zones.

### The migration itself

Two options, both taking minutes for a few hundred thousand rows:

1. **Re-ingest through the tool (preferred).** `exachem_db.py export` writes every row as one JSON line, with the stored JSON text unchanged; `exachem_db.py import` reads those lines into the PostgreSQL database. This guarantees the PostgreSQL schema is the canonical one and exercises the same code path as day-to-day pushes.
2. **`pgloader`** migrates a SQLite file directly: `pgloader sqlite:///path/to/exachem_results.db pgsql://user@host/exachem`. It creates the tables and converts types automatically; the JSON columns arrive as `text` and are converted to `JSONB` with one `ALTER TABLE ... TYPE jsonb USING result::jsonb` afterwards.

In either case, verify with row counts per cluster and a comparison of the set of fingerprints before retiring the SQLite files.

### Running PostgreSQL in a Docker container

A database in a container is a network service, not a file: sharing it means running the container on a machine everyone can reach, publishing its port, and giving each person a login. A laptop does not qualify (it sleeps, changes address, and sits behind NAT); a lab VM or server with a stable hostname is the minimum. If the institution offers managed PostgreSQL, that replaces everything in this subsection with a request for a database and a few logins.

```yaml
# docker-compose.yml on the shared machine
services:
  db:
    image: postgres:17
    restart: unless-stopped
    ports:
      - "5432:5432"                 # all interfaces: reachable from other machines
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}   # required by the image; superuser password
      POSTGRES_DB: exachem
    volumes:
      - pgdata:/var/lib/postgresql/data         # without a volume, data is lost with the container
volumes:
  pgdata:
```

The official image is preconfigured to listen on all interfaces and to accept password-authenticated connections from any host, so publishing the port is all the networking required. Only when supplying a custom PostgreSQL configuration file does `listen_addresses` have to be set by hand.

### User roles

Run once as the superuser, then once per person. Writers get exactly the rights the ingest tool needs and nothing else; nobody but the administrator holds the superuser password.

```sql
CREATE ROLE exachem_writer NOLOGIN;
GRANT SELECT, INSERT ON runs TO exachem_writer;
GRANT USAGE, SELECT ON SEQUENCE runs_id_seq TO exachem_writer;   -- needed for the id column
CREATE ROLE exachem_reader NOLOGIN;
GRANT SELECT ON runs TO exachem_reader;

CREATE ROLE alice LOGIN PASSWORD '...' IN ROLE exachem_writer;
CREATE ROLE bob   LOGIN PASSWORD '...' IN ROLE exachem_reader;
```

Each person then sets one environment variable and uses the tool as before:

```bash
export EXACHEM_DB_URL="postgresql://alice:...@dbhost.example.org:5432/exachem"
exachem-db push output.workspace.remote.2026-08-27T15:20:06
```

Optional: if people should be able to correct or delete only their own rows, PostgreSQL can enforce that inside the table with row-level security, using the `submitted_by` column defaulted to the connecting role's name. This gives ownership without the web service described in section 8.

### Access and operational notes

- **Inside the institution's network**, the published port is enough.
- **From outside**, without exposing the port to the internet, users open an SSH tunnel to a machine that can reach the host and point the connection string at the tunnel's local port. Exposing the service publicly belongs to the stretch goal in section 8.
- **Published ports bypass the host's `ufw` firewall**: Docker routes container traffic before those rules apply. Restrict access with the network or a firewall in front of the machine. For local testing publish as `127.0.0.1:5432:5432` so nothing outside can connect.
- **Mount the volume exactly at `/var/lib/postgresql/data`** for PostgreSQL 17 and below; a mount one level up does not persist the data.
- **Back up** with a scheduled `pg_dump` from inside the container. A volume is persistence, not a backup.

## 8. Future work (stretch goal): multiple users and public access

Not part of the initial deliverable. Recorded here so that the phase 1 and 2 design, and the shared PostgreSQL setup of section 7, do not close the door on it.

Users must not connect to the database directly. A small web service in front of it authenticates users (sign in with ORCID or GitHub, per-user tokens for the command line), validates submissions (the input part against ExaChem's own JSON schema in `docs/schema/input_schema.json` of the ExaChem repository), enforces size and rate limits, and records ownership and visibility (private, group, public). Reads of public records need no account. This turns the service into a public-facing endpoint, which affects hosting and security review, and is the main reason it is deferred.

## 9. Open questions

- Which additional problem-size measures should become columns (Cholesky vectors, active orbitals, tile sizes)?
- Should the side files ExaChem writes next to the JSON (`*.sinfo.json`, `*.runcontext.json`) also be stored?
- Should build information (compiler, MPI, BLAS library) be recorded? It is not available from ExaChem's output today and would have to come from the pipeline's module list.
- Who hosts the PostgreSQL server for the shared phase?
