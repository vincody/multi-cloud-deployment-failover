"""Offline contracts for the Azure-side watcher. No AWS access or boto3 needed."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parents[2] / 'scripts')]
import cloud_watcher as cw

CFG = {'aws_region': 'ap-southeast-1', 'aws_instance_id': 'i-0123456789abcdef0', 'aws_origin': 'https://aws.example.com',
       'azure_origin': 'https://azure.example.com', 'health_check_id': 'hc-1', 'interval': 5, 'threshold': 3}


def observations(*statuses):
    return {'HealthCheckObservations': [{'StatusReport': {'Status': s}} for s in statuses]}


class CheckWeb(unittest.TestCase):
    def check(self, effect=None, status=200):
        response = Mock(status=status)
        response.__enter__ = lambda s: response
        response.__exit__ = lambda s, *a: False
        with patch.object(cw.urllib.request, 'urlopen', side_effect=effect, return_value=response):
            return cw.check_web(CFG)

    def test_ok_and_server_errors(self):
        self.assertTrue(self.check())
        self.assertFalse(self.check(urllib.error.HTTPError('u', 503, 'down', {}, None)))

    def test_client_errors_dns_and_tls_are_not_proof_of_failure(self):
        self.assertTrue(self.check(urllib.error.HTTPError('u', 404, 'nf', {}, None)))
        self.assertTrue(self.check(urllib.error.URLError(socket.gaierror('no dns'))))
        self.assertTrue(self.check(urllib.error.URLError(cw.ssl.SSLError('bad cert'))))

    def test_refusal_and_timeout_count_as_down(self):
        self.assertFalse(self.check(urllib.error.URLError(ConnectionRefusedError())))
        self.assertFalse(self.check(TimeoutError()))


class CheckRoute53(unittest.TestCase):
    def check(self, response=None, error=None):
        client = Mock()
        client.get_health_check_status.side_effect = error
        client.get_health_check_status.return_value = response
        with patch.object(cw, 'route53', return_value=client):
            return cw.check_route53(CFG)

    def test_down_only_when_no_checker_sees_it_healthy(self):
        self.assertFalse(self.check(observations('Failure: timeout', 'Failure: refused')))
        self.assertTrue(self.check(observations('Failure: timeout', 'Success: HTTP Status Code 200')))

    def test_unknown_is_not_a_failure(self):
        self.assertTrue(self.check(observations()))
        self.assertTrue(self.check(error=RuntimeError('api down')))


class Failover(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.owner = 'watcher:2026-10-23T10:00:00Z'
        for name, value in {'check_web': Mock(return_value=False), 'check_route53': Mock(return_value=False),
                            'incident_owner': Mock(return_value=self.owner), 'azure_writable': Mock(return_value=True),
                            'stop_aws': Mock(side_effect=lambda cfg: self.calls.append('stop'))}.items():
            patcher = patch.object(cw, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        run = Mock(side_effect=lambda phase, owner: self.calls.append(phase))
        patcher = patch.object(cw.promote, 'run_locked', run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def states(self, *names):
        patcher = patch.object(cw, 'aws_state', Mock(side_effect=list(names)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fence_comes_before_promote(self):
        self.states('running', 'stopping', 'stopped')
        cw.failover(CFG, sleep=lambda _: None)
        self.assertEqual(self.calls, ['preflight', 'stop', 'promote'])

    def test_already_stopped_instance_is_not_stopped_again(self):
        self.states('stopped', 'stopped')
        cw.failover(CFG, sleep=lambda _: None)
        self.assertEqual(self.calls, ['preflight', 'promote'])

    def test_aws_answering_on_final_check_aborts_before_any_action(self):
        cw.check_web.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'final check'):
            cw.failover(CFG, sleep=lambda _: None)
        self.assertEqual(self.calls, [])

    def test_fence_timeout_never_promotes(self):
        self.states('running', *['stopping'] * 3)
        with patch.object(cw, 'FENCE_TIMEOUT', -1), self.assertRaisesRegex(RuntimeError, 'not promoting'):
            cw.failover(CFG, sleep=lambda _: None)
        self.assertNotIn('promote', self.calls)

    def test_unexpected_ec2_state_never_promotes(self):
        self.states('terminated')
        with self.assertRaisesRegex(RuntimeError, 'fencing not proven'):
            cw.failover(CFG, sleep=lambda _: None)
        self.assertNotIn('promote', self.calls)


class Blocked(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for patcher in (patch.object(cw.promote, 'ROOT', self.root), patch.object(cw, 'PAUSE', self.root / 'watcher.pause')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def blocked(self, answer):
        effect = answer if isinstance(answer, Exception) else None
        with patch.object(cw.promote, 'sql', Mock(side_effect=effect, return_value=answer)):
            return cw.blocked()

    def test_standby_with_nothing_pending_may_act(self):
        self.assertIsNone(self.blocked('t'))

    def test_primary_means_done(self):
        self.assertEqual(self.blocked('f'), 'done')

    def test_cannot_query_database_means_wait(self):
        self.assertEqual(self.blocked(RuntimeError('kubectl failed')), 'busy')

    def test_unfinished_incident_or_pause_wait_for_a_person(self):
        (self.root / 'failover-incident.json').write_text('{}')
        self.assertEqual(self.blocked('t'), 'busy')
        (self.root / 'failover-incident.json').unlink()
        (self.root / 'watcher.pause').write_text('')
        self.assertEqual(self.blocked('t'), 'busy')

    def test_owner_is_reused_across_reruns(self):
        (self.root / 'failover-incident.json').write_text(json.dumps({'owner': 'watcher:2026-01-01T00:00:00Z'}))
        self.assertEqual(cw.incident_owner(), 'watcher:2026-01-01T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
