# HBM-Power offline adapter

This directory contains only an adapter and an output contract. It does not contain HBM-Power or DRAMPower source code.

The supported handoff is:

```text
external CMU-SAFARI/HBM-Power runner
  → average power and modeled duration per supplied stack trace
  → time_ns,hbm_stack_0_w,... CSV
  → manifest with tool/input/output hashes
  → HBFSim Phase-II trace providers
```

The adapter invokes one caller-provided trace per modeled HBM stack. It emits a zero-power terminal sample so HBFSim's hold interpolation does not extend a finite experiment forever.

`--power-scale` is deliberately explicit because the released artifact uses different runner scopes in different experiments:

- Figure 14's calibrated HBM2 configuration reports one modeled stack, so the scale is `1`.
- Figure 16's HBM3E runner reports one pseudo-channel and the paper multiplies by 192 pseudo-channels for a six-stack H200. A homogeneous per-stack projection therefore uses `32` pseudo-channels per stack. The figure also adds a separately modeled idle baseline; pass its NOP trace with `--baseline-trace`.

These outputs are literature-bounded model projections. They are not measurements of the GDDR7 RTX PRO 6000, not HBF specification values, and not thermal calibration data.

Example HBM2 invocation:

```bash
python3 hbm_power_adapter.py \
  --runner /external/HBM-Power/sources/drampower/build/bin/HBM2_runner \
  --organization /external/HBM-Power/sources/figure14/configs/HBM2_organization.json \
  --timing /external/HBM-Power/sources/figure14/configs/HBM2_1.2Gbps_timing_BL4.json \
  --power /external/HBM-Power/sources/figure14/configs/IDD_ours_allzeros.json \
  --stack-trace /external/HBM-Power/traces/llm_batch_sweep/bs1_ctx1024.csv \
  --technology hbm2 \
  --output-csv hbm2-power.csv \
  --manifest hbm2-power.manifest.json
```

## Long-timescale mapping

`energy_conserving_mapper.py` maps a finite adapter trace onto an explicit
macro schedule.  It does not concatenate or loop the short trace.  The mapper
integrates the source once, separates manifest-declared baseline and active
power, and applies this equation independently to every stack:

```text
E_out = P_baseline * resident_equivalent_time
      + P_active * active_equivalent_time
```

The schedule is a contiguous CSV with columns:

```text
start_ns,end_ns,phase,resident_fraction,activity_fraction
```

The activity fraction must not exceed the resident fraction.  The output
manifest records source, schedule, and output hashes; source and target energy;
and the numerical conservation error.  The mapped trace is a macro power and
energy envelope only.  It does not preserve command-level temporal correlation,
does not turn a mismatched HBM workload into an application measurement, and
does not represent the local GDDR7 GPU.
