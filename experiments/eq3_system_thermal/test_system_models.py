import math
import unittest

from causal_workload import CausalExecutor, build_architecture_trace, TRACE_ORIGIN
from reliability import DAY_NS, ReliabilityLedger, arrhenius_acceleration


class ReliabilityTests(unittest.TestCase):
    def test_age_cadence_wear_and_commit_rules(self):
        ledger = ReliabilityLedger({"ea_ev": 1.04, "tref_k": 358.15,
                                    "initial_equivalent_age_ns": DAY_NS * 9})
        self.assertEqual(arrhenius_acceleration(358.15, 1.04, 358.15), 1.0)
        ledger.advance_temperature("hbf0/ch0/d0/p0/b7", 0, 100, 358.15)
        ledger.record_program("hbf0/ch0/d0/p0/b7", 100, True, "p0")
        ledger.record_program("hbf0/ch0/d0/p0/b7", 100, False, "p1")
        ledger.record_erase("hbf0/ch0/d0/p0/b7", 100, True, "e0")
        ledger.record_refresh_terminal("hbf0/ch0/d0/p0/b7", 100, False, "m0")
        before = ledger.snapshot()["blocks"]["hbf0/ch0/d0/p0/b7"]
        self.assertEqual(before["program_phase_started_count"], 2)
        self.assertEqual(before["program_completed_count"], 1)
        self.assertEqual(before["erase_phase_started_count"], 1)
        self.assertEqual(before["erase_completed_count"], 1)
        self.assertEqual(before["equivalent_age_ns"], DAY_NS * 9 + 100)
        self.assertEqual(before["next_wall_due_ns"], DAY_NS)  # equivalent age does not compress wall cadence
        ledger.record_refresh_terminal("hbf0/ch0/d0/p0/b7", 100, True, "m1")
        after = ledger.snapshot()["blocks"]["hbf0/ch0/d0/p0/b7"]
        self.assertEqual(after["equivalent_age_ns"], 0)
        self.assertEqual(after["next_wall_due_ns"], DAY_NS + 100)

    def test_ea_has_explicit_conservative_trigger_consumer(self):
        hot = ReliabilityLedger({"ea_ev": 1.08, "refresh_trigger": "equivalent_age_or_wall",
                                 "initial_equivalent_age_ns": DAY_NS - 10})
        hot.advance_temperature("b0", 0, 1, 400.0)
        self.assertIn("EQUIVALENT_AGE_24H_CONSERVATIVE_POLICY", hot.due_reasons("b0", 1))
        wall = ReliabilityLedger({"ea_ev": 1.08, "refresh_trigger": "wall_only",
                                  "initial_equivalent_age_ns": DAY_NS * 2})
        wall.advance_temperature("b0", 0, 1, 400.0)
        self.assertEqual(wall.due_reasons("b0", 1), [])

    def test_retry_is_explicit_scenario_not_rber(self):
        ledger = ReliabilityLedger({"ea_ev": 1.01})
        ledger.record_retry_scenario("b0", 0, 2, 17, 1e-9, "fixed-ecc-arm")
        row = ledger.drain_events()[-1]
        self.assertEqual(row["evidence_class"], "SCENARIO_ASSUMPTION_NO_RBER_CLAIM")


class CausalTests(unittest.TestCase):
    @staticmethod
    def small_trace():
        return {'trace_origin':TRACE_ORIGIN,'batches':[
            {'arrival_ns':at,'terminal_task_id':f'c{i}','token_ids':[str(i)],'tasks':[
                {'task_id':f'r{i}','type':'storage','tensor':{'tensor_id':'weight','bytes':4096},
                 'issue_after':[],'consume_after':[],'consumer_count':1,'batch_interval_id':i},
                {'task_id':f'c{i}','type':'compute','duration_ns':5,'depends_on':[f'r{i}']}]}
            for i,at in enumerate((0,100))]}

    def test_hbm_cache_fill_and_hits_require_actual_service(self):
        executor=CausalExecutor(self.small_trace(),{
            'cache_mode':'external_hbm','cache_capacity_bytes':4096,'coalescing_enabled':True,
            'prefetch_wait_mode':'wait_at_consumption','stripe_unit_bytes':4096,
            'stripe_targets':[{'stack':'hbf0','channel':'0','route':'direct'}],
            'fast_stripe_targets':[{'stack':'hbm0','channel':'0','route':'direct'}]})
        job=executor.poll(0)[0]
        executor.complete(job['job_id'],10,4096)
        self.assertNotIn('weight',executor.cache)
        fill=executor.poll(10)[0]
        self.assertEqual(fill['operation'],'hbm_fill')
        executor.complete(fill['job_id'],20,4096)
        self.assertIn('weight',executor.cache)
        second=executor.poll(100)[0]
        self.assertEqual(second['stack'],'hbm0')
        self.assertNotIn('r1',executor.done)
        executor.complete(second['job_id'],110,4096)
        executor.poll(110)
        self.assertNotIn('c1',executor.done)
        executor.poll(115)
        self.assertEqual(executor.done['c1'],115)

    def test_retry_consumes_work_before_unique_logical_success(self):
        x=CausalExecutor(self.small_trace(),{'cache_mode':'disabled','cache_capacity_bytes':0,
            'coalescing_enabled':True,'prefetch_wait_mode':'wait_at_consumption','stripe_unit_bytes':4096,
            'retry_count_per_source_read':1,
            'stripe_targets':[{'stack':'hbf0','channel':'0','route':'direct'}]})
        read=x.poll(0)[0];x.complete(read['job_id'],10,4096)
        self.assertNotIn('r0',x.done)
        retry=x.poll(10)[0];self.assertEqual(retry['operation'],'retry')
        x.complete(retry['job_id'],20,4096)
        self.assertEqual(x.done['r0'],20)
        success=[r for r in x.events if r['kind']=='storage_complete']
        self.assertEqual(len(success),1)
        self.assertEqual(success[0]['retry_count'],1)

    def test_compute_is_a_shared_resource(self):
        trace=self.small_trace();trace['batches'][1]['arrival_ns']=0
        executor=CausalExecutor(trace,{'cache_mode':'disabled','cache_capacity_bytes':0,
            'coalescing_enabled':False,'prefetch_wait_mode':'wait_at_consumption','stripe_unit_bytes':4096,
            'stripe_targets':[{'stack':'hbf0','channel':'0','route':'direct'}]})
        for job in executor.poll(0):executor.complete(job['job_id'],10,job['bytes'])
        executor.poll(10);executor.poll(15);executor.poll(20)
        intervals=[(r['start_ns'],r['completion_ns']) for r in executor.events if r['kind']=='compute_complete']
        self.assertEqual(intervals,[(10,15),(15,20)])

    def test_issue_stall_blocks_compute_until_prefetch_delivery(self):
        trace={'trace_origin':TRACE_ORIGIN,'batches':[{'arrival_ns':0,'terminal_task_id':'end',
            'token_ids':['t'],'tasks':[
            {'task_id':'read','type':'storage','tensor':{'tensor_id':'a','bytes':4096},
             'issue_after':[],'consume_after':[],'consumer_count':1,'batch_interval_id':0},
            {'task_id':'compute','type':'compute','depends_on':['read'],'duration_ns':100},
            {'task_id':'prefetch','type':'storage','tensor':{'tensor_id':'b','bytes':4096},
             'issue_after':['read'],'consume_after':['compute'],'consumer_count':1,'batch_interval_id':0},
            {'task_id':'end','type':'compute','depends_on':['prefetch'],'duration_ns':1}]}]}
        starts={}
        for mode in ('stall_at_issue','wait_at_consumption'):
            x=CausalExecutor(trace,{'cache_mode':'disabled','cache_capacity_bytes':0,
                'coalescing_enabled':True,'prefetch_wait_mode':mode,'stripe_unit_bytes':4096,
                'stripe_targets':[{'stack':'hbf0','channel':'0','route':'direct'}]})
            read=x.poll(0)[0];x.complete(read['job_id'],10,4096)
            prefetch=x.poll(10)[0]
            x.complete(prefetch['job_id'],100,4096);x.poll(100)
            starts[mode]=next(e['start_ns'] for e in x.events if e['kind']=='compute_start')
        self.assertEqual(starts,{'stall_at_issue':100,'wait_at_consumption':10})

    def test_streaming_retirement_preserves_facts_and_accepts_next_batch(self):
        cfg=self.config('Qwen/Qwen2.5-7B-Instruct')
        trace=build_architecture_trace(cfg)
        x=CausalExecutor(trace,{'cache_mode':'disabled','cache_capacity_bytes':0,
            'coalescing_enabled':True,'prefetch_wait_mode':'wait_at_consumption','stripe_unit_bytes':4096,
            'stripe_targets':[{'stack':'hbf0','channel':'0','route':'direct'}]})
        now=0
        for _ in range(1000):
            jobs=x.poll(now)
            if jobs:
                now+=1
                for job in jobs:x.complete(job['job_id'],now,job['bytes'])
            elif (event:=x.next_internal_event_ns()) is not None:now=event
            else:break
        retired=x.retire_completed_batches(now)
        self.assertEqual(retired[0]['token_count'],3)
        self.assertEqual(x.tasks,{})
        next_trace=build_architecture_trace({**cfg,'first_interval':1})
        x.append_trace(next_trace)
        self.assertTrue(x.poll(now))
        with self.assertRaisesRegex(ValueError,'duplicate'):x.append_trace(next_trace)

    @staticmethod
    def config(model):
        return {"model_id": model, "batch_intervals": 1, "batch_size": 3,
                "batch_interval_ns": 13, "prefetch_layers": 2,
                "attention_compute_ns_per_token": 3,
                "mlp_compute_ns_per_token": 5,
                "output_compute_ns_per_token": 11,
                "embedding_access": "full_weight_stress"}

    def test_official_sizes_and_external_completion_gate(self):
        for model, expected in (("Qwen/Qwen2.5-7B-Instruct", 15231233024),
                                ("Qwen/Qwen2.5-72B-Instruct", 145412407296)):
            trace = build_architecture_trace(self.config(model))
            tensors = {}
            for task in trace["batches"][0]["tasks"]:
                if task["type"] == "storage":
                    tensors[task["tensor"]["tensor_id"]] = task["tensor"]["bytes"]
            self.assertEqual(sum(tensors.values()), expected)

        trace = build_architecture_trace(self.config("Qwen/Qwen2.5-7B-Instruct"))
        executor = CausalExecutor(trace, {"cache_mode":"disabled", "coalescing_enabled":True, "prefetch_wait_mode":"wait_at_consumption", 
            "cache_capacity_bytes": 0, "migration_mode": "fixed",
            "stripe_unit_bytes": 4096,
            "default_placement": {"stack": "hbf0", "channel": "0", "route": "direct"},
        })
        first = executor.poll(0)
        self.assertTrue(first)
        self.assertFalse(any(x["complete"] for x in executor.result(1_000_000)["tokens"]))
        # Actual completion timestamps, not model bytes, unlock the dependency DAG.
        now = 7
        for job in first:
            executor.complete(job["job_id"], now, job["bytes"])
        for _ in range(1000):
            jobs = executor.poll(now)
            if jobs:
                now += 7
                for job in jobs:
                    executor.complete(job["job_id"], now, job["bytes"])
                continue
            event = executor.next_internal_event_ns()
            if event is not None:
                now = event
                continue
            if all(x["complete"] for x in executor.result(now)["tokens"]):
                break
            self.fail("causal executor stalled")
        result = executor.result(now)
        self.assertTrue(all(x["complete"] for x in result["tokens"]))
        self.assertEqual(len({x["completion_ns"] for x in result["tokens"]}), 1)
        self.assertNotEqual(result["tokens"][0]["completion_ns"] % 20_000_000, 0)
        self.assertTrue(all(job["metadata"]["batch_consumer_count"] == 3
                            for job in result["jobs"] if job["operation"] == "read"))
        consumed = [x for x in result["events"] if x["kind"] == "storage_consumed"]
        self.assertTrue(any(x["consume_ns"] > x["ready_ns"] for x in consumed))
        self.assertTrue(all("batch_interval_id" in job["metadata"]
                            for job in result["jobs"] if job["operation"] == "read"))

    def test_migration_read_program_commit_and_erase_are_distinct(self):
        executor=CausalExecutor(self.small_trace(),{
            'cache_mode':'disabled','cache_capacity_bytes':0,'coalescing_enabled':True,
            'prefetch_wait_mode':'wait_at_consumption','stripe_unit_bytes':4096,
            'migration_mode':'basic','migration_capacity_bytes':1048576,
            'migration_access_threshold':1,
            'stripe_targets':[{'stack':'hbf0','channel':'0','route':'direct'}],
            'fast_stripe_targets':[{'stack':'hbf1','channel':'0','route':'direct'}]})
        job=executor.poll(0)[0];executor.complete(job['job_id'],10,4096)
        copy=executor.offer_migrations(10)[0]
        self.assertEqual(copy['stack'],'hbf0');self.assertEqual(copy['operation'],'read')
        executor.complete(copy['job_id'],20,copy['bytes'])
        program=executor.poll(20)[0]
        self.assertEqual(program['stack'],'hbf1');self.assertEqual(program['operation'],'migration_program')
        self.assertNotEqual(executor.tensor_tier.get('weight'),'fast')
        executor.complete(program['job_id'],30,program['bytes'])
        self.assertEqual(executor.tensor_tier['weight'],'fast')
        erase=executor.poll(30)[0]
        self.assertEqual(erase['stack'],'hbf0');self.assertEqual(erase['operation'],'erase')
        self.assertEqual(erase['bytes'],1048576)
        executor.complete(erase['job_id'],40,erase['bytes'])
        self.assertEqual(executor.pending_migrations,{})
        next_read=executor.poll(100)[0]
        self.assertEqual(next_read['stack'],'hbf1')

    def test_migration_commit_defers_source_erase_until_outstanding_source_read_finishes(self):
        trace=self.small_trace()
        trace['batches'][1]['arrival_ns']=11
        executor=CausalExecutor(trace,{
            'cache_mode':'disabled','cache_capacity_bytes':0,'coalescing_enabled':False,
            'prefetch_wait_mode':'wait_at_consumption','stripe_unit_bytes':4096,
            'migration_mode':'basic','migration_capacity_bytes':1048576,
            'migration_access_threshold':1,
            'stripe_targets':[{'stack':'hbf0','channel':'0','route':'direct'}],
            'fast_stripe_targets':[{'stack':'hbf1','channel':'0','route':'direct'}]})
        first=executor.poll(0)[0]
        executor.complete(first['job_id'],10,first['bytes'])
        migration_read=executor.offer_migrations(10)[0]
        outstanding=executor.poll(11)[0]
        self.assertEqual(outstanding['stack'],'hbf0')
        executor.complete(migration_read['job_id'],20,migration_read['bytes'])
        destination=executor.poll(20)[0]
        executor.complete(destination['job_id'],30,destination['bytes'])
        self.assertEqual(executor.tensor_tier['weight'],'fast')
        self.assertFalse(any(job['operation']=='erase' for job in executor.poll(30)))
        executor.complete(outstanding['job_id'],35,outstanding['bytes'])
        erase=next(job for job in executor.poll(35) if job['operation']=='erase')
        self.assertEqual(erase['stack'],'hbf0')
        executor.complete(erase['job_id'],40,erase['bytes'])
        self.assertEqual(executor.pending_migrations,{})

        next_trace={'trace_origin':TRACE_ORIGIN,'batches':[{
            'interval_id':2,'arrival_ns':50,'terminal_task_id':'c2','token_ids':['2'],
            'tasks':[{'task_id':'r2','type':'storage',
                      'tensor':{'tensor_id':'weight','bytes':4096},
                      'issue_after':[],'consume_after':[],'consumer_count':1,
                      'batch_interval_id':2},
                     {'task_id':'c2','type':'compute','duration_ns':5,
                      'depends_on':['r2']}]}]}
        executor.append_trace(next_trace)
        next_read=next(job for job in executor.poll(50) if job['operation']=='read')
        self.assertEqual(next_read['stack'],'hbf1')

    def test_full_capacity_lru_serves_later_interval_without_external_reads(self):
        trace = build_architecture_trace({**self.config("Qwen/Qwen2.5-7B-Instruct"),
                                          "batch_intervals": 2,
                                          "batch_interval_ns": 10_000})
        executor = CausalExecutor(trace, {"cache_mode":"disabled", "coalescing_enabled":True, "prefetch_wait_mode":"wait_at_consumption", 
            "cache_mode":"ideal_metadata_only", "cache_capacity_bytes": trace["official_metadata"]["tensor_payload_bytes"],
            "migration_mode": "fixed",
            "stripe_unit_bytes": 4096,
            "default_placement": {"stack": "hbf0", "channel": "0", "route": "direct"},
        })
        now = 0
        for _ in range(2000):
            jobs = executor.poll(now)
            if jobs:
                now += 1
                for job in jobs:
                    executor.complete(job["job_id"], now, job["bytes"])
            elif (event := executor.next_internal_event_ns()) is not None:
                now = event
            elif all(x["complete"] for x in executor.result(now)["tokens"]):
                break
            else:
                self.fail("two-interval cache execution stalled")
        result = executor.result(now)
        self.assertTrue(all(x["complete"] for x in result["tokens"]))
        self.assertTrue(any(x["kind"] == "cache_hit" for x in result["events"]))
        unique_tensors = {job["metadata"]["tensor_id"] for job in result["jobs"]}
        self.assertEqual(len(result["jobs"]), len(unique_tensors))

    def test_large_tensor_stripes_all_targets_and_waits_for_last_child(self):
        config = {**self.config("Qwen/Qwen2.5-7B-Instruct"),
                  "embedding_access": "selected_token_rows"}
        trace = build_architecture_trace(config)
        embed = trace["batches"][0]["tasks"][0]["tensor"]
        self.assertEqual(embed["bytes"], 3584 * 2 * 3)
        self.assertEqual(embed["access_semantics"], "SELECTED_TOKEN_ROWS_SYNTHETIC_IDENTITIES")
        targets = [{"stack": f"hbf{i}", "channel": str(i), "route": "direct"}
                   for i in range(4)]
        executor = CausalExecutor(trace, {"cache_mode":"disabled", "coalescing_enabled":True, "prefetch_wait_mode":"wait_at_consumption", "cache_capacity_bytes": 0,
                                          "migration_mode": "fixed",
                                          "stripe_unit_bytes": 4096,
                                          "stripe_targets": targets})
        jobs = executor.poll(0)
        group = "causal:interval0:l0:attn_read"
        children = [job for job in jobs if job["metadata"]["parent_group_id"] == group]
        self.assertEqual({job["stack"] for job in children}, {"hbf0", "hbf1", "hbf2", "hbf3"})
        self.assertEqual(sum(job["bytes"] for job in children),
                         executor.tasks["interval0:l0:attn_read"]["tensor"]["bytes"])
        for child in children[:-1]:
            executor.complete(child["job_id"], 5, child["bytes"])
        self.assertNotIn("external_ready_ns", executor.tasks["interval0:l0:attn_read"])
        executor.complete(children[-1]["job_id"], 9, children[-1]["bytes"])
        self.assertEqual(executor.tasks["interval0:l0:attn_read"]["external_ready_ns"], 9)


if __name__ == "__main__":
    unittest.main()
