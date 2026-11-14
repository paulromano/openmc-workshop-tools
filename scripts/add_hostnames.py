"""Add ws_hostname tags to Jupyter instances."""

import sys

import boto3

from utils import get_aws_tag, get_group_instances, unused_hostnames


# Connect to EC2.
ec2 = boto3.client('ec2', region_name='us-east-1')

# Get the group name from the commandline.
groupname = sys.argv[1]

instances = get_group_instances(ec2, groupname)
hostnames = unused_hostnames(groupname, instances)

# Assign hostnames to the instances that need them.
for instance in instances:
    if get_aws_tag(instance.get('Tags', []), 'ws_hostname') is not None:
        continue
    hostname = next(hostnames)

    resp = ec2.create_tags(
        Resources=(instance['InstanceId'], ),
        Tags=[{'Key': 'ws_hostname', 'Value': hostname}])
    print(resp)
    print()
