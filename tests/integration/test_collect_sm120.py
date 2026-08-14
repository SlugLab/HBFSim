#!/usr/bin/env python3
"""Black-box tests for the read-only SM120 evidence collector."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / "scripts/calibration/collect_sm120.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_executable(path: pathlib.Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def invoke(arguments: list[str], expected: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run([sys.executable, str(COLLECTOR), *arguments],
                               text=True, capture_output=True, env=env,
                               check=False, timeout=30)
    require(completed.returncode == expected,
            f"expected {expected}, got {completed.returncode}: {completed.stderr}")
    return completed


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hbfsim-collect-test-") as temporary:
        root = pathlib.Path(temporary)
        tools = root / "tools"
        tools.mkdir()
        log = root / "commands.log"
        benchmark = tools / "benchmark"
        ncu = tools / "ncu"
        nvcc = tools / "nvcc"
        nvidia_smi = tools / "nvidia-smi"
        write_executable(benchmark, """#!/usr/bin/env python3
import hashlib,json,sys
case=sys.argv[sys.argv.index('--case-id')+1]
value=hashlib.sha256(case.encode()).hexdigest()
print(json.dumps({'schema_version':1,'case_id':case,'bit_exact':True,
 'output_sha256':value,'expected_sha256':value,'timestamps':{'issue':1,'end':2},
 'smid':0,'warpid':0,'cluster_ctarank':0}))
""")
        write_executable(nvcc, """#!/bin/sh
echo 'Cuda compilation tools, release 13.0, V13.0.88'
""")
        write_executable(ncu, """#!/usr/bin/env python3
import os,subprocess,sys
with open(os.environ['FAKE_COMMAND_LOG'],'a') as out: out.write(' '.join(sys.argv[1:])+'\\n')
if '--version' in sys.argv:
 print('Version 2025.4.1.0 (build fake)')
 raise SystemExit(0)
index=sys.argv.index('--')
run=subprocess.run(sys.argv[index+1:],text=True,capture_output=True)
print('ID,Kernel Name,Metric Name,Metric Value')
metrics={
 'sm__pipe_tma_cycles_active.avg.pct_of_peak_sustained_active':5,
 'smsp__inst_executed_pipe_lsu.avg.pct_of_peak_sustained_active':10,
 'smsp__warp_issue_stalled_long_scoreboard_per_warp_active.pct':15,
 'smsp__warp_issue_stalled_barrier_per_warp_active.pct':20,
 'smsp__warp_issue_stalled_membar_per_warp_active.pct':25,
 'lts__t_sectors.sum':30,
 'dram__bytes.sum':35,
 'gpu__time_duration.sum':100,
}
for name,value in metrics.items(): print(f'0,kernel,{name},{value}')
print(run.stdout,end='')
print(run.stderr,end='',file=sys.stderr)
raise SystemExit(run.returncode)
""")
        write_executable(nvidia_smi, """#!/usr/bin/env python3
import sys
query=' '.join(sys.argv[1:])
if '--query-compute-apps=' in query:
 raise SystemExit(0)
if '--query-gpu=' in query:
 print('0, NVIDIA RTX PRO 6000 Blackwell Server Edition, GPU-test, 00000000:01:00.0, 0x2BB510DE, 12.0, 1830, 14001, 600.00, 45')
 raise SystemExit(0)
raise SystemExit(2)
""")
        cases = root / "cases.json"
        cases.write_text(json.dumps({
            "manifest_schema_version": 1, "suite": "training",
            "warmup": 1, "repetitions": 3,
            "exact_profile_contract": {
                "cache_condition": "warm_l2",
                "concurrency_condition": "exclusive_process",
                "cluster_shape": [1, 1, 1],
                "thresholds": {"p50_percent": 5, "p95_percent": 10,
                               "counter_percent": 10},
                "limits": {"max_thread_futures": 16,
                           "max_warp_futures": 256,
                           "max_cta_futures": 2048,
                           "max_cluster_futures": 8192,
                           "max_thread_async_objects": 8,
                           "max_warp_async_objects": 128,
                           "max_cta_async_objects": 1024,
                           "max_cluster_async_objects": 4096},
            },
            "cases": [{"id": "fake-load", "operation_class": "ordinary_load"}],
        }, sort_keys=True))
        env = dict(os.environ)
        env["FAKE_COMMAND_LOG"] = str(log)
        env["CUDA_VISIBLE_DEVICES"] = "0"
        env["PATH"] = str(tools) + os.pathsep + env.get("PATH", "")
        env["HBFSIM_TEST_CUDA_DRIVER_VERSION"] = "13020"
        output = root / "evidence"
        invoke(["--suite", "training", "--cases", str(cases),
                "--benchmark", str(benchmark), "--ncu", str(ncu),
                "--output-dir", str(output)], 0, env)
        manifest = json.loads((output / "manifest.json").read_text())
        require(manifest["schema_version"] == 1, "wrong manifest schema")
        require(manifest["suite"] == "training", "wrong suite")
        require(manifest["tools"]["ncu"]["version"].startswith("2025.4.1.0"),
                "wrong ncu version")
        require("release 13.0" in manifest["tools"]["nvcc"]["version"],
                "wrong CUDA version")
        require(manifest["environment"]["CUDA_VISIBLE_DEVICES"] == "0",
                "environment not captured")
        require(len(manifest["environment_sha256"]) == 64,
                "environment hash missing")
        require(manifest["calibration_environment"]["target"] == {
            "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
            "gpu_uuid": "GPU-test", "pci_vendor_id": 0x10DE,
            "pci_device_id": 0x2BB5,
            "compute_capability_major": 12,
            "compute_capability_minor": 0, "driver_version": 13020,
        }, "target snapshot missing")
        require(manifest["exact_profile_contract"] ==
                json.loads(cases.read_text())["exact_profile_contract"],
                "frozen exact contract missing")
        require(manifest["gpu_processes"] == [] and
                manifest["exclusive_process_observed"] is True,
                "collector did not prove exclusive process")
        require(len(manifest["gpu_snapshots"]) >= 2,
                "operating snapshots missing")
        require(len(manifest["runs"]) == 3, "wrong repetition count")
        require(len(manifest["observations"]) == 3,
                "parsed observations missing")
        require(all(item["native_latency_ns"] == 100 and
                    len(item["contention_vector"]) == 4 and
                    len(item["return_contention_vector"]) == 2
                    for item in manifest["observations"]),
                "Nsight metrics were not normalized into observations")
        for member in manifest["members"]:
            path = output / member["path"]
            require(path.is_file() and not path.is_symlink(), "missing raw member")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"],
                    "raw hash mismatch")
        command_text = log.read_text()
        command_tokens = command_text.split()
        for forbidden in ("-lgc", "-lmc", "-pl", "-c", "compute-mode"):
            require(forbidden not in command_tokens,
                    f"mutation flag used: {forbidden}")

        invoke([], 64, env)
        invoke(["--suite", "training", "--cases", str(cases),
                "--benchmark", str(benchmark), "--ncu", str(ncu),
                "--output-dir", str(output)], 66, env)
        link = root / "cases-link.json"
        link.symlink_to(cases)
        invoke(["--suite", "training", "--cases", str(link),
                "--benchmark", str(benchmark), "--ncu", str(ncu),
                "--output-dir", str(root / "link-output")], 66, env)

        broken = tools / "broken-ncu"
        write_executable(broken, "#!/bin/sh\necho 'Version 0'\nexit 0\n")
        failed_output = root / "failed"
        invoke(["--suite", "training", "--cases", str(cases),
                "--benchmark", str(benchmark), "--ncu", str(broken),
                "--output-dir", str(failed_output)], 70, env)
        require(not failed_output.exists(), "partial output was published")
        require(not list(root.glob(".*.partial-*")), "partial directory leaked")

        write_executable(nvidia_smi, """#!/usr/bin/env python3
import sys
query=' '.join(sys.argv[1:])
if '--query-compute-apps=' in query:
 print('GPU-test, 999, other-process, 1024')
 raise SystemExit(0)
print('0, NVIDIA RTX PRO 6000 Blackwell Server Edition, GPU-test, 00000000:01:00.0, 0x2BB510DE, 12.0, 1830, 14001, 600.00, 45')
""")
        blocked = root / "blocked"
        invoke(["--suite", "training", "--cases", str(cases),
                "--benchmark", str(benchmark), "--ncu", str(ncu),
                "--output-dir", str(blocked)], 70, env)
        require(not blocked.exists(), "competing GPU work was not fail closed")
    print(json.dumps({"status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
