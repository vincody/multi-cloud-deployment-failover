"""Offline HTTPS contracts; no cloud or ACME requests."""
import base64
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('https_deployer', HERE / 'deploy.py')
https = importlib.util.module_from_spec(spec); spec.loader.exec_module(https)
remote = {'__name__': 'https_rpc_library'}
exec(base64.b64decode(https.Host.remote_script), remote)


class FakeHost:
    events = []
    fail = None
    def __init__(self, site, config, key):self.site = site
    def wait(self):pass
    def call(self, action, payload=None):
        self.events.append((self.site, action))
        if (self.site, action) == self.fail:
            raise https.workloads.DeploymentError('fixture failed')
        return {'ok': True}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        key = Path(self.temp.name) / 'key'; key.write_text('fixture')
        self.config = {'aws_host': '192.0.2.1', 'azure_host': '192.0.2.2', 'azure_user': 'azureuser', 'private_key': str(key), 'image_ref': 'ghcr.io/example/app@sha256:'+'a'*64, 'postgres_ref': 'postgres@sha256:'+'b'*64, 'aws_region': 'ap-southeast-1', 'azure_region': 'indiasouthcentral', 'domain': 'example.com', 'email': 'operator@example.com', 'nameservers': ['ns1','ns2','ns3','ns4']}
        FakeHost.events=[]; FakeHost.fail=None
    def execute(self):
        with patch.object(https.workloads.shutil, 'which', return_value='/ssh'), contextlib.redirect_stdout(io.StringIO()):
            https.deploy(self.config, FakeHost)
    def test_dns_preflight_and_pause_both_before_sequential_issuance(self):
        self.execute();e=FakeHost.events
        self.assertLess(e.index(('azure','https_prepare')),e.index(('aws','https_apply')))
        self.assertLess(e.index(('azure','https_pause')),e.index(('aws','https_apply')))
        self.assertLess(e.index(('aws','https_verify')),e.index(('azure','https_apply')))
    def test_renewal_is_restored_on_both_after_failure(self):
        FakeHost.fail=('aws','https_apply')
        with self.assertRaises(https.workloads.DeploymentError):self.execute()
        self.assertIn(('azure','https_schedule'),FakeHost.events)
        self.assertNotIn(('azure','https_apply'),FakeHost.events)
    def test_dns_failure_stops_before_acme(self):
        FakeHost.fail=('azure','https_prepare')
        with self.assertRaises(https.workloads.DeploymentError):self.execute()
        self.assertFalse(any(a=='https_apply' for _,a in FakeHost.events))
    def test_remote_refuses_incorrect_public_delegation(self):
        commands=[]
        def run(args,**kwargs):
            commands.append(args)
            return SimpleNamespace(returncode=0,stdout='wrong-nameserver.example\n' if args[0]=='dig' else '')
        with patch.dict(remote, {'run':run,'kubectl':lambda *a,**kw:None}), self.assertRaisesRegex(RuntimeError,'delegation is not ready'):
            remote['https_prepare']({'site':'aws','domain':'example.com','nameservers':['ns1','ns2','ns3','ns4'],'public_ip':'192.0.2.1'})
        self.assertFalse(any(a[0]=='certbot' for a in commands))
    def test_credentials_are_read_from_external_file_and_tokens_rejected(self):
        p=Path(self.temp.name)/'credentials';p.write_text('[default]\naws_access_key_id=FIXTURE\naws_secret_access_key=fixtureSecret\n')
        self.assertEqual(https.credentials(str(p))['aws_secret_access_key'],'fixtureSecret')
        p.write_text(p.read_text()+'aws_session_token=temporary\n')
        with self.assertRaises(https.workloads.DeploymentError):https.credentials(str(p))
    def test_ingress_uses_both_san_hosts_tls_and_host_specific_redirect(self):
        docs=remote['ingress']({'site':'azure','domain':'example.com'})['items']
        tls=docs[1]['spec']['tls'][0]
        self.assertEqual(tls['hosts'],['azure-origin.example.com','app.example.com'])
        self.assertEqual(tls['secretName'],'issues-tls')
        self.assertTrue(docs[0]['spec']['redirectScheme']['permanent'])
        self.assertEqual(len(docs[2]['spec']['rules']),2)
    def test_renewal_schedules_no_catchup_and_bounded_runtime(self):
        writes={}
        with patch.dict(remote, {'write':lambda path,text,**kw:writes.update({path:text}), 'run':lambda *a,**kw:SimpleNamespace(returncode=0,stdout='')}):
            remote['https_schedule']({'site':'azure'})
        text=writes['/etc/systemd/system/certbot.timer.d/10-dcs29-schedule.conf']
        self.assertIn('06,18:00:00 UTC',text);self.assertIn('Persistent=false',text)
        self.assertIn('TimeoutStartSec=45min',writes['/etc/systemd/system/certbot.service.d/10-dcs29-renewal.conf'])
    def test_ssh_transports_script_and_secret_on_stdin_not_command(self):
        host=https.Host('azure',self.config,self.config['private_key'])
        with patch.object(https.workloads.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"ok":true,"result":{}}')) as command:
            host.call('https_prepare',{'credentials':{'aws_secret_access_key':'hidden'}})
        args=command.call_args
        self.assertNotIn('hidden',str(args.args));self.assertIn('hidden',args.kwargs['input'])
        self.assertLess(len(args.args[0][-1]),1000)
    def test_actual_stdin_bootloader_dispatches_the_combined_rpc(self):
        captured=[]
        real_run=subprocess.run
        def local(args,**kwargs):
            # Execute only the Python bootloader locally, with an unknown action;
            # this exercises decoding/dispatch without any root/system mutations.
            code=args[-1].removeprefix('sudo -n python3 -c "').removesuffix('"')
            result=real_run([sys.executable,'-c',code],**kwargs)
            captured.append(json.loads(result.stdout))
            return result
        host=https.Host('azure',self.config,self.config['private_key'])
        with patch.object(https.workloads.subprocess,'run',side_effect=local), self.assertRaises(https.workloads.DeploymentError):
            host.call('unknown_action')
        self.assertEqual(captured[0]['error'],'KeyError: operation failed')


if __name__=='__main__':unittest.main()
