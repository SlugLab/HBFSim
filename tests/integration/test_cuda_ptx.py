#!/usr/bin/env python3

import pathlib
import subprocess
import sys


def main() -> int:
    executable = pathlib.Path(sys.argv[1])
    cuobjdump = pathlib.Path(sys.argv[2])
    completed = subprocess.run(
        [str(cuobjdump), "--dump-ptx", str(executable)],
        text=True,
        capture_output=True,
        check=True,
    )
    assert ".target sm_120" in completed.stdout
    assert ".entry unsupported_hbf_kernel" in completed.stdout
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
