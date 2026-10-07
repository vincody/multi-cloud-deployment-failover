"""HTTPS actions appended to the existing root RPC helpers."""
import time


def cert_name(p):
    if p['site'] not in ('aws', 'azure'):
        raise RuntimeError('Invalid certificate site')
    return 'dcs29-' + p['site']


def https_prepare(p):
    cert_name(p)
    if run(['systemctl', 'is-active', 'k3s'], optional=True).returncode:
        raise RuntimeError('K3s is guarded/stopped; confirm writer state before HTTPS setup')
    kubectl('-n', 'issues', 'rollout', 'status', 'deployment/issues-app', '--timeout=180s')
    kubectl('get', 'crd', 'middlewares.traefik.io')
    run(['apt-get', '-o', 'Acquire::Retries=5', 'update'])
    run(['apt-get', '-o', 'Acquire::Retries=5', 'install', '-y', 'certbot', 'python3-certbot-dns-route53', 'dnsutils', 'openssl'])
    expected = {v.lower().rstrip('.') for v in p['nameservers']}
    for resolver in ('1.1.1.1', '8.8.8.8'):
        found = {v.lower().rstrip('.') for v in run(['dig', '+short', 'NS', p['domain'], '@' + resolver]).stdout.splitlines()}
        if found != expected:
            raise RuntimeError('Public DNS delegation is not ready; update registrar nameservers and wait before retrying')
        origin = p['site'] + '-origin.' + p['domain']
        if p['public_ip'] not in run(['dig', '+short', 'A', origin, '@' + resolver]).stdout.splitlines():
            raise RuntimeError('Origin DNS does not point to this VM')
    if p['site'] == 'azure':
        path = Path('/root/.aws/credentials')
        if p.get('credentials'):
            vals = p['credentials']
            write(path, '[default]\n' + ''.join(f'{k} = {v}\n' for k, v in vals.items()))
        if not path.is_file():
            raise RuntimeError('Azure DNS credentials missing; provide azure_dns_credentials_file outside the repo')
        os.chmod(path, 0o600)
    return {'ok': True}


def https_pause(p):
    run(['systemctl', 'stop', 'certbot.timer'])
    # Wait for an already-running renewal to finish before issuing shared-domain certs.
    deadline = time.monotonic() + 600
    while run(['systemctl', 'is-active', 'certbot.service'], optional=True).returncode == 0:
        if time.monotonic() > deadline:
            raise RuntimeError('Renewal is still running; do not run two DNS-01 challenges for the shared host')
        time.sleep(2)
    return {'ok': True}


def ingress(p):
    hosts = [p['site'] + '-origin.' + p['domain'], 'app.' + p['domain']]
    rule = lambda host: {'host': host, 'http': {'paths': [{'path': '/', 'pathType': 'Prefix', 'backend': {'service': {'name': 'issues-app', 'port': {'number': 80}}}}]}}
    secure = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress', 'metadata': {'name': 'issues-https', 'namespace': 'issues', 'annotations': {'traefik.ingress.kubernetes.io/router.entrypoints': 'websecure', 'traefik.ingress.kubernetes.io/router.tls': 'true'}}, 'spec': {'ingressClassName': 'traefik', 'tls': [{'hosts': hosts, 'secretName': 'issues-tls'}], 'rules': [rule(h) for h in hosts]}}
    redirect = {'apiVersion': 'traefik.io/v1alpha1', 'kind': 'Middleware', 'metadata': {'name': 'https-redirect', 'namespace': 'issues'}, 'spec': {'redirectScheme': {'scheme': 'https', 'permanent': True}}}
    http = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress', 'metadata': {'name': 'issues-http', 'namespace': 'issues', 'annotations': {'traefik.ingress.kubernetes.io/router.entrypoints': 'web', 'traefik.ingress.kubernetes.io/router.middlewares': 'issues-https-redirect@kubernetescrd'}}, 'spec': {'ingressClassName': 'traefik', 'rules': [rule(h) for h in hosts]}}
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [redirect, secure, http]}


def https_apply(p):
    name = cert_name(p)
    folder = '/etc/letsencrypt/live/' + name
    # Certbot keeps a valid matching certificate instead of force-renewing on retry.
    command = ['certbot', 'certonly', '--non-interactive', '--agree-tos', '--dns-route53', '--keep-until-expiring', '--cert-name', name, '--email', p['email'], '-d', 'app.' + p['domain'], '-d', p['site'] + '-origin.' + p['domain']]
    if p['site'] == 'azure':
        command = ['env', 'AWS_PROFILE=default', 'AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials'] + command
    run(command, timeout=900)
    run(['openssl', 'x509', '-in', folder + '/cert.pem', '-noout', '-checkend', '86400'])
    hook = f'''#!/bin/sh
set -eu
test "${{RENEWED_LINEAGE:-}}" = "{folder}" || exit 0
/usr/local/bin/k3s kubectl -n issues create secret tls issues-tls --cert="{folder}/fullchain.pem" --key="{folder}/privkey.pem" --dry-run=client -o json | /usr/local/bin/k3s kubectl apply -f -
'''
    hook_path = '/etc/letsencrypt/renewal-hooks/deploy/20-dcs29-k3s'
    write(hook_path, hook, mode=0o750)
    run(['env', 'RENEWED_LINEAGE=' + folder, hook_path])
    manifest = json.dumps(ingress(p))
    write(ROOT / 'https-ingress.json', manifest, mode=0o640)
    kubectl('apply', '--dry-run=server', '-f', '-', data=manifest)
    kubectl('apply', '-f', '-', data=manifest)
    # Preserve workload automation's ingress artifact as the actual redirect config.
    write(ROOT / 'ingress-http.yaml', json.dumps(ingress(p)['items'][2]), mode=0o640)
    ufw = run(['sh', '-c', 'command -v ufw'], optional=True)
    if not ufw.returncode and 'Status: active' in run(['ufw', 'status']).stdout:
        run(['ufw', 'allow', '443/tcp'])
    return {'ok': True}


def https_verify(p):
    # VM-local routing, real SNI/hostname and public trust chain; never curl -k.
    for hostname in (p['site'] + '-origin.' + p['domain'], 'app.' + p['domain']):
        deadline = time.monotonic() + 120
        while True:
            result = run(['curl', '--silent', '--show-error', '--fail', '--noproxy', '*', '--resolve', hostname + ':443:127.0.0.1', 'https://' + hostname + '/version'], optional=True, timeout=15)
            if result.returncode == 0:
                version = json.loads(result.stdout)
                if version.get('environment') != p['site']:
                    raise RuntimeError('HTTPS routed to the wrong app environment')
                break
            if time.monotonic() > deadline:
                raise RuntimeError('HTTPS SNI/trust/app verification failed; inspect certificate and Ingress')
            time.sleep(2)
        response = run(['curl', '--silent', '--show-error', '--noproxy', '*', '--resolve', hostname + ':80:127.0.0.1', '-o', '/dev/null', '-w', '%{http_code}|%{redirect_url}', 'http://' + hostname + '/version']).stdout
        if response.split('|')[0] not in ('301', '302', '307', '308') or response.split('|')[1] != 'https://' + hostname + '/version':
            raise RuntimeError('HTTP redirect is not configured correctly')
    write(ROOT / 'https-result.json', json.dumps({'site': p['site'], 'domain': p['domain'], 'certificate': cert_name(p), 'verified': True}, indent=2))
    return {'ok': True}


def https_schedule(p):
    hour = '00,12' if p['site'] == 'aws' else '06,18'
    write('/etc/systemd/system/certbot.timer.d/10-dcs29-schedule.conf', f'[Timer]\nOnCalendar=\nOnCalendar=*-*-* {hour}:00:00 UTC\nRandomizedDelaySec=0\nPersistent=false\n')
    # Bounded service duration keeps the normal two schedules well apart.
    text = '[Service]\nTimeoutStartSec=45min\n'
    if p['site'] == 'azure':
        text += 'Environment=AWS_PROFILE=default AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials\n'
    write('/etc/systemd/system/certbot.service.d/10-dcs29-renewal.conf', text)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'certbot.timer'])
    run(['systemctl', 'restart', 'certbot.timer'])
    return {'ok': True}


ACTIONS = {f.__name__: f for f in (https_prepare, https_pause, https_apply, https_verify, https_schedule)}
if __name__ == '__main__':
    sys.exit(main())
