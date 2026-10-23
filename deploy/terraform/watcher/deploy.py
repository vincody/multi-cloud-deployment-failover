"""Install the failover watcher on the Azure VM. AWS credentials come from a local file; only its path is in state."""
import base64
import configparser
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'automation'))
import deploy as workloads

SHARED_CORE = HERE.parents[2] / 'scripts' / 'watcher_core.py'


class Host(workloads.Host):
    remote_script = base64.b64encode(
        (HERE.parent / 'automation' / 'remote.py').read_bytes().split(b"if __name__ == '__main__':")[0]
        + (HERE / 'remote.py').read_bytes()
    ).decode()


def credentials(path):
    if not path:
        return None
    parser = configparser.RawConfigParser()
    if not parser.read(Path(path).expanduser(), encoding='utf-8-sig') or not parser.has_section('default'):
        raise workloads.DeploymentError('Watcher credential file requires an INI [default] profile')
    values = dict(parser.items('default'))
    if set(values) != {'aws_access_key_id', 'aws_secret_access_key'} or any(not re.fullmatch(r'[A-Za-z0-9/+_=.-]+', v) for v in values.values()):
        raise workloads.DeploymentError('Use the watcher IAM user access key/secret; no temporary token for an unattended service')
    return values


def validate(config):
    key = workloads.validate(config)
    checks = {'aws_instance_id': r'i-[0-9a-f]{8,17}', 'health_check_id': r'[0-9a-f-]{36}',
              'aws_origin_host': r'[a-z0-9][a-z0-9.-]+\.[a-z]{2,}', 'azure_origin_host': r'[a-z0-9][a-z0-9.-]+\.[a-z]{2,}'}
    for name, pattern in checks.items():
        if not isinstance(config.get(name), str) or not re.fullmatch(pattern, config[name]):
            raise workloads.DeploymentError(f'Invalid {name}')
    if not 2 <= int(config['threshold']) <= 20 or not 2 <= int(config['interval']) <= 60:
        raise workloads.DeploymentError('threshold must be 2-20 checks and interval 2-60 seconds')
    return key


def bundle():
    files = {name: (HERE / name).read_text(encoding='utf-8') for name in ('cloud_watcher.py', 'promote.py')}
    files['watcher_core.py'] = SHARED_CORE.read_text(encoding='utf-8')
    return files


def deploy(config, host_factory=Host):
    key = validate(config)
    values = credentials(config.get('credentials_file', ''))
    azure = host_factory('azure', config, key)
    azure.wait()
    status = azure.call('status')
    if status.get('writable') or status.get('role') != 'standby' or not status.get('replication_connected'):
        raise workloads.DeploymentError('Azure is not a streaming standby; install the watcher only on a healthy baseline')
    azure.call('watcher_prepare', {
        'files': bundle(), 'credentials': values,
        'config': {'aws_region': config['aws_region'], 'aws_instance_id': config['aws_instance_id'],
                   'aws_origin': 'https://' + config['aws_origin_host'], 'azure_origin': 'https://' + config['azure_origin_host'],
                   'health_check_id': config['health_check_id'], 'interval': int(config['interval']), 'threshold': int(config['threshold'])}})
    del values
    azure.call('watcher_check')
    azure.call('watcher_enable')
    print('Failover watcher installed and active on the Azure VM.', flush=True)


def main():
    try:
        deploy(json.loads(os.environ['DCS29_WATCHER_CONFIG']))
    except Exception as exc:
        print(str(exc) if isinstance(exc, workloads.DeploymentError) else f'{type(exc).__name__}: watcher installation stopped; inspect VM logs', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
