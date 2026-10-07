"""Root-side registration actions appended to the existing SSH RPC helpers."""


def registered_node():
    import re
    data=json.loads(Path('/var/lib/amazon/ssm/registration').read_text())
    def values(value):
        if isinstance(value,dict):
            for item in value.values():yield from values(item)
        elif isinstance(value,list):
            for item in value:yield from values(item)
        elif isinstance(value,str):yield value
    nodes={v for v in values(data) if re.fullmatch(r'mi-[0-9a-f]{17}',v)}
    if len(nodes)!=1:
        raise RuntimeError('Agent registration ID missing; inspect SSM registration without exposing its contents')
    return nodes.pop()


def ssm_guard(p):
    guard=Path('/etc/systemd/system/k3s.service.d/10-dr-start-guard.conf')
    if not guard.is_file() or 'ExecStartPre=/usr/bin/test -f /run/issues-k3s-start-approved' not in guard.read_text():
        raise RuntimeError('K3s restart guard missing; do not arm automatic failover')
    if not Path('/run/issues-k3s-start-approved').exists() or run(['systemctl','is-active','k3s'],optional=True).returncode:
        raise RuntimeError('K3s is stopped/guarded; baseline must be restored before arming')
    if not (ROOT/'automation-result.json').exists():
        raise RuntimeError('Initial replication has not been verified')
    return {'ok':True}


def ssm_status(p):
    import re
    marker=ROOT/'ssm-dr.json'
    if not marker.exists():
        if Path('/var/lib/amazon/ssm/registration').exists():
            raise RuntimeError('Existing SSM registration is unmanaged; explicit adoption required')
        return {'node':None}
    result=json.loads(marker.read_text())
    if result.get('region')!='us-east-1' or not re.fullmatch(r'mi-[0-9a-f]{17}',result.get('node','')):
        raise RuntimeError('Invalid SSM registration marker')
    if not Path('/var/lib/amazon/ssm/registration').exists():
        raise RuntimeError('SSM registration was lost; restore identity instead of registering again')
    if registered_node()!=result['node']:
        raise RuntimeError('SSM identity differs from the ownership marker; registration stopped')
    run(['systemctl','enable','--now','amazon-ssm-agent'])
    return result


def ssm_register(p):
    if (ROOT/'ssm-dr.json').exists() or Path('/var/lib/amazon/ssm/registration').exists():
        raise RuntimeError('Refusing to overwrite an existing agent registration')
    if Path('/snap/amazon-ssm-agent').exists():
        raise RuntimeError('An SSM snap installation already exists; migrate it explicitly before hybrid registration')
    if not (ROOT/'automation-result.json').exists():
        raise RuntimeError('Initial replication must be verified before registering failover control')
    if run(['systemctl','is-active','k3s'],optional=True).returncode:
        raise RuntimeError('K3s is guarded; do not enable failover control')
    run(['curl','--fail','--show-error','--location','--retry','3','https://s3.us-east-1.amazonaws.com/amazon-ssm-us-east-1/latest/debian_amd64/amazon-ssm-agent.deb','-o','/tmp/dcs29-ssm-agent.deb'])
    run(['dpkg','-i','/tmp/dcs29-ssm-agent.deb'])
    run(['systemctl','stop','amazon-ssm-agent'])
    run(['/usr/bin/amazon-ssm-agent','-register','-code',p['activation_code'],'-id',p['activation_id'],'-region','us-east-1'])
    node=registered_node()
    write(ROOT/'ssm-dr.json',json.dumps({'node':node,'region':'us-east-1'}))
    run(['systemctl','enable','--now','amazon-ssm-agent'])
    return {'ok':True}


ACTIONS={f.__name__:f for f in (ssm_status,ssm_register,ssm_guard,status,api)}
if __name__=='__main__':
    sys.exit(main())
