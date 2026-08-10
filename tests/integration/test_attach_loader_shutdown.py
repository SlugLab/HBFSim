#!/usr/bin/env python3

import pathlib
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    source = pathlib.Path(sys.argv[1]).read_text()
    block = source.find("pthread_sigmask(SIG_BLOCK")
    open_probe = source.find("bpf_object__open_file")
    require(block >= 0 and block < open_probe,
            "termination signals must be blocked before libbpf can create threads")
    require("sigwait(&termination_signals" in source,
            "attach loader must synchronously wait for termination")
    require("wait_status != 0" in source,
            "attach loader must reject sigwait errors")
    require("std::signal" not in source and "pause()" not in source,
            "attach loader must not use an asynchronous signal handler")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
