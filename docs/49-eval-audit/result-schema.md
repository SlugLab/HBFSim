# Result exchange schema v1 + 2026-09-08 campaign extensions

一行=一个确切 run/condition/replicate 的一个 metric；没有隐含的 xy 配对。CSV required base columns 按用户定义完整保留：

```text
provenance,eq,figure,panel,run_id,git_sha,hardware,model,workload,mode,backend,
operation,profile,delay_us,independent_work_us,overlap_ratio,request_bytes,qd,
rho,tR_us,parallel_units,active_sequences,prefetch_policy,metric,value,unit,replicate
```

扩展列（统一 header，适用值不能缺）：

```text
schema_version,branch,series,warps,occupancy,split,num_experts,top_k,
source_file,source_function,source_line_start,source_line_end
```

`schema_version=1`，git_sha 为40位小写hex。`source_file/function/lines` 指产生/计算该metric的实现/collector，而raw工件路径在run manifest；MOCK 指向generator，不能指向GPU日志。数据row source来源应固定在该run的git snapshot，未提交改动另记patch hash于manifest。审计结论不用数据provenance凑数：INFERRED/STATIC_REPRODUCED存在source ledger中，不填入性能CSV。

整数：request_bytes、qd、parallel_units、active_sequences、warps、E/k、replicate。非适用维度留空，不写0或nan；value必须有限数。失败点不伪造value，原始状态JSON记fail/unsupported/timeout并在报告交代缺格；正式完整heatmap缺格拒绝，实际失败区域应由后续明确的状态面板契约扩展，不能当前填零。旧renderer的rho与coverage/hit/miss/U/E/Gini/Jaccard为[0,1]；signed delta与relative error允许负数；时间/bytes/token/带宽等原始物理值非负。units集中定义于 `validate_results.py:UNITS`。

`series` 是显式比较臂；不能让renderer按model名字猜含义。一个series内部除了横轴及其决定的维度，profile/hardware/workload等必须一致；不同provenance不会混成一条曲线。同run不能混provenance；物理baseline与model输出用独立run_id，通过上层paired_run_id关联。

## Raw and derived artifacts

每次运行保留 `run.json`（完整profile内容/hash、checkpoint/config/tensor hash、command/env/toolchain、硬件身份、input/arrival/prompt hash、timing单位/起点、checksum/覆盖和失败状态）；逐请求 `requests.jsonl`、逐step `steps.jsonl`、SASS mapping。CSV是可重算的summary。

EQ4 的 expert_frequency/reuse 分布存 JSONL 的 layer_id/sequence_id/step_id/expert_id/value；CSV汇总行可额外增加这些维度列（validator允许额外字段，但renderer把变化维度视为独立context，不能无声跨层聚合）。aggregate exporter 对 token 加权和macro平均的选择写manifest，不由renderer决定。byte metric记录 eligible denominator、unique/total、shared/attention/KV是否包含；不同分母禁止同名合并。

## Strict final manifest

正式输入旁边 `manifest.json`：

```json
{
  "schema_version": 1,
  "runs": {
    "device-run-0001": {
      "provenance": "MEASURED",
      "git_sha": "<actual 40-hex SHA>",
      "branch": "eval_base",
      "raw_artifact": "raw/device-run-0001.json",
      "raw_sha256": "<SHA256 of raw artifact>",
      "measurement_scope": "physical_hardware"
    }
  }
}
```

上述是格式示意，不是有效结果。VALIDATED_MODEL额外要求 calibration_id、heldout_validation_id、validity_domain；PROJECTED要求 assumptions，包含N/tR转换、trace-composition、budget与prefetch假设。正式发布前人工核对这些ID指向不可变的通过记录；validator的hash和字段检查不会自动证明硬件真实、标定有效或科学结论正确。

`--strict-no-mock` 逐行检查包括未被当前 `--figure` 绘制的rows，任何MOCK即退出2；缺manifest/raw或hash不符退出2。输出路径任何段名为`final`也强制此门。无mock flag的普通预览仍严格检查schema，发现MOCK自动加水印。final临时stage成功后才写到目的目录；失败不修改既有输出，旧图也不能被当成本次成功。

## JSON compatibility and renderer contract

`--input something.json` 接受完全相同字段的row对象数组（数值可为JSON number，空维度null）。不是各backend原生任意JSON；生产exporter负责转换。一旦CSV/JSON契约满足，mock与正式完全共用plot_lines/plot_heatmap，不使用两套绘图逻辑。同cell的delta_hw_us与delta_sim_us、oracle_residual_us与residual_us、service_gbs与decode_norm在渲染前精确配对。

Raw correctness、statistical claim gates、source-code capabilities属于 [claim gates](claim-gates.md)，不能把“schema valid”写成“experiment passes”。


## Current campaign extension contract (2026-09-08)

This section governs new EQ1–EQ4 artifacts. Existing v1 rows remain readable. New fields are additive metadata or extra CSV columns; no runtime ABI or existing validator semantics changed in this task. Unsupported figures/metrics remain BLOCKED_RENDERER until a reviewed schema/exporter/renderer patch implements them. A document contract is not automatic enforcement.

Every new run records `evidence_kind` and `validation_scope`, independently from legacy `provenance=MEASURED|VALIDATED_MODEL|PROJECTED|MOCK`:

| evidence_kind | Scope / legacy mapping restriction |
|---|---|
| physical_measurement | Direct identified device observables only; MEASURED |
| controlled_gpu_semantic_experiment | GPU checksum/readiness/timing under explicit supported scope; modeled HBF delay remains PROJECTED |
| numerical_cross_validation | ROM vs independent solver or simulator comparison; never physical silicon validation |
| controlled_test_only_model | Stress controller/energy/thermal mechanism; PROJECTED |
| literature_constrained_projection | Source-constrained hypothetical HBF; PROJECTED |
| live_workload_emulation | Actual live workload, explicitly separate physical GPU from modeled HBF |
| trace_replay / trace_composed | Explicit arrival/dependency/composition assumptions; never live serving |
| mock | Artificial renderer/test data only; MOCK |

New manifests bind source SHA+dirty patch SHA, build/environment/model/config/profile/ROM/trace/renderer SHA256, GPU UUID and storage serial/BDF/filesystem UUID, logical and resolved paths and migration mapping, backing device/cache state, calibration/held-out split, replicate/seed, coverage denominator and unsupported bytes, field definitions, failure reason and raw relative paths. Include `scientific_validation_passed` only with corresponding independently checked gate receipts. Changing a path or copying a PASS artifact does not renew its measurement.

EQ1 raw/derived fields: `L0_us,L1_us,extra_delay_us,W_observed_us,issue_block_us,consume_residual_us,total_exposed_stall_us,delta_stall_us,native_wall_us,instrumented_wall_us`; clock=`globaltimer|cuda_event|host_wall|model_event`, time_scale, SASS/build mappings and diagnostic/timing-run separation. Native and modeled completion origin must be explicit; target faster than native is outside simple positive-injection scope.

EQ2 per-window ledger: `model_time_start_ns,model_time_end_ns,window_ns,offered_useful_bytes,admitted_bytes,completed_useful_bytes,physical_read_bytes,physical_write_bytes,refresh_bytes,read_retry_bytes,queue_begin,queue_end,pending_bytes,command_counts_by_die_plane,energy_J,power_W,hotspot_C,layer_min_C,layer_max_C,policy_state,throttle_duration_ns,served_Bps`. Bytes completed within the half-open window define service; planned/offered cap does not. `off` has temperature absent, not zero; shadow/active share physics-input and demand hashes; full arm-specific package profile hashes differ and are retained. Model timestamps and execution wall seconds never share a field. HBM command-to-ROM aggregation preserves energy and records lost timing correlation; it is not a real transient workload.

EQ3 raw capacity fields: `C_HBM_bytes,C_HBF_bytes,r,C_fixed_bytes,C_KV_bytes,C_HBF_cache_bytes,C_staging_bytes,C_other_bytes,C_pool_bytes,kappa,beta,reservation_kind,rho_requested,rho_achieved,eligible_bytes,capacity_path,media_scaling_kind`. Do not clip `rho_requested>1`: retain it in manifest/additive metric and label full-fit/excess budget. Legacy renderer base `rho` can only carry validated `rho_achieved<=1`; it cannot silently stand in for requested allocation. Feasibility includes alignment, whole tile/stage/pinning, real KV minimum and topology mapping. Failure cells record `OOM|INFEASIBLE|UNSUPPORTED|BLOCKED`, never zero performance.

EQ4: actual scheduler active IDs/count per layer/step, E/k, expert union/frequency/entropy/Gini/Jaccard/reuse distance, demand and speculation request IDs, useful/late/useless bytes, cache pin/source lifetime, extra media traffic and energy. Declare serial miss, concurrent demand, or causal one-layer lookahead separately; replacement policy is a separate field. Dense matching records capacity/active-compute/precision differences.

New formal rendering must reject MOCK, absent pairing/units/hashes, incompatible context/profile/time domains, unsupported new metric types and missing gate scope. No CI with one repeat; no contour without measured/model crossing; no interpolation over failed cells. Current renderer already enforces its legacy schema/hash/mock/pairing subset; thermal time-series, unbounded requested rho and new allocation panels need the separate follow-up in minimal-followups.md.

Thermal pairing uses `physics_inputs_sha256` over canonical geometry/material/cooling/energy/policy/ROM/media inputs and `paired_demand_sha256`. Keep each arm's full `package_profile_sha256` separately. The only excluded package fields for the existing stress pair are `name` and `stage`, whose exact differences are recorded; no threshold, energy or geometry difference may be excluded. A future true-off switch is likewise an explicit treatment field, not a hidden physical difference.
