# Basic parameterized HBM CPU timing

Status: implementation and fixed tests prepared; not executed while the serial R03 reference is
running. This module is a `PARAMETRIC_HBM_SCENARIO`, not a DRAM timing model or real backend.

`tools/eq3_basic_hbm.py` provides one independent FIFO media server per configured stack. A read
or write uses

```
media_duration_ns = media_latency_ns[op]
                  + ceil(bytes * 1e9 / media_bandwidth_Bps[op])
```

Both terms are explicit per-stack inputs. The repository contains a 2.048 TB/s raw-interface
design value derived from a 2048-bit interface and an 8 Gbit/s design point. Treating that raw
interface ceiling as media service bandwidth is therefore labeled
`DERIVED_WITH_TRANSFER_ASSUMPTION`, not a calibrated HBM media rate. No supported source supplies
the requested media latency. The opt-in example uses 100 ns for reads and writes and labels it
`SCENARIO_ASSUMPTION`; the class itself has no hidden latency or bandwidth default.

The event API is `arrival(request)`, `submit(request_id, time_ns)`, `advance(target_ns)`,
`next_event_ns()`, `take_facts()`, and `take_media_completions()`. Arrival, submit, media start,
and media completion remain distinct facts. A media completion has the shared handoff fields
`phase=MEDIA_DONE`, `request_id`, `stack`, `bytes`, and `time_ns`, plus `requires_fabric=true`.
It is not a reported request completion. A separate fabric module owns GPU-link or cascaded-link
arbitration and delay, so link service is not counted here a second time.

Die and plane remain `UNKNOWN`; supplied die/plane placement is rejected rather than inferred
from an address. With no energy parameter, media energy is JSON null and status is
`UNKNOWN_UNPARAMETERIZED`, never zero. Refresh returns `UNSUPPORTED_CAPABILITY`. The module does
not model channels, banks, row buffers, DRAM timing commands, refresh interference, QoS, or a
physical HBM energy model.

`tools/test_eq3_basic_hbm.py` prepares fixed coverage for the latency/bandwidth formula,
same-stack FIFO order, cross-stack parallelism, event boundaries, fabric handoff, unknown
energy/die/plane, refresh capability, and invalid lifecycle/placement inputs. These tests still
require execution through the shared serial runner after R03 releases the CPU slot.
