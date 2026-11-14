"""Start Jupyter Lab servers on the AWS instances."""

import subprocess
import sys

import boto3


# Define parameters.
KEYPAIR_PATH = '/home/romano/.ssh/id_rsa'

# Connect to EC2.
ec2 = boto3.client('ec2')

# Get the group name from the commandline.
groupname = sys.argv[1]
filename = sys.argv[2]

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
    subprocess.run(['scp', '-o', 'StrictHostKeyChecking=no', filename, f'ubuntu@{inst_ip}:'])
