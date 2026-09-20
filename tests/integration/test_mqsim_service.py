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

    def invoke(self, commands, *, success=True, extra=(), profile=None):
        selected_profile = self.profile if profile is None else Path(profile)
        result = subprocess.run([str(BINARY), '--profile', str(selected_profile), *extra],
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

    def test_default_off_gate_service_parity(self):
        direct, _ = self.invoke([dict(command='submit', requests=[self.request(1)]),
                                 dict(command='until', deadline_ns=100000), dict(command='finish')])
        gated, _ = self.invoke([dict(command='try_submit', request=self.request(1)),
                                dict(command='until', deadline_ns=100000), dict(command='finish')])
        self.assertEqual(direct[0]['submission_gate'], 'OFF')
        self.assertTrue(gated[1]['gate']['submitted'])
        self.assertEqual(gated[1]['gate']['external_wait_ns'], 0)
        self.assertEqual(direct[2]['completion'], gated[2]['completion'])
        direct_events = [event for reply in direct for event in reply.get('events', [])]
        gated_events = [event for reply in gated for event in reply.get('events', [])]
        self.assertEqual(direct_events, gated_events)

    def test_enabled_gate_uses_existing_horizon_and_retry(self):
        target = 200000
        commands = [dict(command='try_submit', request=self.request(1)),
                    dict(command='until', deadline_ns=target),
                    dict(command='try_submit', request=self.request(1)),
                    dict(command='until', deadline_ns=1000000), dict(command='finish')]
        replies, _ = self.invoke(commands, extra=('--gate-not-before-ns', str(target)))
        self.assertEqual(replies[0]['submission_gate'], 'ENGINEERING_FIXTURE_ENABLED')
        self.assertFalse(replies[1]['gate']['submitted'])
        self.assertEqual(replies[1]['gate']['target_ns'], target)
        self.assertEqual(replies[2]['now_ns'], target)
        self.assertTrue(replies[3]['gate']['submitted'])
        self.assertEqual(replies[3]['gate']['backend_arrival_ns'], target)
        self.assertEqual(replies[3]['gate']['external_wait_ns'], target)
        self.assertEqual(replies[4]['completion']['request_id'], 1)
        self.assertEqual(replies[-1]['issued'], 1)
        self.assertEqual(replies[-1]['completed'], 1)

    def test_native_command_observation_off_on_parity_and_identity(self):
        request = self.request(1)
        request['bytes'] = 32768  # two LPAs: native MQSim maps them independently
        commands = [dict(command='submit', requests=[request]),
                    dict(command='until', deadline_ns=1000000), dict(command='finish')]
        off, _ = self.invoke(commands)
        on, _ = self.invoke(commands, extra=('--native-command-observations', 'on'))
        self.assertEqual(off[0]['native_command_observations'], 'OFF')
        self.assertEqual(on[0]['native_command_observations'], 'ON')
        self.assertEqual(off[2]['completion'], on[2]['completion'])
        self.assertEqual([e for r in off for e in r['events']],
                         [e for r in on for e in r['events']])
        self.assertFalse([e for r in off for e in r['native_command_events']])
        native = [e for r in on for e in r['native_command_events']]
        self.assertTrue(native)
        commands_by_id = {}
        saw_request = False
        request_transaction_ids = set()
        request_channels = set()
        for event in native:
            commands_by_id.setdefault(event['command_id'], []).append(event)
            self.assertTrue(event['transactions'])
            for transaction in event['transactions']:
                self.assertIsNone(transaction['stack'])
                self.assertTrue(transaction['logical_page'] is None or
                                isinstance(transaction['logical_page'], int))
                saw_request |= transaction['external_request_id'] == 1
                if transaction['external_request_id'] == 1:
                    request_transaction_ids.add(transaction['transaction_id'])
                    request_channels.add(transaction['channel'])
                self.assertIsInstance(transaction['transaction_id'], int)
                self.assertGreater(transaction['transaction_id'], 0)
        self.assertTrue(saw_request)
        self.assertGreaterEqual(len(request_transaction_ids), 2)
        self.assertGreaterEqual(len(request_channels), 2)
        for phases in commands_by_id.values():
            phase_ids = [event['phase'] for event in phases]
            times = [event['time_ns'] for event in phases]
            self.assertEqual(phase_ids[:3], [0, 1, 2])
            self.assertEqual(times, sorted(times))
            issued_transactions = {t['transaction_id'] for t in phases[0]['transactions']}
            for event in phases[1:3]:
                self.assertEqual({t['transaction_id'] for t in event['transactions']},
                                 issued_transactions)

    def make_small_eight_stack_fixture(self):
        profile_path = Path(self.temp.name)/'eight-stack-engineering-profile.json'
        profile = json.loads((ROOT/'configs/profiles/nominal.json').read_text())
        profile.update(name='ENGINEERING_FIXTURE_SMALL_8_STACK_1_DIE_PER_STACK',
                       capacity_bytes=128 << 20, hbm_cache_bytes=64 << 20,
                       channels=8, dies_per_channel=1, planes_per_die=1,
                       pages_per_block=256, queue_depth=16, time_scale=1)
        profile_path.write_text(json.dumps(profile))
        # Deliberately permuted: success proves the consumer uses the explicit
        # physical channel groups, rather than guessing channel == stack index.
        channels = [4, 0, 5, 1, 6, 2, 7, 3]
        mapping = dict(schema_version=1, physical_kind='HBF', route='direct',
                       address_layout='GLOBAL_PAGE_STRIPE_V1',
                       plane_allocation_scheme='CWDP', page_bytes=profile['page_bytes'],
                       channels=8, dies_per_channel=1,
                       evidence='ENGINEERING_FIXTURE_SMALL_8_STACK_1_DIE_PER_STACK',
                       stacks=[dict(id=f'hbf{i}', declared_dies=1, channels=[channel])
                               for i, channel in enumerate(channels)])
        map_path = Path(self.temp.name)/'eight-stack-map.json'
        map_path.write_text(json.dumps(mapping))
        return profile_path, map_path, channels

    def test_explicit_eight_hbf_stack_map_reaches_native_channels(self):
        profile_path, map_path, channels = self.make_small_eight_stack_fixture()
        requests = []
        for i in range(8):
            row = dict(request_id=i+1, issue_ns=0, stack_local_page=0,
                       bytes=16384, operation='read', stack=f'hbf{i}', route='direct')
            requests.append(row)
        commands = [dict(command='submit', requests=requests)]
        commands.extend(dict(command='until', deadline_ns=10_000_000) for _ in requests)
        commands.append(dict(command='finish'))
        replies, _ = self.invoke(commands, profile=profile_path,
                                 extra=('--stack-map', str(map_path)))
        self.assertEqual(replies[0]['stack_mapping'],
                         'ACTUAL_MQSIM_CHANNEL_PARTITIONED_HBF_STACKS')
        self.assertEqual(replies[0]['stack_mapping_evidence'],
                         'ENGINEERING_FIXTURE_CONFIGURATION_NOT_RESEARCH_GEOMETRY')
        self.assertEqual(replies[0]['native_command_observations'], 'ON')
        self.assertEqual(replies[-1]['issued'], 8)
        self.assertEqual(replies[-1]['completed'], 8)
        completions = [r['completion']['request_id'] for r in replies
                       if r.get('completion') is not None]
        self.assertEqual(set(completions), set(range(1, 9)))
        self.assertEqual(len(completions), 8)
        demand = {}
        for reply in replies:
            for event in reply.get('native_command_events', []):
                for transaction in event['transactions']:
                    request_id = transaction['external_request_id']
                    if request_id in range(1, 9):
                        demand.setdefault(request_id, transaction)
                        self.assertEqual(transaction['stack'], f'hbf{request_id-1}')
                        self.assertEqual(transaction['expected_stack'], f'hbf{request_id-1}')
                        self.assertEqual(transaction['channel'], channels[request_id-1])
                        self.assertEqual(transaction['external_logical_page'], request_id-1)
                        self.assertEqual(transaction['backend_logical_page'], channels[request_id-1])
        self.assertEqual(set(demand), set(range(1, 9)))

    def test_enabled_stack_map_rejects_mismatch_and_multi_page(self):
        profile_path, map_path, _ = self.make_small_eight_stack_fixture()
        mismatch = dict(request_id=1, issue_ns=0, logical_address=0, stack_local_page=0,
                        bytes=16384, operation='read', stack='hbf1', route='direct')
        multi_page = dict(request_id=1, issue_ns=0, stack_local_page=0, bytes=32768,
                          operation='read', stack='hbf0', route='direct')
        for row in (mismatch, multi_page):
            with self.subTest(row=row):
                self.invoke([dict(command='submit', requests=[row])], success=False,
                            profile=profile_path, extra=('--stack-map', str(map_path)))

    def test_stack_map_configuration_rejects_profile_overlap_and_incomplete_groups(self):
        profile_path, map_path, _ = self.make_small_eight_stack_fixture()
        base = json.loads(map_path.read_text())
        variants = []
        profile_mismatch = json.loads(json.dumps(base))
        profile_mismatch['channels'] = 9
        variants.append((profile_mismatch, 'stack map does not match'))
        overlap = json.loads(json.dumps(base))
        overlap['stacks'][1]['channels'] = overlap['stacks'][0]['channels']
        variants.append((overlap, 'overlapping or invalid mqsim channel group'))
        incomplete = json.loads(json.dumps(base))
        incomplete['stacks'].pop()
        variants.append((incomplete, 'invalid mqsim stack-map geometry'))
        for index, (config, expected_error) in enumerate(variants):
            with self.subTest(index=index):
                invalid_path = Path(self.temp.name)/f'invalid-stack-map-{index}.json'
                invalid_path.write_text(json.dumps(config))
                _, failure = self.invoke([dict(command='finish')], success=False,
                                         profile=profile_path,
                                         extra=('--stack-map', str(invalid_path)))
                self.assertIn(expected_error, failure.stderr.lower())


if __name__ == '__main__':
    unittest.main()
