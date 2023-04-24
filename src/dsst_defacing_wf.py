import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

import deface
import generate_mappings


def get_args():
    parser = argparse.ArgumentParser(
        description='Deface anatomical scans for a given BIDS dataset or a subject directory in BIDS format.')

    parser.add_argument('--input', '-i', action='store', type=Path, required=True, dest='input',
                        help='Path to input BIDS dataset.')

    parser.add_argument('--output', '-o', action='store', type=Path, required=True, dest='output',
                        help='Path to output BIDS dataset with defaced scan.')

    parser.add_argument('--participant-id', '-p', dest='subj_id', action='store', required=False, default=None,
                        help="Subject ID associated with the participant. Since the input dataset is assumed to be \
                        BIDS valid, this argument expects subject IDs with 'sub-' prefix.")
    # TODO Test the session id argument
    parser.add_argument('--session-id', '-s', dest='sess_id', action='store', required=False, default=None,
                        help="Session ID associated with the subject ID. If the BIDS input dataset contains sessions, \
                        then this argument expects session IDs with 'ses-' prefix.")
    parser.add_argument('--swarm', dest='swarm', action='store_true', default=False,
                        help='If this argument is provide, the script begins a swarm job that runs the pipeline on every subject in the dataset in parallel.')
    parser.add_argument('--no-clean', dest='no_clean', action='store_true', default=False,
                        help='If this argument is provided, then AFNI intermediate files are preserved.')

    args = parser.parse_args()
    # Arguments related checks
    if not args.subj_id and args.sess_id:  # Invalid: subjid not provided but sessid provided
        print("Session ID provided without a subject ID. Invalid Argument.")
        raise ValueError

    return args.input.resolve(), args.output.resolve(), args.subj_id, args.sess_id, args.swarm, args.no_clean


def run_command(cmdstr):
    p = subprocess.Popen(cmdstr, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
    while True:
        line = p.stdout.readline()
        line = str(line, 'utf-8')[:-1]
        print(line)
        if line == '' and p.poll() != None:
            break
    if p.returncode != 0:
        raise Exception("Non zero return code: %d" % p.returncode)
    # subprocess.run(cmdstr, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf8', shell=True)


def write_to_file(file_content, filepath):
    ext = filepath.split('.')[-1]
    with open(filepath, 'w') as f:
        if ext == 'json':
            json.dump(file_content, f, indent=4)
        else:
            f.writelines(file_content)


def get_sess_dirs(subj_dir_path, mapping_dict):
    sess_dirs = [subj_dir_path / key if key.startswith('ses-') else "" for key in
                 mapping_dict[subj_dir_path.name].keys()]
    return sess_dirs


def start_swarm_job(input_dir, output_dir, no_clean):
    cmd_list = []
    for subj_dir in input_dir.glob('sub-*'):
        subj_id = subj_dir.name
        script_path = os.path.realpath(__file__)
        if no_clean:
            cmd_list.append(
                f"python {script_path} -i {input_dir} -o {output_dir} -p {subj_id} --no-clean")
        else:
            cmd_list.append(
                f"python {script_path} -i {input_dir} -o {output_dir} -p {subj_id}")

    output_dir.mkdir(parents=True, exist_ok=True)
    swarm_file = output_dir / 'defacing_subject_level.swarm'
    with open(swarm_file, 'w') as f:
        f.write('\n'.join(cmd_list))

    if swarm_file.exists():
        if output_dir.joinpath('swarm_logs').exists():
            shutil.rmtree(output_dir.joinpath('swarm_logs'))
        run_command(f"swarm -f {swarm_file} --merge-output --logdir {output_dir}/swarm_logs")
    else:
        raise FileNotFoundError


def main():
    # get command line arguments
    input_dir, output, subj_id, sess_id, swarm_flag, no_clean = get_args()

    if swarm_flag:
        start_swarm_job(input_dir, output, no_clean)

    else:
        # run generate mapping script
        mapping_dict = generate_mappings.crawl(input_dir, output)

        # create a separate bids tree with only defaced scans
        bids_defaced_outdir = output / 'bids_defaced'
        bids_defaced_outdir.mkdir(parents=True, exist_ok=True)

        afni_refacer_failures = []  # list to capture afni_refacer_run failures

        if subj_id and not sess_id:  # parallel execution at subject level
            subj_dir = input_dir / subj_id
            subj_sess_list = [(subj_dir, sess_dir) for sess_dir in get_sess_dirs(subj_dir, mapping_dict)]

        elif subj_id and sess_id:  # parallel execution at session level
            subj_dir = input_dir / subj_id
            subj_sess_list = [(subj_dir, subj_dir / sess_id)]

        else:  # neither subjid nor sessid given; running pipeline serially
            subj_sess_list = []
            for subj_dir in list(input_dir.glob('sub-*')):
                subj_sess_list.extend([(subj_dir, sess_dir) for sess_dir in get_sess_dirs(subj_dir, mapping_dict)])

        # calling deface.py script
        for subj_sess in subj_sess_list:
            missing_refacer_out = deface.deface_primary_scan(input_dir, subj_sess[0], subj_sess[1], mapping_dict,
                                                             bids_defaced_outdir, no_clean)
            if missing_refacer_out is not None:
                afni_refacer_failures.extend(missing_refacer_out)

        with open(output / 'logs' / 'failed_afni_refacer_output.txt', 'w') as f:
            f.write('\n'.join(afni_refacer_failures))  # TODO Not very useful when running the pipeline in parallel


if __name__ == "__main__":
    main()
