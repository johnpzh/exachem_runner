NF_WORKSPACE="output.workspace.nf.$(date +%FT%T)"
# REMOTE_WORKSPACE_DIR_BASENAME="output.workspace.remote.\$(date +%FT%T)"
REMOTE_WORKSPACE_DIR_BASENAME="output.workspace.remote.7777"

nextflow run ../scripts/nf01.run_exachem.nextflow_slurm.nf \
    --remote_workspace_dir_basename "${REMOTE_WORKSPACE_DIR_BASENAME}" \
    --do_fetch_results FALSE \
    -c nextflow.config.nf \
    -work-dir "${NF_WORKSPACE}" \
    -ansi-log false


nextflow run ../scripts/nf02.restart_exachem.nf \
    --remote_workspace_dir_basename "${REMOTE_WORKSPACE_DIR_BASENAME}" \
    -c nextflow.config.restart2.nf \
    -work-dir "${NF_WORKSPACE}" \
    -ansi-log false

