#!/usr/bin/env python3
import pathlib
import re
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def body(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for pos in range(opening, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[opening:pos + 1]
    raise RuntimeError(f"unterminated function {signature}")


def between(source: str, begin: str, end: str) -> str:
    start = source.index(begin)
    return source[start:source.index(end, start + len(begin))]


def main() -> int:
    source = pathlib.Path(sys.argv[1]).read_text()
    require("static_assert(sizeof(AccessAccountingConfig) == 32)" in source,
            "config ABI is not fixed at 32 bytes")
    require("static_assert(sizeof(AccessAccountingCounters) == 176)" in source,
            "counter ABI is not fixed at 176 bytes")

    begin = body(source, "int access_begin(")
    for symbol in ("__hbfsim_access_accounting_config",
                   "__hbfsim_access_accounting_counters"):
        require(symbol in begin, f"begin does not resolve {symbol}")
    require(begin.index("sync()") < begin.index("std::vector<AccessBoundModule>"),
            "begin does not synchronize before module resolution/reset")
    require("config_bytes != sizeof(AccessAccountingConfig)" in begin and
            "counters_bytes != sizeof(AccessAccountingCounters)" in begin,
            "begin does not fail closed on ABI-size mismatch")
    require(begin.count("access_disable(resolved, put)") >= 3,
            "begin failure paths do not best-effort disable configured modules")
    require(begin.index("put(bound.counters_address, &zero") <
            begin.index("enabled.enabled = 1"),
            "begin enables accounting before reset")
    require(begin.index("enabled.enabled = 1") <
            begin.index("access_session.active = true"),
            "begin publishes the session before module configuration succeeds")

    snapshot = body(source, "long long access_snapshot(")
    require(snapshot.index("initial_sync_ok") < snapshot.index("read(&counters"),
            "snapshot reads counters before synchronization")
    require("std::vector<std::optional<AccessAccountingCounters>>" in snapshot,
            "read failures can be represented only as zero counters")
    require("counter_read_failed" in snapshot and
            "module_unloaded_during_request" in snapshot,
            "snapshot lacks explicit read/unload failure states")
    require(snapshot.index("access_disable(access_session.modules, put)") <
            snapshot.index("final_sync_ok"),
            "snapshot does not disable before its final synchronization")
    require("capacity >= required" in snapshot and
            "return static_cast<long long>(required)" in snapshot,
            "snapshot does not implement required-size retry semantics")

    render = between(source, "std::string access_snapshot_json(",
                     "long long access_snapshot(")
    require("access_session.late_modules.empty()" in render and
            "late_module_after_begin" in render,
            "late modules do not force an explicit incomplete snapshot")
    require("if (!values[i])" in render and
            '"INCOMPLETE"' in render and "errors[i]" in render,
            "missing/read-failed modules are silently represented by zero")
    require('complete ? "COMPLETE" : "INCOMPLETE"' in render,
            "snapshot status is not exact COMPLETE/INCOMPLETE")

    abort = body(source, "int access_abort(")
    require(abort.index("access_disable") < abort.index("sync()"),
            "abort does not disable before synchronization")
    for exported in ("hbfsim_access_accounting_begin_v2",
                     "hbfsim_access_accounting_snapshot_v2",
                     "hbfsim_access_accounting_abort_v2",
                     "hbfsim_eval_delay_begin_v1",
                     "hbfsim_eval_delay_snapshot_v1",
                     "hbfsim_eval_delay_abort_v1"):
        require(f'extern "C"' in source[source.index(exported) - 80:
                                         source.index(exported)],
                f"{exported} is not exported with C linkage")
    require("static_assert(sizeof(EvalDelayConfig) == 32)" in source and
            "static_assert(sizeof(EvalDelayCounters) == 48)" in source and
            "static_assert(sizeof(EvalDelayTrace) == 40)" in source,
            "eval-delay ABI sizes are not fixed")
    eval_begin = between(source, "int eval_begin(", "long long eval_snapshot(")
    require("__hbfsim_eval_delay_config" in eval_begin and
            "__hbfsim_eval_delay_counters" in eval_begin and
            "cuMemAlloc_v2" in eval_begin and "trace_capacity" in eval_begin,
            "eval begin does not bind per-module config/counters/trace")
    eval_snapshot = between(source, "long long eval_snapshot(",
                            "int eval_abort(")
    require(eval_snapshot.index("initial_sync") < eval_snapshot.index("read(&counters") and
            eval_snapshot.index("eval_disable") < eval_snapshot.index("final_sync") <
            eval_snapshot.index("eval_free"),
            "eval snapshot ordering is not sync/read/disable/sync/free")
    require("trace_overflow" in eval_snapshot and
            "late_module_after_begin" in eval_snapshot and
            'complete ? "COMPLETE" : "INCOMPLETE"' in eval_snapshot,
            "eval snapshot lacks fail-closed status inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
