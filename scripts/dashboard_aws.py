"""EC2 queries and operations used by the workshop dashboard."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from update_proxy import validate_group
from utils import get_aws_tag


REGION = 'us-east-1'
LIVE_STATES = ['pending', 'running', 'stopping', 'stopped', 'shutting-down']
TERMINATABLE_STATES = {'pending', 'running', 'stopping', 'stopped'}


@dataclass(frozen=True)
class WorkshopInstance:
    instance_id: str
    group: str
    hostname: str
    state: str
    public_dns: str
    private_ip: str
    instance_type: str
    system_check: str = 'unknown'
    instance_check: str = 'unknown'


@dataclass(frozen=True)
class FleetSnapshot:
    instances: tuple[WorkshopInstance, ...]
    health_error: str = ''


@dataclass(frozen=True)
class LaunchRequest:
    group: str
    count: int
    skip_proxy: bool = False

    def validate(self):
        validate_group(self.group)
        if self.count <= 0:
            raise ValueError('The instance count must be positive.')
        if len(self.group) + len(str(self.count - 1)) > 63:
            raise ValueError('Use a shorter group name so hostnames fit within 63 characters.')


def hostname_order(instance):
    """Keep numbered hostnames in order, such as student2 before student10."""
    return (instance.group, tuple(int(part) if part.isdigit() else part.casefold()
                                 for part in re.split(r'(\d+)', instance.hostname)),
            instance.instance_id)


class EC2Service:
    def __init__(self, ec2=None):
        self._ec2 = ec2

    @property
    def ec2(self):
        # Create the client in the refresh worker, since credential lookup can
        # involve network requests. The client is then shared between workers.
        if self._ec2 is None:
            self._ec2 = boto3.client('ec2', region_name=REGION, config=Config(
                connect_timeout=5, read_timeout=10,
                retries={'mode': 'standard', 'total_max_attempts': 2},
            ))
        return self._ec2

    def snapshot(self):
        """Read course instances and their health in batches, with pagination."""
        instances = [instance
                     for page in self.ec2.get_paginator('describe_instances').paginate(
                         Filters=[{'Name': 'tag-key', 'Values': ['ws_group']},
                                  {'Name': 'instance-state-name', 'Values': LIVE_STATES}])
                     for reservation in page['Reservations']
                     for instance in reservation['Instances']]
        running = [instance['InstanceId'] for instance in instances
                   if instance['State']['Name'] == 'running']
        statuses = {}
        health_error = ''
        try:
            # EC2 permits at most 100 explicit instance IDs per status request.
            # Never send an empty list, which would query the whole account.
            for start in range(0, len(running), 100):
                for page in self.ec2.get_paginator('describe_instance_status').paginate(
                        InstanceIds=running[start:start + 100], IncludeAllInstances=True):
                    statuses.update((status['InstanceId'], status)
                                    for status in page['InstanceStatuses'])
        except (BotoCoreError, ClientError) as exc:
            health_error = str(exc)

        result = []
        for instance in instances:
            instance_id = instance['InstanceId']
            tags = instance.get('Tags', [])
            state = instance['State']['Name']
            status = statuses.get(instance_id, {})
            result.append(WorkshopInstance(
                instance_id=instance_id,
                group=get_aws_tag(tags, 'ws_group') or '',
                hostname=get_aws_tag(tags, 'ws_hostname') or '(unnamed)',
                state=state,
                public_dns=instance.get('PublicDnsName') or '—',
                private_ip=instance.get('PrivateIpAddress') or '—',
                instance_type=instance.get('InstanceType') or '—',
                system_check=(status.get('SystemStatus', {}).get('Status', 'unknown')
                              if state == 'running' else '—'),
                instance_check=(status.get('InstanceStatus', {}).get('Status', 'unknown')
                                if state == 'running' else '—'),
            ))
        return FleetSnapshot(tuple(sorted(result, key=hostname_order)), health_error)

    def change_state(self, action, instance):
        """Recheck the selected instance's group and state before changing it."""
        required_states = {'reboot': 'running', 'start': 'stopped'}
        if action not in required_states:
            raise ValueError(f'Unknown instance action: {action}')
        response = self.ec2.describe_instances(InstanceIds=[instance.instance_id])
        current = [item for reservation in response['Reservations']
                   for item in reservation['Instances']
                   if item['InstanceId'] == instance.instance_id]
        if not current or get_aws_tag(current[0].get('Tags', []), 'ws_group') != instance.group:
            raise ValueError('The instance is no longer in the selected group. Refresh the table.')
        state = current[0]['State']['Name']
        if state != required_states[action]:
            raise ValueError(f'The instance is now {state}. Refresh the table before trying again.')
        operation = self.ec2.reboot_instances if action == 'reboot' else self.ec2.start_instances
        operation(InstanceIds=[instance.instance_id])

    def terminate_instances(self, instances):
        """Recheck the reviewed IDs and group before requesting termination."""
        if not instances:
            raise ValueError('Select at least one instance to terminate.')
        if len({instance.group for instance in instances}) != 1:
            raise ValueError('Terminate instances from one group at a time.')
        reviewed = {instance.instance_id: instance for instance in instances}
        instance_ids = list(reviewed)
        current = {}
        # Read every instance before issuing any termination requests. The
        # reviewed list stays fixed even if new instances join the group.
        for start in range(0, len(instance_ids), 1000):
            for page in self.ec2.get_paginator('describe_instances').paginate(
                    InstanceIds=instance_ids[start:start + 1000]):
                current.update((instance['InstanceId'], instance)
                               for reservation in page['Reservations']
                               for instance in reservation['Instances'])
        for instance_id, instance in reviewed.items():
            actual = current.get(instance_id)
            if actual is None or get_aws_tag(actual.get('Tags', []), 'ws_group') != instance.group:
                raise ValueError(f'{instance_id} is no longer in the reviewed group. '
                                 'Refresh the table and review the instances again.')
            if actual['State']['Name'] not in TERMINATABLE_STATES:
                raise ValueError(f"{instance_id} is now {actual['State']['Name']}. "
                                 'Refresh the table and review the instances again.')
        for start in range(0, len(instance_ids), 1000):
            self.ec2.terminate_instances(InstanceIds=instance_ids[start:start + 1000])


async def launch_group(request, output):
    """Stream the existing launcher's output and return its exit status."""
    request.validate()
    command = [sys.executable, '-u', str(Path(__file__).with_name('launch_instances.py')),
               request.group, str(request.count)]
    if request.skip_proxy:
        command.append('--skip-proxy')
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        async for line in process.stdout:
            output(line.decode(errors='replace').rstrip())
        return await process.wait()
    except asyncio.CancelledError:
        # A forced shutdown must not leave a launcher creating more instances.
        if process.returncode is None:
            process.terminate()
            await process.wait()
        raise
