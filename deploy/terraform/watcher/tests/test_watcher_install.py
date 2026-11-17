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
        'health_check_id': '11111111-2222-3333-4444-555555555555', 'aws_origin_host': 'aws.example.com',
        'azure_origin_host': 'azure.example.com', 'threshold': 3, 'interval': 5}


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

    def test_bundle_ships_the_shared_decision_core_and_the_recovery_files(self):
        self.assertEqual(set(installer.bundle()), {'cloud_watcher.py', 'promote.py', 'watcher_core.py', 'recovery.py', 'recovery_ops.py', 'rpc.py',
                                                   'unguard.py', 'templates/postgres-standby.yaml.tmpl'})

    def test_auto_start_enables_the_unit_on_azure_after_the_watcher(self):
        host = self.host({'writable': False, 'role': 'standby', 'replication_connected': True})
        installer.deploy(BASE | {'auto_start': True}, host_factory=lambda *a: host)
        actions = [c.args[0] for c in host.call.call_args_list]
        self.assertEqual(actions, ['status', 'watcher_prepare', 'watcher_check', 'watcher_enable', 'unguard_enable'])
        prepared = next(c.args[1] for c in host.call.call_args_list if c.args[0] == 'watcher_prepare')
        self.assertTrue(prepared['config']['auto_start'])

    def test_auto_start_is_off_unless_asked_for(self):
        host = self.host({'writable': False, 'role': 'standby', 'replication_connected': True})
        installer.deploy(BASE, host_factory=lambda *a: host)
        self.assertNotIn('unguard_enable', [c.args[0] for c in host.call.call_args_list])

    def test_aws_only_receives_the_rpc_files(self):
        self.assertEqual(set(installer.recovery_bundle()), {'promote.py', 'recovery_ops.py', 'rpc.py'})

    def test_recovery_installs_the_aws_side_before_the_check(self):
        host = self.host({'writable': False, 'role': 'standby', 'replication_connected': True})
        host.call.side_effect = lambda action, payload=None: (
            {'writable': False, 'role': 'standby', 'replication_connected': True} if action == 'status'
            else {'public_key': 'ssh-ed25519 AAAA dcs29-recovery'} if action == 'recovery_keygen' else {'ok': True})
        installer.deploy(BASE | {'auto_recovery': True, 'auto_failback': True}, host_factory=lambda *a: host)
        actions = [c.args[0] for c in host.call.call_args_list]
        self.assertEqual(actions, ['status', 'watcher_prepare', 'recovery_keygen', 'recovery_install', 'watcher_check', 'watcher_enable'])
        prepared = next(c.args[1] for c in host.call.call_args_list if c.args[0] == 'watcher_prepare')
        self.assertEqual(prepared['config']['recovery'], {'enabled': True, 'failback': True})
        installed = next(c.args[1] for c in host.call.call_args_list if c.args[0] == 'recovery_install')
        self.assertEqual(installed['public_key'], 'ssh-ed25519 AAAA dcs29-recovery')

    def test_without_recovery_nothing_is_installed_on_aws_and_failback_is_ignored(self):
        host = self.host({'writable': False, 'role': 'standby', 'replication_connected': True})
        installer.deploy(BASE | {'auto_failback': True}, host_factory=lambda *a: host)
        prepared = next(c.args[1] for c in host.call.call_args_list if c.args[0] == 'watcher_prepare')
        self.assertEqual(prepared['config']['recovery'], {'enabled': False, 'failback': False})
        self.assertNotIn('recovery_install', [c.args[0] for c in host.call.call_args_list])


if __name__ == '__main__':
    unittest.main()
