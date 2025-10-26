#!/bin/bash
#SBATCH --job-name="exachem_runner"          # Default; overridden by --job-name
#SBATCH --partition=slurm                    # Default; overridden by --partition
######SBATCH --partition=short
######SBATCH --exclude=dc[119,077]
######SBATCH --account=BR26_PENG599               # Default; overridden by --account
#SBATCH -N 1                                 # Default; overridden by --nodes
#SBATCH --time=04:44:44                      # Default; overridden by --time
#SBATCH --output=output.%x.%j.out.log
#SBATCH --error=output.%x.%j.err.log
######SBATCH --mail-type=FAIL
######SBATCH --mail-user=zhen.peng@pnnl.gov
#SBATCH --exclusive

#### sinfo -p <partition>
#### sinfo -N -r -l
#### srun -A CENATE -N 1 -t 20:20:20 --pty -u /bin/bash

#First make sure the module commands are available.
source /etc/profile.d/modules.sh

#Set up your environment you wish to run in with module commands.
echo
echo "loaded modules"
echo
module purge
# module load python/miniconda25.5.1
module load gcc/14.2.0 openmpi/5.0.7 cmake intel/2025.2.0
module list &> _modules.lis_
cat _modules.lis_
/bin/rm -f _modules.lis_

#Python version
# source /share/apps/python/miniconda25.5.1/etc/profile.d/conda.sh
# eval "$(conda shell.bash hook)"
# conda activate pp
echo
echo "python version"
echo
command -v python
python --version


#Next unlimit system resources, and set any other environment variables you need.
ulimit -s unlimited
echo
echo limits
echo
ulimit -a

echo
echo "Environment Variables"
echo
printenv
# echo
# echo "ldd output"
# echo
# ldd your_executable

#Now you can put in your parallel launch command.
#For each different parallel executable you launch we recommend
#adding a corresponding ldd command to verify that the environment
#that is loaded corresponds to the environment the executable was built in.


# set -euo pipefail
set -eu

# Helper function for arg parsing (simple getopts-style)
INPUT_FILE=""
NPROCESSES=""
while [ $# -gt 0 ]; do
  case $1 in
    "--input")
      INPUT_FILE="$2"
      shift 2
      ;;
    "--np")
      NPROCESSES="$2"
      shift 2
      ;;
    *)
      echo "Unknown option $1"
      echo "Usage: $0 --input <file> --np <int>"
      exit 1
      ;;
  esac
done

if [ -z "$INPUT_FILE" ]; then
  echo "Error: --input <file> is required."
  exit 1
fi

if [ -z "$NPROCESSES" ]; then
  NPROCESSES=2
  echo "Warning: --np <int> is not provided. Set to default 2."
fi

export TAMM_INSTALL_PATH=$HOME/local/install/tamm/
# export INPUT_FILE="/qfs/people/peng599/pppp/exachem_workflow_automation_project/exachem/inputs/ozone.json"

set -x
mpirun -n ${NPROCESSES} "${TAMM_INSTALL_PATH}/bin/ExaChem" "${INPUT_FILE}" | tee output.${SLURM_JOB_NAME}.${SLURM_JOB_ID}.pure_out.log
set +x
