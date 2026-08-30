#!/usr/bin/env python3

import pathlib
import subprocess
import sys


def main() -> int:
    probe = pathlib.Path(sys.argv[1])
    objdump = pathlib.Path(sys.argv[2])
    completed = subprocess.run(
        [str(objdump), "-h", "-t", str(probe)],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "kprobe/unsupported_hbf_kernel" in completed.stdout
    assert "kprobe/fused_moe_kernel" in completed.stdout
    assert "cuda__hbf_cov" in completed.stdout
    assert "cuda__hbf_moe" in completed.stdout
    assert "license" in completed.stdout
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
