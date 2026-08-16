#!/usr/bin/env python3

import importlib.util
import pathlib


SCRIPT = pathlib.Path(__file__).with_name("test_thermal_timing_live.py")
SPEC = importlib.util.spec_from_file_location("thermal_timing_live", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main() -> int:
    selected = "GPU-selected"
    rows = "\n".join((
        "GPU-other, 10, other, 8192",
        "GPU-selected, 20, selected-a, 1024",
        "GPU-selected, 21, selected-b, 2048",
    ))
    summary = MODULE.parse_compute_processes(rows, selected)
    assert summary == "pid=20:selected-a:1024 MiB, pid=21:selected-b:2048 MiB"
    assert MODULE.parse_compute_processes("", selected) == ""

    try:
        MODULE.parse_compute_processes("malformed", selected)
    except RuntimeError as error:
        assert "malformed" in str(error)
    else:
        raise AssertionError("malformed process inventory was accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
