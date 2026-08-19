# ExaChem Runner

Automate the [ExaChem](https://github.com/ExaChem/exachem) workflow from local machine.

## Install Dependency

Get Nextflow from their website ([https://nextflow.io/](https://nextflow.io/)).

## Set up Input

Mannually pull out [ExaChem](https://github.com/ExaChem/exachem)'s input folder ([https://github.com/ExaChem/exachem/tree/main/inputs](https://github.com/ExaChem/exachem/tree/main/inputs)) into `submodules/exachem/inputs`.

## Run

First, please modify the parameters in the config file `pg00_submit_job/nextflow.params.config` accordingly.

| Parameter | Description |
| --- | --- |
| `input` | ExaChem input JSON for the first run |
| `restart_input` | ExaChem input JSON for the restart run, needs `SCF.restart` set to `true` |
| `account` | Slurm account to charge the job to |
| `mail_user` | Email address for Slurm job failure notifications (Slurm `--mail-user`) |
| `slurm_partition` | Slurm partition to submit to (Slurm `-p`) |
| `slurm_job_time_limit` | Slurm job time limit, e.g. `1h`, `2d`, or `04:44:44` |
| `nodes` | Number of nodes to reserve (Slurm `-N`) |
| `np` | Number of MPI ranks (`mpirun -n`) |
| `remote_host` | Remote server host name, as `ssh` resolves it |
| `nextflow_slurm_template_file` | Template pipeline run on the remote for the first run |
| `nextflow_restart_template_file` | Template pipeline run on the remote for the restart run |
| `do_fetch_results` | Whether to copy the results back to the local machine |
| `remote_tamm_install_path` | TAMM installation path on the remote server |
| `remote_workspace_dir_basename` | Remote working directory name, set automatically by the scripts in `sbatch/` |

The ones that usually need changing are `account`, `mail_user`, `input`, `slurm_partition`, `remote_host`, and `remote_tamm_install_path`.

Second, in the repo root, run

```bash
cd pg00_submit_job
bash sbatch/run02.run_nextflow_exachem_remote.v2.nextflow_slurm.sh
```

The current Nextflow session's log will be saved in directory `output.workspace.nf.<date_time>`.
The results fetched from the remote server will be saved in directory `output.workspace.remote.<date_time>`.

Or you can use the script `run03.run_nextflow_exachem_remote.v3.and_restart.sh` to try run-and-restart.
That one reuses the first run's remote directory, and submits `restart_input` instead of `input`.
So `restart_input` should point to an input with `SCF.restart` set to `true`, and with `common.file_prefix` and `basis.basisset` matching the first run, since that is how the restart locates the previous output directory `<file_prefix>.<basisset>_files`.


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

