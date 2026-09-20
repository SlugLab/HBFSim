# Basic parameterized transfer fabric

Status: approved minimal engineering implementation design, 2026-09-20.
The module is default-off and has no existing runtime consumer. Parameters are
`SCENARIO_ASSUMPTION`, not calibrated HBM/HBF or product values.

## Boundary and interface

`tools/eq3_basic_fabric.py` begins after a backend says media data are ready.
It does not issue NAND/DRAM commands, predict media readiness, change MQSim or
`CpuService`, call user code, or run a thermal solver.

```python
fabric = BasicFabric(config)
fabric.enqueue(request_id, source_stack, route, bytes, ready_ns)
if fabric.reserve_source(request_id, source_stack, route, bytes, arrival_ns):
    submit_to_backend(request_id)
fabric.mark_source_ready(request_id, backend_completion_ns)
fabric.advance(horizon_ns)
fabric.next_event_ns()       # integer timestamp or None
fabric.completions()         # immutable copies; polling, no callback/re-entry
fabric.resource_state()      # read-only ownership snapshot for leak checks
fabric.immutable_facts()     # normalized topology/parameters/limitations
```

Accepted transfers are HBF `direct`, HBF `relay`, and HBM `direct`. IDs are
globally unique. Time and byte fields are non-negative integer nanoseconds and
positive integer bytes. Every stage transports the request's original byte
count; completion occurs exactly once.

`enqueue()` is the synthetic mode: its ready timestamp enters the modeled
shared-fill stage. A real command backend whose completion already includes
NAND data-out to the controller uses the bounded reservation mode instead.
`reserve_source()` occupies the lowest free source-base bank and returns
`True`; only then may the consumer submit the command. HBF commands reserve an
HBF bank. HBM local commands reserve the same HBM base-bank pool later used by
relayed HBF chunks, so local media output and relay receive both have bounded
storage. With both applicable banks occupied it returns
`False` without creating a request. `mark_source_ready()` publishes the backend
completion into that reserved bank and deliberately adds no fill latency or
energy. This prevents both duplicate transfer timing and unbounded buffering
of data already fetched by the backend.

## State and resources

An HBF request moves through:

1. media-ready queue;
2. the stack's single parameterized shared-TSV/SRAM-fill resource;
3. one of two HBF SRAM banks;
4. either its independent GPU-HBF direct link, or its pair-private HBF-HBM
   relay link into one of two HBM relay SRAM banks;
5. for relay, the paired HBM GPU link; then completion.

An HBM local read enters the same HBM GPU-link queue used by relayed HBF data.
It does not consume HBF media or HBF SRAM. HBM local and relay traffic therefore
contend at the required shared link.

Every HBF has its own fill/direct/relay resources and every HBM has its own GPU
link and relay banks. Pairing is one-to-one. There is no package-global lock.
Two ready HBF banks may drain concurrently through direct and relay links.
Only fill is shared; bank capacity and downstream availability still apply.

Each link/fill stage has explicit `latency_ns` and `bandwidth_bytes_per_s`.
Duration is `latency_ns + ceil(bytes * 1e9 / bandwidth_bytes_per_s)`. HBF and
HBM bank counts and byte capacities are explicit configuration fields; fixed
tests use two banks, while validation does not silently invent values.

## Determinism

At one timestamp the engine performs, in order:

1. complete every ending stage in request sequence order;
2. release its link/bank and expose the next state;
3. make backend-completion and synthetic source-ready arrivals visible;
4. admit eligible work in original enqueue order, using the lowest free bank.

New zero-duration events are impossible because all bandwidths and transported
bytes are positive. Enqueue does not execute work; `advance()` is the sole event
progression boundary. Completion is polled after `advance()`, so no callback can
re-enter scheduling.

## Evidence limits

Fixed tests cover bytes/IDs, deterministic timestamps, bank backpressure,
same-HBF direct/relay overlap, shared HBM GPU-link serialization, pair
independence, event ordering, and invalid configuration. Passing them means the
composition contract works on CPU. It does not validate real SRAM sizes,
bandwidths, UCIe/HBM timing, arbitration policy, energy, or live MQSim behavior.
