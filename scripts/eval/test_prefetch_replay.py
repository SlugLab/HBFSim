#!/usr/bin/env python3
"""Deterministic CPU analytical gold; the toy service is never MQSim evidence."""
import unittest

from prefetch_replay import replay


class ConstantDelayOracle:
    source = 'TEST_ONLY_CONSTANT_DELAY'

    def __init__(self, delay=10):
        self.now = 0
        self.delay = delay
        self.pending = []
        self.submitted = []

    def submit(self, request):
        self.submitted.append(dict(request))
        ready = max(self.now, self.pending[-1][0] if self.pending else 0)+self.delay
        self.pending.append((ready, request['request_id']))

    def until(self, horizon):
        if horizon < self.now:
            raise ValueError('backdated horizon')
        if self.pending and self.pending[0][0] <= horizon:
            self.now, request_id = self.pending.pop(0)
            return dict(request_id=request_id, reported_complete=self.now)
        self.now = horizon
        return None


def objects(layers=3, experts=2):
    return {(l,e):dict(bytes=512, transfer_bytes=512, logical_address=(l*experts+e)*512)
            for l in range(layers) for e in range(experts)}


def nodes(steps=2, layers=3, compute=20):
    return [dict(step=s, layer=l, compute_ns=compute, members={'a':[0]})
            for s in range(steps) for l in range(layers)]


class PrefetchGold(unittest.TestCase):
    def run_case(self, policy, trace=None, capacity=1024, initial=()):
        service = ConstantDelayOracle()
        result = replay(trace or nodes(), objects(), capacity_bytes=capacity,
                        initial_resident=initial, policy=policy, service=service)
        return result, service

    def test_on_demand_chain_matches_analytical_oracle(self):
        result, _ = self.run_case('on_demand')
        self.assertEqual(result['decode_complete_ns'], 180)
        self.assertEqual(result['demand_lookups'], 6)
        self.assertEqual(result['cache_hits']+result['cache_misses'], 6)
        self.assertEqual(result['traffic_bytes'], 6*512)

    def test_prefetch_hides_only_visible_compute_and_records_final_waste(self):
        result, _ = self.run_case('one_layer_ahead')
        self.assertEqual(result['decode_complete_ns'], 150)
        prefetch = [r for r in result['requests'] if r['origin']=='prefetch']
        self.assertEqual([r['classification'] for r in prefetch], ['useful']*3+['useless'])
        self.assertEqual(result['traffic_bytes'], 7*512)
        self.assertTrue(all(r['prediction_basis']['visible_at_ns'] <= r['issue_ns'] for r in prefetch))

    def test_late_prefetch_promotes_without_duplicate_issue(self):
        result, _ = self.run_case('one_layer_ahead', nodes(compute=5))
        late = [r for r in result['requests'] if r['classification']=='late']
        self.assertEqual(len(late), 3)
        self.assertTrue(all(r['ready_ns']-r['first_demand_ns']==5 for r in late))
        self.assertEqual(len(result['requests']), 7)

    def test_mutating_unseen_routes_cannot_change_prior_prediction(self):
        a, _ = self.run_case('one_layer_ahead')
        changed = nodes()
        changed[4]['members']['a']=[1]
        b, _ = self.run_case('one_layer_ahead', changed)
        cutoff = a['nodes'][4]['visible_ns']
        before = lambda r:[(x['expert'],x['issue_ns'],x['prediction_basis']) for x in r['requests']
                           if x['origin']=='prefetch' and x['issue_ns']<cutoff]
        self.assertEqual(before(a), before(b))
        self.assertTrue(any(r['classification']=='useless' and r['expert']==[1,0]
                            for r in b['requests']))

    def test_pinned_current_weights_prevent_speculative_eviction(self):
        result, _ = self.run_case('one_layer_ahead', capacity=512)
        self.assertEqual(result['decode_complete_ns'], 180)
        self.assertFalse(any(r['origin']=='prefetch' for r in result['requests']))
        self.assertTrue(result['prefetch_skipped'])

    def test_full_residency_issues_no_media_and_preserves_compute(self):
        result, _ = self.run_case('one_layer_ahead', capacity=1536,
                                  initial=((0,0),(1,0),(2,0)))
        self.assertEqual(result['decode_complete_ns'], 120)
        self.assertEqual(result['traffic_bytes'], 0)
        self.assertEqual(result['cache_hits'], 6)

    def test_infeasible_batch_fails_before_io(self):
        trace=nodes(steps=1)
        trace[0]['members']={'a':[0], 'b':[1]}
        service=ConstantDelayOracle()
        with self.assertRaisesRegex(ValueError, 'INFEASIBLE'):
            replay(trace, objects(), capacity_bytes=512, initial_resident=(),
                   policy='on_demand', service=service)
        self.assertEqual(service.submitted, [])

    def test_none_serializes_issue_while_on_demand_shares_queue(self):
        trace=nodes(steps=1)
        for row in trace:
            row['members']={'a':[0], 'b':[1] if row['layer']==0 else [0]}
        sync, _ = self.run_case('none', trace, capacity=1536)
        asynchronous, _ = self.run_case('on_demand', trace, capacity=1536)
        self.assertEqual([r['issue_ns'] for r in sync['requests'][:2]], [0,10])
        self.assertEqual([r['issue_ns'] for r in asynchronous['requests'][:2]], [0,0])

    def test_failed_speculative_reservation_does_not_evict_ready_data(self):
        layout={(0,0):512,(0,1):1536,(1,0):512,(2,0):1024}
        address=0
        inventory={}
        for key,size in layout.items():
            inventory[key]=dict(bytes=size,transfer_bytes=size,logical_address=address)
            address+=size
        trace=nodes()
        trace[0]['members']['a']=[0,1]
        result=replay(trace,inventory,capacity_bytes=2560,initial_resident=(),
                      policy='one_layer_ahead',service=ConstantDelayOracle())
        self.assertTrue(any(r['expert']==[0,1] and r['time_ns']==80 for r in result['prefetch_skipped']))
        self.assertFalse(any(r['expert']==[1,0] and r['time_ns']==80 for r in result['evictions']))


if __name__=='__main__':
    unittest.main()
