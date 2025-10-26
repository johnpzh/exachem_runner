import argparse
import subprocess
import time
import datetime
import json
import sys
import os

# Configuration
REMOTE_HOST = "deception"  # Replace. TODO: make it an command line argument
# REMOTE_DIR = "/path/to/remote/work/dir"
POLL_INTERVAL = 10  # Seconds

def run_ssh(cmd, remote_host):
    """Helper to run SSH command and return output."""
    ssh_options="-r -o StrictHostKeyChecking=no" # Ref: https://askubuntu.com/questions/87449/how-to-disable-strict-host-key-checking-in-ssh
    # full_cmd = ["ssh", REMOTE_HOST, cmd]
    full_cmd = f"ssh {ssh_options} {remote_host} {cmd}"
    result = subprocess.run(full_cmd, capture_output=True, text=True, check=True, shell=True)
    return result


def check_if_exists_on_remote(name, remote_host):
    """Check if a file exists on the remote host vis SSH"""
    cmd = f"test -e {name}"
    result = run_ssh(cmd, remote_host)
    return result.returncode == 0


def run_scp_to_remote(file_list: list, remote_host, remote_dir):
    """Run scp command to copy files from local to remote destination"""
    ssh_options="-r -o StrictHostKeyChecking=no" # Ref: https://askubuntu.com/questions/87449/how-to-disable-strict-host-key-checking-in-ssh
    file_string = " ".join(file_list)
    full_cmd = f"scp {ssh_options} {file_string} {remote_host}:{remote_dir}/"
    subprocess.run(full_cmd, check=True, shell=True)


def run_scp_to_local(remote_file_list: list, remote_host):
    """Run scp command to copy files from remote to local"""
    ssh_options="-r -o StrictHostKeyChecking=no" # Ref: https://askubuntu.com/questions/87449/how-to-disable-strict-host-key-checking-in-ssh
    for file in remote_file_list:
        full_cmd = f'scp {ssh_options} {remote_host}:"{file}" .'
        subprocess.run(full_cmd, check=True, shell=True)


def get_output_dir_name(input_file) -> str:
    """Get the output directory name based on input file name"""
    base_name = os.path.basename(input_file)
    name_wo_ext = os.path.splitext(base_name)[0]

    basisset = ""
    # Read the json file to get basis:basisset
    with open(input_file, 'r') as fin:
        data = json.load(fin)
        basisset = data['basis']['basisset']
    if not basisset:
        raise ValueError("Error: basisset not found in input json file.")

    output_dir_name = f"{name_wo_ext}.{basisset}_files/"
    return output_dir_name


def main():
    parser = argparse.ArgumentParser(description="Automate ExaChem Slurm workflow")
    parser.add_argument("--input", required=True, help="Input file path")
    parser.add_argument("--account", required=True, help="Slurm account")
    parser.add_argument("--nodes", type=int, default=1, help="Number of nodes")
    parser.add_argument("--np", type=int, default=2, help="Number of processors for mpirun (-np)")

    args = parser.parse_args()

    ###########################################
    # Step 0: Get remote workspace directory
    ###########################################
    cmd = "pwd"
    result = run_ssh(cmd=cmd, remote_host=REMOTE_HOST)
    dir_name = f'output.workspace.{datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}'
    remote_workspace_dir = f"{result.stdout.strip()}/{dir_name}"

    # Create remote workspace
    cmd = f"mkdir -p {remote_workspace_dir}"
    run_ssh(cmd=cmd, remote_host=REMOTE_HOST)
    print(f"Created remote workspace directory {remote_workspace_dir}.")

    ########################################################
    # Step 1: Copy input file and sbatch template to remote
    ########################################################
    template_file_name = "sbatch00.run_exachem.sh"
    sbatch_template_file = f"../scripts/{template_file_name}"
    file_list = [args.input, sbatch_template_file]
    run_scp_to_remote(file_list=file_list, remote_host=REMOTE_HOST, remote_dir=remote_workspace_dir)
    print(f"Copied {file_list} to remote host {REMOTE_HOST}.")

    ###########################
    # Step 2: Submit Slurm job
    ###########################
    submit_cmd = f"cd {remote_workspace_dir} && \
                   sbatch --nodes={args.nodes} --account={args.account} \
                     {template_file_name} --input {args.input} --np {args.np}"
    result = run_ssh(submit_cmd)
    job_id = result.stdout.split()[-1]  # Parse "Submitted batch job <ID>"
    if not job_id.isdigit():
        print("Error: Job submission failed.")
        sys.exit(1)
    print(f"Job {job_id} submitted. Monitoring...")

    ##############################
    # Step 3: Poll for completion
    ##############################
    while True:
        status_cmd = f"squeue -j {job_id} -h -o %T"
        result = run_ssh(status_cmd, capture_output=False)  # Ignore stderr for ended jobs
        status = result.stdout.strip()
        if (not status) or ("RUNNING" not in status) and ("PENDING" not in status):
            # Ref: https://slurm.schedmd.com/squeue.html
            break
        print(f"Job {job_id} still active. Retrying in {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)
    print(f"Job {job_id} completed.")

    ########################
    # Step 4: Fetch results
    ########################

    # Fetch printout
    printout_file = f'output.*.{job_id}.*.log'
    run_scp_to_local(remote_file_list=[printout_file], remote_host=REMOTE_HOST)
    output_dir_name = get_output_dir_name(args.input)
    print(f"Fectched printout file to local {printout_file}.")

    # Check if it is "restricted" or "unrestricted"
    restricted = ""
    if check_if_exists_on_remote(name=f"{remote_workspace_dir}/{output_dir_name}/restricted", remote_host=REMOTE_HOST):
        restricted = "restricted"
    elif check_if_exists_on_remote(name=f"{remote_workspace_dir}/{output_dir_name}/unrestricted", remote_host=REMOTE_HOST):
        restricted = "unrestricted"
    else:
        raise ValueError("Error: neither restricted or unrestricted is found under {remote_workspace_dir}/{output_dir_name}/")

    # Fetch json output directory
    output_json_dir = f"{remote_workspace_dir}/{output_dir_name}/{restricted}/json"
    run_scp_to_local(remote_file_list=[output_json_dir], remote_host=REMOTE_HOST)
    print(f"Fetched json output directory to local.")

    print("Workflow complete.")

if __name__ == "__main__":
    main()