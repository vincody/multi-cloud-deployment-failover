"""Offline contracts for the recovery state machine, driven by a small two-node simulation."""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE)]
import recovery as rc

GATE_LSN = '0/6000'


class Sim:
    """Azure starts as the open writer, AWS as a stopped (down) former primary."""

    def __init__(self):
        self.roles = {'azure': {'role': 'primary', 'writes': True}, 'aws': {'role': 'down', 'writes': False}}
        self.log, self.fail = [], {}
        self.lag_at_final = False
        self.ec2 = ['stopped', 'stopped', 'pending', 'running']  # the operator starts EC2 after two polls

    def node(self, name):
        sim = self

        class Node:
            def call(self, action, payload=None, timeout=None):
                sim.log.append((name, action, payload or {}))
                if (name, action) in sim.fail:
                    raise sim.fail[(name, action)]
                return getattr(sim, '_' + action)(name, payload or {})
        return Node()

    def events(self):
        return [(n, a) for n, a, _ in self.log]

    def _ping(self, name, p):
        return {'ok': True}

    def _db_state(self, name, p):
        r = self.roles[name]
        if r['role'] == 'down':
            return {'k3s': False, 'role': 'down'}
        if r['role'] == 'primary':
            return {'k3s': True, 'role': 'primary', 'writes_enabled': r['writes'], 'flush_lsn': '0/5000'}
        return {'k3s': True, 'role': 'standby', 'receiver': 'streaming', 'replay_lsn': '0/5000'}

    def _gate(self, name, p):
        self.roles[name]['writes'] = p['enabled']
        return {'lsn': GATE_LSN}

    def _wait_replay(self, name, p):
        return {'caught_up': not (self.lag_at_final and p['lsn'] == GATE_LSN)}

    def _fence_db(self, name, p):
        self.roles[name]['role'] = 'down'
        return {'fenced': True}

    def _unfence_db(self, name, p):
        self.roles[name]['role'] = 'primary'
        return {}

    def _rejoin(self, name, p):
        self.roles[name] = {'role': 'standby', 'writes': False}
        return {'pvc': p['pvc']}

    def _app_scale(self, name, p):
        return {'ok': True}

    def _promote_local(self, name, p):
        self.roles[name] = {'role': 'primary', 'writes': True}
        return {'role': 'primary'}


class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'failover-incident.json').write_text('{}')
        self.sim = Sim()
        self.clock = 0
        self.pauses = 0

    def deps(self, failback=True, paused=None):
        sim = self.sim

        def sleep(seconds):
            self.clock += seconds

        def aws_state():
            return sim.ec2[0] if len(sim.ec2) == 1 else sim.ec2.pop(0)

        def stop():
            sim.log.append(('ec2', 'stop', {}))

        return SimpleNamespace(
            failback=failback, store=rc.Store(self.root / 'recovery-state.json'), template='template',
            azure=sim.node('azure'), aws=sim.node('aws'), aws_state=aws_state, stop_aws=stop,
            aws_writable=lambda: True, paused=paused or (lambda: False), clear_incident=lambda: (self.root / 'failover-incident.json').unlink(),
            sleep=sleep, monotonic=lambda: self.clock, log=lambda message: None,
            poll=10, catchup_timeout=600, lock_timeout=60, healthy_timeout=60)

    def index(self, event):
        return self.sim.events().index(event)

    def test_full_cycle_returns_the_writer_to_aws_and_rearms(self):
        d = self.deps()
        self.assertEqual(rc.run(d), 'rearmed')
        for step in [('aws', 'rejoin'), ('azure', 'gate'), ('azure', 'fence_db'), ('aws', 'promote_local'), ('azure', 'rejoin')]:
            self.assertIn(step, self.sim.events())
        self.assertNotIn(('ec2', 'start'), self.sim.events())
        self.assertGreaterEqual(self.clock, 20)  # it waited for the operator instead of starting EC2
        self.assertLess(self.index(('aws', 'rejoin')), self.index(('azure', 'fence_db')))
        self.assertLess(self.index(('azure', 'fence_db')), self.index(('aws', 'promote_local')))
        self.assertLess(self.index(('aws', 'promote_local')), self.index(('azure', 'rejoin')))
        self.assertEqual((self.sim.roles['aws']['role'], self.sim.roles['azure']['role']), ('primary', 'standby'))
        self.assertIsNone(d.store.load())
        self.assertFalse((self.root / 'failover-incident.json').exists())

    def test_rejoin_reads_the_azure_address_and_promotion_the_locked_lsn(self):
        rc.run(self.deps())
        rejoins = [(n, p) for n, a, p in self.sim.log if a == 'rejoin']
        self.assertEqual(rejoins[0][1]['source_ip'], rc.AZURE_IP)
        self.assertEqual(rejoins[1][1]['source_ip'], rc.AWS_IP)
        self.assertTrue(all(p['pvc'].startswith('postgres-data-rejoin-') for _, p in rejoins))
        promote = next(p for n, a, p in self.sim.log if a == 'promote_local')
        self.assertEqual(promote['min_lsn'], GATE_LSN)

    def test_without_failback_azure_stays_the_writer(self):
        d = self.deps(failback=False)
        self.assertEqual(rc.run(d), 'recovered')
        self.assertEqual((self.sim.roles['aws']['role'], self.sim.roles['azure']['role']), ('standby', 'primary'))
        self.assertTrue(self.sim.roles['azure']['writes'])
        for forbidden in [('azure', 'fence_db'), ('aws', 'promote_local')]:
            self.assertNotIn(forbidden, self.sim.events())
        self.assertEqual(d.store.load()['phase'], 'complete')
        self.assertEqual(rc.run(self.deps(failback=False)), 'recovered')  # a restart is a no-op

    def test_azure_must_be_the_open_writer_before_waiting_for_aws(self):
        self.sim.roles['azure']['writes'] = False
        with self.assertRaises(rc.OperatorNeeded):
            rc.run(self.deps())

    def test_unexpected_ec2_state_stops_for_an_operator(self):
        self.sim.ec2 = ['terminated']
        with self.assertRaisesRegex(rc.OperatorNeeded, 'terminated'):
            rc.run(self.deps())

    def test_it_never_starts_ec2_and_keeps_waiting_while_aws_is_stopped(self):
        self.sim.ec2 = ['stopped'] * 50 + ['running']
        rc.run(self.deps(failback=False))
        self.assertGreaterEqual(self.clock, 500)
        self.assertFalse(any(n == 'ec2' for n, a in self.sim.events()))

    def test_it_waits_for_the_rpc_after_the_instance_is_running(self):
        self.sim.ec2 = ['running']
        calls = []
        original = self.sim._ping

        def ping(name, p):
            calls.append(1)
            if len(calls) < 3:
                raise rc.RemoteError('sshd not up yet')
            return original(name, p)
        self.sim._ping = ping
        rc.run(self.deps(failback=False))
        self.assertEqual(len(calls), 3)

    def test_a_second_primary_on_aws_is_fenced_and_never_rejoined(self):
        self.sim.roles['aws'] = {'role': 'primary', 'writes': True}
        with self.assertRaisesRegex(rc.OperatorNeeded, 'second primary'):
            rc.run(self.deps())
        self.assertIn(('ec2', 'stop'), self.sim.events())
        self.assertNotIn(('aws', 'rejoin'), self.sim.events())

    def test_lag_at_the_final_position_reopens_azure_and_never_fences(self):
        self.sim.lag_at_final = True
        with self.assertRaisesRegex(rc.OperatorNeeded, 'writes re-enabled'):
            rc.run(self.deps())
        self.assertNotIn(('azure', 'fence_db'), self.sim.events())
        self.assertTrue(self.sim.roles['azure']['writes'])

    def test_failed_promotion_with_aws_still_standby_restores_azure(self):
        self.sim.fail[('aws', 'promote_local')] = rc.RemoteError('replay paused')
        with self.assertRaisesRegex(rc.OperatorNeeded, 'Azure writer restored'):
            rc.run(self.deps())
        self.assertEqual((self.sim.roles['azure']['role'], self.sim.roles['azure']['writes']), ('primary', True))
        self.assertEqual(self.sim.roles['aws']['role'], 'standby')

    def test_promotion_error_after_aws_became_writer_continues(self):
        def promote(name, p):
            self.sim.roles['aws'] = {'role': 'primary', 'writes': True}
            raise rc.RemoteError('ssh dropped after promote')
        self.sim._promote_local = promote
        self.assertEqual(rc.run(self.deps()), 'rearmed')

    def test_unreachable_aws_after_failed_promotion_leaves_azure_stopped_for_a_person(self):
        self.sim.fail[('aws', 'promote_local')] = rc.RemoteError('ssh dropped')
        original = self.sim._db_state

        def state(name, p):
            if name == 'aws' and self.sim.roles['azure']['role'] == 'down':
                raise rc.RemoteError('unreachable')
            return original(name, p)
        self.sim._db_state = state
        with self.assertRaisesRegex(rc.OperatorNeeded, 'unreachable'):
            rc.run(self.deps())
        self.assertNotIn(('azure', 'unfence_db'), self.sim.events())

    def test_resume_after_the_azure_fence_does_not_repeat_earlier_steps(self):
        self.sim.roles = {'azure': {'role': 'down', 'writes': False}, 'aws': {'role': 'standby', 'writes': False}}
        d = self.deps()
        d.store.save({'phase': 'failback-promote', 'final_lsn': GATE_LSN, 'aws_pvc': 'postgres-data-rejoin-1'})
        self.assertEqual(rc.run(d), 'rearmed')
        self.assertNotIn(('azure', 'gate'), self.sim.events())
        self.assertNotIn(('azure', 'fence_db'), self.sim.events())
        self.assertNotIn(('ec2', 'start'), self.sim.events())

    def test_rejoin_retry_reuses_the_same_new_pvc(self):
        self.sim.fail[('aws', 'rejoin')] = rc.RemoteError('clone interrupted')
        with self.assertRaises(rc.RemoteError):
            rc.run(self.deps())
        del self.sim.fail[('aws', 'rejoin')]
        self.sim.ec2 = ['running']
        rc.run(self.deps())
        pvcs = {p['pvc'] for n, a, p in self.sim.log if (n, a) == ('aws', 'rejoin')}
        self.assertEqual(len(pvcs), 1)

    def test_pause_file_holds_every_step(self):
        answers = iter([True, True, False])
        d = self.deps(failback=False, paused=lambda: next(answers, False))
        rc.run(d)
        self.assertGreaterEqual(self.clock, 20)


if __name__ == '__main__':
    unittest.main()
