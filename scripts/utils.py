from itertools import count


def get_aws_tag(tags, key):
    """Retrieve a tag value for the given key.

    Returns None if the given key is not in the tags.

    """
    for tag in tags:
        if tag['Key'] == key:
            return tag['Value']

    return None


def get_group_instances(ec2, groupname):
    """Get all instances in a workshop group, including subsequent pages."""
    return [instance
            for page in ec2.get_paginator('describe_instances').paginate(
                Filters=[{'Name': 'tag:ws_group', 'Values': [groupname]}])
            for reservation in page['Reservations']
            for instance in reservation['Instances']]


def unused_hostnames(groupname, instances):
    """Yield group0, group1, etc., skipping names already assigned in the group."""
    taken = {get_aws_tag(instance.get('Tags', []), 'ws_hostname') for instance in instances}
    for index in count():
        hostname = f'{groupname}{index}'
        if hostname not in taken:
            yield hostname
