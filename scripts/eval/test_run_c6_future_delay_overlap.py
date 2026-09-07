import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)) if str(HERE) not in sys.path else None
if importlib.util.find_spec("resource_guard") is None:
    guard = types.ModuleType("resource_guard")
    guard.ResourceBusy = type("ResourceBusy", (RuntimeError,), {})
    guard.ResourceGuard = object
    sys.modules["resource_guard"] = guard
    manifest = types.ModuleType("run_manifest")
    manifest.artifact_inventory = lambda _path: ({}, [])
    manifest.atomic_json = lambda path, value: path.write_text(json.dumps(value))
    manifest.environment_snapshot = lambda: {}
    manifest.git_snapshot = lambda _root: {}
    manifest.now = lambda: "fixture-time"
    sys.modules["run_manifest"] = manifest
    matrix = types.ModuleType("run_matrix")
    matrix.FailedRun = type("FailedRun", (RuntimeError,), {})
    matrix.InterruptedRun = type("InterruptedRun", (RuntimeError,), {})
    matrix.run_child = None
    sys.modules["run_matrix"] = matrix

import run_c6_future_delay_overlap as target


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "sha256": target.sha256(data)}


def image_pair(root, work):
    folder = root / f"image-k{work}"
    identity = dict(schema_version=2, workload="single_active_lane_fixed_work_v1",
                    active_lane_mask=1, fixed_work_count=work,
                    mapping_validation="NOT_PROVEN",
                    compiler={"tool": "ptxas", "architecture": "sm_120", "optimization": "-O3"})
    future = dict(identity, selected_kernel=target.SELECTED_KERNEL, native_kernel=target.NATIVE_KERNEL)
    for name in ("original_ptx", "transformed_ptx", "cubin", "build_manifest", "disassembly_manifest", "nvdisasm", "cuobjdump"):
        data = f"fixture-{work}-{name}".encode()
        future[name] = put(folder / name, data)
        if name in ("original_ptx", "transformed_ptx", "cubin"):
            future[name]["bytes"] = len(data)
    native = dict(identity, selected_kernel=target.NATIVE_KERNEL, transform_mode="native_untransformed",
                  original_ptx=dict(future["original_ptx"]))
    for name in ("cubin", "build_manifest", "disassembly_manifest", "nvdisasm", "cuobjdump"):
        data = f"ordinary-fixture-{work}-{name}".encode()
        native[name] = put(folder / ("native-" + name), data)
        if name == "cubin":
            native[name]["bytes"] = len(data)
    paths = folder / "binding.json", folder / "native-binding.json"
    paths[0].write_text(json.dumps(future), encoding="utf-8")
    paths[1].write_text(json.dumps(native), encoding="utf-8")
    return paths, future, native


def oracle(work):
    # Independent affine exponentiation of the fixed MAD chain, not a copied loop.
    outputs = []
    for lane in range(32):
        a, b, ra, rb, n, mask = 0x19660d, 0x3c6ef35f + lane, 1, 0, work, 0xffffffff
        while n:
            if n & 1:
                ra, rb = (a * ra) & mask, (a * rb + b) & mask
            a, b = (a * a) & mask, (a * b + b) & mask
            n >>= 1
        seed = 0x9e3779b9 ^ ((lane * 0x9e3779b9 + 0x85ebca6b) & mask)
        outputs.append((0xa5a55a5a if lane == 0 else 0) ^ ((ra * seed + rb) & mask) ^ 0xd1b54a35)
    return outputs


def accounting(future, record=None):
    values = dict.fromkeys(("issued", "pending", "model_ready", "consumed", "drained", "terminal_error",
        "native_loads", "native_bytes", "rejected", "groups_issued", "groups_completed", "trace_count", "trace_overflow"), 0)
    values["next_reservation"] = 2 if future else 1
    traces = []
    if future:
        values.update(issued=1, model_ready=1, consumed=1, groups_issued=1, groups_completed=1, trace_count=2)
        for index in range(2):
            traces.append(dict(reservation_id=1, address=0x10000, instruction_id=15, bytes=4, lane=0,
                group_mask=1, event=0 if index == 0 else 5, status=index,
                issue_ns=record["arrival_ns"], ready_ns=record["ready_ns"],
                finish_ns=record["arrival_ns"] + 500 if index == 0 else record["wait_exit_ns"] - 100))
    return values, traces


def raw_fixture(future, native, prework=10000):
    work = future["fixed_work_count"]
    outputs, launches = oracle(work), []
    for ordinal in range(33):
        arm, within = ("native", "future0", "futureD")[ordinal // 11], ordinal % 11
        delay = 20000 if arm == "futureD" else 0
        records = []
        for lane in range(32):
            active = arm != "native" and lane == 0
            arrival, begin = 1000000, 1000000 + prework
            end = begin + (100 if work == 0 else 4100)
            enter = end + 100
            leave = max(enter, arrival + delay) + 500
            record = dict(launch_epoch=101 + ordinal, configured_delay_ns=delay if active else 0,
                helper_entry_ns=arrival - 100 if active else 0, arrival_ns=arrival if active else 0,
                helper_issue_exit_ns=arrival + 600 if active else 0,
                native_instruction_after_ns=begin - 100, work_begin_ns=begin, work_end_ns=end,
                wait_enter_ns=enter if active else 0, wait_exit_ns=leave if active else 0,
                consumer_after_ns=leave + 100 if active else end + 100,
                ready_ns=arrival + delay if active else 0, reservation_id=1 if active else 0,
                lane=lane, status=1, valid_bits=1023 if active else 824,
                work_count=work, output_bits=outputs[lane])
            records.append(record)
        counters, traces = accounting(arm != "native", records[0])
        launches.append(dict(arm=arm, delay_ns=delay, work_count=work, warmup=within == 0,
            sample=max(0, within - 1), launch_epoch=101 + ordinal, validation="PASS",
            sentinel_outputs=[value ^ 0xffffffff for value in outputs], observed_outputs=outputs,
            expected_outputs=[0] * 32, records=records, counters=counters, traces=traces,
            trace_copy_bounded=True, cuda_event_elapsed_ms=1.0))
    return dict(schema_version=2, evidence="GPU_ACQUISITION", validation_status="UNVALIDATED",
        scientific_claim=False, c6_3_closed=False, c6_4_closed=False, g5_closed=False,
        overlap_closed=False, native_completion_timing=False, instruction_id=15,
        workload="single_active_lane_fixed_work_v1", active_lane_mask=1, fixed_work_count=work,
        fixed_work_markers={"native": work, "future": work}, launches=launches, cleanup={"fixture": True},
        native_image_binding={**{name + "_sha256": future[name]["sha256"] for name in
            ("original_ptx", "transformed_ptx", "cubin", "build_manifest", "disassembly_manifest", "nvdisasm", "cuobjdump")},
            "mapping_validation": "NOT_PROVEN", "same_retained_buffer": True},
        native_control_binding={**{name + "_sha256": native[name]["sha256"] for name in
            ("original_ptx", "cubin", "build_manifest", "disassembly_manifest", "nvdisasm", "cuobjdump")},
            "cubin_bytes": native["cubin"]["bytes"], "selected_kernel": target.NATIVE_KERNEL,
            "mapping_validation": "NOT_PROVEN", "same_retained_buffer": True, "load_result": 0,
            "future_requirements_lookup": 500, "future_requirements_absent": True, "distinct_modules": True},
        native_input=dict(address=0x20000, bytes=128, registered=False,
            words=[((lane * 0x45d9f3b) & 0xffffffff) ^ 0xa5a55a5a for lane in range(32)],
            future_words=[((lane * 0x45d9f3b) & 0xffffffff) ^ 0xa5a55a5a for lane in range(32)],
            future_address=0x10000, future_registered_bytes=4096, contents_equal=True, disjoint=True))


class SingleLaneFixedWorkTests(unittest.TestCase):
    def test_observer_preparation_precedes_anchor_without_record_mutation(self):
        source = (Path(__file__).resolve().parents[2] /
                  "src/cuda_runtime/device/hbf_device.cu").read_text()
        prepare = source.split("__device__ FutureDelayBinding future_delay_prepare(", 1)[1]
        prepare = prepare.split("__device__ void future_delay_commit(", 1)[0]
        self.assertIn("future_delay_record_is_zero(*record)", prepare)
        self.assertNotIn("record->", prepare)
        issue = source.split("__hbfsim_timing_future_issue_v1(", 1)[1]
        issue = issue.split("__hbfsim_timing_future_poll_v1(", 1)[0]
        self.assertLess(issue.index("diagnostic_helper_entry=EvalDelayClock{}()"),
                        issue.index("future_delay_prepare(address,bytes,instruction)"))
        self.assertLess(issue.index("future_delay_prepare(address,bytes,instruction)"),
                        issue.index("const auto arrival=EvalDelayClock{}()"))
        self.assertLess(issue.index("const auto arrival=EvalDelayClock{}()"),
                        issue.index("future_delay_commit("))
        self.assertLess(issue.index("future_delay_commit("), issue.index("future_liveness(h,f.control_generation,arrival)"))

    def test_both_explicit_pairs_and_rejections_precede_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a, f0, n0 = image_pair(root, 0)
            b, _f1, _n1 = image_pair(root, 4096)
            pairs, paths, verified = target.load_image_pairs({0: a, 4096: b})
            self.assertEqual(set(pairs), {0, 4096})
            self.assertNotEqual(pairs[0][0]["cubin"], pairs[4096][0]["cubin"])
            self.assertEqual(verified["k0_binding_original_ptx"], b"fixture-0-original_ptx")
            with mock.patch.object(target, "ROOT", root), mock.patch.object(target, "ResourceGuard") as guard, mock.patch.object(target, "run_child") as child:
                for index, selected in enumerate(({0: a}, {0: b, 4096: a})):
                    with self.assertRaises(ValueError):
                        target.execute(root / f"results/gold/timing-future-unit/bad{index}", root / "no-build", root / "no-profile", selected, "GPU-test")
                for field, value in (("schema_version", 1), ("active_lane_mask", 0xffffffff), ("fixed_work_count", False)):
                    invalid = dict(f0); invalid[field] = value
                    a[0].write_text(json.dumps(invalid), encoding="utf-8")
                    with self.assertRaises(ValueError): target.load_image_pairs({0: a, 4096: b})
                a[0].write_text(json.dumps(f0), encoding="utf-8")
                invalid = copy.deepcopy(n0); invalid["original_ptx"]["sha256"] = "0" * 64
                a[1].write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "original PTX"): target.load_image_pairs({0: a, 4096: b})
                a[1].write_text(json.dumps(n0), encoding="utf-8")
                for variant, pattern in (("ptx", "original and transformed PTX"),
                                         ("cubin", "future and ordinary native cubin")):
                    invalid_future, invalid_native = copy.deepcopy(f0), copy.deepcopy(n0)
                    if variant == "ptx":
                        invalid_future["transformed_ptx"] = dict(f0["original_ptx"])
                    else:
                        invalid_native["cubin"] = dict(f0["cubin"])
                    a[0].write_text(json.dumps(invalid_future), encoding="utf-8")
                    a[1].write_text(json.dumps(invalid_native), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, pattern):
                        target.execute(root / f"results/gold/timing-future-unit/relabel-{variant}",
                                       root / "no-build", root / "no-profile", {0: a, 4096: b}, "GPU-test")
                a[0].write_text(json.dumps(f0), encoding="utf-8")
                a[1].write_text(json.dumps(n0), encoding="utf-8")
                guard.assert_not_called(); child.assert_not_called()

    def test_numeric_active_inactive_oracle_and_fixed_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            for work in (0, 4096):
                _paths, future, native = image_pair(Path(directory), work)
                raw = raw_fixture(future, native)
                value = target.validate_raw(raw, future, native)
                self.assertEqual(target.expected_outputs(work), oracle(work))
                self.assertEqual(value["active_work_ns"]["native"], [100 if work == 0 else 4100] * 10)
                interval = value["future_d_samples"][0]["intervals"][0]
                self.assertEqual(interval["prework_from_anchor_ns"], 10000)
                self.assertEqual(interval["work_ns"], 100 if work == 0 else 4100)
                self.assertEqual(interval["wait_ns"], 10300 if work == 0 else 6300)
                self.assertFalse(value["future_d_samples"][0]["expired_before_work"])
                invalid = copy.deepcopy(raw); invalid["fixed_work_markers"]["native"] = 4096 if work == 0 else 0
                with self.assertRaisesRegex(ValueError, "markers"): target.validate_raw(invalid, future, native)

    def test_bad_inactive_output_and_single_lane_conservation_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, future, native = image_pair(Path(directory), 0)
            raw = raw_fixture(future, native)
            edits = [lambda x: x["launches"][11]["observed_outputs"].__setitem__(1, 0),
                lambda x: x["launches"][11]["records"][1].__setitem__("helper_entry_ns", 1),
                lambda x: x["launches"][11]["counters"].__setitem__("issued", 32),
                lambda x: x["launches"][11]["traces"][0].__setitem__("group_mask", 0xffffffff),
                lambda x: x["launches"][11]["traces"][1].__setitem__("address", 0x10004),
                lambda x: x["native_control_binding"].__setitem__("future_requirements_absent", False)]
            for edit in edits:
                invalid = copy.deepcopy(raw); edit(invalid)
                with self.assertRaises(ValueError): target.validate_raw(invalid, future, native)

    def test_non_identifying_and_measured_work_separation_remain_unvalidated(self):
        with tempfile.TemporaryDirectory() as directory:
            pairs = {k: image_pair(Path(directory), k)[1:] for k in (0, 4096)}
            values = {k: target.validate_raw(raw_fixture(*pair), *pair) for k, pair in pairs.items()}
            combined = target.combine_analyses(values)
            self.assertEqual(combined["status"], "WINDOW_AND_WORK_CONTROLS_PRESENT_UNVALIDATED")
            self.assertEqual((combined["total_launches"], combined["issued_ready_consumed_each"], combined["trace_count"]), (68, 46, 92))
            self.assertFalse(combined["scientific_claim"])
            values[4096]["active_work_ns"]["native"] = [100] * 10
            self.assertEqual(target.combine_analyses(values)["status"], "NON_IDENTIFYING_WORK_CONTROLS_NOT_SEPARATED")
            values[0] = target.validate_raw(raw_fixture(*pairs[0], prework=21000), *pairs[0])
            combined = target.combine_analyses(values)
            self.assertEqual(combined["status"], "NON_IDENTIFYING_PREWORK_COVERS_DELAY")
            self.assertEqual(combined["futureD_samples_expired_before_work"], 10)

    def exercise_execute(self, root, fail_second=False):
        selected, records = {}, {}
        for work in (0, 4096):
            paths, future, native = image_pair(root, work)
            selected[work], records[work] = paths, raw_fixture(future, native)
        for name in ("build/benchmarks/cuda/c6_future_delay_overlap", "build/libptxpass_hbf.so", "build/libhbfsim_launch_gate.so",
                     "build/hbfsimd", "profile.json", "build/CMakeCache.txt", "benchmarks/cuda/c6_future_delay_overlap.cu",
                     "benchmarks/cuda/CMakeLists.txt", "src/cuda_runtime/device/hbf_device.cu", "include/hbfsim/timing_future_abi.hpp"):
            put(root / name, b"fixture-common-file")
        observed = []
        def child(argv, env, folder, name, guard, callback, signals, timeout, poll):
            work = int(argv[argv.index("--fixed-work-count") + 1])
            observed.append((work, timeout, guard, tuple(argv)))
            self.assertEqual(argv[argv.index("--binding") + 1], str(selected[work][0]))
            self.assertEqual(argv[argv.index("--native-binding") + 1], str(selected[work][1]))
            (folder / "producer.stderr.log").write_text("fixture failure" if work == 4096 and fail_second else "")
            if work == 4096 and fail_second:
                return 1
            raw = records[work]
            (folder / "raw.json").write_text(json.dumps(raw))
            active = raw["launches"][11]["records"][0]
            counters, traces = accounting(True, active)
            partial = dict(capture_complete=True, launches=raw["launches"], cleanup=raw["cleanup"],
                instruction_discovery=dict(config="ALL_ZERO_DIAGNOSTIC_DISABLED", counters=counters, traces=traces))
            (folder / "raw.json.partial.json").write_text(json.dumps(partial))
            return 0
        original_finalize = target.finalize_attempt
        def atomic(path, value): path.write_text(json.dumps(value), encoding="utf-8")
        def finalize(out, manifest, status, final, signals):
            return original_finalize(out, manifest, status, final, signals,
                inventory_fn=lambda _out: ({}, []), atomic_fn=atomic)
        guard = mock.MagicMock(); guard.__enter__.return_value = guard
        with mock.patch.object(target, "ROOT", root), mock.patch.object(target, "ResourceGuard", return_value=guard) as constructor, mock.patch.object(target, "run_child", side_effect=child), mock.patch.object(target, "git_snapshot", return_value={"fixture": True}), mock.patch.object(target, "environment_snapshot", return_value={}), mock.patch.object(target, "atomic_json", side_effect=atomic), mock.patch.object(target, "finalize_attempt", side_effect=finalize):
            out = root / "results/gold/timing-future-unit/attempt"
            result = target.execute(out, root / "build", root / "profile.json", selected, "GPU-fixture")
            constructor.assert_called_once()
        self.assertEqual([item[:2] for item in observed], [(0, 120), (4096, 120)])
        self.assertIs(observed[0][2], observed[1][2])
        return result, out

    def test_two_owned_children_share_one_guard_with_explicit_image_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            result, out = self.exercise_execute(Path(directory))
            self.assertEqual(result["state"], "CAPTURED_UNVALIDATED")
            summary = json.loads((out / "diagnostic/analysis.json").read_text())
            self.assertEqual(summary["matrix_output_words"], 2112)
            self.assertEqual(summary["total_launches"], 68)

    def test_second_child_failure_retains_first_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            result, out = self.exercise_execute(Path(directory), fail_second=True)
            self.assertEqual(result["state"], "INVALID_DIAGNOSTIC")
            self.assertTrue((out / "diagnostic/k0/analysis.json").exists())
            self.assertEqual((out / "diagnostic/k4096/producer.stderr.log").read_text(), "fixture failure")
            self.assertFalse((out / "diagnostic/analysis.json").exists())

    def test_cli_requires_both_pairs_and_pending_signal_vetoes_success(self):
        with mock.patch.object(sys, "argv", ["run", "--execute"]), mock.patch.object(target, "execute") as execute:
            with self.assertRaises(SystemExit) as error: target.main()
            self.assertEqual(error.exception.code, 2); execute.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            status = {}
            final = target.finalize_attempt(Path(directory), {}, status, "CAPTURED_UNVALIDATED", {"signal": 15},
                inventory_fn=lambda _path: ({}, []), atomic_fn=lambda path, value: path.write_text(json.dumps(value)))
            self.assertEqual(final, "INTERRUPTED")
            self.assertEqual(status["state"], "INTERRUPTED")


if __name__ == "__main__":
    unittest.main()
