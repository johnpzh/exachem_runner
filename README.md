# ExaChem Runner
Automate the [ExaChem](https://github.com/ExaChem/exachem) workflow from local machine.

## Set up
Fetch the [ExaChem](https://github.com/ExaChem/exachem) as submodule by running
```bash
git submodule update --init
```
## Run
First, change the `account` and `input` in the script `pg00_submit_job/sbatch/run00.run_exachem_remote.sh` accordingly.

Second, in the repo root, run
```bash
cd pg00_submit_job
bash sbatch/run00.run_exachem_remote.sh
```

If you want to run Nextflow script, do
```bash
cd pg00_submit_job
bash sbatch/run01.run_nextflow_exachem_remote.sh
```

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