"""Initial HTTPS setup, after DNS delegation; no credentials in Terraform state."""
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


class Host(workloads.Host):
    # Reuse root RPC helpers without executing its main before HTTPS actions exist.
    remote_script = base64.b64encode(
        (HERE.parent / 'automation' / 'remote.py').read_bytes().split(b"if __name__ == '__main__':")[0]
        + (HERE / 'remote.py').read_bytes()
    ).decode()


def credentials(path):
    if not path:
        return None
    parser = configparser.RawConfigParser()
    if not parser.read(Path(path).expanduser(), encoding='utf-8-sig') or not parser.has_section('default'):
        raise workloads.DeploymentError('DNS credential file requires an INI [default] profile')
    values = dict(parser.items('default'))
    if set(values) != {'aws_access_key_id', 'aws_secret_access_key'} or any(not re.fullmatch(r'[A-Za-z0-9/+_=.-]+', v) for v in values.values()):
        raise workloads.DeploymentError('Use the DNS-only IAM user access key/secret; no temporary token for unattended renewal')
    return values


def deploy(config, host_factory=Host):
    key = workloads.validate(config)
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', config['domain']) or '.' not in config['domain'] or '..' in config['domain']:
        raise workloads.DeploymentError('Invalid public domain')
    if not re.fullmatch(r'[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', config['email']):
        raise workloads.DeploymentError('Invalid certificate email')
    if len(config.get('nameservers', [])) != 4:
        raise workloads.DeploymentError('Expected the four hosted-zone nameservers')
    values = credentials(config.get('credentials_file', ''))
    hosts = {site: host_factory(site, config, key) for site in ('aws', 'azure')}
    payloads = {}
    # Both preflights precede ACME changes. Never start a guarded/fenced VM.
    for site, host in hosts.items():
        host.wait()
        payloads[site] = {'site': site, 'domain': config['domain'], 'email': config['email'], 'nameservers': config['nameservers'], 'public_ip': config[site + '_host']}
        host.call('https_prepare', {**payloads[site], 'credentials': values if site == 'azure' else None})
    del values
    failure = None
    try:
        # Stop both renewal timers first; shared app TXT must not be changed concurrently.
        for host in hosts.values():
            host.call('https_pause')
        for site, host in hosts.items():
            print(f'[{site}] Issuing/checking certificate and configuring HTTPS...', flush=True)
            host.call('https_apply', payloads[site])
            host.call('https_verify', payloads[site])
    except Exception as exc:
        failure = exc
    finally:
        for site, host in hosts.items():
            try:
                host.call('https_schedule', payloads[site])
            except Exception as exc:
                if failure is None:
                    failure = exc
    if failure is not None:
        raise failure
    print('HTTPS verified on both origins and shared hostname; renewal timers configured.', flush=True)


def main():
    try:
        deploy(json.loads(os.environ['DCS29_HTTPS_CONFIG']))
    except Exception as exc:
        message = str(exc) if isinstance(exc, workloads.DeploymentError) else f'{type(exc).__name__}: HTTPS deployment stopped; inspect VM logs'
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
