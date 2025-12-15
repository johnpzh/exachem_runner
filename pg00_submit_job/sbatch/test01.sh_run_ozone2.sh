module purge
module load gcc/14.2.0 openmpi/5.0.7 cmake intel/2025.2.0 java/24.0.2
ulimit -s unlimited

mpirun -n 2 /qfs/people/peng599/local/install/tamm/bin/ExaChem \
    /people/peng599/pppp/exachem_workflow_automation_project/exachem_runner/pg00_submit_job/data/ozone2.json 2>&1 \
    | tee output.test01.sh_run_ozone2.sh.log