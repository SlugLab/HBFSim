#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys
import tempfile


def main() -> int:
    transform = pathlib.Path(sys.argv[1]).resolve()
    ptxas = pathlib.Path(sys.argv[2]).resolve()
    target = sys.argv[3]
    fixture = pathlib.Path("tests/fixtures/ptx/tma_sm120.ptx").read_text()
    request = {
        "input": {
            "full_ptx": fixture,
            "to_patch_kernel": "tma_sm120",
            "async_futures": True,
        }
    }
    completed = subprocess.run(
        [str(transform)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    response = json.loads(completed.stdout)
    assert response["modified"] is True
    assert response["coverage"]["rewritten_instructions"] == 2
    output = response["output_ptx"]
    assert "__hbfsim_tma_issue" in output
    assert "HBFSim conjunctive TMA barrier poll" in output

    with tempfile.TemporaryDirectory(prefix="hbfsim-tma-ptx-") as directory:
        ptx_path = pathlib.Path(directory) / "transformed.ptx"
        cubin_path = pathlib.Path(directory) / "transformed.cubin"
        ptx_path.write_text(output)
        subprocess.run(
            [str(ptxas), f"--gpu-name={target}", str(ptx_path),
             "--output-file", str(cubin_path)],
            check=True,
        )
        assert cubin_path.stat().st_size > 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
