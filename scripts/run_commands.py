"""Start Jupyter Lab servers on the AWS instances."""

import subprocess
import sys

import boto3


# Define parameters.
KEYPAIR_PATH = '/home/romano/.ssh/id_rsa'

# Connect to EC2.
ec2 = boto3.client('ec2', region_name='us-east-1')

# Get the group name from the commandline.
groupname = sys.argv[1]

# Get the instances with the ws_group tag set to the given group name.
filt = {'Name': 'tag:ws_group', 'Values': [groupname]}
resp = ec2.describe_instances(Filters=[filt])

# Get the public IP addresses for each running instance.
instance_ips = []
for res in resp['Reservations']:
    for inst in res['Instances']:
        # Ignore instances that are not running.
        if inst['State']['Code'] != 16:
            continue

        instance_ips.append(inst['PublicIpAddress'])

# SSH into each instance and start the server.
for inst_ip in instance_ips:
    print(inst_ip)
    args = ['ssh', '-A', '-o', 'UserKnownHostsFile=/dev/null', '-o',
            'StrictHostKeyChecking=no', '-i', KEYPAIR_PATH,
            f'ubuntu@{inst_ip}', 'bash -i -l']
    ssh_process = subprocess.Popen(args, stdin=subprocess.PIPE,
        universal_newlines=True, bufsize=0)
    ssh_process.stdin.write('cd ~/openmc-nea-course/\n')
    ssh_process.stdin.write('git pull\n')
    ssh_process.stdin.write('exit\n')
    ssh_process.stdin.close()
    ssh_process.wait()
    if ssh_process.returncode < 0:
        raise subprocess.CalledProcessError
    print()
