NF_WORKSPACE="output.workspace.nf.$(date +%FT%T)"

nextflow run ../scripts/nf00.run_exachem.nf \
    -C nextflow.config.nf \
    -work-dir "${NF_WORKSPACE}" \
    -ansi-log false
