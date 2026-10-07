"""Offline safety contracts. Requires boto3 in the development environment, never AWS credentials."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

import boto3
from botocore.stub import Stubber

HERE=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
handler=load('dr_handler',HERE/'lambda'/'handler.py')
with patch.dict(sys.modules,{'fcntl':SimpleNamespace()}):
    command=load('dr_azure',HERE/'lambda'/'azure_command.py')
OWNER='arn:aws:states:us-east-1:123456789012:execution:dcs29-auto-failover:incident'


class Tests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'PRIMARY_REGION':'ap-southeast-1','PRIMARY_INSTANCE':'i-0123456789abcdef0','LOCK_TABLE':'dcs29-dr-latch','EXECUTION_PREFIX':OWNER.rsplit(':',1)[0]+':','ALARM_NAME':'dcs29-auto-failover','AWS_ORIGIN':'https://aws.example.com','AZURE_ORIGIN':'https://azure.example.com','NODE_PARAMETER':'/dcs29/dr/azure-node'})
        self.env.start();self.addCleanup(self.env.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'automation-result.json').write_text('{}')
    def event(self,action,**kw):return {'action':action,'owner':OWNER,**kw}
    def test_duplicate_incident_is_noop_using_real_sdk_validation(self):
        ddb=boto3.client('dynamodb',region_name='us-east-1',aws_access_key_id='fixture',aws_secret_access_key='fixture')
        with Stubber(ddb) as stub:
            stub.add_client_error('put_item',service_error_code='ConditionalCheckFailedException')
            stub.add_response('get_item',{'Item':{'owner':{'S':OWNER+'-previous'}}})
            with patch.object(handler,'client',return_value=ddb):
                self.assertTrue(handler.handler(self.event('acquire'),None)['duplicate'])
            stub.assert_no_pending_responses()
    def test_same_execution_can_resume_ambiguous_latch_acquisition(self):
        ddb=boto3.client('dynamodb',region_name='us-east-1',aws_access_key_id='fixture',aws_secret_access_key='fixture')
        with Stubber(ddb) as stub:
            stub.add_client_error('put_item',service_error_code='ConditionalCheckFailedException')
            stub.add_response('get_item',{'Item':{'owner':{'S':OWNER}}})
            with patch.object(handler,'client',return_value=ddb):
                self.assertFalse(handler.handler(self.event('acquire'),None)['duplicate'])
            stub.assert_no_pending_responses()
    def test_healthy_aws_blocks_fencing(self):
        cw=Mock();cw.describe_alarms.return_value={'MetricAlarms':[{'StateValue':'ALARM'}]}
        with patch.object(handler,'client',return_value=cw),patch.object(handler,'probe',return_value=200),self.assertRaisesRegex(RuntimeError,'recovered'):
            handler.confirm_failure()
    def test_timeout_is_failure_but_dns_error_is_not(self):
        cw=Mock();cw.describe_alarms.return_value={'MetricAlarms':[{'StateValue':'ALARM'}]}
        with patch.object(handler,'client',return_value=cw),patch.object(handler,'probe',side_effect=TimeoutError()):handler.confirm_failure()
        import socket
        with patch.object(handler,'client',return_value=cw),patch.object(handler,'probe',side_effect=urllib.error.URLError(socket.gaierror('bad DNS'))),self.assertRaisesRegex(RuntimeError,'DNS/TLS'):
            handler.confirm_failure()
    def test_promotion_not_sent_until_ec2_stopped(self):
        ssm=Mock();ssm.describe_instance_information.return_value={'InstanceInformationList':[{'PingStatus':'Online'}]}
        with patch.object(handler,'client',return_value=ssm),patch.object(handler,'azure_node',return_value='mi-0123456789abcdef0'),patch.object(handler,'aws_state',return_value='stopping'),self.assertRaisesRegex(RuntimeError,'fencing'):
            handler.send('promote',OWNER)
        ssm.send_command.assert_not_called()
    def test_fence_poll_is_bounded(self):
        with patch.object(handler,'latch'),patch.object(handler,'aws_state',return_value='stopping'):
            self.assertFalse(handler.handler(self.event('fence_status',polls=2),None)['ready'])
            with self.assertRaisesRegex(RuntimeError,'timed out'):handler.handler(self.event('fence_status',polls=40),None)
    def test_failed_or_malformed_ssm_result_blocks_next_phase(self):
        ssm=Mock();ssm.get_command_invocation.return_value={'Status':'Success','StandardOutputContent':'not a verified database result'}
        with patch.object(handler,'latch'),patch.object(handler,'client',return_value=ssm),self.assertRaisesRegex(RuntimeError,'role verification'):
            handler.handler(self.event('command_status',command_id='fixture',node='mi-fixture',phase='preflight',polls=0),None)
    def test_unowned_primary_refuses_promotion(self):
        def sql(q):return '170001' if q.startswith('SHOW') else 'f'
        with patch.object(command,'ROOT',self.root),patch.object(command,'sql',side_effect=sql),patch.object(command.subprocess,'run',return_value=SimpleNamespace(returncode=0)),self.assertRaisesRegex(RuntimeError,'unowned'):
            command.execute('promote',OWNER)
    def test_receipt_precedes_promotion_and_retry_reuses_owner(self):
        queries=[];promoted=False
        def sql(q):
            nonlocal promoted
            queries.append(q)
            if q.startswith('SHOW'):return '170001'
            if q=='SELECT pg_is_in_recovery()':return 'f' if promoted else 't'
            if 'pg_is_wal_replay_paused' in q:return 'f'
            if 'pg_promote' in q:
                self.assertEqual(json.loads((self.root/'failover-incident.json').read_text())['owner'],OWNER)
                promoted=True;return 't'
            return 't'
        with patch.object(command,'ROOT',self.root),patch.object(command,'sql',side_effect=sql),patch.object(command.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
            command.execute('promote',OWNER);command.execute('promote',OWNER)
        self.assertEqual(sum('pg_promote' in q for q in queries),1)
    def test_replay_paused_refuses_before_receipt(self):
        def sql(q):return '170001' if q.startswith('SHOW') else 't'
        with patch.object(command,'ROOT',self.root),patch.object(command,'sql',side_effect=sql),patch.object(command.subprocess,'run',return_value=SimpleNamespace(returncode=0)),self.assertRaisesRegex(RuntimeError,'paused'):
            command.execute('promote',OWNER)
        self.assertFalse((self.root/'failover-incident.json').exists())
    def test_workflow_paths_and_no_mutation_retry(self):
        workflow=json.loads((HERE/'workflow.json.tmpl').read_text().replace('${lambda_arn}','arn:fixture'))
        states=workflow['States']
        self.assertEqual(states['Fenced']['Choices'][0]['Next'],'PromoteAzure')
        self.assertEqual(states['Fenced']['Default'],'FenceDelay')
        for name in ['AzurePreflight','PromoteAzure','RequestFence']:
            self.assertNotIn('Retry',states[name])
        for name,value in states.items():
            if 'Next' in value:self.assertIn(value['Next'],states)
            if 'Default' in value:self.assertIn(value['Default'],states)
        policy=(HERE.parent/'failover.tf').read_text()
        self.assertNotIn('dynamodb:DeleteItem',policy)


if __name__=='__main__':unittest.main()
