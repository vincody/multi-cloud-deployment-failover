"""Ordered, fail-closed initial deployment via SSH. Python standard library only."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REMOTE = base64.b64encode((HERE / 'remote.py').read_bytes()).decode()


class DeploymentError(RuntimeError):
    pass


def validate(config):
    required = ('aws_host', 'azure_host', 'azure_user', 'private_key', 'image_ref', 'postgres_ref', 'aws_region', 'azure_region')
    if any(not isinstance(config.get(key), str) or not config[key] for key in required):
        raise DeploymentError('Missing deployment configuration')
    for key in ('aws_host', 'azure_host'):
        if ipaddress.ip_address(config[key]).version != 4:
            raise DeploymentError('Only IPv4 peers are supported')
    if config['aws_host'] == config['azure_host']:
        raise DeploymentError('AWS/Azure must have different peer addresses')
    for key in ('aws_region', 'azure_region', 'azure_user'):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', config[key]):
            raise DeploymentError(f'Invalid {key}')
    if not re.fullmatch(r'ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}', config['image_ref']):
        raise DeploymentError('Invalid pinned app image')
    if not re.fullmatch(r'(docker.io/library/)?postgres@sha256:[0-9a-f]{64}', config['postgres_ref']):
        raise DeploymentError('Invalid pinned PostgreSQL image')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,24}', config.get('standby_generation', 'initial')):
        raise DeploymentError('Invalid standby generation')
    key = Path(config['private_key']).expanduser()
    if not key.is_file():
        raise DeploymentError('SSH private key file does not exist locally')
    if not shutil.which('ssh'):
        raise DeploymentError('OpenSSH ssh is required on the operator laptop')
    return str(key.resolve())


def render(name, config, site):
    replacements = {
        '__LOCAL_IP__': '10.200.29.1' if site == 'aws' else '10.200.29.2',
        '__POSTGRES_IMAGE__': config['postgres_ref'],
        '__APP_IMAGE__': config['image_ref'],
        '__SITE__': site,
        '__REGION__': config[site + '_region'],
        '__STANDBY_GENERATION__': config.get('standby_generation', 'initial'),
    }
    text = (HERE / 'templates' / (name + '.yaml.tmpl')).read_text()
    for key, value in replacements.items():
        text = text.replace(key, value)
    if re.search(r'__[A-Z_]+__', text):
        raise DeploymentError('Unrendered manifest placeholders')
    return text


class Host:
    remote_script = REMOTE
    def __init__(self, site, config, private_key):
        self.site = site
        self.address = config[site + '_host']
        self.user = 'ubuntu' if site == 'aws' else config['azure_user']
        self.key = private_key

    def call(self, action, payload=None):
        command = 'sudo -n python3 -c "import base64,io,json,sys;r=json.load(sys.stdin);sys.stdin=io.StringIO(json.dumps(r[\'request\']));exec(base64.b64decode(r[\'script\']))"'
        result = subprocess.run(
            ['ssh', '-i', self.key, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4', self.user + '@' + self.address, command],
            input=json.dumps({'script': self.remote_script, 'request': {'action': action, 'payload': payload or {}}}),
            text=True, capture_output=True, timeout=960,
        )
        try:
            response = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise DeploymentError(f'{self.site}: SSH/RPC failed during {action}; check key, cloud-init, SSH CIDR and VM logs') from None
        if result.returncode or not response.get('ok'):
            raise DeploymentError(f"{self.site}: {action}: {response.get('error', 'operation failed')}")
        return response['result']

    def wait(self):
        # Retry only a read-only connection probe. Mutations are never blindly retried.
        deadline = time.monotonic() + 600
        while True:
            try:
                result = subprocess.run(['ssh', '-i', self.key, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10', self.user + '@' + self.address, 'true'], capture_output=True, timeout=15)
                if not result.returncode:
                    return
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() >= deadline:
                raise DeploymentError(f'{self.site}: SSH did not become available')
            time.sleep(5)


def check_roles(aws, azure):
    first, second = aws.call('status'), azure.call('status')
    if not first.get('writable') or second.get('writable') or second.get('role') != 'standby' or not second.get('replication_connected'):
        raise DeploymentError('Final database role/streaming check failed; no promotion will be attempted')
    versions = [host.call('api', {'path': '/version'}) for host in (aws, azure)]
    if versions[0].get('environment') != 'aws' or versions[1].get('environment') != 'azure' or versions[0].get('commit_sha') != versions[1].get('commit_sha'):
        raise DeploymentError('Application cloud/commit verification failed')
    return first, second


def deploy(config, host_factory=Host):
    private_key = validate(config)
    hosts = {site: host_factory(site, config, private_key) for site in ('aws', 'azure')}
    aws, azure = hosts['aws'], hosts['azure']
    azure_pvc = 'postgres-data-standby-' + config.get('standby_generation', 'initial')
    for site, host in hosts.items():
        print(f'[{site}] Waiting for SSH and cloud-init...', flush=True)
        host.wait()
        host.call('preflight', {'site': site, 'pvc': 'postgres-data' if site == 'aws' else azure_pvc})
    public_keys = {}
    for site, host in hosts.items():
        public_keys[site] = host.call('prepare', {'local_ip': '10.200.29.1' if site == 'aws' else '10.200.29.2', 'peer_ip': '10.200.29.2' if site == 'aws' else '10.200.29.1'})['public_key']
    for site, host in hosts.items():
        peer = 'azure' if site == 'aws' else 'aws'
        host.call('wireguard', {'local_ip': '10.200.29.1' if site == 'aws' else '10.200.29.2', 'peer_ip': '10.200.29.2' if site == 'aws' else '10.200.29.1', 'peer_host': config[peer + '_host'], 'peer_key': public_keys[peer]})
    print('[wireguard] Both peers configured; database clone will verify the protected connection.', flush=True)
    values = aws.call('credentials', {'export': True})['values']
    azure.call('credentials', {'values': values})
    del values
    # Primary -> app Running -> schema, then clone; never initialize schema on standby.
    for name, template in [('postgres-config', 'postgres-config'), ('postgres-endpoint', 'postgres-endpoint'), ('postgres', 'postgres-aws')]:
        aws.call('apply', {'name': name, 'yaml': render(template, config, 'aws')})
    aws.call('database_wait', {'site': 'aws', 'pvc': 'postgres-data'})
    aws.call('apply', {'name': 'issues-app', 'yaml': render('app', config, 'aws')})
    aws.call('app_bootstrap')
    aws.call('app_wait')
    ingress_aws = aws.call('apply', {'name': 'ingress-http', 'yaml': render('ingress-http', config, 'aws')})
    print('[aws] Primary and Issues schema ready.', flush=True)
    for name, template in [('postgres-config', 'postgres-config'), ('postgres-endpoint', 'postgres-endpoint'), ('postgres', 'postgres-azure')]:
        azure.call('apply', {'name': name, 'yaml': render(template, config, 'azure'), 'new_standby_pvc': azure_pvc if name == 'postgres' else None})
    azure.call('database_wait', {'site': 'azure', 'pvc': azure_pvc})
    azure.call('apply', {'name': 'issues-app', 'yaml': render('app', config, 'azure')})
    azure.call('app_wait')
    ingress_azure = azure.call('apply', {'name': 'ingress-http', 'yaml': render('ingress-http', config, 'azure')})
    check_roles(aws, azure)
    sample = aws.call('sample')
    issue_id = sample['id']
    deadline = time.monotonic() + 120
    while True:
        try:
            replica = azure.call('api', {'path': '/api/issues/' + issue_id})
            source = aws.call('api', {'path': '/api/issues/' + issue_id})
            if replica == source and replica['issue']['id'] == issue_id:
                break
        except DeploymentError:
            pass  # Read-only replay poll; POST used a durable request_id before this loop.
        if time.monotonic() >= deadline:
            raise DeploymentError('Smoke issue/history did not replay to Azure within 120 seconds')
        time.sleep(2)
    check_roles(aws, azure)
    for host, ingress in [(aws, ingress_aws), (azure, ingress_azure)]:
        if not ingress.get('preserved'):
            result = host.call('http_probe', {'path': '/api/issues/' + issue_id})
            if result != source:
                raise DeploymentError('HTTP Ingress did not return the replicated smoke issue')
    summary = {'schema': 'issues', 'issue_id': issue_id, 'aws_role': 'primary', 'azure_role': 'standby', 'image': config['image_ref'], 'postgres': config['postgres_ref'], 'azure_pvc': azure_pvc}
    for host in hosts.values():
        host.call('finish', summary)
    print(f'Automation verified: AWS writer, Azure streaming standby, issue {issue_id} replicated. HTTP Ingress ready.', flush=True)
    return summary


def main():
    try:
        config = json.loads(os.environ['DCS29_AUTOMATION_CONFIG'])
        deploy(config)
    except Exception as exc:
        message = str(exc) if isinstance(exc, DeploymentError) else f'{type(exc).__name__}: deployment stopped; inspect the documented phase logs'
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
