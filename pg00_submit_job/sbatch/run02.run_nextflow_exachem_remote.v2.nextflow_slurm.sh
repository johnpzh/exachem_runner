NF_WORKSPACE="output.workspace.nf.$(date +%FT%T)"
REMOTE_WORKSPACE_DIR_BASENAME="output.workspace.remote.$(date +%FT%T)"

nextflow run ../scripts/nf01.run_exachem.nextflow_slurm.nf \
    --remote_workspace_dir_basename "${REMOTE_WORKSPACE_DIR_BASENAME}" \
    -c nextflow.params.config \
    -work-dir "${NF_WORKSPACE}" \
    -ansi-log false
