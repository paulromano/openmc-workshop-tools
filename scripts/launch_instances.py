"""Launch AWS instances that will host the Jupyter Lab notebooks.

Each instance receives ws_group and a unique ws_hostname tag during launch.
Names follow the existing group0, group1, etc. convention, skipping names
already assigned in the group. Run only one launcher per group at a time.
"""

import argparse
import uuid

import boto3

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
    args = parser.parse_args(argv)
    if args.n_instances <= 0:
        parser.error('n_instances must be positive')

    ec2 = boto3.client('ec2', region_name='us-east-1')
    existing = get_group_instances(ec2, args.groupname)
    hostnames = unused_hostnames(args.groupname, existing)

    for _ in range(args.n_instances):
        hostname = next(hostnames)
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
        print(f"{hostname}: {instance['InstanceId']} "
              f"{instance.get('PrivateIpAddress', '(private IP pending)')}", flush=True)

    print('Hostnames assigned. Allow time for the instances and Jupyter to finish starting.')


if __name__ == '__main__':
    main()
