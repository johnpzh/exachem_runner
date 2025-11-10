// -----------
// Parameters
// -----------
params.input = "/path/to/submodules/exachem/inputs/ozone.json"
params.account = "BR26_PENG599"
params.nodes = 1
params.np = 2
params.remote_host = "deception"
params.poll_interval = 10
params.slurm_template_file = "/path/to/scripts/sbatch00.run_exachem.sh"

// ----------
// Utilities
// ----------

def get_basisset_name(json_file) {
    new groovy.json.JsonSlurper().parseText(json_file.text)
        ?.basis
        ?.basisset
}

// ----------
// Processes
// ----------
process create_remote_workspace {
    output:
    env "remote_workspace_dir", emit: remote_workspace_dir
    val true, emit: is_successful

    script:
    """
    #!/bin/bash

    # Get remote workspace directory name
    remote_pwd=\$(ssh -o StrictHostKeyChecking=no ${params.remote_host} 'pwd')
    dir_name="output.workspace.\$(date +%FT%T)"
    remote_dir="\${remote_pwd}/\${dir_name}"

    # Create the remote directory
    ssh -o StrictHostKeyChecking=no ${params.remote_host} "mkdir -p \${remote_dir}"

    # Set the output
    remote_workspace_dir="\${remote_dir}"

    echo "Created remote workspace directory \${remote_workspace_dir} ."
    """
}

process copy_to_remote {
    input:
    val remote_workspace_dir

    output:
    val true, emit: is_successful

    script:
    """
    #!/bin/bash

    file_string="${params.input} ${params.slurm_template_file}"
    set -x
    scp -r -o StrictHostKeyChecking=no \${file_string} ${params.remote_host}:"${remote_workspace_dir}/"
    set +x

    echo 'Copied \${file_string} to remote ${params.remote_host}:"${remote_workspace_dir}"'
    """
}

process submit_slurm_job {
    input:
    val remote_workspace_dir
    val copy_is_done

    output:
    env "job_id", emit: job_id
    val true, emit: is_successful

    script:
    """
    #!/bin/bash

    input_basename=\$(basename "${params.input}")
    template_basename=\$(basename "${params.slurm_template_file}")
    submit_cmd="cd ${remote_workspace_dir} && \
                sbatch --nodes=${params.nodes} --account=${params.account} \
                \${template_basename} --input \${input_basename} --np ${params.np}"
    job_id=\$(ssh -o StrictHostKeyChecking=no ${params.remote_host} "\${submit_cmd}" | awk '{print \$NF}')

    if [[ ! "\${job_id}" =~ ^[0-9]+\$ ]]; then
        echo "Error: Job submission failed." >&2
        exit 1
    fi

    echo "Job \${job_id} submitted on remote."
    """
}


process monitor_slurm_job {
    input:
    val job_id

    output:
    val true, emit: is_successful

    script:
    """
    #!/bin/bash

    status_cmd="squeue -j ${job_id} -h -o %T"
    while true; do
        status=\$(ssh -o StrictHostKeyChecking=no ${params.remote_host} "\${status_cmd}")
        if [[ -z "\${status}" ]] || [[ "\${status}" != "RUNNING" && "\${status}" != "PENDING" ]]; then
            # Ref: https://slurm.schedmd.com/squeue.html
            break
        fi
        echo "Job ${job_id} still active. Retrying in ${params.poll_interval}s..."
        sleep ${params.poll_interval}
    done

    echo "Job ${job_id} completed."
    """
}


process fetch_remote_results {
    input:
    val remote_workspace_dir
    val job_id
    val basisset
    val monitor_is_done

    output:
    val true, emit: is_successful

    script:
    """
    #!/bin/bash
    dir_name=\$(basename "${remote_workspace_dir}")
    mkdir -p "\${dir_name}"

    # Fetch printout
    set -x
    scp -r -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/output.*.${job_id}.*.log" "./\${dir_name}"
    set +x

    echo "Fectched remote printout files output.*.${job_id}.*.log to local directory ./\${dir_name}/ ."

    # Get output directory name
    input_basename=\$(basename "${params.input}")
    input_name="\${input_basename%.*}"
    output_dir_remote="\${input_name}.${basisset}_files"

    # Check if it is "restricted" or "unrestricted"
    restricted=""
    if ssh -o StrictHostKeyChecking=no ${params.remote_host} "test -d ${remote_workspace_dir}/\${output_dir_remote}/restricted"; then
        restricted="restricted"
    elif ssh -o StrictHostKeyChecking=no ${params.remote_host} "test -d ${remote_workspace_dir}/\${output_dir_remote}/unrestricted"; then
        restricted="unrestricted"
    else
        echo "Error: Cannot find 'restricted' or 'unrestricted' directory in output." >&2
        exit 1
    fi

    # Fetch JSON output directory
    output_json_dir="${remote_workspace_dir}/\${output_dir_remote}/\${restricted}/json"
    set -x
    scp -r -o StrictHostKeyChecking=no ${params.remote_host}:"\${output_json_dir}" "./\${dir_name}/"
    set +x

    set -x
    cp -r "\${dir_name}" "${launchDir}/"
    set +x
    echo "Copied local results \${dir_name}/ to ${launchDir}/ ."
    """
}

// ---------
// Workflow
// ---------
workflow {
    /* -------------------------------------- */
    /* Step 1: get remote workspace directory */
    /* -------------------------------------- */
    create_remote_workspace()
    remote_workspace_dir = create_remote_workspace.out.remote_workspace_dir
    // remote_workspace_dir.view { item -> "Created remote workspace directory ${item} ." }

    /* ------------------------------------------------------*/
    /* Step 2: copy input file and sbatch template to remote */
    /* ------------------------------------------------------*/
    copy_to_remote(remote_workspace_dir)

    /* -------------------------*/
    /* Step 3: submit Slurm job */
    /* -------------------------*/
    submit_slurm_job(remote_workspace_dir, copy_to_remote.out.is_successful)
    job_id = submit_slurm_job.out.job_id

    /* ------------------------------------ */
    /* Step 4: monitor the slurm job status */
    /* ------------------------------------ */
    monitor_slurm_job(job_id)

    /* --------------------- */
    /* Step 5: fetch results */
    /* --------------------- */
    // Read the basisset value
    basisset_name = channel.fromPath(params.input)
                           .map { f -> get_basisset_name(f) }
                           .first()
    fetch_remote_results(remote_workspace_dir,
                         job_id,
                         basisset_name,
                         monitor_slurm_job.out.is_successful)
}