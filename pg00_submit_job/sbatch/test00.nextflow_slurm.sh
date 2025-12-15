nextflow run ../scripts/template00.nextflow.slurm.run_mpirun.nf \
  -work-dir "output.workspace.nf.$(date +%FT%T)" \
  -ansi-log false \
  --input "/qfs/people/peng599/pppp/exachem_workflow_automation_project/exachem/inputs/ozone.json" \
  --np 2 \
  --tamm_install_path "/qfs/people/peng599/local/install/tamm" \
  --account BR26_PENG599