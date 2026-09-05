# Result exchange schema v1

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

整数：request_bytes、qd、parallel_units、active_sequences、warps、E/k、replicate。非适用维度留空，不写0或nan；value必须有限数。失败点不伪造value，原始状态JSON记fail/unsupported/timeout并在报告交代缺格；正式完整heatmap缺格拒绝，实际失败区域应由后续明确的状态面板契约扩展，不能当前填零。rho与coverage/hit/miss/U/E/Gini/Jaccard为[0,1]；signed delta与relative error允许负数；时间/bytes/token/带宽等原始物理值非负。units集中定义于 `validate_results.py:UNITS`。

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
