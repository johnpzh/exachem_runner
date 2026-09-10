
// Command-line values such as `--do_fetch_results false` arrive as the String "false", which
// Groovy treats as true; accept booleans and true/false strings alike
def as_bool(value) {
    return value.toString().trim().equalsIgnoreCase("true")
}

// ----------
// Processes
// ----------

process get_remote_workspace_dir {
    output:
    env "remote_workspace_dir", emit: remote_workspace_dir
    val true, emit: is_successful

    script:
    """
    remote_pwd=\$(ssh -o StrictHostKeyChecking=no ${params.remote_host} 'pwd')
    remote_workspace_dir="\${remote_pwd}/${params.remote_workspace_dir_basename}"
    """
}

process upload_restart_input {
    input:
    val remote_workspace_dir

    output:
    val true, emit: is_successful

    script:
    """
    file_string="${params.restart_input} ${params.nextflow_restart_template_file}"
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
    env "submit_rc", emit: submit_rc

    script:
    """
    input_basename=\$(basename "${params.restart_input}")
    template_basename=\$(basename "${params.nextflow_restart_template_file}")
    # Params must use the --name=value form: with the space-separated form, an empty
    # value (e.g. --slurm_partition "") makes Nextflow set the param to 'true' instead
    submit_cmd="cd ${remote_workspace_dir} && \
                nextflow run \${template_basename} \
                    -work-dir \"output.workspace.nf.submit_slurm.\$(date +%FT%T)\" \
                    -ansi-log false \
                    --input=\"${remote_workspace_dir}/\${input_basename}\" \
                    --nodes=${params.nodes} \
                    --np=${params.np} \
                    --tamm_install_path=\"${params.remote_tamm_install_path}\" \
                    --account=\"${params.account}\" \
                    --mail_user=\"${params.mail_user}\" \
                    --slurm_partition=\"${params.slurm_partition}\" \
                    --slurm_qos=\"${params.slurm_qos}\" \
                    --slurm_constraint=\"${params.slurm_constraint}\" \
                    --slurm_job_time_limit=\"${params.slurm_job_time_limit}\""
    # Do not fail this process when the remote run fails: capture the exit status instead, so that
    # the logs of a failed run are still fetched and stored in the results database
    ssh -o StrictHostKeyChecking=no "${params.remote_host}" "\${submit_cmd}" && submit_rc=0 || submit_rc=\$?
    echo "Remote run finished with exit status \${submit_rc}"
    """
}

process fetch_remote_results {
    input:
    val remote_workspace_dir
    val submit_rc

    output:
    val true, emit: is_successful

    script:
    """
    #!/bin/bash
    dir_name=\$(basename "${remote_workspace_dir}")
    mkdir -p "\${dir_name}"

    # The logs exist for every run, finished or not, so failing to fetch them is an error
    set -e
    set -x
    scp -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/output.*.err.log" "./\${dir_name}"
    scp -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/output.*.out.log" "./\${dir_name}"
    scp -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/output.*.pure_out.log" "./\${dir_name}"
    set +x

    # A run that did not finish leaves no json/ directory; fetch it only when it exists
    if ssh -o StrictHostKeyChecking=no ${params.remote_host} "test -d '${remote_workspace_dir}/json'"; then
        scp -r -o StrictHostKeyChecking=no ${params.remote_host}:"${remote_workspace_dir}/json" "./\${dir_name}/"
        echo "Fetched remote printout files output.*.err.log, output.*.out.log, output.*.pure_out.log, and json/ to local directory ./\${dir_name}/ ."
    else
        echo "Warning: no json/ directory in remote ${remote_workspace_dir} (the run did not finish); fetched the logs only."
    fi

    set -x
    if [ -d "${launchDir}/\${dir_name}" ]; then
        rm -rf "${launchDir}/\${dir_name}"
        echo "Warning: \"${launchDir}/\${dir_name}\" already exists. Removed it first."
    fi
    cp -r "\${dir_name}" "${launchDir}/"
    set +x
    echo "Copied local results \${dir_name}/ to ${launchDir}/ ."
    """

}

process publish_results {
    // Store the fetched run in the SQLite results database (tools/exachem_db.py), right after the
    // fetch, because a later restart overwrites the result JSON on disk. Runs on the local machine.
    input:
    val remote_workspace_dir
    val submit_rc
    val fetch_is_done

    output:
    val true, emit: is_successful

    script:
    // A relative results_db is meant relative to the launch directory (pg00_submit_job with the
    // sbatch/ scripts); the task itself runs in a work directory, so resolve it here
    def db_path = file(params.results_db).toAbsolutePath()
    """
    #!/bin/bash
    set -e
    dir_name=\$(basename "${remote_workspace_dir}")
    python3 "${params.exachem_db_tool}" --db "${db_path}" push --cluster "${params.remote_host}" --exit-code "${submit_rc}" "${launchDir}/\${dir_name}"
    """
}

// ---------
// Workflow
// ---------
workflow {
    /* -------------------------------------- */
    /* Step 1: get remote workspace directory */
    /* -------------------------------------- */
    get_remote_workspace_dir()
    remote_workspace_dir = get_remote_workspace_dir.out.remote_workspace_dir

    /* ------------------------------------------------------*/
    /* Step 2: upload restart input file to remote workspace */
    /* ------------------------------------------------------*/
    upload_restart_input(remote_workspace_dir)

    /* -------------------------*/
    /* Step 3: submit Slurm job */
    /* -------------------------*/
    submit_slurm_job(remote_workspace_dir, upload_restart_input.out.is_successful)

    /* --------------------- */
    /* Step 4: fetch results */
    /* --------------------- */
    if (as_bool(params.do_fetch_results)) {
        fetch_remote_results(remote_workspace_dir, submit_slurm_job.out.submit_rc)

        /* --------------------------------------------- */
        /* Step 5: store the run in the results database */
        /* --------------------------------------------- */
        if (as_bool(params.do_publish_results) && params.results_db) {
            publish_results(remote_workspace_dir, submit_slurm_job.out.submit_rc,
                            fetch_remote_results.out.is_successful)
        }
    }
}