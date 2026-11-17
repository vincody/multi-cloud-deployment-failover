"""Offline contracts for the VM-local recovery operations. kubectl, systemctl and psql are replaced by fakes."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE)]
import recovery_ops as ops
import rpc

IMAGE = 'postgres@sha256:' + 'b' * 64
TEMPLATE = (HERE / 'templates' / 'postgres-standby.yaml.tmpl').read_text(encoding='utf-8')


class Render(unittest.TestCase):
    def test_template_renders_with_no_placeholder_left(self):
        text = ops.render_standby(TEMPLATE, 'postgres-data-rejoin-20260101000000', '10.200.29.2', IMAGE, 0)
        self.assertIn('claimName: postgres-data-rejoin-20260101000000', text)
        self.assertIn('value: 10.200.29.2', text)
        self.assertIn('replicas: 0', text)
        self.assertIn(IMAGE, text)

    def test_rejects_unexpected_values(self):
        for args in [('bad name', '10.200.29.2', IMAGE, 0), ('postgres-data-x', '8.8.8.8', IMAGE, 0),
                     ('postgres-data-x', '10.200.29.2', 'postgres:latest', 0), ('postgres-data-x', '10.200.29.2', IMAGE, 2)]:
            with self.assertRaises(RuntimeError, msg=str(args)):
                ops.render_standby(TEMPLATE, *args)

    def test_wal_positions_are_validated_before_reaching_sql(self):
        self.assertEqual(ops.lsn_of('0/16B3748'), '0/16B3748')
        for bad in ("0/1'; DROP TABLE x;--", '', None, 'zz/1'):
            with self.assertRaises(RuntimeError):
                ops.lsn_of(bad)


class Gate(unittest.TestCase):
    def test_refuses_on_a_standby_and_checks_the_result(self):
        with patch.object(ops.promote, 'sql', Mock(return_value='t')):
            with self.assertRaisesRegex(RuntimeError, 'primary'):
                ops.gate({'enabled': False})
        answers = iter(['f', '', 'f', '0/6000'])
        with patch.object(ops.promote, 'sql', Mock(side_effect=lambda q: next(answers))) as sql:
            self.assertEqual(ops.gate({'enabled': False}), {'lsn': '0/6000'})
            self.assertIn('writes_enabled=false', sql.call_args_list[1].args[0])
            self.assertIn('pg_advisory_xact_lock(2900,1)', sql.call_args_list[1].args[0])

    def test_a_gate_that_did_not_change_is_an_error(self):
        answers = iter(['f', '', 't'])
        with patch.object(ops.promote, 'sql', Mock(side_effect=lambda q: next(answers))):
            with self.assertRaisesRegex(RuntimeError, 'did not change'):
                ops.gate({'enabled': False})


class Fence(unittest.TestCase):
    def test_port_detection(self):
        listening = 'LISTEN 0 244 10.200.29.2:5432 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n'
        with patch.object(ops, 'run', return_value=SimpleNamespace(stdout=listening)):
            self.assertTrue(ops.postgres_listening())
        with patch.object(ops, 'run', return_value=SimpleNamespace(stdout='LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n')):
            self.assertFalse(ops.postgres_listening())

    def test_fence_needs_proof_that_the_database_is_gone(self):
        with patch.object(ops, 'kube') as kube, patch.object(ops, 'pod_exists', side_effect=[True, False]), \
                patch.object(ops, 'postgres_listening', return_value=False):
            self.assertEqual(ops.fence_db(sleep=lambda s: None), {'fenced': True})
            self.assertIn('--replicas=0', kube.call_args.args)
        with patch.object(ops, 'kube'), patch.object(ops, 'pod_exists', return_value=False), \
                patch.object(ops, 'postgres_listening', return_value=True), \
                patch.object(ops.time, 'monotonic', side_effect=[0, 1, 500]):
            with self.assertRaisesRegex(RuntimeError, 'fencing not proven'):
                ops.fence_db(sleep=lambda s: None)


class Replay(unittest.TestCase):
    def test_reports_not_caught_up_instead_of_raising_on_timeout(self):
        with patch.object(ops.promote, 'sql', Mock(return_value='t|f')), patch.object(ops.time, 'monotonic', side_effect=[0, 1, 100]):
            self.assertEqual(ops.wait_replay({'lsn': '0/5000', 'timeout': 5}, sleep=lambda s: None), {'caught_up': False})

    def test_refuses_to_judge_a_primary(self):
        with patch.object(ops.promote, 'sql', Mock(return_value='f|t')):
            with self.assertRaisesRegex(RuntimeError, 'not a standby'):
                ops.wait_replay({'lsn': '0/5000'})


class Promote(unittest.TestCase):
    def test_refuses_a_primary_a_paused_replay_and_a_bad_position(self):
        with patch.object(ops.promote, 'sql', Mock(return_value='f')):
            with self.assertRaisesRegex(RuntimeError, 'Not a standby'):
                ops.promote_local({'min_lsn': '0/5000'})
        with self.assertRaises(RuntimeError):
            ops.promote_local({'min_lsn': 'nope'})

    def test_promotes_only_after_replay_reached_the_final_position(self):
        seen = []

        def sql(query):
            seen.append(query)
            if 'pg_is_in_recovery' in query:
                return 't' if 'pg_promote' not in ''.join(seen) else 'f'
            if 'pg_is_wal_replay_paused' in query:
                return 'f'
            if 'pg_last_wal_replay_lsn' in query:
                return 't'
            if 'pg_promote' in query:
                return 't'
            return 't'
        with patch.object(ops.promote, 'sql', side_effect=sql), patch.object(ops, 'gate', return_value={'lsn': '0/7000'}) as gate:
            self.assertEqual(ops.promote_local({'min_lsn': '0/6000'}), {'role': 'primary'})
        self.assertLess(next(i for i, q in enumerate(seen) if 'pg_last_wal_replay_lsn' in q), next(i for i, q in enumerate(seen) if 'pg_promote' in q))
        gate.assert_called_once_with({'enabled': True})


class Rejoin(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.calls = []
        self.guarded = True
        self.config = root / '99-rejoin.yaml'
        self.pvc_json = json.dumps({'spec': {'volumeName': 'pv-1'}})

        def kube(*args, **kwargs):
            self.calls.append('kube ' + ' '.join(args))
            return SimpleNamespace(stdout=self.pvc_json if 'pvc' in args else '', returncode=0)

        def run(args, **kwargs):
            self.calls.append('run ' + ' '.join(args))
            return SimpleNamespace(stdout='', returncode=0)

        def start():
            self.calls.append('start-without-agent')
            self.config.write_text('disable-agent: true\n')
        self.existing = ('postgres-data', IMAGE)
        for patcher in (patch.object(ops, 'ROOT', root), patch.object(ops, 'REJOIN_CONFIG', self.config), patch.object(ops, 'kube', kube),
                        patch.object(ops, 'run', run), patch.object(ops, 'wait_api', lambda *a, **k: None),
                        patch.object(ops, 'k3s_active', lambda: not self.guarded), patch.object(ops, 'start_without_agent', start),
                        patch.object(ops, 'current_pvc', lambda: self.existing)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = root
        self.payload = {'pvc': 'postgres-data-rejoin-1', 'source_ip': '10.200.29.2', 'template': TEMPLATE}

    def position(self, text):
        return next(i for i, c in enumerate(self.calls) if text in c)

    def test_guarded_vm_never_runs_the_old_primary(self):
        ops.rejoin(self.payload)
        self.assertEqual(self.calls[0], 'start-without-agent')
        scale_down = self.position('scale statefulset/postgres --replicas=0')
        delete_old = self.position('delete pod postgres-0')
        apply = self.position('apply -f')
        restart = self.position('run systemctl restart k3s')
        scale_up = self.position('scale statefulset/postgres --replicas=1')
        self.assertTrue(scale_down < delete_old < apply < restart < scale_up)
        self.assertFalse(self.config.exists())
        self.assertIn('--replicas=0', self.calls[self.position('deployment/issues-app')])
        self.assertIn('persistentVolumeReclaimPolicy', ' '.join(self.calls))
        self.assertIn('replicas: 1', (self.root / 'postgres.yaml').read_text())
        self.assertIn('replicas: 0', (self.root / 'postgres-rejoin.yaml').read_text())

    def test_running_vm_is_rebuilt_without_touching_the_guard(self):
        self.guarded = False
        ops.rejoin(self.payload)
        self.assertNotIn('start-without-agent', self.calls)
        self.assertFalse(any('systemctl' in c for c in self.calls))
        self.assertLess(self.position('apply -f'), self.position('scale statefulset/postgres --replicas=1'))

    def test_a_repeat_for_the_same_pvc_does_not_reapply_the_manifest(self):
        self.guarded = False
        self.existing = ('postgres-data-rejoin-1', IMAGE)
        ops.rejoin(self.payload)
        self.assertFalse(any(c.startswith('kube apply') for c in self.calls))
        self.assertFalse(any('delete pod' in c for c in self.calls))
        self.assertTrue(any('replicas=1' in c for c in self.calls))

    def test_an_interrupted_rejoin_still_leaves_agent_mode_when_repeated(self):
        self.guarded = False
        self.existing = ('postgres-data-rejoin-1', IMAGE)
        self.config.write_text('disable-agent: true\n')
        ops.rejoin(self.payload)
        self.assertFalse(self.config.exists())
        self.assertTrue(any('systemctl restart k3s' in c for c in self.calls))


class Rpc(unittest.TestCase):
    def call(self, request):
        out = io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO(json.dumps(request))), patch.object(sys, 'stdout', out):
            code = rpc.main()
        return code, json.loads(out.getvalue())

    def test_only_listed_actions_are_callable(self):
        code, response = self.call({'action': 'os.system', 'payload': {}})
        self.assertEqual((code, response['ok']), (1, False))
        self.assertEqual(set(ops.ACTIONS), {'ping', 'db_state', 'gate', 'wait_replay', 'fence_db', 'unfence_db', 'app_scale', 'rejoin', 'promote_local'})

    def test_errors_do_not_leak_details(self):
        with patch.dict(ops.ACTIONS, {'ping': Mock(side_effect=ValueError('secret=hunter2'))}):
            code, response = self.call({'action': 'ping'})
        self.assertEqual(response, {'ok': False, 'error': 'ValueError: operation failed'})

    def test_success_is_wrapped(self):
        with patch.dict(ops.ACTIONS, {'ping': Mock(return_value={'ok': True})}):
            self.assertEqual(self.call({'action': 'ping'}), (0, {'ok': True, 'result': {'ok': True}}))


if __name__ == '__main__':
    unittest.main()
