"""Register Azure as an SSM hybrid node; activation secret never enters state."""
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'automation'))
import deploy as workloads


class Host(workloads.Host):
    remote_script = base64.b64encode((HERE.parent / 'automation' / 'remote.py').read_bytes().split(b"if __name__ == '__main__':")[0] + (HERE / 'bootstrap_remote.py').read_bytes()).decode()


def aws(config, *args, optional=False):
    r = subprocess.run(['aws', '--region', 'us-east-1', *args, '--output', 'json', '--no-cli-pager'], capture_output=True, text=True, timeout=60)
    if r.returncode:
        if optional:return None
        raise workloads.DeploymentError('AWS registration API failed; check operator permissions, do not print activation credentials')
    return json.loads(r.stdout) if r.stdout.strip() else {}


def deploy(c):
    if aws(c, 'sts', 'get-caller-identity')['Account'] != c['account_id']:
        raise workloads.DeploymentError('AWS CLI account differs from Terraform; registration stopped')
    key = workloads.validate(c)
    host = Host('azure', c, key)
    primary = Host('aws', c, key)
    for side in (primary, host):
        side.wait()
        side.call('ssm_guard')
    workloads.check_roles(primary, host)
    result = host.call('ssm_status')
    activation = None
    try:
        if not result.get('node'):
            activation = aws(c, 'ssm', 'create-activation', '--iam-role', c['hybrid_role'], '--registration-limit', '1', '--default-instance-name', 'dcs29-azure-dr', '--expiration-date', (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
            host.call('ssm_register', {'activation_id': activation['ActivationId'], 'activation_code': activation['ActivationCode']})
            result = host.call('ssm_status')
        node = result['node']
        if not node.startswith('mi-'):
            raise workloads.DeploymentError('Hybrid registration did not return a managed instance')
        deadline = time.monotonic()+180
        while True:
            info=aws(c,'ssm','describe-instance-information','--filters',json.dumps([{'Key':'InstanceIds','Values':[node]}]))['InstanceInformationList']
            if info and info[0]['PingStatus']=='Online':break
            if time.monotonic()>deadline:raise workloads.DeploymentError('Azure SSM did not become Online')
            time.sleep(5)
        aws(c,'ssm','add-tags-to-resource','--resource-type','ManagedInstance','--resource-id',node,'--tags','Key=Project,Value=dcs29','Key=Purpose,Value=azure-dr')
        aws(c,'ssm','put-parameter','--name',c['node_parameter'],'--type','String','--value',node,'--overwrite')
        print('Azure SSM registration verified; managed node ID stored in the control region.',flush=True)
    finally:
        if activation:
            aws(c,'ssm','delete-activation','--activation-id',activation['ActivationId'])


if __name__=='__main__':
    try:deploy(json.loads(os.environ['DCS29_FAILOVER_CONFIG']))
    except Exception as exc:
        print(str(exc) if isinstance(exc, workloads.DeploymentError) else 'SSM registration stopped; inspect VM/SSM status',file=sys.stderr)
        sys.exit(1)
