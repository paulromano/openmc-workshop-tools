"""Launch AWS instances that will host the Jupyter Lab notebooks.

Note that this script will attach a "ws_group" tag to each instance and set it
to the given group name. The group name is purely a bookkeeping measure which
makes it easier to e.g handle machines for workshop hosts differently than
machines for participants.

"""

import sys

import boto3


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
IMAGE_ID = 'ami-0c5753a97399407e3' # NEA Course 2026-05

KEYPAIR_NAME = 'X1 Carbon 2022'
SECURITY_GROUP = 'sg-130bcb6b'

# Connect to EC2.
ec2 = boto3.client('ec2', region_name='us-east-1')

# Get the group name and number of instances from commandline args.
groupname = sys.argv[1]
n_instances = int(sys.argv[2])

# Launch the instances.
resp = ec2.run_instances(
    ImageId=IMAGE_ID,
    MinCount=n_instances,
    MaxCount=n_instances,
    InstanceType='c7i-flex.large',
    KeyName=KEYPAIR_NAME,
    SecurityGroupIds=(SECURITY_GROUP, ),
    TagSpecifications=[{
      'ResourceType': 'instance',
      'Tags': [{'Key': 'ws_group', 'Value': groupname}]
    }],
    )

print(resp)
print()

for inst in resp['Instances']:
    print(inst['InstanceId'])
    print(inst['PrivateIpAddress'])
    print()
