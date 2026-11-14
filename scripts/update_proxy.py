"""Update the NGINX proxy configuration for a workshop group."""

import argparse
import ipaddress
import re
import shlex
import subprocess

import boto3

from utils import get_aws_tag, get_group_instances


KEYPAIR_PATH = '/home/romano/.ssh/id_rsa'
PROXY_IPS = ('184.73.99.182',)

# The configuration arrives on stdin. Temporary files exist only on the proxy.
# Restore the previous file if installing, checking, or reloading fails.
DEPLOY_SCRIPT = '''set -eu
configuration=$(mktemp)
backup=$(mktemp)
trap 'rm -f "$configuration" "$backup"' EXIT
cat > "$configuration"
destination="/etc/nginx/conf.d/$1.conf"
had_previous=0
if sudo test -e "$destination"; then
    sudo cp "$destination" "$backup"
    had_previous=1
fi
if sudo install -m 644 "$configuration" "$destination" &&
        sudo nginx -t && sudo service nginx reload; then
    exit 0
fi
if [ "$had_previous" = 1 ]; then
    sudo install -m 644 "$backup" "$destination"
else
    sudo rm -f "$destination"
fi
exit 1
'''


def validate_group(groupname):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*', groupname):
        raise ValueError('The group name must start with a letter or digit and contain only letters, digits, or hyphens')


def build_configuration(instances):
    entries = []
    hostnames = set()
    for instance in instances:
        if instance['State']['Name'] not in {'pending', 'running'}:
            continue
        hostname = get_aws_tag(instance.get('Tags', []), 'ws_hostname')
        if not hostname or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', hostname):
            raise ValueError(f"Instance {instance['InstanceId']} has a missing or invalid ws_hostname tag")
        if hostname in hostnames:
            raise ValueError(f'Duplicate ws_hostname tag: {hostname}')
        hostnames.add(hostname)
        if not instance.get('PrivateIpAddress'):
            raise ValueError(f"Instance {instance['InstanceId']} has no private IP address yet")
        ip = ipaddress.IPv4Address(instance['PrivateIpAddress'])
        entries.append(
            'server {\n'
            f'  server_name {hostname}.openmccourse.org;\n'
            '  include /etc/nginx/conf.d/templ/server_templ.conf;\n'
            '  location / {\n'
            f'    proxy_pass http://{ip}:8888;\n'
            '    include /etc/nginx/conf.d/templ/loc_templ.conf;\n'
            '  }\n'
            '}\n\n')
    if not entries:
        raise ValueError('No pending or running instances found; the proxy configuration was left unchanged')
    return ''.join(entries)


def update_proxy(groupname, instances=None):
    """Stream a group's configuration over SSH, check NGINX, and reload it."""
    validate_group(groupname)
    if instances is None:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        instances = get_group_instances(ec2, groupname)
    configuration = build_configuration(instances)
    command = shlex.join(['bash', '-c', DEPLOY_SCRIPT, 'bash', groupname])
    for proxy_ip in PROXY_IPS:
        print(f'Updating proxy {proxy_ip} for {groupname}...', flush=True)
        subprocess.run([
            'ssh', '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'StrictHostKeyChecking=no', '-o', 'KexAlgorithms=ecdh-sha2-nistp521',
            '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
            '-i', KEYPAIR_PATH, f'ec2-user@{proxy_ip}', command,
        ], input=configuration, text=True, check=True)
    print('Proxy configuration updated.', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('groupname')
    args = parser.parse_args(argv)
    update_proxy(args.groupname)


if __name__ == '__main__':
    main()
