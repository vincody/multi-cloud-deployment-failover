"""Watcher install actions appended to the existing root RPC helpers (run on the Azure VM)."""
import time

WATCHER_DIR = Path('/opt/dcs29/watcher')
CREDENTIALS = Path('/etc/dcs29/watcher-aws-credentials')
UNIT = """[Unit]
Description=DCS29 failover watcher
After=network-online.target k3s.service
Wants=network-online.target

[Service]
Type=simple
Environment=AWS_SHARED_CREDENTIALS_FILE=/etc/dcs29/watcher-aws-credentials AWS_PROFILE=default
ExecStart=/usr/bin/python3 /opt/dcs29/watcher/cloud_watcher.py
Restart=on-failure
RestartSec=10
# Exit 2 means the watcher stopped on purpose and a person must look; do not retry it.
RestartPreventExitStatus=2

[Install]
WantedBy=multi-user.target
"""


def watcher_prepare(p):
    if run(['systemctl', 'is-active', 'k3s'], optional=True).returncode:
        raise RuntimeError('K3s is guarded/stopped; confirm the writer state before installing the watcher')
    if not (ROOT / 'automation-result.json').exists():
        raise RuntimeError('Initial replication has not been verified')
    run(['apt-get', '-o', 'Acquire::Retries=5', 'update'])
    run(['apt-get', '-o', 'Acquire::Retries=5', 'install', '-y', 'python3-boto3'])
    for name, text in p['files'].items():
        write(WATCHER_DIR / name, text, mode=0o700)
    if p.get('credentials'):
        write(CREDENTIALS, '[default]\n' + ''.join(f'{k} = {v}\n' for k, v in p['credentials'].items()))
    if not CREDENTIALS.is_file():
        raise RuntimeError('Watcher AWS credentials missing; provide azure_watcher_credentials_file outside the repo')
    os.chmod(CREDENTIALS, 0o600)
    write('/etc/dcs29/watcher.json', json.dumps(p['config'], indent=2), mode=0o640)
    write('/etc/systemd/system/dcs29-watcher.service', UNIT, mode=0o644)
    run(['systemctl', 'daemon-reload'])
    return {'ok': True}


def watcher_check(p):
    env = ['env', 'AWS_SHARED_CREDENTIALS_FILE=' + str(CREDENTIALS), 'AWS_PROFILE=default']
    if run(env + ['python3', str(WATCHER_DIR / 'cloud_watcher.py'), '--check'], optional=True, timeout=300).returncode:
        raise RuntimeError('Watcher setup check failed; run "python3 /opt/dcs29/watcher/cloud_watcher.py --check" on the VM with the credentials env to see why')
    return {'ok': True}


def watcher_enable(p):
    run(['systemctl', 'enable', '--now', 'dcs29-watcher'])
    time.sleep(3)
    if run(['systemctl', 'is-active', '--quiet', 'dcs29-watcher'], optional=True).returncode:
        raise RuntimeError('Watcher service is not active; inspect journalctl -u dcs29-watcher')
    return {'ok': True}


ACTIONS = {f.__name__: f for f in (watcher_prepare, watcher_check, watcher_enable)}
if __name__ == '__main__':
    sys.exit(main())
