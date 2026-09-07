"""CPU-only orchestration tests for the provisional owned triplet parent."""

import json
import os
from pathlib import Path
import py_compile
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import hf_routing_runner as runner


class Guard:
    def __init__(self, events):
        self.events = events
        self.child = None
        self.started = False
        self.active = False

    def __enter__(self):
        self.active = True
        self.events.append("guard-enter")
        return self

    def check(self, phase="periodic"):
        self.events.append(("guard-check", phase))

    def __exit__(self, *unused):
        self.active = False
        self.events.append("guard-exit")


class RoutingRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=runner.ROOT / "results/gold")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.addCleanup(self._cleanup_private_work)
        self.events = []
        self.identities = []
        self.cuda_requests = []
        self.bad_cuda_binding = False
        self.fail_arm = None
        self.raise_arm = None
        self.signal_objects = []
        self.current_calls = 0
        self.current_fail_at = None
        self.omit_status_arm = None
        self.invalid_wrapper_arm = None
        self.missing_bootstrap_arm = None
        self.malformed_bootstrap_arm = None
        self.bad_bootstrap_binding_arm = None
        self.corrupt_status_arm = None
        self.guard = None
        self.postcheck_guard_states = []
        self.snapshots = (
            SimpleNamespace(
                bundle=self.base / "metadata",
                receipt_bytes=b"receipt",
                complete_bytes=b"complete",
                artifacts=(),
            ),
            SimpleNamespace(manifest_bytes=b"runtime-manifest", artifacts=()),
            SimpleNamespace(manifest_bytes=b"startup-manifest", artifacts=()),
            SimpleNamespace(manifest_bytes=b"tuning-manifest", config_bytes=None),
        )

    def _cleanup_private_work(self):
        fixed = runner.ROOT / "results/tmp/hf-routing"
        for status in self.base.rglob("triplet-status.json"):
            document = json.loads(status.read_bytes())
            path = Path(document["private_work_root"])
            if path.parent == fixed and path.name.startswith(status.parent.name + "-"):
                shutil.rmtree(path, ignore_errors=True)

    def dependencies(self):
        test = self

        class Context:
            def __enter__(self):
                return Guard(test.events).__enter__()

            def __exit__(self, *args):
                test.events.append("guard-exit")

        def freeze(*args):
            test.events.append("freeze")
            return test.snapshots

        def topology(uuid):
            test.events.append("topology")
            return dict(
                index=0,
                uuid=uuid,
                name=runner.DEVICE_NAME,
                capability=[12, 0],
                mig="Disabled",
            )

        def source(*args):
            return {"root": str(runner.ROOT), "root_identity": [1, 2], "files": {}}

        def launch(
            argv,
            env,
            attempt,
            phase,
            guard,
            status_callback,
            signals,
            timeout,
            poll_seconds,
            **kwargs,
        ):
            arm = Path(attempt).name
            test.signal_objects.append(signals)
            if arm == test.raise_arm:
                raise RuntimeError("launch-stage-original")
            identity = dict(
                pid=100 + len(test.identities),
                boot_id="b",
                start_time=50 + len(test.identities),
                ppid=1,
                pgrp=100 + len(test.identities),
                session=100 + len(test.identities),
            )
            test.identities.append(identity)
            status_callback(identity, phase)
            guard.started = True
            process = Path(attempt)
            envelope_path = Path(argv[argv.index("--envelope") + 1])
            envelope = json.loads(envelope_path.read_bytes())
            output = Path(envelope["request"]["output_dir"])
            output.mkdir()
            input_raw = b"{}"
            status_raw = json.dumps(
                dict(
                    status="FAILED"
                    if arm == test.fail_arm
                    else "ARM_RETURNED_UNVALIDATED",
                    arm=arm,
                    scientific_validation_passed=False,
                    provenance="MOCK",
                ),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()

            test.cuda_requests.append(dict(envelope["request"]))
            if envelope["request"].get("capture_cuda_route_events", False):
                for name in ("protocol.json","raw-return.json","raw-routes.npy"):
                    (output/name).write_bytes(b"MOCK event binding bytes")
                counts=dict(batch_events=384,save_batches=8,reader_calls=1,saved_slots=39,
                            decode_nodes=336,observed_intervals=335,missing_terminal=1)
                event=dict(status="UNVALIDATED_ROUTE_INTERVAL_CAPTURE",counts=counts,
                    scientific_validation_passed=False,gpu_uuid=envelope["request"]["gpu_uuid"],
                    worker_identity=identity,
                    timing_semantics="ROUTE_TO_ROUTE_DEVICE_ELAPSED_INCLUDING_CAPTURE_AND_SCHEDULING",
                    bindings=dict(arm=arm,run_id=envelope["request"]["run_id"],
                        input_binding_sha256=runner._digest(input_raw),
                        protocol_sha256=runner._digest((output/"protocol.json").read_bytes()),
                        raw_return_sha256=runner._digest((output/"raw-return.json").read_bytes()),
                        raw_routes_sha256=runner._digest((output/"raw-routes.npy").read_bytes())))
                if test.bad_cuda_binding:event["bindings"]["raw_routes_sha256"]="0"*64
                event_raw=runner._canonical(event)
                (output/"route-device-events.json").write_bytes(event_raw)
                status_document=json.loads(status_raw)
                status_document["route_cuda_events"]=dict(enabled=True,status=event["status"],counts=counts,
                    artifact="route-device-events.json",sha256=runner._digest(event_raw))
                status_raw=runner._canonical(status_document)
            if "routing_capture" in envelope["request"]:
                protocol=__import__('hf_routing_worker');members=[]
                for ordinal,prompt in enumerate(envelope['request']['prompt_members']):
                    prefix='member-'+str(ordinal).zfill(4)+'-'
                    payloads={'raw-return.json':runner._canonical(dict(ordinal=ordinal,prompt=prompt)),
                              'raw-routes.npy':('routes-'+str(ordinal)).encode(),
                              'routing.jsonl':runner._canonical(dict(member=ordinal)),
                              'trace-summary.json':runner._canonical(dict(member=ordinal,status='CAPTURED_UNVALIDATED'))}
                    for suffix,raw in payloads.items():(output/(prefix+suffix)).write_bytes(raw)
                    members.append(dict(ordinal=ordinal,prompt_token_ids=prompt,
                        request_id='request-'+str(ordinal).zfill(4),
                        raw_return_sha256=runner._digest(payloads['raw-return.json']),
                        raw_routes_sha256=runner._digest(payloads['raw-routes.npy']),
                        routing_path=str(output/(prefix+'routing.jsonl')),
                        routing_sha256=runner._digest(payloads['routing.jsonl']),
                        trace_summary_sha256=runner._digest(payloads['trace-summary.json'])))
                indexes=[]
                for wave in range(2):
                    name='members-index-wave-'+str(wave)+'.json'
                    selected=members[wave*8:(wave+1)*8]
                    index=dict(schema_version=1,source_kind='CAPTURED_ROUTE',inventory_sha256='c'*64,
                        members=[dict(member_id='member-'+str(row['ordinal']).zfill(4),
                            path=row['routing_path'],sha256=row['routing_sha256']) for row in selected])
                    raw=runner._canonical(index);(output/name).write_bytes(raw)
                    indexes.append(dict(wave_index=wave,path=str(output/name),sha256=runner._digest(raw)))
                manifest=dict(schema_version=1,status='CAPTURED_16_MEMBERS_UNVALIDATED',
                    cell_id=runner.ROUTING_CAPTURE_CELL,members=members,member_indexes=indexes,
                    scientific_validation_passed=False)
                manifest_raw=runner._canonical(manifest);(output/'member-manifest.json').write_bytes(manifest_raw)
                composition=protocol.make_trace_composition(
                    dict(mode='MULTI_PROMPT_ROUTING_CAPTURE',prompt_members=envelope['request']['prompt_members'],
                         **envelope['request']['routing_capture']),
                    members,indexes)
                composition_raw=runner._canonical(composition);(output/'trace-composition.json').write_bytes(composition_raw)
                status_document=json.loads(status_raw)
                status_document['routing_capture']=dict(cell_id=runner.ROUTING_CAPTURE_CELL,member_count=16,
                    member_manifest_sha256=runner._digest(manifest_raw),
                    trace_composition_sha256=runner._digest(composition_raw),
                    status='TRACE_COMPOSED_ROUTING_CAPTURE')
                status_raw=runner._canonical(status_document)

            (output / "input-binding.json").write_bytes(input_raw)
            if arm == test.corrupt_status_arm:
                (output / "worker-status.json").write_bytes(b"{corrupt")
            elif arm != test.omit_status_arm:
                (output / "worker-status.json").write_bytes(status_raw)
            if arm != test.fail_arm:
                source_sha = runner._digest(
                    runner._canonical(envelope["request"]["project_sources"])
                )
                wrapper = dict(
                    schema_version=1,
                    status="ARM_RETURNED_UNVALIDATED",
                    arm=arm,
                    provenance="MOCK",
                    scientific_validation_passed=False,
                    envelope_sha256=argv[argv.index("--envelope-sha256") + 1],
                    input_binding_sha256=runner._digest(input_raw),
                    worker_status_sha256=runner._digest(status_raw),
                    device_observations_sha256=None,
                    source_manifest_sha256=source_sha,
                )
                if arm == test.invalid_wrapper_arm:
                    wrapper["worker_status_sha256"] = "0" * 64
                (process / "owned-worker-status.json").write_bytes(
                    runner._canonical(wrapper)
                )
                if arm != test.missing_bootstrap_arm:
                    prefix = Path(
                        envelope["request"]["transport_env"]["PYTHONPYCACHEPREFIX"]
                    )
                    prefix_info = prefix.lstat()
                    ownership = process / "ownership-record.json"
                    bootstrap = dict(
                        schema_version=1,
                        status="OWNED_BOOTSTRAP_VERIFIED",
                        arm=arm,
                        envelope_sha256=argv[
                            argv.index("--envelope-sha256") + 1
                        ],
                        ownership_record_sha256=runner._digest(
                            ownership.read_bytes()
                        ),
                        source_manifest_sha256=source_sha,
                        prefix=str(prefix),
                        prefix_identity=[prefix_info.st_dev, prefix_info.st_ino],
                        runtime_environment_sha256=runner._digest(
                            runner._canonical(envelope["request"]["runtime_env"])
                        ),
                        transport_environment=envelope["request"]["transport_env"],
                        interpreter_facts=runner._test_bootstrap_facts(prefix),
                        scientific_validation_passed=False,
                    )
                    if arm == test.bad_bootstrap_binding_arm:
                        bootstrap["envelope_sha256"] = "0" * 64
                    if arm == test.malformed_bootstrap_arm:
                        (process / "bootstrap.json").write_bytes(b"{broken")
                    else:
                        (process / "bootstrap.json").write_bytes(
                            runner._canonical(bootstrap)
                        )
            return 1 if arm == test.fail_arm else 0

        def current(*args):
            test.current_calls += 1
            test.postcheck_guard_states.append(
                None if test.guard is None else test.guard.active
            )
            if test.current_calls == test.current_fail_at:
                raise ValueError("final-current-original")
            test.events.append("current")

        def guard_factory(*args, **kwargs):
            test.guard = Guard(test.events)
            return test.guard

        return dict(
            freeze_inputs=freeze,
            topology_probe=topology,
            capture_project=source,
            guard_factory=guard_factory,
            run_child=launch,
            current_check=current,
            source_recheck=lambda *a: test.events.append("source-recheck"),
            bootstrap_facts=lambda p: runner._test_bootstrap_facts(p),
            signal_state=lambda: {"signal": 0},
        )

    def test_triplet_uses_one_guard_three_distinct_processes_and_fresh_work_roots(self):
        out = self.base / "owned-attempt"
        result = runner.run_triplet(
            self.base / "metadata", out, "GPU-X", _test_dependencies=self.dependencies()
        )
        self.assertEqual(result["status"], "PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED")
        self.assertEqual(
            [r["arm"] for r in result["arms"]], ["native", "capture", "repeat"]
        )
        self.assertEqual(len({i["pid"] for i in self.identities}), 3)
        self.assertEqual(self.events.count("guard-enter"), 1)
        self.assertEqual(self.events.count("guard-exit"), 1)
        self.assertFalse(result["scientific_validation_passed"])
        self.assertEqual(result["provenance"], "MOCK")
        self.assertFalse((out / "COMPLETE.json").exists())
        self.assertFalse((out / "DONE").exists())
        work = [r["work_dir"] for r in result["arms"]]
        self.assertEqual(len(set(work)), 3)

    def test_opt_in_multi_prompt_capture_uses_one_owned_arm_and_preserves_default_triplet(self):
        out=self.base/'multi-prompt'
        result=runner.run_triplet(self.base/'metadata',out,'GPU-X',
            routing_capture_cell='routing_capture-01469',_test_dependencies=self.dependencies())
        self.assertEqual(result['status'],'PROVISIONAL_ROUTING_CAPTURE_RETURNED_UNVALIDATED')
        self.assertEqual([row['arm'] for row in result['arms']],['capture'])
        self.assertEqual(len(self.identities),1)
        request=self.cuda_requests[0]
        self.assertEqual(request['routing_capture'],runner._routing_capture_request())
        self.assertEqual(request['prompt_members'],runner._routing_capture_prompts())
        self.assertNotIn('capture_cuda_route_events',request)
        self.assertEqual(self.events.count('guard-enter'),1);self.assertEqual(self.events.count('guard-exit'),1)
        manifest=json.loads((out/'arms/capture/member-manifest.json').read_bytes())
        self.assertEqual(len(manifest['members']),16);self.assertEqual(len(manifest['member_indexes']),2)
        with self.assertRaises(ValueError):
            runner.run_triplet(self.base/'metadata',self.base/'bad-combination','GPU-X',
                routing_capture_cell='routing_capture-01469',capture_cuda_route_events=True,
                _test_dependencies=self.dependencies())
        self.identities=[];self.events=[];self.cuda_requests=[];self.guard=None
        legacy=runner.run_triplet(self.base/'metadata',self.base/'legacy-default','GPU-X',
            _test_dependencies=self.dependencies())
        self.assertEqual(legacy['status'],'PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED')
        self.assertEqual([row['arm'] for row in legacy['arms']],['native','capture','repeat'])
        self.assertEqual(len(self.identities),3)

    def test_arm_failure_stops_later_arms_but_runs_final_current_and_source_checks(
        self,
    ):
        self.fail_arm = "capture"
        out = self.base / "failed"
        with self.assertRaises(runner.TripletFailure):
            runner.run_triplet(
                self.base / "metadata",
                out,
                "GPU-X",
                _test_dependencies=self.dependencies(),
            )
        self.assertEqual(len(self.identities), 2)
        self.assertGreaterEqual(self.events.count("current"), 2)
        self.assertGreaterEqual(self.events.count("source-recheck"), 2)
        status = json.loads((out / "triplet-status.json").read_text())
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(status["arms"][-1]["worker_status"]["status"], "FAILED")

    def test_topology_failure_prevents_guard_and_launch(self):
        deps = self.dependencies()
        deps["topology_probe"] = lambda _: dict(
            index=1,
            uuid="GPU-X",
            name=runner.DEVICE_NAME,
            capability=[12, 0],
            mig="Disabled",
        )
        with self.assertRaises(ValueError):
            runner.run_triplet(
                self.base / "metadata",
                self.base / "bad-topology",
                "GPU-X",
                _test_dependencies=deps,
            )
        self.assertEqual(self.identities, [])
        self.assertIn("guard-enter", self.events)
        self.assertIn("guard-exit", self.events)

    def test_status_callback_is_exclusive_durable_and_idempotently_bound(self):
        attempt = self.base / "callback"
        process = attempt / "process/native"
        process.mkdir(parents=True)
        binding = dict(
            arm="native",
            run_id="r",
            attempt_dir=str(attempt),
            process_dir=str(process),
            work_dir="/w",
            output_dir="/o",
            gpu_uuid="GPU-X",
            resource_class="GPU_EXCLUSIVE",
            envelope_sha256="a" * 64,
            source_manifest_sha256="b" * 64,
        )
        parent = dict(pid=1, boot_id="b", start_time=1, ppid=0, pgrp=1, session=1)
        callback = runner._ownership_callback(process, binding, parent)
        child = dict(pid=2, boot_id="b", start_time=2, ppid=1, pgrp=2, session=2)
        callback(child, "producer")
        callback(child, "producer")
        record = json.loads((process / "ownership-record.json").read_text())
        self.assertEqual(record["child"], child)
        changed = dict(child, start_time=3)
        with self.assertRaises(ValueError):
            callback(changed, "producer")
        self.assertTrue((process / "ownership-progress.json").is_file())

    def test_ack_mutation_deletion_and_replacement_are_rejected_on_later_callback(self):
        for case in ("mutation", "deletion", "replacement"):
            process = self.base / ("ack-" + case)
            process.mkdir()
            binding = dict(
                arm="native",
                run_id="r",
                attempt_dir=str(self.base),
                process_dir=str(process),
                work_dir="/w",
                output_dir="/o",
                gpu_uuid="GPU-X",
                resource_class="GPU_EXCLUSIVE",
                envelope_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                attempt_dir_identity=[1, 2],
                process_dir_identity=[3, 4],
            )
            parent = dict(pid=1, boot_id="b", start_time=1, ppid=0, pgrp=1, session=1)
            child = dict(pid=2, boot_id="b", start_time=2, ppid=1, pgrp=2, session=2)
            callback = runner._ownership_callback(process, binding, parent)
            callback(child, "producer")
            record = process / "ownership-record.json"
            if case == "mutation":
                changed = json.loads(record.read_bytes())
                changed["run_id"] = "changed"
                record.write_text(json.dumps(changed))
            elif case == "deletion":
                record.unlink()
            else:
                record.unlink()
                record.write_text(json.dumps(dict(replaced=True)))
            with self.subTest(case=case), self.assertRaises(ValueError):
                callback(child, "producer")

    def test_bounded_native_command_rejects_overflow_timeout_and_nonzero(self):
        python = runner.PINNED_PYTHON
        with self.assertRaises(ValueError):
            runner._bounded_command([python, "-c", "print('x'*70000)"], 0.5, 65536)
        with self.assertRaises(TimeoutError):
            runner._bounded_command(
                [python, "-c", "import time;time.sleep(1)"], 0.05, 65536
            )
        code, out, err = runner._bounded_command(
            [python, "-c", "import sys;sys.exit(7)"], 0.5, 65536
        )
        self.assertEqual(code, 7)
        self.assertEqual((out, err), (b"", b""))

    def test_parent_and_arm_prefix_checks_reject_canonical_ancestor_replacement(self):
        worker = __import__("hf_owned_worker")
        parent = self.base / "prefix-parent"
        prefix = parent / "cache/python"
        prefix.mkdir(parents=True)
        with mock.patch.object(runner.sys, "pycache_prefix", str(prefix)):
            parent_retained = runner._parent_prefix_check(prefix, None)
        arm_retained = {
            "directory_identity": worker._check_empty_prefix(prefix),
            "ancestors": worker._path_binding(prefix),
        }
        moved = self.base / "prefix-parent-moved"
        parent.rename(moved)
        parent.mkdir()
        (parent / "cache").mkdir()
        (moved / "cache/python").rename(prefix)
        with (
            mock.patch.object(runner.sys, "pycache_prefix", str(prefix)),
            self.assertRaises(ValueError),
        ):
            runner._parent_prefix_check(prefix, parent_retained)
        with self.assertRaises(ValueError):
            runner._arm_prefix_check(worker, prefix, arm_retained)

    def test_triplet_passes_one_signal_state_object_to_all_attempted_arms(self):
        runner.run_triplet(
            self.base / "metadata",
            self.base / "signal-shared",
            "GPU-X",
            _test_dependencies=self.dependencies(),
        )
        self.assertEqual(len(self.signal_objects), 3)
        self.assertEqual(len({id(value) for value in self.signal_objects}), 1)

    def test_late_and_final_recheck_signals_fail_with_durable_observation(self):
        for mode in ("triplet-final", "final-recheck"):
            self.events = []
            self.identities = []
            self.signal_objects = []
            self.current_calls = 0
            signals = {"signal": 0}
            deps = self.dependencies()
            deps["signal_state"] = lambda: signals
            original_factory = deps["guard_factory"]
            if mode == "triplet-final":
                def guard_factory(*args, **kwargs):
                    guard = original_factory(*args, **kwargs)
                    original_check = guard.check

                    def check(phase="periodic"):
                        original_check(phase)
                        if phase == "triplet-final":
                            signals["signal"] = 15

                    guard.check = check
                    return guard

                deps["guard_factory"] = guard_factory
            else:
                original_current = deps["current_check"]

                def current(*args):
                    original_current(*args)
                    if self.current_calls == 7:
                        signals["signal"] = 2

                deps["current_check"] = current
            out = self.base / ("late-signal-" + mode)
            caught = None
            try:
                runner.run_triplet(
                    self.base / "metadata",
                    out,
                    "GPU-X",
                    _test_dependencies=deps,
                )
            except Exception as error:
                caught = error
            with self.subTest(mode=mode):
                self.assertIsNotNone(caught)
                self.assertIn("signal", str(caught))
                status = json.loads((out / "triplet-status.json").read_bytes())
                self.assertEqual(status["status"], "FAILED")
                self.assertEqual(status["signal"], signals["signal"])
                self.assertEqual(len(status["arms"]), 3)

    def test_signal_scope_remains_active_through_finalization_and_status_fsync(self):
        signals = {"signal": 0}
        active = {"value": False}
        observed = []

        class SignalContext:
            def __enter__(self):
                active["value"] = True
                return signals

            def __exit__(self, *unused):
                active["value"] = False

        original_write = runner._write_exclusive

        def write(path, document):
            if Path(path).name == "triplet-status.json":
                observed.append(active["value"])
            return original_write(path, document)

        deps = self.dependencies()
        deps["signal_state"] = lambda: signals
        with (
            mock.patch.object(runner, "nullcontext", return_value=SignalContext()),
            mock.patch.object(runner, "_write_exclusive", side_effect=write),
        ):
            result = runner.run_triplet(
                self.base / "metadata",
                self.base / "signal-finalization-scope",
                "GPU-X",
                _test_dependencies=deps,
            )
        self.assertEqual(result["status"], "PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED")
        self.assertEqual(observed, [True])
        self.assertFalse(active["value"])

    def test_signal_during_status_write_rewrites_owned_status_after_cutoff(self):
        for preserve_primary in (False, True):
            self.events = []
            self.identities = []
            self.signal_objects = []
            self.current_calls = 0
            self.fail_arm = "native" if preserve_primary else None
            signals = {"signal": 0}
            active = {"value": False}
            replace_scope = []

            class SignalContext:
                def __enter__(self):
                    active["value"] = True
                    return signals

                def __exit__(self, *unused):
                    active["value"] = False

            original_write = runner._write_exclusive
            original_replace = runner._replace_json

            def write(path, document):
                if Path(path).name == "triplet-status.json":
                    signals["signal"] = 15
                return original_write(path, document)

            def replace(path, document):
                if Path(path).name == "triplet-status.json":
                    replace_scope.append(active["value"])
                return original_replace(path, document)

            deps = self.dependencies()
            deps["signal_state"] = lambda: signals
            out = self.base / (
                "signal-during-status-primary"
                if preserve_primary
                else "signal-during-status-success"
            )
            caught = None
            with (
                mock.patch.object(runner, "nullcontext", return_value=SignalContext()),
                mock.patch.object(runner, "_write_exclusive", side_effect=write),
                mock.patch.object(runner, "_replace_json", side_effect=replace),
            ):
                try:
                    runner.run_triplet(
                        self.base / "metadata",
                        out,
                        "GPU-X",
                        _test_dependencies=deps,
                    )
                except Exception as error:
                    caught = error
            with self.subTest(preserve_primary=preserve_primary):
                self.assertIsNotNone(caught)
                status = json.loads((out / "triplet-status.json").read_bytes())
                self.assertEqual(status["status"], "FAILED")
                self.assertEqual(status["signal"], 15)
                self.assertEqual(replace_scope, [True])
                self.assertFalse(active["value"])
                if preserve_primary:
                    self.assertIn("native arm failed", status["error"]["message"])
                    self.assertNotIn("signal", status["error"]["message"])

    def test_status_demotion_rejects_same_byte_replaced_status_target(self):
        signals = {"signal": 0}
        replace_calls = []
        original_write = runner._write_exclusive
        original_replace = runner._replace_json

        def write(path, document):
            identity = original_write(path, document)
            if Path(path).name == "triplet-status.json":
                raw = Path(path).read_bytes()
                replacement = Path(path).with_name("replacement-triplet-status.json")
                replacement.write_bytes(raw)
                os.replace(replacement, path)
                signals["signal"] = 15
            return identity

        def replace(path, document):
            if Path(path).name == "triplet-status.json":
                replace_calls.append(str(path))
            return original_replace(path, document)

        deps = self.dependencies()
        deps["signal_state"] = lambda: signals
        out = self.base / "signal-replaced-status-target"
        with (
            mock.patch.object(runner, "_write_exclusive", side_effect=write),
            mock.patch.object(runner, "_replace_json", side_effect=replace),
            self.assertRaisesRegex(Exception, "status.*changed|status.*identity"),
        ):
            runner.run_triplet(
                self.base / "metadata", out, "GPU-X", _test_dependencies=deps
            )
        self.assertEqual(replace_calls, [])

    def test_malformed_or_misbound_bootstrap_stops_after_first_arm(self):
        for attribute in ("malformed_bootstrap_arm", "bad_bootstrap_binding_arm"):
            self.setUp()
            setattr(self, attribute, "native")
            out = self.base / ("bootstrap-" + attribute)
            caught = None
            try:
                runner.run_triplet(
                    self.base / "metadata",
                    out,
                    "GPU-X",
                    _test_dependencies=self.dependencies(),
                )
            except Exception as error:
                caught = error
            with self.subTest(attribute=attribute):
                self.assertIsNotNone(caught)
                status = json.loads((out / "triplet-status.json").read_bytes())
                self.assertEqual(status["status"], "FAILED")
                self.assertEqual(len(status["arms"]), 1)
                self.assertEqual(
                    status["arms"][0]["primary_error"]["stage"], "worker-status"
                )

    def test_launch_exception_retains_attempted_arm_and_original_stage(self):
        self.raise_arm = "capture"
        out = self.base / "launch-exception"
        with self.assertRaisesRegex(runner.TripletFailure, "launch-stage-original"):
            runner.run_triplet(
                self.base / "metadata",
                out,
                "GPU-X",
                _test_dependencies=self.dependencies(),
            )
        status = json.loads((out / "triplet-status.json").read_bytes())
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual([arm["arm"] for arm in status["arms"]], ["native", "capture"])
        self.assertEqual(
            status["arms"][-1]["primary_error"]["message"], "launch-stage-original"
        )
        self.assertFalse(status["arms"][-1]["owned_exit_observed"])

    def test_every_postlaunch_failure_has_one_finalized_arm_and_stops_triplet(self):
        cases = (
            ("omit_status_arm", "worker-status"),
            ("invalid_wrapper_arm", "worker-status"),
            ("missing_bootstrap_arm", "worker-status"),
            ("corrupt_status_arm", "worker-status"),
        )
        for index, (attribute, stage) in enumerate(cases):
            self.setUp()
            setattr(self, attribute, "native")
            out = self.base / ("postlaunch-" + str(index))
            with self.subTest(case=attribute), self.assertRaises(Exception):
                runner.run_triplet(
                    self.base / "metadata",
                    out,
                    "GPU-X",
                    _test_dependencies=self.dependencies(),
                )
            document = json.loads((out / "triplet-status.json").read_bytes())
            self.assertEqual(document["status"], "FAILED")
            self.assertEqual(len(document["arms"]), 1)
            arm = document["arms"][0]
            self.assertEqual(arm["arm"], "native")
            self.assertEqual(arm["primary_error"]["stage"], stage)
            self.assertEqual(len(self.identities), 1)
            self.assertIn(True, self.postcheck_guard_states)

    def test_arm_postcheck_failure_preserves_primary_and_runs_under_guard(self):
        self.current_fail_at = 2
        out = self.base / "arm-postcheck-failure"
        with self.assertRaisesRegex(ValueError, "final-current-original"):
            runner.run_triplet(
                self.base / "metadata",
                out,
                "GPU-X",
                _test_dependencies=self.dependencies(),
            )
        document = json.loads((out / "triplet-status.json").read_bytes())
        self.assertEqual(len(document["arms"]), 1)
        arm = document["arms"][0]
        self.assertEqual(arm["primary_error"]["stage"], "postchecks")
        self.assertEqual(arm["primary_error"]["message"], "final-current-original")
        self.assertTrue(any(row.get("check") == "current" for row in arm["postcheck_errors"]))
        self.assertIn(True, self.postcheck_guard_states)

    def test_owned_exit_observation_failure_still_finalizes_under_guard(self):
        out = self.base / "owned-exit-observation-failure"
        with (
            mock.patch.object(
                runner,
                "_owned_exit_observation",
                side_effect=OSError("owned-exit-observation-sentinel"),
            ),
            self.assertRaisesRegex(Exception, "owned-exit-observation-sentinel"),
        ):
            runner.run_triplet(
                self.base / "metadata",
                out,
                "GPU-X",
                _test_dependencies=self.dependencies(),
            )
        document = json.loads((out / "triplet-status.json").read_bytes())
        self.assertEqual(len(document["arms"]), 1)
        arm = document["arms"][0]
        self.assertEqual(arm["primary_error"]["stage"], "owned-exit")
        self.assertFalse(arm["owned_exit_observed"])
        self.assertIn(True, self.postcheck_guard_states)

    def test_final_recheck_failure_demotes_provisional_status(self):
        self.current_fail_at = 7
        out = self.base / "final-failure"
        with self.assertRaisesRegex(ValueError, "final-current-original"):
            runner.run_triplet(
                self.base / "metadata",
                out,
                "GPU-X",
                _test_dependencies=self.dependencies(),
            )
        status = json.loads((out / "triplet-status.json").read_bytes())
        self.assertEqual(status["status"], "FAILED")
        self.assertIn("final_recheck_error", status)

    def test_failed_durable_callback_prevents_acknowledged_child_work(self):
        import run_matrix

        attempt = self.base / "ack-failure"
        attempt.mkdir()
        marker = attempt / "must-not-run"
        guard = Guard(self.events)

        def fail_callback(*unused):
            raise OSError("durable record failed")

        argv = [
            runner.PINNED_PYTHON,
            "-I",
            "-S",
            "-B",
            "-c",
            "open(" + repr(str(marker)) + ",'w').write('ran')",
        ]
        with self.assertRaises(OSError):
            run_matrix.run_child(
                argv,
                dict(os.environ),
                attempt,
                "producer",
                guard,
                fail_callback,
                {"signal": 0},
                5,
                0.01,
                bootstrap_no_site=True,
            )
        self.assertFalse(marker.exists())

    def test_three_actual_isolated_children_are_distinct_and_keep_prefixes_empty(self):
        import run_matrix
        import run_manifest

        identities = []
        guard = Guard(self.events)
        signal_state = {"signal": 0}
        source_root = self.base / "poison-source"
        source_root.mkdir()
        source = source_root / "poisoned_owned.py"
        source.write_text("VALUE='poisoned'\n")
        source_cache = source_root / "__pycache__"
        source_cache.mkdir()
        cached = source_cache / (
            "poisoned_owned." + runner.sys.implementation.cache_tag + ".pyc"
        )
        py_compile.compile(
            str(source),
            cfile=str(cached),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        )
        cached_before = cached.read_bytes()
        source.write_text("VALUE='actual-source'\n")
        parent = run_manifest.process_identity(os.getpid(), include_zombies=True)
        for index in range(3):
            process = self.base / ("actual-child-" + str(index))
            process.mkdir()
            prefix = process / "cache/python"
            prefix.mkdir(parents=True)
            marker = process / "ran"

            binding = dict(
                arm="native",
                run_id="actual-child-" + str(index),
                attempt_dir=str(self.base),
                process_dir=str(process),
                work_dir=str(process / "work"),
                output_dir=str(process / "output"),
                gpu_uuid="GPU-X",
                resource_class="GPU_EXCLUSIVE",
                envelope_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                attempt_dir_identity=[self.base.stat().st_dev, self.base.stat().st_ino],
                process_dir_identity=[process.stat().st_dev, process.stat().st_ino],
            )
            acknowledge = runner._ownership_callback(process, binding, parent)

            def callback(child, phase):
                acknowledge(child, phase)
                if not identities or identities[-1]["pid"] != child["pid"]:
                    identities.append(child)

            argv = [
                runner.PINNED_PYTHON,
                "-I",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=" + str(prefix),
                "-c",
                "import sys;from pathlib import Path;sys.path.insert(0,"
                + repr(str(source_root))
                + ");import poisoned_owned;Path("
                + repr(str(marker))
                + ").write_text(poisoned_owned.VALUE)",
            ]
            env = dict(os.environ, PYTHONPYCACHEPREFIX=str(prefix))
            code = run_matrix.run_child(
                argv,
                env,
                process,
                "producer",
                guard,
                callback,
                signal_state,
                10,
                0.01,
                bootstrap_no_site=True,
            )
            self.assertEqual(code, 0)
            self.assertEqual(marker.read_text(), "actual-source")
            self.assertEqual(list(prefix.rglob("*")), [])
            record = json.loads((process / "ownership-record.json").read_bytes())
            self.assertEqual(record["child"]["pid"], identities[-1]["pid"])
        self.assertEqual(len({row["pid"] for row in identities}), 3)
        self.assertEqual(cached.read_bytes(), cached_before)

    def test_public_parser_has_only_bundle_output_uuid_and_no_injection(self):
        names = {a.dest for a in runner._parser()._actions}
        self.assertEqual(
            names, {"help", "metadata_bundle", "fresh_out", "selected_uuid", "capture_cuda_route_events", "routing_capture_cell"}
        )


    def test_cuda_route_flag_wire_sidecar_gate_and_false_success_rejection(self):
        for bad in (False,True):
            self.bad_cuda_binding=bad;self.cuda_requests=[]
            out=self.base/('cuda-bad' if bad else 'cuda-good')
            if bad:
                with self.assertRaisesRegex(runner.TripletFailure,'route-event artifact'):
                    runner.run_triplet(self.base/'metadata',out,'GPU-X',capture_cuda_route_events=True,
                        _test_dependencies=self.dependencies())
                self.assertEqual([r['arm'] for r in self.cuda_requests],['native','capture'])
            else:
                result=runner.run_triplet(self.base/'metadata',out,'GPU-X',capture_cuda_route_events=True,
                    _test_dependencies=self.dependencies())
                self.assertEqual(result['status'],'PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED')
                self.assertEqual([r.get('capture_cuda_route_events',False) for r in self.cuda_requests],[False,True,True])
                self.assertNotIn('capture_cuda_route_events',self.cuda_requests[0])
        with self.assertRaisesRegex(ValueError,'explicit boolean'):
            runner.run_triplet(self.base/'metadata',self.base/'never-created','GPU-X',capture_cuda_route_events=1)


if __name__ == "__main__":
    unittest.main()
