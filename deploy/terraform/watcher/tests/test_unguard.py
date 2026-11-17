"""Offline contracts for releasing the K3s guard on the Azure VM."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE)]
import unguard as ug

CFG = {'aws_region': 'ap-southeast-1', 'aws_instance_id': 'i-0123456789abcdef0', 'aws_origin': 'https://aws.example.com'}


class Decide(unittest.TestCase):
    def test_a_standby_always_starts(self):
        for peer in ('fenced', 'standby', 'down', 'primary', 'unknown', 'not-needed'):
            self.assertTrue(ug.decide('standby', peer)[0], peer)

    def test_a_local_primary_starts_only_when_aws_cannot_be_a_writer(self):
        for peer in ('fenced', 'standby', 'down'):
            self.assertTrue(ug.decide('primary', peer)[0], peer)
        self.assertFalse(ug.decide('primary', 'primary')[0])
        self.assertFalse(ug.decide('primary', 'unknown')[0])

    def test_an_unidentified_or_empty_data_directory_never_starts(self):
        for role in (None, 'empty'):
            self.assertFalse(ug.decide(role, 'fenced')[0], role)

    def test_the_pause_file_vetoes_everything(self):
        self.assertFalse(ug.decide('standby', 'fenced', paused=True)[0])


class LocalRole(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / 'postgres.yaml'
        self.storage = self.root / 'storage'
        self.manifest.write_text('volumes:\n- name: data\n  persistentVolumeClaim:\n    claimName: postgres-data-rejoin-1\n- name: config\n  configMap:\n    name: postgres-config\n')

    def data(self, claim='postgres-data-rejoin-1', version=True, standby=False):
        folder = self.storage / f'pvc-1234_issues_{claim}' / 'pgdata'
        folder.mkdir(parents=True)
        if version:
            (folder / 'PG_VERSION').write_text('17')
        if standby:
            (folder / 'standby.signal').write_text('')

    def role(self):
        return ug.local_role(self.manifest, self.storage)

    def test_reads_the_role_from_the_data_directory(self):
        self.data(standby=True)
        self.assertEqual(self.role(), 'standby')

    def test_a_promoted_directory_is_a_primary(self):
        self.data()
        self.assertEqual(self.role(), 'primary')

    def test_empty_missing_and_ambiguous_are_not_a_role(self):
        self.assertIsNone(self.role())
        self.data(version=False)
        self.assertEqual(self.role(), 'empty')
        (self.storage / 'pvc-9999_issues_postgres-data-rejoin-1' / 'pgdata').mkdir(parents=True)
        self.assertIsNone(self.role())

    def test_only_the_claim_in_the_active_manifest_counts(self):
        self.data(claim='postgres-data-old', standby=True)
        self.assertIsNone(self.role())
        self.manifest.write_text('claimName: a\nclaimName: b\n')
        self.assertIsNone(self.role())
        self.manifest.unlink()
        self.assertIsNone(self.role())


class PeerView(unittest.TestCase):
    def view(self, ec2, rpc=None, http=None):
        def answer(value):
            def call():
                if isinstance(value, Exception):
                    raise value
                return value
            return call
        return ug.peer_view(answer(ec2), answer(rpc), answer(http))

    def test_a_stopped_instance_is_fenced_without_asking_anything_else(self):
        self.assertEqual(self.view('stopped', rpc=OSError('x')), 'fenced')

    def test_a_running_instance_is_judged_by_its_own_report(self):
        self.assertEqual(self.view('running', rpc='standby'), 'standby')
        self.assertEqual(self.view('running', rpc=None, http='primary'), 'primary')
        self.assertEqual(self.view('running', rpc=OSError('x'), http='down'), 'down')

    def test_nothing_answering_is_unknown_never_safe(self):
        self.assertEqual(self.view('running'), 'unknown')
        self.assertEqual(self.view('terminated', rpc='standby'), 'unknown')
        self.assertEqual(self.view(RuntimeError('api down')), 'unknown')

    def test_ec2_api_failure_still_accepts_a_direct_answer(self):
        self.assertEqual(self.view(RuntimeError('api down'), rpc='standby'), 'standby')


class Run(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'automation-result.json').write_text('{}')
        for patcher in (patch.object(ug, 'ROOT', root), patch.object(ug, 'probes', lambda cfg: (None, None, None))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.start = Mock()
        self.sleeps = []

    def run_it(self, roles, peers, active=(False,), paused=lambda: False):
        roles, peers, active = iter(roles), iter(peers), iter(active)
        return ug.run(CFG, local=lambda: next(roles), view=lambda *a: next(peers), sleep=self.sleeps.append, start=self.start,
                      active=lambda: next(active, False), paused=paused)

    def test_nothing_to_do_when_k3s_is_already_running(self):
        self.assertEqual(self.run_it([], [], active=(True,)), 'already-running')
        self.start.assert_not_called()

    def test_standby_starts_immediately_without_asking_the_peer(self):
        self.assertEqual(self.run_it(['standby'], []), 'started')
        self.start.assert_called_once()

    def test_a_primary_waits_until_the_peer_is_known_then_starts(self):
        self.assertEqual(self.run_it(['primary'] * 3, ['unknown', 'unknown', 'fenced']), 'started')
        self.assertEqual(len(self.sleeps), 2)
        self.start.assert_called_once()

    def test_two_possible_primaries_never_start(self):
        calls = []

        def view(*args):
            calls.append(1)
            if len(calls) > 3:
                raise KeyboardInterrupt
            return 'primary'
        with self.assertRaises(KeyboardInterrupt):
            ug.run(CFG, local=lambda: 'primary', view=view, sleep=self.sleeps.append, start=self.start, active=lambda: False)
        self.start.assert_not_called()

    def test_the_pause_file_holds_a_standby_too(self):
        answers = iter([True, True, False])
        self.assertEqual(self.run_it(['standby'] * 3, [], paused=lambda: next(answers)), 'started')
        self.assertEqual(len(self.sleeps), 2)

    def test_refuses_when_the_deployment_was_never_verified(self):
        (ug.ROOT / 'automation-result.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'never verified'):
            self.run_it(['standby'], [])
        self.start.assert_not_called()

    def test_start_writes_the_marker_before_starting_k3s(self):
        order = []
        with patch.object(ug, 'MARKER', Mock(touch=lambda: order.append('marker'))), \
                patch.object(ug.subprocess, 'run', side_effect=lambda cmd, **kwargs: order.append(cmd[1]) or Mock(returncode=0)):
            ug.start_k3s()
        self.assertEqual(order, ['marker', 'reset-failed', 'start'])


if __name__ == '__main__':
    unittest.main()
