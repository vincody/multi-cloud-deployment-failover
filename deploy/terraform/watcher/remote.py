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


UNGUARD_UNIT = """[Unit]
Description=DCS29 release the K3s start guard on Azure when it is safe
After=network-online.target wg-quick@wg0.service
Wants=network-online.target

[Service]
Type=simple
Environment=AWS_SHARED_CREDENTIALS_FILE=/etc/dcs29/watcher-aws-credentials AWS_PROFILE=default
ExecStart=/usr/bin/python3 /opt/dcs29/watcher/unguard.py
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
"""


def unguard_enable(p):
    """Azure only: enabled for the next boot. Not started now, K3s is already running."""
    if not (ROOT / 'automation-result.json').exists():
        raise RuntimeError('Initial replication has not been verified')
    if not (WATCHER_DIR / 'unguard.py').is_file():
        raise RuntimeError('unguard.py was not installed; run the watcher installation first')
    write('/etc/systemd/system/dcs29-unguard.service', UNGUARD_UNIT, mode=0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'dcs29-unguard'])
    return {'ok': True}


RECOVERY_KEY = Path('/etc/dcs29/recovery_ed25519')
RECOVERY_KEY_PATTERN = r'ssh-ed25519 [A-Za-z0-9+/]{40,}={0,2} dcs29-recovery'


def recovery_keygen(p):
    """Azure: create the key the controller uses to reach the AWS recovery RPC. The private half never leaves."""
    if not RECOVERY_KEY.exists():
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'dcs29-recovery', '-f', str(RECOVERY_KEY)])
    return {'public_key': Path(str(RECOVERY_KEY) + '.pub').read_text().strip()}


def recovery_install(p):
    """AWS: install the recovery RPC and authorize only the Azure key, bound to the tunnel address and one command."""
    import pwd
    if not (ROOT / 'automation-result.json').exists():
        raise RuntimeError('Initial replication has not been verified')
    if not re.fullmatch(RECOVERY_KEY_PATTERN, p['public_key']):
        raise RuntimeError('Invalid recovery public key')
    for name, text in p['files'].items():
        write(WATCHER_DIR / name, text, mode=0o700)
    entry = pwd.getpwnam('ubuntu')
    folder = Path(entry.pw_dir) / '.ssh'
    folder.mkdir(mode=0o700, exist_ok=True)
    keys = folder / 'authorized_keys'
    kept = [line for line in (keys.read_text().splitlines() if keys.exists() else []) if 'dcs29-recovery' not in line]
    kept.append('from="10.200.29.2",command="/usr/bin/sudo -n /usr/bin/python3 /opt/dcs29/watcher/rpc.py",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ' + p['public_key'])
    write(keys, '\n'.join(kept) + '\n')
    for path in (folder, keys):
        os.chown(path, entry.pw_uid, entry.pw_gid)
    ufw = run(['sh', '-c', 'command -v ufw'], optional=True)
    if not ufw.returncode and 'Status: active' in run(['ufw', 'status']).stdout:
        run(['ufw', 'allow', 'in', 'on', 'wg0', 'from', '10.200.29.2', 'to', '10.200.29.1', 'port', '22', 'proto', 'tcp'])
    return {'ok': True}


ACTIONS = {f.__name__: f for f in (watcher_prepare, watcher_check, watcher_enable, recovery_keygen, recovery_install, unguard_enable)}
if __name__ == '__main__':
    sys.exit(main())
