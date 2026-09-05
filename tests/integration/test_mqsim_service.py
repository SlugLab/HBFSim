#!/usr/bin/env python3
"""CPU interactive-service controls; no physical storage or measured serving."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BINARY = Path(sys.argv.pop(1)).resolve()
ROOT = Path(__file__).resolve().parents[2]


class ServiceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)/'profile.json'
        profile = json.loads((ROOT/'configs/profiles/nominal.json').read_text())
        profile.update(capacity_bytes=16 << 30, hbm_cache_bytes=64 << 20, queue_depth=2, time_scale=1)
        self.profile.write_text(json.dumps(profile))

    def request(self, request_id, issue=0):
        return dict(request_id=request_id, issue_ns=issue, logical_address=(request_id-1)*16384,
                    bytes=16384, operation='read')

    def invoke(self, commands, *, success=True, extra=()):
        result = subprocess.run([str(BINARY), '--profile', str(self.profile), *extra],
                                input=''.join(json.dumps(c)+'\n' for c in commands),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()], result

    def test_compute_horizon_and_shared_service_conservation(self):
        commands = [dict(command='submit', requests=[self.request(1)]),
                    dict(command='until', deadline_ns=1000),
                    dict(command='submit', requests=[self.request(2, 1000)]),
                    dict(command='until', deadline_ns=100000),
                    dict(command='until', deadline_ns=100000), dict(command='finish')]
        replies, _ = self.invoke(commands)
        self.assertEqual(replies[0]['service_source'], 'MQSIM_SIMULATED')
        self.assertEqual(replies[0]['provenance'], 'PROJECTED')
        self.assertEqual(replies[2]['now_ns'], 1000)
        self.assertIsNone(replies[2]['completion'])
        self.assertEqual(replies[-1]['issued'], 2)
        self.assertEqual(replies[-1]['completed'], 2)
        self.assertEqual(replies[-1]['completed_bytes'], 32768)
        events = [event for reply in replies for event in reply.get('events', [])]
        self.assertEqual(len(events), 6)
        self.assertEqual(max(event['device_outstanding'] for event in events), 2)
        self.assertEqual({event['request_id'] for event in events}, {1, 2})

    def test_invalid_batch_duplicate_and_pending_finish_reject(self):
        invalid = self.request(2)
        invalid['bytes'] = -512
        for commands in ([dict(command='submit', requests=[self.request(1), invalid])],
                         [dict(command='submit', requests=[self.request(1), self.request(1)])],
                         [dict(command='submit', requests=[self.request(1)]), dict(command='finish')],
                         [dict(command='until', deadline_ns=1000), dict(command='until', deadline_ns=999)]):
            with self.subTest(commands=commands):
                replies, _ = self.invoke(commands, success=False)
                self.assertFalse(any(r.get('status') == 'FINISHED' for r in replies))

    def test_unmapped_topology_and_unframed_eof_reject(self):
        _, failure = self.invoke([], success=False, extra=('--parallel-units', '7'))
        self.assertIn('PROJECTED_ANALYTICAL', failure.stderr)
        self.invoke([dict(command='submit', requests=[self.request(1)])], success=False)

    def test_bandwidth_bound_is_not_returned_early(self):
        profile = json.loads(self.profile.read_text())
        profile['aggregate_bandwidth_bytes_per_s'] = 100000
        self.profile.write_text(json.dumps(profile))
        replies, _ = self.invoke([dict(command='submit', requests=[self.request(1)]),
                                  dict(command='until', deadline_ns=1000000),
                                  dict(command='until', deadline_ns=200000000), dict(command='finish')])
        self.assertIsNone(replies[2]['completion'])
        self.assertEqual(replies[3]['now_ns'], 163840000)
        self.assertEqual(replies[3]['completion']['reported_complete'], 163840000)


if __name__ == '__main__':
    unittest.main()
