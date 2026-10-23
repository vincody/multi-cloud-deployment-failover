"""DCS29 failover watcher. Runs as a systemd service on the Azure VM, outside the AWS region it watches.

It fails over only when two views of the AWS origin agree it is down for N consecutive checks:
  1. this VM probes https://<aws-origin>/health/database directly;
  2. Route 53's health checkers (many regions) report the same endpoint as failing.
Then it fences first (stops the EC2 instance and waits for `stopped`), promotes the local PostgreSQL
standby, and verifies that Azure accepts writes. Route 53 moves traffic on its own once Azure is the
only healthy writer. The watcher never fails back and never starts the old primary.

    cloud_watcher.py          run the watch loop
    cloud_watcher.py --check  verify credentials, AWS API access and the local standby, change nothing
"""
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import promote
import watcher_core

CONFIG = Path(os.environ.get('DCS29_WATCHER_CONFIG', '/etc/dcs29/watcher.json'))
PAUSE = Path('/opt/dcs29/watcher.pause')
FENCE_TIMEOUT = 240


def log(message):
    print(datetime.now(timezone.utc).isoformat(timespec='seconds'), message, flush=True)


def load_config():
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    for key in ('aws_region', 'aws_instance_id', 'aws_origin', 'azure_origin', 'health_check_id', 'interval', 'threshold'):
        if key not in cfg:
            raise RuntimeError(f'Watcher config is missing {key}')
    return cfg


def ec2(cfg):
    import boto3
    return boto3.client('ec2', region_name=cfg['aws_region'])


def route53():
    import boto3
    return boto3.client('route53', region_name='us-east-1')


def check_web(cfg):
    """True when AWS looks alive or we cannot tell. Only refusal, timeout or HTTP 5xx count as down.

    DNS, TLS and 4xx problems say more about this VM or the config than about the writer,
    so they never contribute to a failover."""
    try:
        with urllib.request.urlopen(cfg['aws_origin'] + '/health/database', timeout=6) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        return error.code < 500
    except urllib.error.URLError as error:
        if isinstance(error.reason, (ssl.SSLError, socket.gaierror)) or not isinstance(error.reason, OSError):
            log(f'Probe error is not proof of failure: {type(error.reason).__name__}')
            return True
        return False
    except (TimeoutError, ConnectionError):
        return False


def check_route53(cfg):
    """False only when Route 53 reports observations and none of its checkers sees the origin healthy."""
    try:
        observations = route53().get_health_check_status(HealthCheckId=cfg['health_check_id'])['HealthCheckObservations']
    except Exception as error:
        log(f'Route 53 status unavailable ({type(error).__name__}); not counted as a failure')
        return True
    if not observations:
        return True
    return any(o['StatusReport']['Status'].startswith('Success') for o in observations)


def aws_state(cfg):
    reservations = ec2(cfg).describe_instances(InstanceIds=[cfg['aws_instance_id']])['Reservations']
    return reservations[0]['Instances'][0]['State']['Name']


def stop_aws(cfg):
    ec2(cfg).stop_instances(InstanceIds=[cfg['aws_instance_id']])


def azure_writable(cfg):
    try:
        with urllib.request.urlopen(cfg['azure_origin'] + '/api/database/status', timeout=8) as response:
            status = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False
    return status.get('role') == 'primary' and status.get('writable') is True


def fence(cfg, sleep=time.sleep):
    state = aws_state(cfg)
    if state not in ('running', 'pending', 'stopping', 'stopped'):
        raise RuntimeError(f'Unexpected EC2 state {state}; fencing not proven')
    if state in ('running', 'pending'):
        stop_aws(cfg)
    deadline = time.monotonic() + FENCE_TIMEOUT
    while aws_state(cfg) != 'stopped':
        if time.monotonic() > deadline:
            raise RuntimeError('EC2 did not reach stopped in time; not promoting')
        sleep(5)
    log('AWS instance is stopped (fenced).')


def incident_owner():
    """One owner per incident, reused if the watcher is run again after a stop."""
    marker = promote.ROOT / 'failover-incident.json'
    if marker.exists():
        return json.loads(marker.read_text())['owner']
    return 'watcher:' + datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def failover(cfg, sleep=time.sleep):
    # Last look before an irreversible step: both views must still say down.
    if check_web(cfg) or check_route53(cfg):
        raise RuntimeError('AWS answered on the final check; not failing over')
    owner = incident_owner()
    promote.run_locked('preflight', owner)
    fence(cfg, sleep)
    promote.run_locked('promote', owner)
    deadline = time.monotonic() + 90
    while not azure_writable(cfg):
        if time.monotonic() > deadline:
            raise RuntimeError('Azure was promoted but does not report itself writable; inspect the VM')
        sleep(3)
    log('Azure is the writer. Route 53 follows once it is the only healthy origin.')


def blocked():
    """'done' once the local database is primary; 'busy' when acting would be unsafe or unwanted."""
    try:
        recovery = promote.sql('SELECT pg_is_in_recovery()') == 't'
    except Exception:
        return 'busy'
    if not recovery:
        return 'done'
    if (promote.ROOT / 'failover-incident.json').exists() or PAUSE.exists():
        return 'busy'  # unfinished incident or operator pause: wait for a person
    return None


def check_setup(cfg):
    """Read-only self-test used by the deploy step before the service is enabled."""
    if aws_state(cfg) != 'running':
        raise RuntimeError('AWS instance is not running; enable the watcher only on a healthy baseline')
    route53().get_health_check_status(HealthCheckId=cfg['health_check_id'])
    promote.run_locked('preflight', 'watcher:2000-01-01T00:00:00Z')
    log('Setup check passed: AWS API reachable, Route 53 readable, local standby caught up.')


def main(argv):
    cfg = load_config()
    if '--check' in argv:
        check_setup(cfg)
        return 0
    outcome = watcher_core.run(lambda: check_web(cfg), lambda: check_route53(cfg), lambda: failover(cfg),
                               cfg['interval'], cfg['threshold'], blocked, log=log)
    log(f'Watcher finished: {outcome}')
    return 2 if outcome == 'operator-needed' else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
