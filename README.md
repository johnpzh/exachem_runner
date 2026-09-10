# ExaChem Runner

Automate the [ExaChem](https://github.com/ExaChem/exachem) workflow from local machine.

## Install Dependency

Get Nextflow from their website ([https://nextflow.io/](https://nextflow.io/)).

The results database tool (`tools/exachem_db.py`) needs Python 3 with its standard library only.

## Set up Input

Manually pull out [ExaChem](https://github.com/ExaChem/exachem)'s input folder ([https://github.com/ExaChem/exachem/tree/main/inputs](https://github.com/ExaChem/exachem/tree/main/inputs)) into `submodules/exachem/inputs`.

## Run

First, please modify the parameters in the config file `pg00_submit_job/nextflow.params.config` accordingly.


| Parameter                        | Description                                                                  |
| -------------------------------- | ---------------------------------------------------------------------------- |
| `input`                          | ExaChem input JSON for the first run                                         |
| `restart_input`                  | ExaChem input JSON for the restart run, needs `SCF.restart` set to `true`    |
| `account`                        | Slurm account to charge the job to                                           |
| `mail_user`                      | Email address for Slurm job failure notifications (Slurm `--mail-user`)      |
| `slurm_partition`                | Slurm partition to submit to (Slurm `-p`); `""` omits the flag               |
| `slurm_qos`                      | Slurm QOS (Slurm `--qos`), e.g. `regular` on Perlmutter; `""` omits the flag |
| `slurm_constraint`               | Slurm constraint (Slurm `--constraint`), e.g. `cpu` on Perlmutter; `""` omits the flag |
| `slurm_job_time_limit`           | Slurm job time limit, e.g. `1h`, `2d`, or `04:44:44`                         |
| `nodes`                          | Number of nodes to reserve (Slurm `-N`)                                      |
| `np`                             | Number of MPI ranks (`mpirun -n`)                                            |
| `remote_host`                    | Remote server host name, as `ssh` resolves it                                |
| `nextflow_slurm_template_file`   | Template pipeline run on the remote for the first run                        |
| `nextflow_restart_template_file` | Template pipeline run on the remote for the restart run                      |
| `do_fetch_results`               | Whether to copy the results back to the local machine                        |
| `remote_tamm_install_path`       | TAMM installation path on the remote server                                  |
| `results_db`                     | SQLite results database each fetched run is stored in; a relative path is relative to the launch directory (`pg00_submit_job/`); `""` disables storing |
| `do_publish_results`             | Whether to store each run in `results_db` right after fetching it            |
| `exachem_db_tool`                | Path of the ingest tool, `tools/exachem_db.py`                               |
| `remote_workspace_dir_basename`  | Remote working directory name, set automatically by the scripts in `sbatch/` |


The ones that usually need changing are `account`, `mail_user`, `input`, `slurm_partition`, `remote_host`, and `remote_tamm_install_path`.

Second, in the repo root, run

```bash
cd pg00_submit_job
bash sbatch/run02.run_nextflow_exachem_remote.v2.nextflow_slurm.sh
```

The current Nextflow session's log will be saved in directory `output.workspace.nf.<date_time>`.
The results fetched from the remote server will be saved in directory `output.workspace.remote.<date_time>`.

With `do_publish_results` on (the default), every fetched run is also stored in the SQLite results database named by `results_db`, by default `pg00_submit_job/exachem_results.db`; see "Results Database" below. The script `run04.run_nextflow_exachem_remote.v4.single_database.sh` does the same as `run02` but makes the database explicit: set `EXACHEM_DB` to point all runs at one shared file (a relative path is taken relative to `pg00_submit_job/`), and it prints the stored runs at the end:

```bash
cd pg00_submit_job
EXACHEM_DB=/path/to/shared/exachem_results.db bash sbatch/run04.run_nextflow_exachem_remote.v4.single_database.sh
```

Or you can use the script `run03.run_nextflow_exachem_remote.v3.and_restart.sh` to try run-and-restart.
That one reuses the first run's remote directory, and submits `restart_input` instead of `input`.
So `restart_input` should point to an input with `SCF.restart` set to `true`, and with `common.file_prefix` and `basis.basisset` matching the first run, since that is how the restart locates the previous output directory `<file_prefix>.<basisset>_files`.

## Results Database (version 0)

`tools/exachem_db.py` stores ExaChem results in a SQLite database, one row per result JSON file plus one per printout of a run that produced none (so failures are kept), so that runs can be queried across each other. Nothing is inferred from file names: result files are recognized by their content (a JSON object with an `output` section, anywhere below the directory), printouts by the header ExaChem writes, under any name, and a printout is matched to its result by the run date that ExaChem writes into both. It needs only Python 3 and its standard library. The design is described in `notes/database_design.md`.

The pipelines call it for you: with `do_publish_results = true` in the config, nf01 and nf02 run a `publish_results` step right after the fetch, so the first run of a run-and-restart is stored before the restart overwrites its JSON on the remote. Failed runs are stored too: the templates use `pipefail` and write their logs straight into the remote workspace directory, the submit step records the remote exit status instead of stopping the workflow and hands it to the tool as `--exit-code`, and the fetch step copes with a missing `json/` directory. The commands below are for storing results by hand, for example folders fetched before this existed:

```bash
# store every fetched result directory (safe to repeat: existing jobs are skipped)
python3 tools/exachem_db.py --db exachem_results.db push --cluster deception pg00_submit_job/output.workspace.remote.*

# look at what is stored
python3 tools/exachem_db.py --db exachem_results.db list
python3 tools/exachem_db.py --db exachem_results.db sql "SELECT result ->> '$.molecule.name' AS molecule, result ->> '$.molecule.basis.basisset' AS basisset, result ->> '$.output.system_info.nbf' AS nbf, result ->> '$.output.CCSD.final_energy.total' AS e_ccsd, result ->> '$.output.CCSD.performance.total_time' AS t_ccsd FROM runs WHERE status = 'success'"
python3 tools/exachem_db.py --db exachem_results.db sql "SELECT run_date, result ->> '$.output.CCSD.iter.1.performance.total_time' AS iter1_s FROM runs WHERE input ->> '$.TASK.ccsd' = 1"

# share with a colleague: they push into their own file, then either side merges the other's
python3 tools/exachem_db.py --db exachem_results.db merge colleague_results.db

# copy the whole database as JSON lines (also the migration path to PostgreSQL later)
python3 tools/exachem_db.py --db exachem_results.db export --out rows.jsonl
python3 tools/exachem_db.py --db other.db import rows.jsonl
```

The database file comes from `--db`, else the environment variable `EXACHEM_DB` (a path or `sqlite:///path`), else `./exachem_results.db` in the current directory. Keep the file on a local disk: SQLite must not be used live on a network filesystem or in a sync folder such as OneDrive.

What one row stores is only what is not inside the documents: `fingerprint` (the SHA-256 of the result JSON text, or of the printout for a run without one, which is what makes re-pushing and merging idempotent), `status`, `cluster`, `run_date`, the ExaChem and TAMM git commits, `submitted_by`, `ingested_at`, and the two documents themselves, `input` and `result`, as JSON text. Two query helpers, `nnodes` and `nproc_total`, are *generated* columns that SQLite computes from the JSON when read, so they cannot disagree with it; more can be added later with one `ALTER TABLE ... ADD COLUMN ... GENERATED ALWAYS AS (json_extract(result, '$...')) VIRTUAL`, without re-ingesting anything. Everything about the calculation itself, task, molecule, basis set, problem size, energies, timings, is a JSON path away, as in the examples above; `list` shows molecule, basis set and basis-function count by reading them from the documents. The full column list with comments is the `CREATE TABLE` at the top of `tools/exachem_db.py`. A run is additionally recognized by its cluster and run date, so a printout-only row is skipped when that run is already stored with its result, and replaced when the result is pushed later. The log files are read for the commits, the run date, the echoed input, and the status, but their contents are not stored; the fetched result directories keep them.

Where the values come from: the result JSON when the job wrote one; the header ExaChem prints at the top of its printout log for the git commits, the run date, and the echoed input file; and the command line for the cluster name. The `molecule` column is ExaChem's own molecule name, which is the input file name without `.json`.

Status values:

| Status      | Meaning                                                                                          |
| ----------- | ------------------------------------------------------------------------------------------------ |
| `success`   | A result JSON was found and stored; the printout with the same `machine_info.date`, if any, supplies the commits |
| `no_result` | A printout shows a normal end, but no JSON with its date exists: a later run in the same directory (a restart) overwrote it |
| `failed`    | The logs contain an error marker (`ERROR`, `tamm_terminate`, `Segmentation fault`, ...)          |
| `timeout`   | The logs contain Slurm's time-limit or cancellation message                                      |
| `unknown`   | None of the above, e.g. a truncated log                                                          |

The status is read off the logs; the pipeline additionally passes the remote exit status as `--exit-code`, which turns an `unknown` into `failed` when the run was reported as failed. Run the tests with `python3 tools/test_exachem_db.py`.

## Adapting to Another Cluster

The pipeline was developed on PNNL's Deception cluster, but the pipeline logic is not tied to it.
Each run involves two layers:

1. The local pipelines (`scripts/nf01.run_exachem.nextflow_slurm.nf` for the first run, `scripts/nf02.restart_exachem.nf` for the restart run), started on your local machine by the run scripts in `pg00_submit_job/sbatch/`. They create a workspace directory on `remote_host` over `ssh`, `scp` the input JSON and a template file into it, launch the template there with Nextflow while forwarding the parameters, and fetch the results back. Since they never talk to the scheduler themselves, they normally need no changes for a new cluster.
2. The template pipelines (`scripts/template00.nextflow.slurm.run_mpirun.v2.nf` for the first run, `scripts/template01.nextflow.slurm.run_mpirun.restart.nf` for the restart run, selected via `nextflow_slurm_template_file` / `nextflow_restart_template_file` in the config). They run on the cluster side, and their `submit_slurm_mpirun` process is what actually submits the Slurm job and runs ExaChem with `mpirun` — so this is where the cluster-specific settings (environment modules, Slurm flags, MPI launcher) live.

Therefore everything cluster-specific sits in two places: the config file `pg00_submit_job/nextflow.params.config`, and the `submit_slurm_mpirun` process of the template files.

### 1. Check the prerequisites on the new cluster

- Key-based (passwordless) SSH access from your local machine, ideally set up as a host alias in `~/.ssh/config`. The scripts run `ssh`/`scp` non-interactively, so they must work without a password prompt. Test with `ssh <alias> pwd`.
- Nextflow (which needs Java) available on the remote `PATH` for non-interactive shells, because the job is launched with `ssh <host> "... && nextflow run ..."`. Test with `ssh <alias> nextflow -version`.
- ExaChem built on that cluster. The appendix build script below is written for Deception — adapt its `module load` list and `MKLROOT` to your site.

### 2. Set the cluster-dependent parameters in the config

These differences are already covered by parameters in `pg00_submit_job/nextflow.params.config`, so no code change is needed:
`remote_host`, `account`, `slurm_partition`, `slurm_qos`, `slurm_constraint`, `slurm_job_time_limit`, `nodes`, `np`, `mail_user`, and `remote_tamm_install_path`.

`slurm_partition`, `slurm_qos`, and `slurm_constraint` are each left out of the Slurm submission when set to the empty string `""`, so a cluster can use any combination of them.
For example, NERSC Perlmutter selects resources with `--qos` and `--constraint` instead of a partition:

```groovy
slurm_partition = ""
slurm_qos = "regular" // or "debug", "premium", ...
slurm_constraint = "cpu" // or "gpu"
```

### 3. Make a cluster-specific copy of the two templates

Rather than editing the two template files in place, copy them per cluster (in the repo root):

```bash
cp scripts/template00.nextflow.slurm.run_mpirun.v2.nf scripts/template00.mycluster.nf
cp scripts/template01.nextflow.slurm.run_mpirun.restart.nf scripts/template01.mycluster.restart.nf
```

and point the config at the copies:

```groovy
nextflow_slurm_template_file = "${projectDir.parent}/scripts/template00.mycluster.nf"
nextflow_restart_template_file = "${projectDir.parent}/scripts/template01.mycluster.restart.nf"
```

This keeps one template per cluster, so switching clusters later is just a config change.

In the copies, adapt these parts of the `submit_slurm_mpirun` process (make the same edits in both files):


| Part                          | What to adapt                                                                                                                                                                                                                                                                                           |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `beforeScript`                | Replace the `module load ...` list with the modules your cluster needs to run ExaChem — normally the same compiler, MPI, and math-library modules used to build it there. Sites initialize the module system differently, so the `source /etc/profile.d/modules.sh` line may need changing or removing. |
| `clusterOptions`              | Keep the parameterized flags (`--qos` and `--constraint` are already covered by `slurm_qos` / `slurm_constraint`) and keep `--output` / `--error` pointing into `${launchDir}`, because the fetch step and the results database expect the logs there; adjust the site-specific ones. Remove `--exclusive` if the site does not allow it, and add flags the site requires, e.g. `--gres=gpu:...` or `--reservation=...`. |
| The `mpirun` line in `script` | Use the MPI launcher your site recommends, e.g. `srun --mpi=pmix -n ${params.np}` instead of `mpirun -n ${params.np}`, plus any process-binding options. Keep the `\| tee ...pure_out.log` part and the `set -o pipefail` above it: the printout is what the results database reads, and `pipefail` is what makes a failed ExaChem visible. |
| `executor 'slurm'`            | Only when the cluster does not run Slurm: switch to the matching [Nextflow executor](https://www.nextflow.io/docs/latest/executor.html) (`pbspro`, `lsf`, `sge`, ...). The `queue` and `time` directives carry over, but `clusterOptions` must be rewritten in that scheduler's own flag syntax.        |


Finally, do a cheap test first: keep a small input such as the ozone example, small `nodes`/`np`, and a short `slurm_job_time_limit`, and check that the Slurm job starts and the outputs come back before scaling up.

## Appendix: How to Build ExaChem

Change current directory to the target directory, then run the following script

```bash
# DECEPTION INSTRUCTIONS
# ----------------------

set -eu

PREV_PWD=$(readlink -f .)

module purge
module load gcc/14.2.0
module load openmpi/5.0.7
module load cmake intel/2025.2.0
module list


export MKLROOT=/vast/projects/ops/rocky9/intel/oneapi/mkl/latest/
export TAMM_INSTALL_PATH=$HOME/local/install

if [ ! -d "$TAMM_INSTALL_PATH" ]; then
    set -x
    mkdir -p "$TAMM_INSTALL_PATH"
    set +x
else
    echo "TAMM_INSTALL_PATH exists ${TAMM_INSTALL_PATH}, skip mkdir."
fi

echo
echo "#### $(date +%FT%T)"
echo "#### Building TAMM first"
echo

#Build TAMM first
git clone --depth 1 https://github.com/NWChemEx/TAMM.git
cd TAMM
mkdir build && cd build
set -x
CC=gcc CXX=g++ FC=gfortran cmake -DCMAKE_INSTALL_PREFIX=$TAMM_INSTALL_PATH/tamm -DCMAKE_BUILD_TYPE=Release ..  -DLINALG_VENDOR=IntelMKL -DLINALG_PREFIX=$MKLROOT  -DMODULES=CC
set +x
make -j4
make install

cd "$PREV_PWD"

echo
echo "#### $(date +%FT%T)"
echo "#### Building exachem"
echo

#Build exachem
git clone https://github.com/ExaChem/exachem
cd exachem
mkdir build && cd build
set -x
CC=gcc CXX=g++ FC=gfortran cmake -DCMAKE_INSTALL_PREFIX=$TAMM_INSTALL_PATH/tamm -DCMAKE_BUILD_TYPE=Release ..  -DLINALG_VENDOR=IntelMKL -DLINALG_PREFIX=$MKLROOT  -DMODULES=CC
set +x
make -j4
make install

cd "$PREV_PWD"

echo
echo "#### $(date +%FT%T)"
echo "#### Building all finished."
echo
```

