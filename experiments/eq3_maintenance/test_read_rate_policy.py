import unittest

from read_rate_policy import (ByteTokenLedger, EngineeringProfile, ReadRatePolicy,
                              StackWindowFacts, WindowFacts)


def profile(**updates):
    values=dict(profile_id='fixture-v1',enabled=True,
                strategy='read_rate_feedback_thermal_guard_v1',window_ns=1_000_000_000,
                target_bytes_per_s=1000,tolerance_fraction=.05,step_bytes=100,
                minimum_budget_bytes=100,maximum_budget_bytes=2000,severe_budget_bytes=50)
    values.update(updates);return EngineeringProfile(**values)


def stack(**updates):
    values=dict(stack_id='hbf0',offered_bytes=1200,delivered_bytes=800,backlog_bytes=400,
                oldest_wait_ns=10,latency_p95_ns=20,censored_requests=1,gate_limited=True,
                backend_busy_fraction=.5,resource_busy=False)
    values.update(updates);return StackWindowFacts(**values)


def facts(item=None,**updates):
    values=dict(start_ns=0,end_ns=1_000_000_000,guard_state='normal',stacks=(item or stack(),),
                current_budget_bytes={'hbf0':800},shared_endpoint_caps_bytes={'hbm0.link':1000},
                route_endpoints={'hbf0':('hbm0.link','hbm0.link')})
    values.update(updates);return WindowFacts(**values)


class ReadRatePolicyTests(unittest.TestCase):
    def decision(self,policy,window):return policy.evaluate(window).stack_decisions[0]

    def test_default_off_is_identity_even_during_shutdown(self):
        p=ReadRatePolicy(profile(enabled=False))
        row=self.decision(p,facts(guard_state='shutdown'))
        self.assertEqual((row.budget_bytes,row.outcome),(800,'DISABLED'))

    def test_all_three_arms_share_severe_and_shutdown(self):
        for strategy in ('guard_only','thermal_hysteresis_guard','read_rate_feedback_thermal_guard_v1'):
            p=ReadRatePolicy(profile(strategy=strategy))
            self.assertEqual(self.decision(p,facts(guard_state='severe')).budget_bytes,50)
            decision=p.evaluate(facts(guard_state='shutdown'))
            self.assertEqual(decision.stack_decisions[0].budget_bytes,0)
            self.assertEqual(decision.shared_endpoint_budget_bytes['hbm0.link'],0)

    def test_guard_only_does_not_apply_light_hysteresis_cap(self):
        row=self.decision(ReadRatePolicy(profile(strategy='guard_only')),
                          facts(guard_state='light',hysteresis_budget_bytes={'hbf0':100}))
        self.assertEqual(row.budget_bytes,2000)
        self.assertEqual(row.outcome,'GUARD_ONLY')

    def test_emergency_recovery_uses_each_strategy_baseline(self):
        p0=ReadRatePolicy(profile(strategy='guard_only'))
        self.assertEqual(self.decision(p0,facts(guard_state='shutdown')).budget_bytes,0)
        self.assertEqual(self.decision(p0,facts(current_budget_bytes={'hbf0':0},
                                                hysteresis_budget_bytes={'hbf0':800})).budget_bytes,2000)
        p1=ReadRatePolicy(profile(strategy='thermal_hysteresis_guard'))
        self.assertEqual(self.decision(p1,facts(guard_state='shutdown')).budget_bytes,0)
        self.assertEqual(self.decision(p1,facts(current_budget_bytes={'hbf0':0},
                                                hysteresis_budget_bytes={'hbf0':800})).budget_bytes,800)
        p2=ReadRatePolicy(profile())
        self.assertEqual(self.decision(p2,facts(guard_state='shutdown')).budget_bytes,0)
        restored=self.decision(p2,facts(current_budget_bytes={'hbf0':0},
                                        item=stack(gate_limited=False,backend_busy_fraction=None,
                                                   resource_busy=None)))
        self.assertEqual(restored.budget_bytes,100)

    def test_light_shared_endpoint_cap_applies_only_to_p1_and_p2(self):
        for strategy,expected in [('guard_only',1000),('thermal_hysteresis_guard',500),
                                  ('read_rate_feedback_thermal_guard_v1',500)]:
            decision=ReadRatePolicy(profile(strategy=strategy,light_fraction=.5)).evaluate(
                facts(endpoint_guard_states={'hbm0.link':'light'}))
            self.assertEqual(decision.shared_endpoint_budget_bytes['hbm0.link'],expected)

    def test_guard_only_and_hysteresis_do_not_learn(self):
        guard=self.decision(ReadRatePolicy(profile(strategy='guard_only')),facts())
        self.assertEqual((guard.action,guard.budget_bytes),('INCREASE',2000))
        hysteresis=self.decision(ReadRatePolicy(profile(strategy='thermal_hysteresis_guard')),
                                 facts(hysteresis_budget_bytes={'hbf0':600}))
        self.assertEqual(hysteresis.budget_bytes,600)

    def test_increase_only_when_gate_limits_and_backend_idle(self):
        p=ReadRatePolicy(profile())
        row=self.decision(p,facts())
        self.assertEqual((row.action,row.budget_bytes),('INCREASE',900))
        busy=self.decision(ReadRatePolicy(profile()),facts(stack(gate_limited=True,
            backend_busy_fraction=1.0,resource_busy=True)))
        self.assertEqual((busy.action,busy.outcome),('HOLD','UNMET_TARGET'))
        self.assertIn('BACKEND_OR_RESOURCE_BUSY_HOLD',busy.reasons)

    def test_unknown_occupancy_holds_and_does_not_infer_retry(self):
        row=self.decision(ReadRatePolicy(profile()),facts(stack(backend_busy_fraction=None,
                                                               resource_busy=None,retry_count=None)))
        self.assertEqual(row.action,'HOLD')
        self.assertEqual(row.reasons,('BOTTLENECK_UNKNOWN_HOLD',))

    def test_insufficient_demand_does_not_learn(self):
        row=self.decision(ReadRatePolicy(profile()),facts(stack(offered_bytes=500,delivered_bytes=500,
                                                               backlog_bytes=0,gate_limited=False)))
        self.assertEqual((row.action,row.outcome),('HOLD','INSUFFICIENT_DEMAND'))

    def test_target_met_holds_or_smooths_overdelivery(self):
        p=ReadRatePolicy(profile())
        stable=self.decision(p,facts(stack(delivered_bytes=970)))
        self.assertEqual(stable.action,'HOLD')
        excess=self.decision(p,facts(stack(delivered_bytes=1200)))
        self.assertEqual((excess.action,excess.budget_bytes),('DECREASE',700))

    def test_failed_increase_rolls_back_on_worse_backlog(self):
        p=ReadRatePolicy(profile())
        first=self.decision(p,facts())
        self.assertEqual(first.action,'INCREASE')
        second_window=facts(stack(delivered_bytes=790,backlog_bytes=500,latency_p95_ns=30),
                            start_ns=1_000_000_000,end_ns=2_000_000_000,
                            current_budget_bytes={'hbf0':900})
        second=self.decision(p,second_window)
        self.assertEqual((second.action,second.budget_bytes),('DECREASE',800))
        self.assertIn('ROLLBACK_NO_DELIVERY_GAIN',second.reasons)

    def test_maintenance_due_is_reported_not_renamed_reliability(self):
        row=self.decision(ReadRatePolicy(profile()),facts(stack(maintenance_due_bytes=4096)))
        self.assertIn('MAINTENANCE_DUE_VISIBLE',row.reasons)

    def test_atomic_bytes_and_duplicate_shared_endpoint_charge_once(self):
        decision=ReadRatePolicy(profile()).evaluate(facts())
        ledger=ByteTokenLedger(decision)
        self.assertTrue(ledger.can_consume('hbf0',['hbm0.link','hbm0.link'],200))
        self.assertEqual(ledger.endpoint['hbm0.link'],1000)
        self.assertTrue(ledger.try_consume('hbf0',['hbm0.link','hbm0.link'],200))
        self.assertEqual(ledger.stack['hbf0'],700)
        self.assertEqual(ledger.endpoint['hbm0.link'],800)
        before=(dict(ledger.stack),dict(ledger.endpoint))
        self.assertFalse(ledger.try_consume('hbf0',['hbm0.link'],900))
        self.assertEqual((ledger.stack,ledger.endpoint),before)


if __name__=='__main__':unittest.main()
