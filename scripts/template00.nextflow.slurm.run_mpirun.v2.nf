params.input = ""
params.np = 2
params.tamm_install_path = ""
params.account = ""

// ----------
// Utilities
// ----------

def get_basisset_name(json_file) {
    new groovy.json.JsonSlurper().parseText(json_file.text)
        ?.basis
        ?.basisset
}

def get_file_prefix(json_file) {
    new groovy.json.JsonSlurper().parseText(json_file.text)
        ?.common
        ?.file_prefix
}

process submit_slurm_mpirun {
    executor 'slurm'
    queue 'slurm'
    time '1h'
    clusterOptions "\
        --job-name='exachem' \
        --account=${params.account} \
        -N 1 \
        --output=output.%x.%j.out.log \
        --error=output.%x.%j.err.log \
        --mail-type=FAIL \
        --mail-user=zhen.peng@pnnl.gov \
        --exclusive"
    beforeScript '\
        source /etc/profile.d/modules.sh; \
        module purge; \
        module load gcc/14.2.0 openmpi/5.0.7 cmake intel/2025.2.0 java/24.0.2; \
        ulimit -s unlimited; \
        command -v python; \
        python --version;'

    input:
    val basisset

    output:
    val true, emit: is_successful

    script:
    """
    ###############
    # Run the task
    ###############
    export TAMM_INSTALL_PATH=${params.tamm_install_path}
    mpirun -n ${params.np} "${params.tamm_install_path}/bin/ExaChem" "${params.input}" | tee output.\${SLURM_JOB_NAME}.\${SLURM_JOB_ID}.pure_out.log

    ################################
    # Prepare output after the task
    ################################
    # Copy printout
    cp output.*.out.log output.*.err.log output.*.pure_out.log "${launchDir}/"
    echo "Copied output.*.out.log output.*.err.log output.*.pure_out.log to ${launchDir}/ ."

    # Get output directory name
    input_basename=\$(basename "${params.input}")
    input_name="\${input_basename%.*}"
    output_dir_remote="\${input_name}.${basisset}_files"

    cp -r "\${output_dir_remote}" "${launchDir}/"

    echo "Copied \${output_dir_remote} to ${launchDir}/ ."

    restricted=""
    if [ -d "\${output_dir_remote}/restricted" ]; then
        restricted="restricted"
    elif [ -d "\${output_dir_remote}/unrestricted" ]; then
        restricted="unrestricted"
    else
        echo "Error: Cannot find 'restricted' or 'unrestricted' directory in output." >&2
        exit 1
    fi

    # Copy json output directory
    output_json_dir="\${output_dir_remote}/\${restricted}/json"
    set -x
    rm -rf "${launchDir}/json" || true
    cp -r "\${output_json_dir}" "${launchDir}/"
    set +x
    echo "Copied \${output_json_dir} to ${launchDir}/ ."
    """
}


process sanity_check {
    script:
    """
    echo "params.input = ${params.input}"
    echo "params.np = ${params.np}"
    echo "params.tamm_install_path = ${params.tamm_install_path}"
    echo "params.account = ${params.account}"

    echo "pwd: \$(pwd)"
    if [ -e "${params.input}" ]; then
        echo "${params.input} exists."
    else
        echo "${params.input} does NOT exist!"
    fi
    """
}

workflow {
    // sanity_check()

    // Read the basisset value
    basisset_name = channel.fromPath(params.input)
                           .map { f -> get_basisset_name(f) }
                           .first()
    // Submit the slurm job
    submit_slurm_mpirun(basisset_name)
}