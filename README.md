# ExaChem Runner
Automate the [ExaChem](https://github.com/ExaChem/exachem) workflow from local machine.

## Install Dependency
Get Nextflow from their website (https://nextflow.io/).

## Set up Input
Mannually pull out [ExaChem](https://github.com/ExaChem/exachem)'s input folder (https://github.com/ExaChem/exachem/tree/main/inputs) into `submodules/exachem/inputs`.

## Run
First, change the `account` and `input` in the configure files `pg00_submit_job/nextflow.config.nf` and `pg00_submit_job/nextflow.config.restart2.nf` accordingly.

Second, in the repo root, run
```bash
cd pg00_submit_job
bash sbatch/run02.run_nextflow_exachem_remote.v2.nextflow_slurm.sh
```
or you can use the script `run03.run_nextflow_exachem_remote.v3.and_restart.sh` to try run-and-restart.

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