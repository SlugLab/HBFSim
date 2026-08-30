#!/usr/bin/env python3

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    executable = pathlib.Path(sys.argv[1])
    target = sys.argv[2] if len(sys.argv) > 2 else "sm_120"
    if not target.removeprefix("sm_").isdigit():
        raise ValueError(f"expected a baseline sm_XX target, got {target!r}")
    fixture = pathlib.Path(
        "tests/fixtures/ptx/supported.ptx"
    ).read_text().replace(".target sm_120", f".target {target}", 1)
    if int(target.removeprefix("sm_")) < 75:
        fixture = fixture.replace(".nc.L2::128B", ".nc")
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

    ptxas_name = sys.argv[3] if len(sys.argv) > 3 else shutil.which("ptxas")
    if ptxas_name is not None and pathlib.Path(ptxas_name).is_file():
        ptxas = pathlib.Path(ptxas_name)
        with tempfile.TemporaryDirectory(prefix="hbfsim-ptx-") as directory:
            ptx_path = pathlib.Path(directory) / "transformed.ptx"
            cubin_path = pathlib.Path(directory) / "transformed.cubin"
            ptx_path.write_text(response["output_ptx"])
            subprocess.run(
                [str(ptxas), f"-arch={target}", str(ptx_path), "-o",
                 str(cubin_path)],
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
