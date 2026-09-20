# Model-size offered-byte workload

`model_workloads.py` generates demand for the rate/fluid/thermal path. It does
not run MQSim, read model tensors, cap demand to service capacity, or estimate
tokens per second.

The catalog freezes official metadata payload sizes: Qwen2.5-7B-Instruct is
15,231,233,024 B, Qwen2.5-72B-Instruct is 145,412,407,296 B, and pinned
Qwen3-235B-A22B is 470,187,269,120 B. The latter is a synthetic complete stored
weight scan. Its model card says 235B total and 22B active; it must not be read
as all 235B weights fetched for each MoE token. Token/s remains `UNKNOWN`.

The validated input follows `model_workload_config.schema.json`. A full pilot
configuration is:

```json
{
  "schema_version": "eq3-rate-model-workload-config-v1",
  "model_id": "Qwen/Qwen2.5-72B-Instruct",
  "full_scans_per_s": 16,
  "pattern": "continuous",
  "stack_count": 4,
  "channels_per_stack": 16,
  "step_ns": 20000000,
  "active_ns": 8000000000,
  "recovery_ns": 4000000000
}
```

The full-duration form uses 20 s active plus 10 s recovery. The bounded pilot
uses 8 s plus 4 s. For the equal-mean burst form, set `pattern` to
`burst_equal_mean`, `burst_period_ns` to 200,000,000, and `burst_on_ns` to
100,000,000. Its on windows use twice the continuous demand and its off windows
use zero, preserving the same active-period mean.

Generate a canonical JSON artifact with:

```sh
python3 -B experiments/eq3_rate_thermal/model_workloads.py \
  --config CONFIG.json --output WORKLOAD.json
```

The output has contiguous 20 ms half-open windows. Every window contains all
4 or 8 `hbfN` stacks and channels `"0"` through `"15"` under
`stack_channel_offered_bytes`; recovery windows explicitly contain zero.
Bytes are nonnegative integers. A global exact fractional carry plus a rotating
uniform remainder preserves the requested complete-scan byte total while
keeping cumulative channel totals within one byte.

The defining demand is

```text
mean offered B/s = tensor_payload_bytes * full_scans_per_s
```

The generator deliberately applies no OCP/channel service cap. Offered demand
may exceed capacity and must enter the downstream fluid backlog. Any downstream
consumer that clips these bytes without accounting for the remainder violates
this interface.
