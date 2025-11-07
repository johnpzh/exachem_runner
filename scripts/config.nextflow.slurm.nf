process run_mpirun {
    executor 'slurm'
    queue 'slurm'
    time '1h'
    clusterOptions '\
        --job-name="1kgenome" \
        --account=datamesh \
        -N 1 \
        --output=R.%x.%j.out \
        --error=R.%x.%j.err \
        --mail-type=FAIL \
        --mail-user=zhen.peng@pnnl.gov \
        --exclusive'
    beforeScript '\
        source /etc/profile.d/modules.sh; \
        module purge; \
        module load gcc/11.2.0 binutils/2.35 cmake/3.29.0 java/23.0.1; \
        ulimit -s unlimited;'

    script:
    """
    echo "run_mpirun()"
    """
}

workflow {
    run_mpirun()
}