#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys
import tempfile


def main() -> int:
    executable = pathlib.Path(sys.argv[1])
    fixture = pathlib.Path("tests/fixtures/ptx/supported.ptx").read_text()
    request = {
        "input": {
            "full_ptx": fixture,
            "to_patch_kernel": "kernel",
            "global_ebpf_map_info_symbol": "map_info",
            "ebpf_communication_data_symbol": "constData",
        },
        "ebpf_instructions": [],
    }
    completed = subprocess.run(
        [str(executable)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    response = json.loads(completed.stdout)
    assert response["modified"] is True
    assert response["coverage"]["rewritten_instructions"] == 3
    assert "__hbfsim_resolve" in response["output_ptx"]

    ptxas = pathlib.Path("/usr/local/cuda-12.8/bin/ptxas")
    if ptxas.is_file():
        with tempfile.TemporaryDirectory(prefix="hbfsim-ptx-") as directory:
            ptx_path = pathlib.Path(directory) / "transformed.ptx"
            cubin_path = pathlib.Path(directory) / "transformed.cubin"
            ptx_path.write_text(response["output_ptx"])
            subprocess.run(
                [str(ptxas), "-arch=sm_120", str(ptx_path), "-o", str(cubin_path)],
                check=True,
            )

    invalid = subprocess.run(
        [str(executable)], input="{", text=True, capture_output=True
    )
    assert invalid.returncode == 64
    assert len(invalid.stderr.rstrip().splitlines()) == 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
