"""Launch AWS instances that will host the Jupyter Lab notebooks.

Each instance receives ws_group and a unique ws_hostname tag during launch.
Names follow the existing group0, group1, etc. convention, skipping names
already assigned in the group. The launcher waits for the new instances to
run and updates the NGINX proxy. Run only one launcher per group at a time.
"""

import argparse
import subprocess
import sys
import uuid

import boto3
from botocore.exceptions import ClientError, WaiterError

from update_proxy import update_proxy, validate_group
from utils import get_group_instances, unused_hostnames


# Define parameters.
#IMAGE_ID = 'ami-0a7819cfcb08b9d71'  # PHYSOR 2022
#IMAGE_ID = 'ami-0479c7fcf536b157c' # NEA Course April 2022
#IMAGE_ID = 'ami-076dfd58ed6b8d3b3' # NEA Course October 2022
#IMAGE_ID = 'ami-0d81e430b6d1d6a17' # NEA Course April 2023
#IMAGE_ID = 'ami-0231bb4c70514bb2f' # Rochester tutorial May 2023
#IMAGE_ID = 'ami-0999bfd8c33cf3ffb' # UKAEA Comp School 1
#IMAGE_ID = 'ami-045fe42f53a8a575f' # UKAEA Comp School 2
#IMAGE_ID = 'ami-019b63f9e8a5ab1dc' # UKAEA Comp School 3
#IMAGE_ID = 'ami-0c44db409cfb0ae22' # SNA+MC 2024 Workshop
#IMAGE_ID = 'ami-00da9d8f9b3db91e4' # NEA Course November 2024
#IMAGE_ID = 'ami-00566067599a2d0fe' # M&C 2025 Workshop
#IMAGE_ID = 'ami-047baccd673ec48aa' # NEA Course 2025-06
#IMAGE_ID = 'ami-07cc3b03befe448b9' # ANFM July 2025
#IMAGE_ID = 'ami-045e275aa4922cd98' # ANFM July 2025 (us-east-1)
#IMAGE_ID = 'ami-0e4a0355118ee4d3b' # NEA Course 2025-11
#IMAGE_ID = 'ami-0c5753a97399407e3' # NEA Course 2026-05
IMAGE_ID = 'ami-0832e5041637c8074' # NEA Course 2026-11

KEYPAIR_NAME = 'X1 Carbon 2022'
SECURITY_GROUP = 'sg-130bcb6b'

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('groupname')
    parser.add_argument('n_instances', type=int)
    parser.add_argument('--skip-proxy', action='store_true',
                        help='Launch and tag instances without waiting or updating the proxy')
    args = parser.parse_args(argv)
    if args.n_instances <= 0:
        parser.error('n_instances must be positive')
    validate_group(args.groupname)

    ec2 = boto3.client('ec2', region_name='us-east-1')
    existing = get_group_instances(ec2, args.groupname)
    hostnames = unused_hostnames(args.groupname, existing)
    launched = {}

    for _ in range(args.n_instances):
        hostname = next(hostnames)
        if len(hostname) > 63:
            parser.error('The generated hostname exceeds the DNS limit of 63 characters')
        # A separate request gives each instance its own hostname at creation.
        response = ec2.run_instances(
            ImageId=IMAGE_ID,
            MinCount=1,
            MaxCount=1,
            InstanceType='c7i-flex.large',
            KeyName=KEYPAIR_NAME,
            SecurityGroupIds=[SECURITY_GROUP],
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [{'Key': 'ws_group', 'Value': args.groupname},
                         {'Key': 'ws_hostname', 'Value': hostname}],
            }],
            ClientToken=str(uuid.uuid4()),
        )
        instance = response['Instances'][0]
        launched[instance['InstanceId']] = hostname
        print(f"{hostname}: {instance['InstanceId']} "
              f"{instance.get('PrivateIpAddress', '(private IP pending)')}", flush=True)

    if args.skip_proxy:
        print('Hostnames assigned. Proxy update skipped.')
        return

    try:
        print('Waiting for the new instances to be running before updating the proxy...', flush=True)
        ec2.get_waiter('instance_running').wait(InstanceIds=list(launched))
        response = ec2.describe_instances(InstanceIds=list(launched))
        new_instances = [instance for reservation in response['Reservations']
                         for instance in reservation['Instances']]
        if {instance['InstanceId'] for instance in new_instances} != set(launched):
            raise RuntimeError('EC2 did not return all newly launched instances')
        for instance in new_instances:
            if instance['State']['Name'] != 'running' or not instance.get('PrivateIpAddress'):
                raise RuntimeError(f"Instance {instance['InstanceId']} is not running with a private IP")
            # These tags were attached at creation; use the known names even
            # if DescribeInstances has not yet returned the new tags.
            instance['Tags'] = [tag for tag in instance.get('Tags', []) if tag['Key'] != 'ws_hostname']
            instance['Tags'].append({'Key': 'ws_hostname', 'Value': launched[instance['InstanceId']]})
        current_group = get_group_instances(ec2, args.groupname)
        current_group = [instance for instance in current_group if instance['InstanceId'] not in launched]
        update_proxy(args.groupname, instances=current_group + new_instances)
    except (ClientError, WaiterError, subprocess.CalledProcessError, ValueError, RuntimeError, KeyError, OSError):
        print('The instances have already been launched. After resolving the error, retry only '
              f'the proxy update: python scripts/update_proxy.py {args.groupname}', file=sys.stderr)
        raise
    print('Launch and proxy update complete. Jupyter may need a little longer to finish starting.')


if __name__ == '__main__':
    main()
