"""Registration safety without contacting AWS or SSH."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

spec = importlib.util.spec_from_file_location('dr_bootstrap', Path(__file__).resolve().parents[1] / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class Tests(unittest.TestCase):
    def config(self):
        return {'account_id': '123456789012', 'hybrid_role': 'dcs29-azure-ssm', 'node_parameter': '/dcs29/dr/azure-node'}

    def test_wrong_cli_account_stops_before_ssh(self):
        with patch.object(bootstrap, 'aws', return_value={'Account': '999999999999'}), patch.object(bootstrap, 'Host') as host:
            with self.assertRaisesRegex(bootstrap.workloads.DeploymentError, 'account differs'):
                bootstrap.deploy(self.config())
            host.assert_not_called()

    def test_activation_deleted_even_when_registration_fails(self):
        calls = []
        def aws(config, *args):
            calls.append(args)
            if args[0] == 'sts': return {'Account': config['account_id']}
            if args[1] == 'create-activation': return {'ActivationId': 'fixture-id', 'ActivationCode': 'fixture-secret'}
            return {}
        azure = Mock()
        def remote(action, *args):
            if action == 'ssm_status': return {'node': None}
            if action == 'ssm_register': raise RuntimeError('registration failed')
            return {'ok': True}
        azure.call.side_effect = remote
        with patch.object(bootstrap, 'aws', side_effect=aws), patch.object(bootstrap, 'Host', side_effect=[azure, Mock()]), patch.object(bootstrap.workloads, 'validate', return_value='fixture-key'), patch.object(bootstrap.workloads, 'check_roles'):
            with self.assertRaisesRegex(RuntimeError, 'registration failed'): bootstrap.deploy(self.config())
        self.assertEqual(calls[-1], ('ssm', 'delete-activation', '--activation-id', 'fixture-id'))

    def test_owned_online_agent_reused_without_new_activation(self):
        calls = []
        def aws(config, *args):
            calls.append(args)
            if args[0] == 'sts': return {'Account': config['account_id']}
            if args[1] == 'describe-instance-information': return {'InstanceInformationList': [{'PingStatus': 'Online'}]}
            return {}
        azure = Mock(); azure.call.return_value = {'node': 'mi-0123456789abcdef0'}
        with patch.object(bootstrap, 'aws', side_effect=aws), patch.object(bootstrap, 'Host', side_effect=[azure, Mock()]), patch.object(bootstrap.workloads, 'validate', return_value='fixture-key'), patch.object(bootstrap.workloads, 'check_roles'):
            bootstrap.deploy(self.config())
        self.assertFalse(any('create-activation' in call for call in calls))
        self.assertTrue(any('put-parameter' in call for call in calls))

    def test_registration_id_extracted_without_assuming_field_layout(self):
        namespace = {'json': json, 'Path': Mock()}
        namespace['Path'].return_value.read_text.return_value = json.dumps({'identity': {'instance': 'mi-0123456789abcdef0'}, 'private': 'fixture'})
        source = (Path(__file__).resolve().parents[1] / 'bootstrap_remote.py').read_text(encoding='utf-8').split('ACTIONS=')[0]
        exec(source, namespace)
        self.assertEqual(namespace['registered_node'](), 'mi-0123456789abcdef0')
        namespace['Path'].return_value.read_text.return_value = json.dumps(['mi-0123456789abcdef0', 'mi-11111111111111111'])
        with self.assertRaisesRegex(RuntimeError, 'ID missing'): namespace['registered_node']()


if __name__ == '__main__': unittest.main()
