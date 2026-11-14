"""Start Jupyter Lab servers on the AWS instances."""

import subprocess
import sys

from prettytable import PrettyTable
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
table = PrettyTable()
table.field_names = ['Hostname', 'DnsName', 'PrivateIp', 'InstanceId', 'InstanceType']
for res in resp['Reservations']:
    for inst in res['Instances']:
        # Ignore instances that are not running.
        if inst['State']['Code'] != 16:
            continue

        for tag in inst['Tags']:
            if tag['Key'] == 'ws_hostname':
                hostname = tag['Value']
                break
        else:
            hostname = 'None'

        table.add_row((
            hostname,
            inst['PublicDnsName'],
            inst['PrivateIpAddress'],
            inst['InstanceId'],
            inst['InstanceType']
        ))

print(table)
