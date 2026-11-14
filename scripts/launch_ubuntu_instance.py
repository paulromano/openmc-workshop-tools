#!/usr/bin/env python3
"""Launch one Ubuntu 26.04 amd64 builder with a larger gp3 root volume.

Usage:
    python scripts/launch_ubuntu_instance.py
    python scripts/launch_ubuntu_instance.py nea-build --root-volume-gb 32
    python scripts/launch_ubuntu_instance.py --dry-run

The instance receives a ws_group tag for get_ip_addresses.py and
terminate_instances.py. Run the NEA setup script separately after connecting.
"""

import argparse
import uuid

import boto3
from botocore.exceptions import ClientError


REGION = 'us-east-1'
KEYPAIR_NAME = 'X1 Carbon 2022'
SECURITY_GROUP = 'sg-130bcb6b'
UBUNTU_PARAMETER = '/aws/service/canonical/ubuntu/server/26.04/stable/current/amd64/hvm/ebs-gp3/ami-id'
CANONICAL_OWNER = '099720109477'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('groupname', nargs='?', default='nea-build', help='ws_group tag (default: nea-build)')
    parser.add_argument('--root-volume-gb', type=int, default=64, help='Root volume size in GiB (default: 64)')
    parser.add_argument('--instance-type', default='c7i-flex.large')
    parser.add_argument('--region', default=REGION)
    parser.add_argument('--keypair-name', default=KEYPAIR_NAME)
    parser.add_argument('--security-group-id', default=SECURITY_GROUP)
    parser.add_argument('--subnet-id', help='Optional subnet in the security group\'s VPC')
    parser.add_argument('--dry-run', action='store_true', help='Check AWS launch permissions without creating an instance')
    args = parser.parse_args(argv)
    if args.root_volume_gb <= 0:
        parser.error('--root-volume-gb must be positive')

    ec2 = boto3.client('ec2', region_name=args.region)
    ssm = boto3.client('ssm', region_name=args.region)
    image_id = ssm.get_parameter(Name=UBUNTU_PARAMETER)['Parameter']['Value']
    image = ec2.describe_images(ImageIds=[image_id])['Images'][0]
    if (image['OwnerId'] != CANONICAL_OWNER or image['Architecture'] != 'x86_64'
            or image['RootDeviceType'] != 'ebs' or image['State'] != 'available'):
        raise ValueError('The Ubuntu parameter did not resolve to an available Canonical amd64 EBS image')
    root_device = image['RootDeviceName']
    root = next(m for m in image['BlockDeviceMappings'] if m['DeviceName'] == root_device)
    minimum_size = root['Ebs']['VolumeSize']
    if args.root_volume_gb < minimum_size:
        parser.error(f'This AMI requires a root volume of at least {minimum_size} GiB')

    request = {
        'ImageId': image_id,
        'MinCount': 1,
        'MaxCount': 1,
        'InstanceType': args.instance_type,
        'KeyName': args.keypair_name,
        'SecurityGroupIds': [args.security_group_id],
        'BlockDeviceMappings': [{
            'DeviceName': root_device,
            'Ebs': {'VolumeSize': args.root_volume_gb, 'VolumeType': 'gp3', 'DeleteOnTermination': True},
        }],
        'TagSpecifications': [{
            'ResourceType': 'instance',
            'Tags': [{'Key': 'Name', 'Value': args.groupname}, {'Key': 'ws_group', 'Value': args.groupname}],
        }],
        # Reuse this token if the SDK retries a launch request.
        'ClientToken': str(uuid.uuid4()),
        'DryRun': args.dry_run,
    }
    if args.subnet_id:
        request['SubnetId'] = args.subnet_id
    print(f"AMI: {image_id} ({image['Name']})", flush=True)
    print(f'Region: {args.region}; type: {args.instance_type}; root: {args.root_volume_gb} GiB gp3', flush=True)
    try:
        response = ec2.run_instances(**request)
    except ClientError as exc:
        if args.dry_run and exc.response['Error']['Code'] == 'DryRunOperation':
            print('Dry run succeeded. No instance was created.')
            return
        raise

    instance_id = response['Instances'][0]['InstanceId']
    print(f'Instance ID: {instance_id}', flush=True)
    print('Waiting for the instance to enter the running state...', flush=True)
    ec2.get_waiter('instance_running').wait(InstanceIds=[instance_id])
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instance = response['Reservations'][0]['Instances'][0]
    print(f"Public DNS: {instance.get('PublicDnsName') or '(none)'}")
    print(f"Public IP:  {instance.get('PublicIpAddress') or '(none)'}")
    print(f"Private IP: {instance.get('PrivateIpAddress') or '(none)'}")
    host = instance.get('PublicDnsName') or instance.get('PublicIpAddress')
    if host:
        print(f'Connect: ssh -A ubuntu@{host}')
        print('SSH and cloud-init may need another minute to become ready.')
    else:
        print('No public address was assigned; connect through your VPC or use a public subnet.')


if __name__ == '__main__':
    main()
