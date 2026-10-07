"""Offline contracts for ordering, role guards and durable retry IDs."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy, remote = load('deploy'), load('remote')


class FakeHost:
    events = []
    promoted = False

    def __init__(self, site, config, key):
        self.site = site

    def wait(self):
        self.events.append((self.site, 'wait'))

    def call(self, action, payload=None):
        self.events.append((self.site, action))
        if action == 'preflight' and self.promoted and self.site == 'azure':
            raise deploy.DeploymentError('Database roles have changed')
        if action == 'prepare': return {'public_key': 'A' * 43 + '='}
        if action == 'credentials': return {'values': {'secret': 'must-not-be-printed'}}
        if action == 'status': return {'writable': self.site == 'aws', 'role': 'primary' if self.site == 'aws' else 'standby', 'replication_connected': True}
        if action == 'sample': return {'id': 'demo-id'}
        if action == 'api' and payload['path'] == '/version': return {'environment': self.site, 'commit_sha': 'fixture'}
        if action in ('api','http_probe'): return {'issue': {'id': 'demo-id'}, 'history': [], 'comments': []}
        return {'ok': True}


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        key = Path(self.temp.name) / 'key'; key.write_text('fixture')
        self.config = {'aws_host': '192.0.2.1', 'azure_host': '192.0.2.2', 'azure_user': 'azureuser', 'private_key': str(key), 'image_ref': 'ghcr.io/example/app@sha256:' + 'a' * 64, 'postgres_ref': 'postgres@sha256:' + 'b' * 64, 'aws_region': 'ap-southeast-1', 'azure_region': 'indiasouthcentral', 'standby_generation': 'initial'}
        FakeHost.events = []; FakeHost.promoted = False

    def test_primary_schema_precedes_standby_and_replication_check(self):
        output = io.StringIO()
        with patch.object(deploy.shutil, 'which', return_value='/ssh'), contextlib.redirect_stdout(output):
            result = deploy.deploy(self.config, FakeHost)
        events = FakeHost.events
        self.assertLess(events.index(('aws', 'app_bootstrap')), events.index(('azure', 'database_wait')))
        self.assertNotIn(('azure', 'app_bootstrap'), events)
        self.assertLess(events.index(('azure', 'app_wait')), events.index(('aws', 'sample')))
        self.assertLess(events.index(('aws', 'sample')), events.index(('azure', 'http_probe')))
        self.assertNotIn('must-not-be-printed', output.getvalue())
        self.assertEqual(result['azure_pvc'], 'postgres-data-standby-initial')

    def test_promoted_azure_aborts_before_any_mutation(self):
        FakeHost.promoted = True
        with patch.object(deploy.shutil, 'which', return_value='/ssh'), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(deploy.DeploymentError):
            deploy.deploy(self.config, FakeHost)
        self.assertFalse(any(action in ('prepare','apply','credentials') for _,action in FakeHost.events))

    def test_templates_keep_clone_fence_and_new_pvc_generation(self):
        self.config['standby_generation'] = 'retry1'
        text = deploy.render('postgres-azure', self.config, 'azure')
        self.assertIn('postgres-data-standby-retry1', text)
        self.assertIn('.clone-complete', text)
        self.assertNotIn('__STANDBY_GENERATION__', text)
        self.assertIn('Incomplete clone: refuse startup', text)

    def test_sample_persists_request_before_post_and_retry_reuses_it(self):
        seen = []
        def api(payload):
            saved = json.loads((Path(self.temp.name) / 'automation-check.json').read_text())
            self.assertEqual(saved, payload['body'])
            seen.append(payload['body']['request_id'])
            return {'id':'demo-id'}
        with patch.object(remote, 'ROOT', Path(self.temp.name)), patch.object(remote, 'api', side_effect=api):
            remote.sample({}); remote.sample({})
        self.assertEqual(seen[0], seen[1])

    def test_missing_or_invalid_key_and_image_fail_locally(self):
        self.config['private_key'] = '/does/not/exist'
        with self.assertRaises(deploy.DeploymentError): deploy.validate(self.config)
        self.config['image_ref'] = 'ghcr.io/example/app:main'
        with self.assertRaises(deploy.DeploymentError): deploy.validate(self.config)

    def test_remote_preflight_refuses_promoted_or_paused_database(self):
        resource = {'metadata': {'labels': {'app.kubernetes.io/managed-by': remote.OWNER}}, 'spec': {'template': {'spec': {'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'postgres-data'}}]}}}}
        def result(text='', code=0):
            return SimpleNamespace(stdout=text, returncode=code)
        with patch.object(remote, 'run', return_value=result()), patch.object(remote, 'get_resource', return_value=resource):
            with patch.object(remote, 'kubectl', return_value=result('f')), self.assertRaisesRegex(RuntimeError, 'roles have changed'):
                remote.preflight({'site': 'azure', 'pvc': 'postgres-data'})
            with patch.object(remote, 'kubectl', side_effect=[result('f'), result('app_control'), result('f')]), self.assertRaisesRegex(RuntimeError, 'Writes are paused'):
                remote.preflight({'site': 'aws', 'pvc': 'postgres-data'})

    def test_verified_offline_standby_cannot_be_replaced_by_initial_retry(self):
        resource = {'metadata': {'labels': {'app.kubernetes.io/managed-by': remote.OWNER}}, 'spec': {'template': {'spec': {'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'postgres-data-standby-initial'}}]}}}}
        (Path(self.temp.name) / 'automation-result.json').write_text('{}')
        with patch.object(remote, 'ROOT', Path(self.temp.name)), patch.object(remote, 'run', return_value=SimpleNamespace(returncode=0)), patch.object(remote, 'get_resource', return_value=resource), patch.object(remote, 'kubectl', return_value=SimpleNamespace(returncode=1, stdout='')), self.assertRaisesRegex(RuntimeError, 'DR recovery runbook'):
            remote.preflight({'site': 'azure', 'pvc': 'postgres-data-standby-retry1'})


if __name__ == '__main__':
    unittest.main()
