NF_WORKSPACE="output.workspace.nf.$(date +%FT%T)"

nextflow run ../scripts/nf01.run_exachem.nextflow_slurm.nf \
    -c nextflow.config.nf \
    -work-dir "${NF_WORKSPACE}" \
    -ansi-log false
