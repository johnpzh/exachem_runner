NF_WORKSPACE="output.workspace.nf.$(date +%FT%T)"
# Expanded once here on purpose: the restart run must target the same remote
# directory the first run created, so both runs need the identical basename
REMOTE_WORKSPACE_DIR_BASENAME="output.workspace.remote.$(date +%FT%T)"

# ---------
# First run
# ---------
nextflow run ../scripts/nf01.run_exachem.nextflow_slurm.nf \
    --remote_workspace_dir_basename "${REMOTE_WORKSPACE_DIR_BASENAME}" \
    --do_fetch_results FALSE \
    -c nextflow.params.config \
    -work-dir "${NF_WORKSPACE}" \
    -ansi-log false

# -----------
# Restart run
# -----------
nextflow run ../scripts/nf02.restart_exachem.nf \
    --remote_workspace_dir_basename "${REMOTE_WORKSPACE_DIR_BASENAME}" \
    -c nextflow.params.config \
    -work-dir "${NF_WORKSPACE}" \
    -ansi-log false
