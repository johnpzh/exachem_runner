process run_mpirun {
    executor 'slurm'
    queue 'slurm'
    time '1h'
    clusterOptions '\
        --job-name="exachem" \
        --account=BR26_PENG599 \
        -N 1 \
        --output=output.%x.%j.out \
        --error=output.%x.%j.err \
        --mail-type=FAIL \
        --mail-user=zhen.peng@pnnl.gov \
        --exclusive'
    beforeScript '\
        source /etc/profile.d/modules.sh; \
        module purge; \
        module load gcc/14.2.0 java/24.0.2; \
        ulimit -s unlimited;'

    script:
    """
    echo "run_mpirun()"
    """
}

workflow {
    run_mpirun()
}