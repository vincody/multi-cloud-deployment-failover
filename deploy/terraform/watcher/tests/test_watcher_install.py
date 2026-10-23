"""Offline contracts for installing the watcher."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent / 'automation')]
spec = importlib.util.spec_from_file_location('watcher_deploy', HERE / 'deploy.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

KEY = Path(tempfile.gettempdir()) / 'dcs29-test-key'
BASE = {'aws_host': '203.0.113.10', 'azure_host': '203.0.113.20', 'azure_user': 'azureuser', 'private_key': str(KEY),
        'image_ref': 'ghcr.io/x/y@sha256:' + 'a' * 64, 'postgres_ref': 'postgres@sha256:' + 'b' * 64,
        'aws_region': 'ap-southeast-1', 'azure_region': 'southeastasia', 'aws_instance_id': 'i-0123456789abcdef0',
        'health_check_id': '11111111-2222-3333-4444-555555555555', 'aws_origin_host': 'aws-origin.example.com',
        'azure_origin_host': 'azure-origin.example.com', 'threshold': 3, 'interval': 5}


class Installer(unittest.TestCase):
    def setUp(self):
        KEY.write_text('fixture')
        self.addCleanup(KEY.unlink)
        patcher = patch.object(installer.workloads.shutil, 'which', return_value='/ssh')
        patcher.start()
        self.addCleanup(patcher.stop)

    def host(self, status):
        host = Mock()
        host.call.side_effect = lambda action, payload=None: status if action == 'status' else {'ok': True}
        return host

    def test_rejects_bad_values(self):
        for change in ({'threshold': 1}, {'interval': 1}, {'aws_instance_id': 'nope'}, {'health_check_id': 'x'}, {'aws_origin_host': 'a b'}):
            with self.assertRaises(installer.workloads.DeploymentError, msg=str(change)):
                installer.validate(BASE | change)

    def test_installs_only_on_a_healthy_standby_and_checks_before_enabling(self):
        host = self.host({'writable': False, 'role': 'standby', 'replication_connected': True})
        installer.deploy(BASE, host_factory=lambda *a: host)
        self.assertEqual([c.args[0] for c in host.call.call_args_list], ['status', 'watcher_prepare', 'watcher_check', 'watcher_enable'])

    def test_refuses_when_azure_is_not_a_streaming_standby(self):
        host = self.host({'writable': True, 'role': 'primary', 'replication_connected': False})
        with self.assertRaises(installer.workloads.DeploymentError):
            installer.deploy(BASE, host_factory=lambda *a: host)
        self.assertEqual([c.args[0] for c in host.call.call_args_list], ['status'])

    def test_credential_file_must_hold_exactly_the_access_key_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'creds'
            path.write_text('[default]\naws_access_key_id = AKIAEXAMPLE\naws_secret_access_key = secret\naws_session_token = t\n')
            with self.assertRaises(installer.workloads.DeploymentError):
                installer.credentials(str(path))
            path.write_text('[default]\naws_access_key_id = AKIAEXAMPLE\naws_secret_access_key = secret\n')
            self.assertEqual(set(installer.credentials(str(path))), {'aws_access_key_id', 'aws_secret_access_key'})

    def test_bundle_ships_the_shared_decision_core(self):
        self.assertEqual(set(installer.bundle()), {'cloud_watcher.py', 'promote.py', 'watcher_core.py'})


if __name__ == '__main__':
    unittest.main()
