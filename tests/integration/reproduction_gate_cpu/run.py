#!/usr/bin/env python3
"""Run the real gate DSO against CPU-only CUDA ABI fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv, **kwargs):
    result = subprocess.run(argv, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{argv}: rc={result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parent
    gate = args.gate.resolve()
    receipt = args.receipt.resolve()
    if receipt.exists():
        parser.error(f"receipt exists: {receipt}")
    with tempfile.TemporaryDirectory(prefix="hbfsim-gate-cpu-") as tmp:
        tmp = Path(tmp)
        driver = tmp / "libcuda.so.1"
        cudart = tmp / "libcudart.so.12"
        commands = [
            ["/usr/bin/gcc", "-shared", "-fPIC", "-Wl,-soname,libcuda.so.1",
             str(fixture / "fake_cuda.c"), "-o", str(driver)],
            ["/usr/bin/gcc", "-shared", "-fPIC", "-Wl,-soname,libcudart.so.12",
             "-Wl,--version-script=" + str(fixture / "cudart.map"),
             str(fixture / "fake_cudart.c"), "-o", str(cudart)],
        ]
        for argv in commands:
            command(argv)
        (tmp / "libcudart.so.13").symlink_to(cudart.name)
        env = os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES="", HBFSIM_FAKE_CUDA_DIR=str(tmp),
                   HBFSIM_GATE_DSO=str(gate),
                   LD_LIBRARY_PATH=str(tmp) + ":" + env.get("LD_LIBRARY_PATH", ""))
        tested = subprocess.run([sys.executable, str(fixture / "run_gate_cpu.py")],
                                env=env, text=True, capture_output=True, timeout=60)
        output = json.loads(tested.stdout) if tested.returncode == 0 else None
        result = {"schema": "hbfsim.gate_real_dso_cpu.v1",
                  "status": "PASS" if tested.returncode == 0 and output and
                  output.get("status") == "PASS" and len(output.get("rows", [])) == 12 else "FAIL",
                  "gate": str(gate), "gate_sha256": sha(gate), "commands": commands,
                  "fake_driver_sha256": sha(driver), "fake_cudart_sha256": sha(cudart),
                  "test_source_sha256": sha(fixture / "run_gate_cpu.py"),
                  "returncode": tested.returncode, "stdout": tested.stdout,
                  "stderr": tested.stderr, "output": output}
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result, indent=2) + "\n")
        print(result["status"], len(output.get("rows", [])) if output else 0,
              str(receipt))
        return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
