// -----------
// Parameters
// -----------
params.input = "/path/to/submodules/exachem/inputs/ozone.json"
params.account = "BR26_PENG599"
params.np = 2
params.remote_host = "deception"
params.nextflow_slurm_template_file = "/path/to/scripts/template00.nextflow.slurm.run_mpirun.nf"
params.remote_workspace_dir_basename = ""
params.do_fetch_results = true

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
    remote_dir="\${remote_pwd}/${params.remote_workspace_dir_basename}"

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

    file_string="${params.input} ${params.nextflow_slurm_template_file}"
    set -x
    scp -r -o StrictHostKeyChecking=no \${file_string} ${params.remote_host}:"${remote_workspace_dir}/"
    set +x

    echo "Copied \${file_string} to remote ${params.remote_host}:\"${remote_workspace_dir}\""
    """
}

process submit_slurm_job {
    input:
    val remote_workspace_dir
    val copy_is_done

    output:
    val true, emit: is_successful

    script:
    """
    #!/bin/bash

    input_basename=\$(basename "${params.input}")
    template_basename=\$(basename "${params.nextflow_slurm_template_file}")
    submit_cmd="cd ${remote_workspace_dir} && \
                nextflow run \${template_basename} \
                    -work-dir \"output.workspace.nf.submit_slurm.\$(date +%FT%T)\" \
                    -ansi-log false \
                    --input \"${remote_workspace_dir}/\${input_basename}\" \
                    --np ${params.np} \
                    --tamm_install_path \"/qfs/people/peng599/local/install/tamm\" \
                    --account BR26_PENG599"
    ssh -o StrictHostKeyChecking=no "${params.remote_host}" "\${submit_cmd}"

    """
}


process fetch_remote_results {
    input:
    val remote_workspace_dir
    val submit_slurm_is_done

    output:
    val true, emit: is_successful

    script:
    """
    #!/bin/bash
    dir_name=\$(basename "${remote_workspace_dir}")
    mkdir -p "\${dir_name}"

    # Fetch printout
    set -x
    scp -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/output.*.err.log" "./\${dir_name}"
    scp -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/output.*.out.log" "./\${dir_name}"
    scp -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/output.*.pure_out.log" "./\${dir_name}"
    scp -r -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/json" "./\${dir_name}/"
    set +x

    echo "Fectched remote printout files output.*.err.log, output.*.out.log, output.*.pure_out.log, and json/ to local directory ./\${dir_name}/ ."

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

    /// When using Nextflow to submit Slurm job, no need to monitor the status, because it only returns after the job finished.
    // /* ------------------------------------ */
    // /* Step 3: monitor the slurm job status */
    // /* ------------------------------------ */
    // monitor_slurm_job(job_id)

    /* --------------------- */
    /* Step 4: fetch results */
    /* --------------------- */
    // // Read the basisset value
    // basisset_name = channel.fromPath(params.input)
    //                        .map { f -> get_basisset_name(f) }
    //                        .first()
    if (params.do_fetch_results) {
        fetch_remote_results(remote_workspace_dir,
                             submit_slurm_job.out.is_successful)
    }
}