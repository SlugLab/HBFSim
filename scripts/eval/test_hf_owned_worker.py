"""CPU-only tests for the fixed owned HF worker bootstrap and wire."""

from dataclasses import dataclass
import hashlib
import json
import os
import py_compile
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import hf_owned_worker as worker


@dataclass(frozen=True)
class Metadata:
    bundle: Path
    receipt_bytes: bytes
    complete_bytes: bytes
    artifacts: tuple


@dataclass(frozen=True)
class Runtime:
    manifest_bytes: bytes
    artifacts: tuple


@dataclass(frozen=True)
class Startup:
    manifest_bytes: bytes
    artifacts: tuple


@dataclass(frozen=True)
class Tuning:
    manifest_bytes: bytes
    config_bytes: bytes | None


class OwnedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.snapshots = (
            Metadata(
                self.base / "metadata",
                b'{"receipt":1}',
                b'{"complete":1}',
                (("donor.json", b"donor"), ("tensors.json", b"tensors")),
            ),
            Runtime(b'{"runtime":1}', (("site-packages/a.py", b"a"),)),
            Startup(b'{"startup":1}', (("site-packages/b.py", b"b"),)),
            Tuning(b'{"tuning":1}', None),
        )
        self.request = dict(
            schema_version=1,
            arm="native",
            run_id="run",
            git_commit="a" * 40,
            environment_fingerprint="b" * 64,
            gpu_uuid="GPU-TEST",
            device_name="TEST GPU",
            device_capability=[12, 0],
            work_dir=str(self.base / "work"),
            output_dir=str(self.base / "out"),
            process_dir=str(self.base / "process"),
            attempt_dir=str(self.base / "attempt"),
            runtime_env={"A": "B"},
            transport_env={"PYTHONPYCACHEPREFIX": str(self.base / "cache")},
            prompt_token_ids=list(range(1000, 1032)),
            project_sources={
                "root": str(self.base),
                "root_identity": [1, 2],
                "files": {},
            },
        )

    def _wire(self, tuning=None):
        values = list(self.snapshots)
        if tuning is not None:
            values[3] = tuning
        envelope, blobs = worker._encode_wire(*values, self.request)
        directory = self.base / ("wire-" + str(len(list(self.base.glob("wire-*")))))
        worker._write_wire(directory, envelope, blobs)
        return directory, envelope, blobs

    def test_exact_wire_round_trip_and_absent_tuning_config(self):
        directory, envelope, blobs = self._wire()
        loaded, raw = worker._read_wire(
            directory / "envelope.json",
            hashlib.sha256((directory / "envelope.json").read_bytes()).hexdigest(),
        )
        rebuilt = worker._decode_snapshots(
            loaded, raw, (Metadata, Runtime, Startup, Tuning)
        )
        self.assertEqual(rebuilt, self.snapshots)
        self.assertEqual(
            [row["ordinal_name"] for row in envelope["blobs"]],
            [f"payload-{i:04d}.bin" for i in range(len(blobs))],
        )
        self.assertIsNone(rebuilt[3].config_bytes)

    def test_retained_wire_rejects_same_byte_file_and_directory_replacement(self):
        for case in ("envelope", "payload", "directory"):
            directory, _, _ = self._wire()
            envelope_path = directory / "envelope.json"
            digest = hashlib.sha256(envelope_path.read_bytes()).hexdigest()
            _, _, binding = worker._read_wire_retained(envelope_path, digest)
            if case == "directory":
                moved = directory.with_name(directory.name + "-moved")
                directory.rename(moved)
                directory.mkdir()
                for source in moved.iterdir():
                    (directory / source.name).write_bytes(source.read_bytes())
            else:
                target = (
                    envelope_path
                    if case == "envelope"
                    else directory / "payload-0000.bin"
                )
                raw = target.read_bytes()
                target.unlink()
                target.write_bytes(raw)
            with self.subTest(case=case), self.assertRaisesRegex(
                ValueError, "identity changed"
            ):
                worker._read_wire_retained(envelope_path, digest, binding)

    def test_request_controls_require_exact_integers_and_facts_cannot_enable_real_path(self):
        owned = tempfile.TemporaryDirectory(
            prefix=".owned-control-types-", dir=worker.ROOT / "results/gold"
        )
        self.addCleanup(owned.cleanup)
        attempt = Path(owned.name)
        work = worker.ROOT / "results/tmp/hf-routing/control-types/native"
        metadata = worker.ROOT / ".owned-control-metadata"
        document = dict(
            schema_version=1,
            arm="native",
            run_id="r",
            git_commit="a" * 40,
            environment_fingerprint="b" * 64,
            gpu_uuid="GPU-X",
            device_name="NVIDIA RTX PRO 6000 Blackwell Server Edition",
            device_capability=[12, 0],
            work_dir=str(work),
            output_dir=str(attempt / "arms/native"),
            process_dir=str(attempt / "process/native"),
            attempt_dir=str(attempt),
            metadata_bundle=str(metadata),
            runtime_env={},
            transport_env={"PYTHONPYCACHEPREFIX": str(work / "cache/python")},
            prompt_token_ids=list(range(1000, 1032)),
            project_sources={},
        )
        self.assertEqual(worker._request(document, "native"), document)
        for field, value in (
            ("prompt_token_ids", [1000.0, *range(1001, 1032)]),
            ("device_capability", [12.0, False]),
            (
                "transport_env",
                {"PYTHONPYCACHEPREFIX": str(work / "other-empty-prefix")},
            ),
        ):
            changed = dict(document, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                worker._request(changed, "native")
        with (
            mock.patch.object(
                worker,
                "_read_wire_retained",
                side_effect=AssertionError("wire/runtime path reached"),
            ),
            self.assertRaisesRegex(ValueError, "require explicit MOCK"),
        ):
            worker.execute_owned(
                "native", Path("/invalid"), "0" * 64, Path("/invalid"), _facts={}
            )

    def test_multi_prompt_request_is_exact_capture_only_and_loaded_plan_preserves_members(self):
        owned=tempfile.TemporaryDirectory(prefix='.owned-multi-prompt-',dir=worker.ROOT/'results/gold')
        self.addCleanup(owned.cleanup);attempt=Path(owned.name)
        work=worker.ROOT/'results/tmp/hf-routing'/attempt.name/'capture'
        prompts=[list(range(1000+32*member,1032+32*member)) for member in range(16)]
        capture=dict(cell_id='routing_capture-01469',member_count=16,active_sequences=8,
            composition_seed=0,composition_rule='two-fixed-waves-seed0-shuffled-slots-v1',
            concurrency_kind='trace-composed',live_scheduler_trace=False,
            actual_scheduler_timestamps=False,prompt_source='FIXED_TOKEN_CONTROL_SET')
        document=dict(schema_version=1,arm='capture',run_id='multi',git_commit='a'*40,
            environment_fingerprint='b'*64,gpu_uuid='GPU-X',
            device_name='NVIDIA RTX PRO 6000 Blackwell Server Edition',device_capability=[12,0],
            work_dir=str(work),output_dir=str(attempt/'arms/capture'),
            process_dir=str(attempt/'process/capture'),attempt_dir=str(attempt),
            metadata_bundle=str(worker.ROOT/'.owned-multi-prompt-metadata'),runtime_env={},
            transport_env={'PYTHONPYCACHEPREFIX':str(work/'cache/python')},
            prompt_token_ids=prompts[0],prompt_members=prompts,routing_capture=capture,
            project_sources={})
        self.assertEqual(worker._request(document,'capture'),document)
        plan=worker._loaded_plan(document,'metadata','runtime','tuning')
        self.assertEqual(plan['prompt_members'],prompts);self.assertEqual(plan['routing_capture'],capture)
        for changed in (dict(document,arm='native'),
                        dict(document,prompt_members=prompts[:-1]),
                        dict(document,routing_capture=dict(capture,composition_seed=1)),
                        dict(document,capture_cuda_route_events=True)):
            with self.subTest(keys=set(changed)),self.assertRaises(ValueError):
                worker._request(changed,changed['arm'])

    def test_wire_wrong_type_count_name_digest_length_extra_link_fifo_and_ancestor_reject(
        self,
    ):
        mutators = []
        mutators.append(lambda d, e: (d / "envelope.json").write_bytes(b"[]"))

        def bool_schema(d, e):
            raw = json.loads((d / "envelope.json").read_bytes())
            raw["schema_version"] = True
            (d / "envelope.json").write_bytes(worker._canonical(raw))

        mutators.append(bool_schema)
        mutators.append(lambda d, e: (d / e["blobs"][0]["ordinal_name"]).unlink())
        mutators.append(
            lambda d, e: (d / e["blobs"][0]["ordinal_name"]).write_bytes(b"changed")
        )

        def bad_name(d, e):
            raw = json.loads((d / "envelope.json").read_bytes())
            raw["blobs"][0]["ordinal_name"] = "other.bin"
            (d / "envelope.json").write_bytes(worker._canonical(raw))

        mutators.append(bad_name)

        def bad_length(d, e):
            raw = json.loads((d / "envelope.json").read_bytes())
            raw["blobs"][0]["length"] += 1
            (d / "envelope.json").write_bytes(worker._canonical(raw))

        mutators.append(bad_length)
        mutators.append(lambda d, e: (d / "extra.bin").write_bytes(b"x"))

        def symlink(d, e):
            p = d / e["blobs"][0]["ordinal_name"]
            p.unlink()
            p.symlink_to(d / e["blobs"][1]["ordinal_name"])

        mutators.append(symlink)
        if hasattr(os, "mkfifo"):

            def fifo(d, e):
                p = d / e["blobs"][0]["ordinal_name"]
                p.unlink()
                os.mkfifo(p)

            mutators.append(fifo)
        for index, mutate in enumerate(mutators):
            directory, envelope, _ = self._wire()
            mutate(directory, envelope)
            digest = hashlib.sha256(
                (directory / "envelope.json").read_bytes()
            ).hexdigest()
            with self.subTest(index=index), self.assertRaises(ValueError):
                worker._read_wire(directory / "envelope.json", digest)
        directory, envelope, _ = self._wire()
        moved = self.base / "moved"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            worker._read_wire(
                directory / "envelope.json",
                hashlib.sha256((moved / "envelope.json").read_bytes()).hexdigest(),
            )

    def test_envelope_hash_is_independent_argv_binding(self):
        directory, _, _ = self._wire()
        right = hashlib.sha256((directory / "envelope.json").read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            worker._read_wire(directory / "envelope.json", "0" * 64)
        self.assertEqual(
            worker._read_wire(directory / "envelope.json", right)[0]["schema_version"],
            1,
        )

    def test_bootstrap_flags_interpreter_prefix_environment_and_ownership_are_fail_closed(
        self,
    ):
        prefix = self.base / "prefix"
        prefix.mkdir()
        facts = dict(
            executable=worker.PINNED_PYTHON,
            isolated=1,
            no_site=1,
            no_user_site=1,
            ignore_environment=1,
            dont_write_bytecode=1,
            pycache_prefix=str(prefix),
            xoptions={"pycache_prefix": str(prefix)},
        )
        worker._check_bootstrap(prefix, facts)
        for key, value in (
            ("executable", "/wrong/python"),
            ("isolated", 0),
            ("no_site", 0),
            ("no_user_site", 0),
            ("ignore_environment", 0),
            ("dont_write_bytecode", 0),
            ("pycache_prefix", str(self.base / "other")),
        ):
            changed = dict(facts)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker._check_bootstrap(prefix, changed)
        (prefix / "old.pyc").write_bytes(b"old")
        with self.assertRaises(ValueError):
            worker._check_bootstrap(prefix, facts)
        (prefix / "old.pyc").unlink()
        actual = {"A": "B", "PYTHONPYCACHEPREFIX": str(prefix)}
        worker._check_environment(
            {"A": "B"}, {"PYTHONPYCACHEPREFIX": str(prefix)}, actual, prefix
        )
        for changed in (
            {"A": "B"},
            {"A": "X", "PYTHONPYCACHEPREFIX": str(prefix)},
            {"A": "B", "PYTHONPYCACHEPREFIX": str(prefix), "EXTRA": "x"},
        ):
            with self.assertRaises(ValueError):
                worker._check_environment(
                    {"A": "B"}, {"PYTHONPYCACHEPREFIX": str(prefix)}, changed, prefix
                )

    def test_runtime_environment_rejects_preload_paths_and_unknown_fields(self):
        work = self.base / "work-env"
        thread_caps = {
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
        }
        self.assertEqual(
            {key: worker.FIXED_ENV[key] for key in thread_caps}, thread_caps
        )
        runtime = {
            "PATH": "/bin",
            **worker.FIXED_ENV,
            **{key: str(work / suffix) for key, suffix in worker.CACHE_ENV.items()},
        }
        self.assertEqual(worker._validate_runtime_environment(runtime, work), runtime)
        for key in ("LD_PRELOAD", "PYTHONPATH", "UNKNOWN"):
            changed = dict(runtime, **{key: "foreign"})
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker._validate_runtime_environment(changed, work)
        for key in thread_caps:
            for replacement in (None, "64"):
                changed = dict(runtime)
                if replacement is None:
                    del changed[key]
                else:
                    changed[key] = replacement
                with self.subTest(key=key, replacement=replacement), self.assertRaises(
                    ValueError
                ):
                    worker._validate_runtime_environment(changed, work)

    def test_ownership_boot_start_parent_session_envelope_and_paths_are_bound(self):
        parent = dict(
            pid=10, boot_id="boot", start_time=20, ppid=1, pgrp=10, session=10
        )
        child = dict(
            pid=11, boot_id="boot", start_time=30, ppid=10, pgrp=11, session=11
        )
        record = dict(
            schema_version=1,
            arm="native",
            envelope_sha256="a" * 64,
            parent=parent,
            child=child,
            run_id="run",
            attempt_dir="/a",
            process_dir="/a/process/native",
            work_dir="/w",
            output_dir="/o",
            gpu_uuid="GPU-X",
            resource_class="GPU_EXCLUSIVE",
            source_manifest_sha256="b" * 64,
            attempt_dir_identity=[1, 2],
            process_dir_identity=[3, 4],
        )
        worker._verify_ownership(record, dict(record), child, parent)
        for path, value in (
            ("child.boot_id", "other"),
            ("child.start_time", 31),
            ("child.ppid", 9),
            ("child.pgrp", 9),
            ("child.session", 9),
            ("envelope_sha256", "c" * 64),
            ("arm", "repeat"),
            ("schema_version", 1.0),
            ("attempt_dir_identity", [1.0, 2]),
        ):
            changed = json.loads(json.dumps(record))
            owner, key = path.split(".") if "." in path else (None, path)
            if owner:
                changed[owner][key] = value
            else:
                changed[key] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                worker._verify_ownership(changed, record, child, parent)

    def test_pinned_site_is_added_only_after_project_paths_and_resolves_torch(self):
        original = list(__import__("sys").path)
        paths = [
            str(worker.ROOT / "scripts/eval"),
            str(worker.ROOT / "adapters/vllm_capacity"),
            str(worker.ROOT),
            "/opt/miniconda3/lib/python313.zip",
            "/opt/miniconda3/lib/python3.13",
            "/opt/miniconda3/lib/python3.13/lib-dynload",
        ]
        try:
            __import__("sys").path[:] = paths
            modules = __import__("sys").modules
            saved = {
                name: modules.pop(name)
                for name in ("site", "sitecustomize", "usercustomize")
                if name in modules
            }
            try:
                worker._install_site_path(worker.PINNED_SITE)
            finally:
                modules.update(saved)
            self.assertEqual(__import__("sys").path, paths + [str(worker.PINNED_SITE)])
            spec = __import__("importlib.util").util.find_spec("torch")
            self.assertIsNotNone(spec)
            self.assertTrue(Path(spec.origin).is_relative_to(worker.PINNED_SITE))
        finally:
            __import__("sys").path[:] = original

    def test_split_torch_gate_precedes_every_vllm_import_and_records_actual_values(
        self,
    ):
        events = []
        props = SimpleNamespace(uuid="GPU-X", name="Device", major=12, minor=0)
        torch = SimpleNamespace(
            _C=SimpleNamespace(_cuda_getDeviceCount=lambda: events.append("raw") or 1)
        )
        torch_cuda = SimpleNamespace(
            device_count=lambda: events.append("public") or 1,
            current_device=lambda: events.append("current") or 0,
            get_device_properties=lambda _: events.append("properties") or props,
        )
        observed = worker._torch_device_gate(
            torch, torch_cuda, "GPU-X", "Device", (12, 0)
        )
        self.assertEqual(
            observed,
            dict(
                raw_count=1,
                public_count=1,
                current_device=0,
                uuid="GPU-X",
                name="Device",
                capability=[12, 0],
            ),
        )
        self.assertEqual(events, ["raw", "public", "current", "properties"])
        bad = SimpleNamespace(_C=SimpleNamespace(_cuda_getDeviceCount=lambda: 2))
        with self.assertRaises(ValueError):
            worker._torch_device_gate(bad, torch_cuda, "GPU-X", "Device", (12, 0))
        for field, value in (("name", "Other Device"), ("major", 11)):
            original = getattr(props, field)
            setattr(props, field, value)
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "identity differs"
            ):
                worker._torch_device_gate(
                    torch, torch_cuda, "GPU-X", "Device", (12, 0)
                )
            setattr(props, field, original)

    def test_torch_cuuid_body_is_strictly_bound_to_prefixed_declared_uuid(self):
        class TorchCUuid:
            def __init__(self, value):
                self.value = value

            def __str__(self):
                return self.value

        expected_body = "f07ea2df-1b6f-9a02-b534-5090abf3c174"
        properties = SimpleNamespace(
            uuid=TorchCUuid(expected_body), name="Device", major=12, minor=0
        )
        torch = SimpleNamespace(
            _C=SimpleNamespace(_cuda_getDeviceCount=lambda: 1, _CUuuid=TorchCUuid)
        )
        torch_cuda = SimpleNamespace(
            device_count=lambda: 1,
            current_device=lambda: 0,
            get_device_properties=lambda _: properties,
        )
        self.assertEqual(
            worker._torch_device_gate(
                torch,
                torch_cuda,
                "GPU-" + expected_body,
                "Device",
                (12, 0),
            )["uuid"],
            "GPU-" + expected_body,
        )
        for observed_uuid, declared_uuid, error in (
            (None, "GPU-" + expected_body, "representation is invalid"),
            (TorchCUuid("not-a-uuid"), "GPU-not-a-uuid", "representation is invalid"),
            (
                TorchCUuid("11111111-2222-3333-4444-555555555555"),
                "GPU-" + expected_body,
                "identity differs",
            ),
        ):
            properties.uuid = observed_uuid
            with self.subTest(observed_uuid=observed_uuid), self.assertRaisesRegex(
                ValueError, error
            ):
                worker._torch_device_gate(
                    torch,
                    torch_cuda,
                    declared_uuid,
                    "Device",
                    (12, 0),
                )

    def test_prefix_property_replacement_prepopulation_and_preload_sentinels_reject(
        self,
    ):
        prefix = self.base / "strict-prefix"
        prefix.mkdir()
        identity = worker._directory_identity(prefix)
        with (
            mock.patch.object(worker.sys, "pycache_prefix", str(prefix)),
            mock.patch.object(
                worker.sys, "_xoptions", {"pycache_prefix": str(prefix)}
            ),
        ):
            worker._worker_prefix_check(prefix, identity)
            (prefix / "old.pyc").write_bytes(b"x")
            with self.assertRaises(ValueError):
                worker._worker_prefix_check(prefix, identity)
        moved = self.base / "moved-prefix"
        prefix.rename(moved)
        prefix.mkdir()
        with (
            mock.patch.object(worker.sys, "pycache_prefix", str(prefix)),
            mock.patch.object(
                worker.sys, "_xoptions", {"pycache_prefix": str(prefix)}
            ),
            self.assertRaises(ValueError),
        ):
            worker._worker_prefix_check(prefix, identity)
        for name in (
            "site",
            "sitecustomize",
            "usercustomize",
            "torch",
            "vllm",
            "flashinfer",
        ):
            with (
                mock.patch.dict(worker.sys.modules, {name: object()}),
                self.subTest(name=name),
                self.assertRaises(ValueError),
            ):
                worker._check_preloaded_bootstrap()

    def test_isolated_stdlib_preload_lists_reject_each_package_and_submodule(self):
        prefix = self.base / "isolated-preload/cache/python"
        prefix.mkdir(parents=True)
        code = r'''
import runpy, sys, types
worker = runpy.run_path(sys.argv[1])
runner = runpy.run_path(sys.argv[2])
checks = (worker["_check_preloaded_bootstrap"], runner["_reject_parent_preloads"])
accepted = []
for check in checks:
    check()
    for package in ("site","sitecustomize","usercustomize","torch","vllm","flashinfer","flashinfer_cubin","numpy","transformers","triton","safetensors","tvm_ffi","huggingface_hub"):
        for name in (package, package + ".owned_sentinel"):
            sys.modules[name] = types.ModuleType(name)
            try:
                check()
            except ValueError as error:
                assert name in str(error), (name, str(error))
            else:
                accepted.append(check.__name__ + ":" + name)
            finally:
                del sys.modules[name]
assert not accepted, accepted
print("PRELOAD_GATES_OK")
'''
        completed = subprocess.run(
            [
                worker.PINNED_PYTHON,
                "-I",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=" + str(prefix),
                "-c",
                code,
                str(worker.ROOT / "scripts/eval/hf_owned_worker.py"),
                str(worker.ROOT / "scripts/eval/hf_routing_runner.py"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "PRELOAD_GATES_OK")
        self.assertEqual(list(prefix.rglob("*")), [])

    def test_namespace_initializer_bytecode_and_native_suffixes_are_rejected(self):
        root = self.base / "namespace-root"
        (root / "adapters/vllm_capacity").mkdir(parents=True)
        worker._namespace_absence(root)
        suffixes = (".pyc", worker.importlib.machinery.EXTENSION_SUFFIXES[0])
        for index, suffix in enumerate(suffixes):
            path = root / (
                ("adapters/__init__" if index == 0 else "adapters/vllm_capacity/__init__")
                + suffix
            )
            path.write_bytes(b"not executable")
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                worker._namespace_absence(root)
            path.unlink()

    def test_device_gate_rejects_torch_or_nvml_mismatch_before_callback(self):
        props = SimpleNamespace(uuid="GPU-X", name="Device", major=12, minor=0)
        torch = SimpleNamespace(
            _C=SimpleNamespace(_cuda_getDeviceCount=lambda: 1),
            cuda=SimpleNamespace(
                device_count=lambda: 1,
                current_device=lambda: 0,
                get_device_properties=lambda _: props,
            ),
        )

        class Nvml:
            @classmethod
            def get_device_uuid(cls, index=0):
                return "GPU-X"

            @classmethod
            def get_device_name(cls, index=0):
                return "Device"

            @classmethod
            def get_device_capability(cls, index=0):
                return (12, 0)

        platform = SimpleNamespace(current_platform=Nvml())
        cuda = SimpleNamespace(NvmlCudaPlatform=Nvml)
        self.assertEqual(
            worker._device_gate(torch, platform, cuda, "GPU-X", "Device", (12, 0))[
                "uuid"
            ],
            "GPU-X",
        )
        mutations = [("uuid", "GPU-Y"), ("name", "Other"), ("major", 11), ("count", 2)]
        for field, value in mutations:
            p = SimpleNamespace(uuid="GPU-X", name="Device", major=12, minor=0)
            t = SimpleNamespace(
                _C=SimpleNamespace(
                    _cuda_getDeviceCount=lambda: value if field == "count" else 1
                ),
                cuda=SimpleNamespace(
                    device_count=lambda: 1,
                    current_device=lambda: 0,
                    get_device_properties=lambda _: p,
                ),
            )
            if field != "count":
                setattr(p, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                worker._device_gate(t, platform, cuda, "GPU-X", "Device", (12, 0))

    def test_integrated_preflight_failures_never_reach_private_runtime_callback(self):
        base = Path(
            tempfile.mkdtemp(
                prefix=".owned-preflight-", dir=worker.ROOT / "results/gold"
            )
        )
        self.addCleanup(lambda: __import__("shutil").rmtree(base))
        attempt = base / "attempt"
        process = attempt / "process/native"
        wire_dir = process / "wire"
        (attempt / "arms").mkdir(parents=True)
        process.mkdir(parents=True)
        work = worker.ROOT / "results/tmp/hf-routing" / base.name / "native"
        self.addCleanup(
            lambda: __import__("shutil").rmtree(work.parent, ignore_errors=True)
        )
        prefix = work / "cache/python"
        prefix.mkdir(parents=True)
        metadata = Metadata(
            worker.ROOT / "results/manifests/test-only", b"{}", b"{}", ()
        )
        snapshots = (
            metadata,
            Runtime(b"{}", ()),
            Startup(b"{}", ()),
            Tuning(b"{}", None),
        )
        project = {"root": "bad"}
        runtime_env = {"A": "B"}
        transport = {"PYTHONPYCACHEPREFIX": str(prefix)}
        request = dict(
            schema_version=1,
            arm="native",
            run_id="r",
            git_commit="a" * 40,
            environment_fingerprint="b" * 64,
            gpu_uuid="GPU-X",
            device_name="NVIDIA RTX PRO 6000 Blackwell Server Edition",
            device_capability=[12, 0],
            work_dir=str(work),
            output_dir=str(attempt / "arms/native"),
            process_dir=str(process),
            attempt_dir=str(attempt),
            metadata_bundle=str(metadata.bundle),
            runtime_env=runtime_env,
            transport_env=transport,
            prompt_token_ids=list(range(1000, 1032)),
            project_sources=project,
        )
        envelope, blobs = worker._encode_wire(*snapshots, request)
        sha = worker._write_wire(wire_dir, envelope, blobs)
        facts = worker._actual_bootstrap_facts()
        facts.update(
            executable=worker.PINNED_PYTHON,
            isolated=1,
            no_site=1,
            no_user_site=1,
            ignore_environment=1,
            dont_write_bytecode=1,
            pycache_prefix=str(prefix),
            xoptions={"pycache_prefix": str(prefix)},
        )
        called = []
        with mock.patch.dict(os.environ, {**runtime_env, **transport}, clear=True):
            wrong = dict(facts, isolated=0)
            with self.assertRaises(ValueError):
                worker.execute_owned(
                    "native",
                    wire_dir / "envelope.json",
                    sha,
                    process / "ownership-record.json",
                    _test_dependencies={"runtime_callback": called.append},
                    _facts=wrong,
                )
        self.assertEqual(called, [])

    def test_valid_mock_full_preflight_uses_real_snapshot_and_current_validators(self):
        import evaluation_inventory
        import hf_moe_tuning
        import hf_routing_runner
        import run_manifest
        import verify_hf_metadata
        from test_evaluation_inventory import hf_fixture
        from test_hf_startup_sources import StartupSourceTests

        metadata_temp = tempfile.TemporaryDirectory(
            prefix=".owned-metadata-fixture-", dir=worker.ROOT
        )
        self.addCleanup(metadata_temp.cleanup)
        metadata_parent = Path(metadata_temp.name)
        _, bundle = hf_fixture(metadata_parent, vocab_size=1032)
        metadata = evaluation_inventory.load_hf_snapshot(bundle)
        source_fixture = StartupSourceTests("runTest")
        source_fixture.setUp()
        self.addCleanup(source_fixture.doCleanups)
        runtime = source_fixture.primary
        startup = source_fixture.snapshot
        tuning_directory = (
            source_fixture.site
            / "vllm/model_executor/layers/fused_moe/configs"
        )
        tuning_directory.mkdir()
        tuning = hf_moe_tuning.collect_tuning_inputs(
            metadata, runtime, hf_routing_runner.DEVICE_NAME
        )
        snapshots = (metadata, runtime, startup, tuning)

        owned_temp = tempfile.TemporaryDirectory(
            prefix=".owned-valid-preflight-", dir=worker.ROOT / "results/gold"
        )
        self.addCleanup(owned_temp.cleanup)
        attempt = Path(owned_temp.name) / "valid-attempt"
        process = attempt / "process/native"
        (attempt / "arms").mkdir(parents=True)
        process.mkdir(parents=True)
        work = (
            worker.ROOT
            / "results/tmp/hf-routing"
            / Path(owned_temp.name).name
            / "native"
        )
        self.addCleanup(
            lambda: __import__("shutil").rmtree(work.parent, ignore_errors=True)
        )
        protocol = __import__("runpy").run_path(
            str(worker.ROOT / "adapters/vllm_capacity/hf_routing_worker.py")
        )
        platform_env = {
            key: os.environ[key] for key in worker.PLATFORM_ENV if key in os.environ
        }
        runtime_env = protocol["make_environment"](work, "GPU-X", platform_env)
        thread_caps = {
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
        }
        self.assertEqual(
            {key: runtime_env[key] for key in thread_caps}, thread_caps
        )
        prefix = hf_routing_runner._prepare_work(work, runtime_env)
        project = worker.capture_project_sources()
        request = dict(
            schema_version=1,
            arm="native",
            run_id="valid-mock-run",
            git_commit="a" * 40,
            environment_fingerprint="b" * 64,
            gpu_uuid="GPU-X",
            device_name=hf_routing_runner.DEVICE_NAME,
            device_capability=[12, 0],
            work_dir=str(work),
            output_dir=str(attempt / "arms/native"),
            process_dir=str(process),
            attempt_dir=str(attempt),
            metadata_bundle=str(bundle),
            runtime_env=runtime_env,
            transport_env={"PYTHONPYCACHEPREFIX": str(prefix)},
            prompt_token_ids=list(range(1000, 1032)),
            project_sources=project,
        )
        envelope, blobs = worker._encode_wire(*snapshots, request)
        envelope_sha = worker._write_wire(process / "wire", envelope, blobs)
        binding = dict(
            arm="native",
            run_id=request["run_id"],
            attempt_dir=str(attempt),
            process_dir=str(process),
            work_dir=str(work),
            output_dir=request["output_dir"],
            gpu_uuid="GPU-X",
            resource_class="GPU_EXCLUSIVE",
            envelope_sha256=envelope_sha,
            source_manifest_sha256=worker._digest(worker._canonical(project)),
            attempt_dir_identity=worker._directory_identity(attempt),
            process_dir_identity=worker._directory_identity(process),
        )
        code = r'''
import json, os, runpy, sys
os.read(int(sys.argv[5]), 1)
ns = runpy.run_path(sys.argv[1])
def callback(request, snapshots):
    output = ns["Path"](request["output_dir"])
    output.mkdir()
    status = {"status":"ARM_RETURNED_UNVALIDATED","arm":request["arm"],"provenance":"MOCK","scientific_validation_passed":False}
    (output / "input-binding.json").write_bytes(ns["_canonical"]({"mock":True}))
    (output / "worker-status.json").write_bytes(ns["_canonical"](status))
    return status
wrapper = ns["execute_owned"](sys.argv[2], ns["Path"](sys.argv[3]), sys.argv[4], ns["Path"](sys.argv[6]), _test_dependencies={"runtime_callback":callback})
print(json.dumps(wrapper, sort_keys=True))
'''
        read_fd, write_fd = os.pipe()
        argv = [
            worker.PINNED_PYTHON,
            "-I",
            "-S",
            "-B",
            "-X",
            "pycache_prefix=" + str(prefix),
            "-c",
            code,
            str(worker.ROOT / "scripts/eval/hf_owned_worker.py"),
            "native",
            str(process / "wire/envelope.json"),
            envelope_sha,
            str(read_fd),
            str(process / "ownership-record.json"),
        ]
        launch_env = dict(runtime_env, PYTHONPYCACHEPREFIX=str(prefix))
        child = subprocess.Popen(
            argv,
            env=launch_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(read_fd,),
            start_new_session=True,
        )
        os.close(read_fd)
        try:
            parent_identity = run_manifest.process_identity(
                os.getpid(), include_zombies=True
            )
            child_identity = run_manifest.process_identity(
                child.pid, include_zombies=True
            )
            callback = hf_routing_runner._ownership_callback(
                process, binding, parent_identity
            )
            callback(child_identity, "producer")
            os.write(write_fd, b"1")
        finally:
            os.close(write_fd)
        stdout, stderr = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 0, stderr)
        wrapper = json.loads(stdout)
        self.assertEqual(wrapper["status"], "ARM_RETURNED_UNVALIDATED")
        self.assertEqual(wrapper["provenance"], "MOCK")
        self.assertIsNone(wrapper["device_observations_sha256"])
        self.assertTrue((process / "bootstrap.json").is_file())
        self.assertEqual(list(prefix.rglob("*")), [])

        alternate_temp = tempfile.TemporaryDirectory(
            prefix=".owned-alternate-metadata-", dir=worker.ROOT
        )
        self.addCleanup(alternate_temp.cleanup)
        _, alternate = hf_fixture(
            Path(alternate_temp.name), vocab_size=1032, max_position_embeddings=32
        )
        verify_hf_metadata.check_current_inputs(alternate)

        def failing_case(case_name, mode):
            arm = "native"
            case_attempt = Path(owned_temp.name) / (case_name + "-attempt")
            process = case_attempt / "process/native"
            (case_attempt / "arms").mkdir(parents=True)
            process.mkdir(parents=True)
            work = (
                worker.ROOT
                / "results/tmp/hf-routing"
                / (Path(owned_temp.name).name + "-" + case_name)
                / "native"
            )
            runtime_env = protocol["make_environment"](
                work, "GPU-X", platform_env
            )
            prefix = hf_routing_runner._prepare_work(work, runtime_env)
            request = dict(
                schema_version=1,
                arm=arm,
                run_id="valid-mock-run",
                git_commit="a" * 40,
                environment_fingerprint="b" * 64,
                gpu_uuid="GPU-X",
                device_name=hf_routing_runner.DEVICE_NAME,
                device_capability=[12, 0],
                work_dir=str(work),
                output_dir=str(case_attempt / "arms/native"),
                process_dir=str(process),
                attempt_dir=str(case_attempt),
                metadata_bundle=str(bundle),
                runtime_env=runtime_env,
                transport_env={"PYTHONPYCACHEPREFIX": str(prefix)},
                prompt_token_ids=list(range(1000, 1032)),
                project_sources=json.loads(json.dumps(project)),
            )
            if mode == "type":
                request["prompt_token_ids"][0] = 1000.0
            elif mode == "prefix":
                request["transport_env"] = {
                    "PYTHONPYCACHEPREFIX": str(work / "cache/other")
                }
            elif mode == "source":
                first = next(iter(request["project_sources"]["files"].values()))
                first["sha256"] = "0" * 64
            elif mode == "namespace":
                first = next(iter(request["project_sources"]["files"].values()))
                request["project_sources"]["files"]["adapters/__init__.py"] = dict(
                    first
                )
            envelope, blobs = worker._encode_wire(*snapshots, request)
            envelope_sha = worker._write_wire(process / "wire", envelope, blobs)
            binding = dict(
                arm=arm,
                run_id=request["run_id"],
                attempt_dir=str(case_attempt),
                process_dir=str(process),
                work_dir=str(work),
                output_dir=request["output_dir"],
                gpu_uuid="GPU-X",
                resource_class="GPU_EXCLUSIVE",
                envelope_sha256=envelope_sha,
                source_manifest_sha256=worker._digest(
                    worker._canonical(request["project_sources"])
                ),
                attempt_dir_identity=worker._directory_identity(case_attempt),
                process_dir_identity=worker._directory_identity(process),
            )
            failure_code = r'''
import json, os, runpy, sys
os.read(int(sys.argv[5]), 1)
ns = runpy.run_path(sys.argv[1])
called = []
def callback(request, snapshots):
    called.append(True)
    if sys.argv[7] == "metadata-only":
        bundle = ns["Path"](sys.argv[8])
        alternate = ns["Path"](sys.argv[9])
        bundle.rename(bundle.with_name("retained-original-refresh"))
        alternate.rename(bundle)
    if sys.argv[7] == "wire-only":
        wire = ns["Path"](request["process_dir"]) / "wire"
        envelope = json.loads((wire / "envelope.json").read_bytes())
        payload = wire / envelope["blobs"][0]["ordinal_name"]
        raw = payload.read_bytes()
        payload.unlink()
        payload.write_bytes(raw)
    raise RuntimeError("callback-primary-sentinel")
try:
    kwargs = ({"_facts":ns["_actual_bootstrap_facts"]()} if sys.argv[7] == "facts" else {"_test_dependencies":{"runtime_callback":callback}})
    ns["execute_owned"](sys.argv[2], ns["Path"](sys.argv[3]), sys.argv[4], ns["Path"](sys.argv[6]), **kwargs)
except BaseException as error:
    print(json.dumps({"type":type(error).__name__,"message":str(error),"stage":getattr(error,"_hf_stage",None),"postchecks":getattr(error,"_hf_postchecks",None),"callback_count":len(called)}, sort_keys=True))
else:
    raise AssertionError("callback failure was swallowed")
'''
            read_fd, write_fd = os.pipe()
            argv = [
                worker.PINNED_PYTHON,
                "-I",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=" + str(prefix),
                "-c",
                failure_code,
                str(worker.ROOT / "scripts/eval/hf_owned_worker.py"),
                arm,
                str(process / "wire/envelope.json"),
                envelope_sha,
                str(read_fd),
                str(process / "ownership-record.json"),
                mode,
                str(bundle),
                str(alternate),
            ]
            launch_env = dict(runtime_env, PYTHONPYCACHEPREFIX=str(prefix))
            if mode == "env":
                launch_env["UNEXPECTED_OWNED_ENV"] = "1"
            child = subprocess.Popen(
                argv,
                env=launch_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                pass_fds=(read_fd,),
                start_new_session=True,
            )
            os.close(read_fd)
            try:
                parent_identity = run_manifest.process_identity(
                    os.getpid(), include_zombies=True
                )
                child_identity = run_manifest.process_identity(
                    child.pid, include_zombies=True
                )
                acknowledge = hf_routing_runner._ownership_callback(
                    process, binding, parent_identity
                )
                acknowledge(child_identity, "producer")
                if mode == "ownership":
                    ownership = process / "ownership-record.json"
                    changed = json.loads(ownership.read_bytes())
                    changed["schema_version"] = 1.0
                    ownership.write_text(json.dumps(changed))
                os.write(write_fd, b"1")
            finally:
                os.close(write_fd)
            stdout, stderr = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, stderr)
            return json.loads(stdout)

        plain_failure = failing_case("callback", "plain")
        self.assertEqual(plain_failure["message"], "callback-primary-sentinel")
        self.assertEqual(plain_failure["stage"], "loaded-arm")
        self.assertEqual(plain_failure["callback_count"], 1)
        self.assertTrue(plain_failure["postchecks"])
        self.assertTrue(
            all(row["status"] == "passed" for row in plain_failure["postchecks"])
        )
        for mode, stage in (
            ("type", "input"),
            ("facts", None),
            ("prefix", "input"),
            ("source", "project"),
            ("namespace", "project"),
            ("ownership", "ownership"),
            ("env", "input"),
        ):
            rejected = failing_case(mode, mode)
            with self.subTest(mode=mode):
                self.assertEqual(rejected["callback_count"], 0)
                self.assertEqual(rejected["stage"], stage)
        wire_failure = failing_case("wire-only", "wire-only")
        self.assertEqual(wire_failure["message"], "callback-primary-sentinel")
        self.assertEqual(wire_failure["stage"], "loaded-arm")
        self.assertEqual(wire_failure["callback_count"], 1)
        self.assertTrue(
            any(
                row["check"] == "wire" and row["status"] == "failed"
                for row in wire_failure["postchecks"]
            )
        )
        metadata_failure = failing_case("metadata-only", "metadata-only")
        self.assertEqual(metadata_failure["message"], "callback-primary-sentinel")
        self.assertEqual(metadata_failure["stage"], "loaded-arm")
        self.assertEqual(metadata_failure["callback_count"], 1)
        self.assertTrue(
            any(
                row["check"] == "metadata-current" and row["status"] == "failed"
                for row in metadata_failure["postchecks"]
            )
        )
        verify_hf_metadata.check_current_inputs(bundle)
        self.assertNotEqual(evaluation_inventory.load_hf_snapshot(bundle), metadata)
        with self.assertRaisesRegex(ValueError, "differs from frozen wire snapshot"):
            worker._recheck_metadata_current(
                evaluation_inventory, verify_hf_metadata, metadata
            )

    def test_fresh_prefix_defeats_valid_unchecked_hash_pyc_without_writing_cache(self):
        source = self.base / "poisoned.py"
        source.write_text("VALUE='poisoned'\n")
        cache = source.parent / "__pycache__"
        cache.mkdir()
        pyc = cache / (
            "poisoned." + worker.sys.implementation.cache_tag + ".pyc"
        )
        py_compile.compile(
            str(source),
            cfile=str(pyc),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        )
        before = pyc.read_bytes()
        source.write_text("VALUE='actual-source'\n")
        command = (
            "import sys;sys.path.insert(0,"
            + repr(str(self.base))
            + ");import poisoned;print(poisoned.VALUE)"
        )
        ordinary = subprocess.check_output(
            [worker.PINNED_PYTHON, "-B", "-c", command], text=True
        ).strip()
        prefix = self.base / "fresh-prefix"
        prefix.mkdir()
        isolated = subprocess.check_output(
            [
                worker.PINNED_PYTHON,
                "-B",
                "-X",
                "pycache_prefix=" + str(prefix),
                "-c",
                command,
            ],
            text=True,
        ).strip()
        self.assertEqual(ordinary, "poisoned")
        self.assertEqual(isolated, "actual-source")
        self.assertEqual(list(prefix.rglob("*")), [])
        self.assertEqual(pyc.read_bytes(), before)

    def test_cli_has_only_fixed_private_arguments_and_rejects_test_material(self):
        parser = worker._parser()
        names = {a.dest for a in parser._actions}
        self.assertEqual(
            names,
            {"help", "_owned_arm", "envelope", "envelope_sha256", "ownership_record"},
        )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--_owned-arm",
                    "native",
                    "--envelope",
                    "wire/envelope.json",
                    "--envelope-sha256",
                    "a" * 64,
                    "--ownership-record",
                    "x",
                    "--loader",
                    "evil",
                ]
            )


    def test_cuda_route_flag_strict_wire_and_actual_isolated_child_plan(self):
        owned=tempfile.TemporaryDirectory(prefix='.owned-cuda-wire-',dir=worker.ROOT/'results/gold')
        self.addCleanup(owned.cleanup)
        attempt=Path(owned.name)
        work=worker.ROOT/'results/tmp/hf-routing'/attempt.name/'capture'
        document=dict(self.request,arm='capture',device_name='NVIDIA RTX PRO 6000 Blackwell Server Edition',
            work_dir=str(work),output_dir=str(attempt/'arms/capture'),process_dir=str(attempt/'process/capture'),
            attempt_dir=str(attempt),metadata_bundle=str(worker.ROOT/'.mock-cuda-wire-metadata'),
            transport_env={'PYTHONPYCACHEPREFIX':str(work/'cache/python')},capture_cuda_route_events=True)
        self.assertEqual(worker._request(document,'capture'),document)
        for extra in ({'capture_cuda_route_events':1},{'capture_cuda_route_events':'true'},
                      {'unknown_timing_factory':'anything'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                worker._request(dict(document,**extra),'capture')
        with self.assertRaises(ValueError):
            worker._request(dict(document,arm='native'),'native')
        old=dict(document);del old['capture_cuda_route_events']
        self.assertFalse(worker._loaded_plan(worker._request(old,'capture'),None,None,None)['capture_cuda_route_events'])
        self.request=document
        directory,_,_=self._wire()
        prefix=attempt/'private-pycache';prefix.mkdir()
        child_code = """
import hashlib,json,pathlib,runpy,sys
ns=runpy.run_path(sys.argv[1]);path=pathlib.Path(sys.argv[2])
envelope,_=ns['_read_wire'](path,sys.argv[3])
request=ns['_request'](envelope['request'],'capture')
plan=ns['_loaded_plan'](request,'MOCK-metadata','MOCK-runtime','MOCK-tuning')
assert plan['capture_cuda_route_events'] is True
assert plan['metadata_snapshot']=='MOCK-metadata'
assert not any(k=='torch' or k.startswith(('torch.','vllm.')) for k in sys.modules)
print(json.dumps({'flag':plan['capture_cuda_route_events'],'arm':request['arm'],'provenance':'MOCK'}))
"""
        path=directory/'envelope.json'
        child=subprocess.run([worker.PINNED_PYTHON,'-I','-S','-B','-X','pycache_prefix='+str(prefix),
            '-c',child_code,str(worker.ROOT/'scripts/eval/hf_owned_worker.py'),str(path),
            hashlib.sha256(path.read_bytes()).hexdigest()],capture_output=True,text=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(json.loads(child.stdout),{'flag':True,'arm':'capture','provenance':'MOCK'})
        self.assertEqual(list(prefix.iterdir()),[])


if __name__ == "__main__":
    unittest.main()
