NF_WORKSPACE="output.workspace.nf.$(date +%FT%T)"
REMOTE_WORKSPACE_DIR_BASENAME="output.workspace.remote.$(date +%FT%T)"

# Same as run02, but every run is stored in one shared results database.
# Point EXACHEM_DB at the file to use; the default is ./exachem_results.db.
# Params are passed in the --name=value form on purpose (see nf01.run_exachem.nextflow_slurm.nf).
RESULTS_DB="${EXACHEM_DB:-exachem_results.db}"

nextflow run ../scripts/nf01.run_exachem.nextflow_slurm.nf \
    --remote_workspace_dir_basename "${REMOTE_WORKSPACE_DIR_BASENAME}" \
    --do_fetch_results=true \
    --do_publish_results=true \
    --results_db="${RESULTS_DB}" \
    -c nextflow.params.config \
    -work-dir "${NF_WORKSPACE}" \
    -ansi-log false

echo
echo "Stored runs (${RESULTS_DB}):"
python3 ../tools/exachem_db.py --db "${RESULTS_DB}" list --limit 10
