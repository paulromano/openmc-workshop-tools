#!/usr/bin/env python3
"""Create a course AMI from one running or stopped EBS-backed instance.

Run locally after completing the builder checks and disk cleanup:
    python scripts/create_ami.py --name "OpenMC NEA Course 2026-11 v1" --dry-run
    python scripts/create_ami.py --name "OpenMC NEA Course 2026-11 v1"

By default, select the single instance with ws_group=nea-build in us-east-1.
If the builder is stopping, wait for it to stop before creating the image.
Running instances reboot during image creation unless --no-reboot is supplied.
This script does not perform the cleanup in step 5 of the build guide.
"""

import argparse
import sys

import boto3
from botocore.exceptions import ClientError, WaiterError


REGION = 'us-east-1'
DEFAULT_TAGS = {
    'UbuntuVersion': '26.04',
    'PythonVersion': '3.14',
    'OpenMCVersion': '0.16.0',
    'CourseBranch': 'inperson-2026-11',
}


def parse_tag(value):
    key, separator, value = value.partition('=')
    if not separator or not key:
        raise argparse.ArgumentTypeError('Tags must have the form KEY=VALUE with a nonempty key')
    return key, value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--group', help='Select by ws_group tag (default: nea-build)')
    source.add_argument('--instance-id', help='Select an instance directly instead of by group')
    parser.add_argument('--name', required=True, help='Unique AMI name within this account and region')
    parser.add_argument('--description', help='Optional AMI description; defaults to the course versions')
    parser.add_argument('--region', default=REGION)
    parser.add_argument('--tag', action='append', type=parse_tag, default=[], metavar='KEY=VALUE',
                        help='Add a tag or override a default version/branch tag; may be repeated')
    parser.add_argument('--dry-run', action='store_true', help='Check AWS permissions without creating an image')
    parser.add_argument('--no-wait', action='store_true', help='Return the AMI ID without waiting for availability')
    parser.add_argument('--no-reboot', action='store_true',
                        help='Skip the normal reboot; snapshots may omit buffered writes on a running instance')
    args = parser.parse_args(argv)

    ec2 = boto3.client('ec2', region_name=args.region)
    if args.instance_id:
        query = {'InstanceIds': [args.instance_id]}
        selection = f'instance {args.instance_id}'
    else:
        selection = f'ws_group={args.group or "nea-build"}'
        query = {'Filters': [
            {'Name': 'tag:ws_group', 'Values': [args.group or 'nea-build']},
            {'Name': 'instance-state-name', 'Values': ['running', 'stopping', 'stopped']},
        ]}
    instances = [instance
                 for page in ec2.get_paginator('describe_instances').paginate(**query)
                 for reservation in page['Reservations']
                 for instance in reservation['Instances']]
    if len(instances) != 1:
        ids = ', '.join(instance['InstanceId'] for instance in instances) or 'none'
        parser.error(f'Expected exactly one builder instance for {selection} in {args.region}; '
                     f'found {len(instances)} ({ids}). '
                     'Use --instance-id to select it explicitly.')
    instance = instances[0]
    instance_id = instance['InstanceId']
    state = instance['State']['Name']
    if state not in {'running', 'stopping', 'stopped'} or instance['RootDeviceType'] != 'ebs':
        parser.error('The builder must be running, stopping, or stopped and have an EBS root volume')
    if state == 'stopping':
        print(f'Builder {instance_id} is stopping; waiting up to 10 minutes for shutdown...', flush=True)
        ec2.get_waiter('instance_stopped').wait(
            InstanceIds=[instance_id], WaiterConfig={'Delay': 15, 'MaxAttempts': 40})
        state = 'stopped'

    tags = {**DEFAULT_TAGS, **dict(args.tag), 'Name': args.name, 'SourceInstanceId': instance_id}
    description = args.description or (
        f"NEA OpenMC course: Ubuntu {tags['UbuntuVersion']}, Python {tags['PythonVersion']}, "
        f"OpenMC {tags['OpenMCVersion']}, branch {tags['CourseBranch']}")
    request = {
        'InstanceId': instance_id,
        'Name': args.name,
        'Description': description,
        'NoReboot': args.no_reboot,
        'DryRun': args.dry_run,
        'TagSpecifications': [
            {'ResourceType': resource, 'Tags': [{'Key': k, 'Value': v} for k, v in tags.items()]}
            for resource in ['image', 'snapshot']
        ],
    }
    print(f'Builder: {instance_id} ({state}); region: {args.region}', flush=True)
    print(f'AMI name: {args.name}', flush=True)
    if state == 'running':
        if args.no_reboot:
            print('Reboot disabled; snapshots may omit buffered writes.', flush=True)
        else:
            print('EC2 will reboot the builder during creation (except in a dry run).', flush=True)
    try:
        response = ec2.create_image(**request)
    except ClientError as exc:
        if args.dry_run and exc.response['Error']['Code'] == 'DryRunOperation':
            print('Dry run succeeded. No AMI was created and the builder was not rebooted.')
            return
        raise

    image_id = response['ImageId']
    print(f'AMI ID: {image_id}', flush=True)
    print(f'Console: https://{args.region}.console.aws.amazon.com/ec2/home?'
          f'region={args.region}#ImageDetails:ImageId={image_id}', flush=True)
    if args.no_wait:
        print('Image creation is in progress. Wait for Available before launching an instance.')
        return image_id

    print('Waiting up to 60 minutes for the AMI to become available...', flush=True)
    try:
        ec2.get_waiter('image_available').wait(
            ImageIds=[image_id], WaiterConfig={'Delay': 15, 'MaxAttempts': 240})
    except WaiterError:
        print(f'Creation was already requested for {image_id}. Check its state before retrying; '
              'ending this script does not cancel image creation.', file=sys.stderr)
        raise
    print(f'AMI available: {image_id}')
    return image_id


if __name__ == '__main__':
    try:
        main()
    except (ClientError, WaiterError) as exc:
        sys.exit(str(exc))
