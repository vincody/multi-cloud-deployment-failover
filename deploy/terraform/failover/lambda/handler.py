"""One bounded action per Step Functions task. Permanent incident latch; fail closed."""
import base64
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

CFG = Config(connect_timeout=4, read_timeout=8, retries={'max_attempts': 2})


def client(service, region=None):
    # No transparent retry of SendCommand after an ambiguous network response.
    config = Config(connect_timeout=4, read_timeout=8, retries={'max_attempts': 0}) if service == 'ssm' else CFG
    return boto3.client(service, region_name=region or 'us-east-1', config=config)


def aws_state():
    instances = client('ec2', os.environ['PRIMARY_REGION']).describe_instances(InstanceIds=[os.environ['PRIMARY_INSTANCE']])['Reservations']
    return instances[0]['Instances'][0]['State']['Name']


def latch(owner):
    item = client('dynamodb').get_item(TableName=os.environ['LOCK_TABLE'], Key={'id': {'S': 'writer'}}, ConsistentRead=True).get('Item', {})
    if item.get('owner', {}).get('S') != owner:
        raise RuntimeError('Incident ownership lost; operation stopped')


def probe(path):
    with urllib.request.urlopen(os.environ['AWS_ORIGIN'] + path, timeout=6) as response:
        return response.status


def confirm_failure():
    alarms = client('cloudwatch').describe_alarms(AlarmNames=[os.environ['ALARM_NAME']])['MetricAlarms']
    if not alarms or alarms[0]['StateValue'] != 'ALARM':
        raise RuntimeError('Alarm recovered or unavailable; do not fence')
    try:
        status = probe('/health/database')
        if 200 <= status < 300:
            raise RuntimeError('AWS writer recovered; do not fence')
        if status < 500:
            raise RuntimeError('Unexpected endpoint response; do not fence')
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            raise RuntimeError('Unexpected client/auth error; operator investigation required') from None
    except urllib.error.URLError as exc:
        # DNS/TLS errors alone are not proof of a failed writer. Only transport timeout/refusal qualifies.
        import socket, ssl
        if isinstance(exc.reason, (ssl.SSLError, socket.gaierror)):
            raise RuntimeError('DNS/TLS verification failed; do not fence') from None
        if not isinstance(exc.reason, OSError):
            raise RuntimeError('Unclassified endpoint error; do not fence') from None
    except (TimeoutError, ConnectionError):
        pass


def azure_node():
    node = client('ssm').get_parameter(Name=os.environ['NODE_PARAMETER'])['Parameter']['Value']
    if not node.startswith('mi-'):
        raise RuntimeError('Invalid Azure SSM node')
    return node


def send(phase, owner):
    node = azure_node()
    info = client('ssm').describe_instance_information(Filters=[{'Key': 'InstanceIds', 'Values': [node]}])['InstanceInformationList']
    if not info or info[0]['PingStatus'] != 'Online':
        raise RuntimeError('Azure SSM node is offline')
    if phase == 'promote' and aws_state() != 'stopped':
        raise RuntimeError('AWS fencing is not confirmed; no promote')
    code = (Path(__file__).parent / 'azure_command.py').read_text() + '\nmain(' + repr(phase) + ',' + repr(owner) + ')\n'
    encoded = base64.b64encode(code.encode()).decode()
    command = 'python3 -c "import base64;exec(base64.b64decode(\'' + encoded + '\'))"'
    response = client('ssm').send_command(InstanceIds=[node], DocumentName='AWS-RunShellScript', Parameters={'commands': [command], 'executionTimeout': ['180']}, TimeoutSeconds=60, Comment='DCS29 ' + phase)
    return {'command_id': response['Command']['CommandId'], 'node': node, 'phase': phase, 'polls': 0}


def handler(event, context):
    action, owner = event['action'], event['owner']
    if not owner.startswith(os.environ['EXECUTION_PREFIX']):
        raise RuntimeError('Invalid execution owner')
    if action == 'acquire':
        try:
            client('dynamodb').put_item(TableName=os.environ['LOCK_TABLE'], Item={'id': {'S': 'writer'}, 'owner': {'S': owner}, 'state': {'S': 'IN_PROGRESS'}}, ConditionExpression='attribute_not_exists(id)')
        except ClientError as exc:
            if exc.response['Error']['Code'] == 'ConditionalCheckFailedException':
                item = client('dynamodb').get_item(TableName=os.environ['LOCK_TABLE'], Key={'id': {'S': 'writer'}}, ConsistentRead=True).get('Item', {})
                return {'duplicate': item.get('owner', {}).get('S') != owner}
            raise
        return {'duplicate': False}
    latch(owner)
    if action == 'confirm':
        confirm_failure(); return {'confirmed': True}
    if action == 'preflight':
        return send('preflight', owner)
    if action == 'fence':
        confirm_failure()
        state = aws_state()
        if state not in ('running', 'pending', 'stopping', 'stopped'):
            raise RuntimeError('Unexpected EC2 state; fencing not proven')
        if state in ('running', 'pending'):
            client('ec2', os.environ['PRIMARY_REGION']).stop_instances(InstanceIds=[os.environ['PRIMARY_INSTANCE']])
        return {'polls': 0}
    if action == 'fence_status':
        count = event.get('polls', 0) + 1
        if count > 40:
            raise RuntimeError('Fencing timed out; do not promote')
        return {'ready': aws_state() == 'stopped', 'polls': count}
    if action == 'promote':
        return send('promote', owner)
    if action == 'command_status':
        count = event.get('polls', 0) + 1
        if count > 30:
            raise RuntimeError('SSM result timed out; do not blindly issue another command')
        try:
            result = client('ssm').get_command_invocation(CommandId=event['command_id'], InstanceId=event['node'])
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'InvocationDoesNotExist':raise
            result = {'Status': 'Pending'}
        status = result['Status']
        if status not in ('Pending', 'InProgress', 'Delayed', 'Success'):
            raise RuntimeError('Azure command failed; inspect SSM command output')
        ready = status == 'Success'
        if ready:
            lines = [s for s in result.get('StandardOutputContent', '').splitlines() if s.startswith('DCS29_RESULT=')]
            expected = 'standby' if event['phase'] == 'preflight' else 'primary'
            if len(lines) != 1 or json.loads(lines[0][13:]).get('role') != expected:
                raise RuntimeError('Azure role verification failed')
        return {**event, 'ready': ready, 'polls': count}
    if action == 'verify':
        if aws_state() != 'stopped':raise RuntimeError('AWS restarted; operator intervention required')
        with urllib.request.urlopen(os.environ['AZURE_ORIGIN'] + '/api/database/status', timeout=8) as response:
            status = json.load(response)
        if status.get('role') != 'primary' or not status.get('writable'):
            raise RuntimeError('Azure writer is not ready')
        return {'verified': True}
    if action == 'complete':
        client('dynamodb').update_item(TableName=os.environ['LOCK_TABLE'], Key={'id': {'S': 'writer'}}, UpdateExpression='SET #s=:s', ConditionExpression='#o=:o', ExpressionAttributeNames={'#s': 'state', '#o': 'owner'}, ExpressionAttributeValues={':s': {'S': 'COMPLETED'}, ':o': {'S': owner}})
        return {'completed': True, 'dns': 'Route53 follows writer health; cache expiry still applies'}
    raise RuntimeError('Unknown action')
