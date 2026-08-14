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
    with tempfile.TemporaryDirectory(prefix="hbfsim-tma-ptx-") as directory:
        cases = (
            ("tests/fixtures/ptx/tma_sm120.ptx", "tma_sm120", target, 3),
            ("tests/fixtures/ptx/tensormap_update_sm120a.ptx",
             "tensormap_update_sm120a", "sm_120a", 5),
            ("tests/fixtures/ptx/tma_im2col_multicast_sm120.ptx",
             "tma_im2col_multicast_sm120", target, 3),
            ("tests/fixtures/ptx/tensormap_copy_sm120a.ptx",
             "tensormap_copy_sm120a", "sm_120a", 5),
            ("tests/fixtures/ptx/tma_reduce_sm120.ptx",
             "tma_reduce_sm120", target, 4),
            ("tests/fixtures/ptx/tma_prefetch_sm120.ptx",
             "tma_prefetch_sm120", target, 5),
            ("tests/fixtures/ptx/tma_cta_group2_sm120a.ptx",
             "tma_cta_group2_sm120a", "sm_120a", 3),
            ("tests/fixtures/ptx/tma_im2col_store_sm120.ptx",
             "tma_im2col_store_sm120", target, 5),
        )
        for index, (fixture_path, kernel, architecture, rewritten) in enumerate(cases):
            fixture = pathlib.Path(fixture_path).read_text()
            request = {
                "input": {
                    "full_ptx": fixture,
                    "to_patch_kernel": kernel,
                    "async_futures": True,
                }
            }
            completed = subprocess.run(
                [str(transform)], input=json.dumps(request), text=True,
                capture_output=True, check=True,
            )
            response = json.loads(completed.stdout)
            assert response["modified"] is True
            assert response["coverage"]["rewritten_instructions"] == rewritten
            output = response["output_ptx"]
            assert "__hbfsim_tma_issue" in output
            if index in (0, 1, 2, 3, 6):
                assert "HBFSim conjunctive TMA barrier poll" in output
            if index == 1:
                assert "__hbfsim_tensormap_replace_begin" in output
                assert "__hbfsim_tensormap_replace_commit" in output
                assert "__hbfsim_tensormap_acquire" in output
            if index == 2:
                assert "cvt.u32.u16" in output
                assert "_offset_0_32" in output
                assert "_mask32" in output
            if index == 3:
                assert "__hbfsim_tensormap_copy_begin" in output
                assert "__hbfsim_tensormap_copy_commit" in output
            if index == 4:
                assert "_reduction], 0;" in output
                assert "HBFSim TMA bulk-group full wait" in output
            if index == 5:
                assert "_direction], 2;" in output
                assert "HBFSim TMA bulk-group read wait" in output
                assert "HBFSim TMA bulk-group full wait" in output
            if index == 6:
                assert "_cta_group], 2;" in output
                assert "cta_group::2" not in output
                assert "_software trap;" in output
            if index == 7:
                assert "_direction], 1;" in output
                assert "_access], 3;" in output
                assert "HBFSim TMA bulk-group read wait" in output
            ptx_path = pathlib.Path(directory) / f"transformed-{index}.ptx"
            cubin_path = pathlib.Path(directory) / f"transformed-{index}.cubin"
            ptx_path.write_text(output)
            subprocess.run(
                [str(ptxas), f"--gpu-name={architecture}", str(ptx_path),
                 "--output-file", str(cubin_path)],
                check=True,
            )
            assert cubin_path.stat().st_size > 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
