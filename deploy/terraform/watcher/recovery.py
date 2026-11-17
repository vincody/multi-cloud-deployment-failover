"""After a failover: wait for an operator to start the fenced AWS VM, rejoin it as a standby and optionally fail back.

The watcher never starts EC2 itself: a person decides when the old primary comes back. Runs inside the Azure watcher. The decision flow is a persisted state machine so a stopped run resumes where
it left off. All I/O is injected (see Deps) so it can be tested offline. Anything unexpected raises
OperatorNeeded and the watcher exits with code 2. Old PVCs are never deleted.

    wait-aws -> rejoin-aws -> complete                               (failback off)
    wait-aws -> rejoin-aws -> failback-lock -> failback-fence -> failback-promote -> rebuild-azure -> rearm
"""
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

AWS_IP = '10.200.29.1'
AZURE_IP = '10.200.29.2'
KEY = Path('/etc/dcs29/recovery_ed25519')
KNOWN_HOSTS = Path('/etc/dcs29/recovery_known_hosts')


class OperatorNeeded(RuntimeError):
    """Stop automation: a person must look before anything else changes."""


class RemoteError(RuntimeError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def save(self, state):
        temp = self.path.with_name(self.path.name + '.new')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)


class RemoteAws:
    """Calls recovery_ops on AWS over WireGuard. The forced command on AWS ignores the command text."""

    def call(self, action, payload=None, timeout=900):
        result = subprocess.run(
            ['ssh', '-i', str(KEY), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={KNOWN_HOSTS}',
             '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4', f'ubuntu@{AWS_IP}', 'dcs29-rpc'],
            input=json.dumps({'action': action, 'payload': payload or {}}), text=True, capture_output=True, timeout=timeout)
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise RemoteError(f'AWS {action}: SSH/RPC failed') from None
        if not response.get('ok'):
            raise RemoteError(f"AWS {action}: {response.get('error', 'operation failed')}")
        return response['result']


class LocalAzure:
    def __init__(self, actions):
        self.actions = actions

    def call(self, action, payload=None, timeout=None):
        return self.actions[action](payload or {})


def new_pvc(now=None):
    return 'postgres-data-rejoin-' + (now or datetime.now(timezone.utc)).strftime('%Y%m%d%H%M%S')


def save(d, state, phase=None, **fields):
    if phase:
        state['phase'] = phase
    state.update(fields)
    d.store.save(state)


def wait_until(d, what, ready, timeout):
    """Poll ready() until true. Pause file is honoured; the deadline raises OperatorNeeded."""
    deadline = d.monotonic() + timeout
    while not ready():
        if d.monotonic() > deadline:
            raise OperatorNeeded(f'Timed out waiting for {what}')
        d.sleep(d.poll)


def azure_is_open_writer(d):
    state = d.azure.call('db_state')
    return state['role'] == 'primary' and bool(state.get('writes_enabled'))


def wait_caught_up(d, standby, source, what):
    """The standby must stream and have replayed the source's current WAL position."""
    def ready():
        target = source.call('db_state')
        if target['role'] != 'primary':
            raise OperatorNeeded(f'{what}: source is not a primary')
        status = standby.call('db_state')
        if status['role'] != 'standby' or status.get('receiver') != 'streaming':
            return False
        return standby.call('wait_replay', {'lsn': target['flush_lsn'], 'timeout': 10})['caught_up']
    wait_until(d, what, ready, d.catchup_timeout)


def wait_aws(d, state):
    """Nothing is started here. Wait, without a deadline, until an operator has started EC2 and its RPC answers."""
    if not azure_is_open_writer(d):
        raise OperatorNeeded('Azure is not the open writer; not rejoining AWS')
    last = None
    while True:
        current = d.aws_state()
        if current == 'running':
            try:
                d.aws.call('ping', timeout=30)
                break
            except (RemoteError, subprocess.SubprocessError, OSError):
                current = 'running (no RPC yet)'
        elif current not in ('stopped', 'pending', 'stopping'):
            raise OperatorNeeded(f'Unexpected EC2 state {current}')
        if current != last:
            d.log(f'Waiting for an operator to start AWS (EC2 is {current}).')
            last = current
        d.sleep(d.poll)
    save(d, state, 'rejoin-aws')


def rejoin_aws(d, state):
    if not azure_is_open_writer(d):
        raise OperatorNeeded('Azure is not the open writer; not rejoining AWS')
    aws = d.aws.call('db_state')
    if aws['role'] == 'primary':
        d.stop_aws()
        raise OperatorNeeded('AWS database is a second primary; AWS instance stopped (fenced). Inspect before rejoining.')
    if not (aws['role'] == 'standby' and aws.get('receiver') == 'streaming' and state.get('aws_pvc')):
        if not state.get('aws_pvc'):
            save(d, state, aws_pvc=new_pvc())
        d.aws.call('rejoin', {'pvc': state['aws_pvc'], 'source_ip': AZURE_IP, 'template': d.template}, timeout=1500)
    wait_caught_up(d, d.aws, d.azure, 'AWS to catch up with Azure')
    d.aws.call('app_scale', {'replicas': 1})
    save(d, state, 'failback-lock' if d.failback else 'complete')


def reopen_azure(d):
    d.azure.call('gate', {'enabled': True})


def failback_lock(d, state):
    if not azure_is_open_writer(d):
        raise OperatorNeeded('Azure is not the open writer; not failing back')
    wait_caught_up(d, d.aws, d.azure, 'AWS to catch up before failback')
    lsn = d.azure.call('gate', {'enabled': False})['lsn']
    try:
        wait_until(d, 'AWS to replay the final WAL position', lambda: d.aws.call('wait_replay', {'lsn': lsn, 'timeout': 10})['caught_up'], d.lock_timeout)
    except Exception:
        reopen_azure(d)
        raise OperatorNeeded('AWS did not reach the final WAL position; Azure writes re-enabled')
    save(d, state, 'failback-fence', final_lsn=lsn)


def failback_fence(d, state):
    d.azure.call('fence_db')
    save(d, state, 'failback-promote')


def restore_azure(d):
    d.azure.call('unfence_db')
    reopen_azure(d)


def failback_promote(d, state):
    try:
        d.aws.call('promote_local', {'min_lsn': state['final_lsn']})
    except Exception as error:
        try:
            aws = d.aws.call('db_state')
        except Exception:
            raise OperatorNeeded(f'AWS promotion failed and AWS is unreachable ({error}); Azure database is stopped')
        if aws['role'] == 'standby':
            restore_azure(d)
            raise OperatorNeeded(f'AWS promotion failed ({error}); Azure writer restored')
        if aws['role'] != 'primary' or not aws.get('writes_enabled'):
            raise OperatorNeeded(f'AWS promotion failed ({error}); both databases need inspection')
    wait_until(d, 'AWS to report itself writable', d.aws_writable, d.healthy_timeout)
    save(d, state, 'rebuild-azure')


def rebuild_azure(d, state):
    if not state.get('azure_pvc'):
        save(d, state, azure_pvc=new_pvc())
    d.azure.call('rejoin', {'pvc': state['azure_pvc'], 'source_ip': AWS_IP, 'template': d.template})
    wait_caught_up(d, d.azure, d.aws, 'Azure to catch up with AWS')
    save(d, state, 'rearm')


def rearm(d, state):
    d.clear_incident()
    d.store.clear()


HANDLERS = {'wait-aws': wait_aws, 'rejoin-aws': rejoin_aws, 'failback-lock': failback_lock, 'failback-fence': failback_fence,
            'failback-promote': failback_promote, 'rebuild-azure': rebuild_azure, 'rearm': rearm}


def run(d):
    """Returns 'recovered' (AWS standby, Azure stays writer) or 'rearmed' (AWS writer again, watching resumes)."""
    state = d.store.load() or {'phase': 'wait-aws', 'started': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    d.store.save(state)
    while True:
        if d.paused():
            d.sleep(d.poll)
            continue
        phase = state['phase']
        if phase == 'complete':
            return 'recovered'
        d.log(f'Recovery phase: {phase}')
        HANDLERS[phase](d, state)
        if phase == 'rearm':
            return 'rearmed'
