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
print('0,kernel,gpu__time_duration.sum,100')
print(run.stdout,end='')
print(run.stderr,end='',file=sys.stderr)
raise SystemExit(run.returncode)
""")
        cases = root / "cases.json"
        cases.write_text(json.dumps({
            "manifest_schema_version": 1, "suite": "training",
            "warmup": 1, "repetitions": 3,
            "cases": [{"id": "fake-load", "operation_class": "ordinary_load"}],
        }, sort_keys=True))
        env = dict(os.environ)
        env["FAKE_COMMAND_LOG"] = str(log)
        env["CUDA_VISIBLE_DEVICES"] = "0"
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
        require(len(manifest["runs"]) == 3, "wrong repetition count")
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
    print(json.dumps({"status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
