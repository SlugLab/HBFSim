# D3 最小串行执行说明

状态：`PREPARED_NOT_EXECUTED`。本说明只冻结已获授权的最小执行顺序；尚未编译
D3 runner、运行固定测试、求稳态或生成/求解 `DOMAIN_V2_IN_RANGE`。盲测轨迹不读取、
不导出、不求解。

## 证据与复用边界

- `USER_CONFIRMED`：统一幅值候选 `{1,.75,.5,.25}`、全 17 组上限、380 K
  筛选上限、train/development 原生 reference 与 RC 完整轨迹，以及 D1 的串行 CPU、
  GPU0/cloud0 和逐点合理资源约束。
- `DOC_DERIVED`：现存
  `/root/hbfsim-exp/eq3_thermal/generated/campaign-RC2MM-{train,development}` 的
  `model.txt` 与 `rc_grid.json` 分别逐字节相同（SHA-256
  `ecc7d24a...c62559`、`b8e611d0...e5a23`），可作为一次 2 mm steady envelope 的
  匹配 model/grid。steady 解不使用其旧 workload events，也不依赖 `dt`。
- 旧 RC2MM 输入的 generation receipt 是 5 ms，且不是新的统一 alpha 输入；旧
  R06 development 原生结果已越过 400 K。因此它们只能复用静态 model/grid 与失败
  证据，不能冒充 D3 新轨迹。
- D3 基础闭环重新导出 reference=2 mm、RC=2 mm、step=20 ms、sample=100 ms。
  这只支持 `REFERENCE_UNQUALIFIED / DISCRETE_EQUIVALENCE`；不授予 0.25 K reference
  资格或 `MODEL_FREEZE`。1 mm 只在 2 mm 给出新细化问题时作为后继。

## 串行命令顺序

所有命令从
`/root/hbfsim-exp/eq3_thermal/integration/main-20260920` 执行，由
`eq3_thermal/plans/decision-execution-v2/run_check.py` 或既有完整 thermal launcher
保存元信息与限额。先等待当前 R03 退出；实际 point ID 不得复用已有目录。

1. 建立新的 build 输出目录后，用既有 fresh 静态库编译，不覆盖历史 binary：

```text
/usr/bin/g++ -std=c++20 -O3 -DNDEBUG -Wall -Wextra -Wpedantic \
  -Iinclude -I/usr/include/eigen3 tools/eq3_campaign_rc_runner.cpp \
  /root/hbfsim-exp/eq3_thermal/plans/minimal-repair-v1/build/libhbfsim_eq3_thermal.a \
  -o /root/hbfsim-exp/eq3_thermal/plans/decision-execution-v2/build/rc_runner_d3
```

2. 在 600 s fixed-test 点中运行：

```text
env PYTHONPATH=tools \
  EQ3_CAMPAIGN_RC_RUNNER=/root/hbfsim-exp/eq3_thermal/plans/decision-execution-v2/build/rc_runner_d3 \
  EQ3_LAYERED_RC_RUNNER=/root/hbfsim-exp/eq3_thermal/build/converter-core-relocated/eq3_layered_rc_runner \
  EQ3_GENERATED_ROOT=/root/hbfsim-exp/eq3_thermal/generated \
  python3 -B -m unittest tools.test_eq3_all_source_cap \
    tools.test_eq3_domain_v2_input tools.test_eq3_campaign_rc_runner -v
```

3. 用公共 source ledger 的 17 个 cap 与现存匹配 2 mm grid 生成一次 cap events。
输出放入新的 `d3-cap-2mm` point；此步不读任何温度轨迹：

```text
env PYTHONPATH=tools python3 -B tools/eq3_all_source_cap.py \
  --profile configs/eq3_thermal/research/candidate_profile.json \
  --power configs/eq3_thermal/research/calibration_power.json \
  --rc-grid /root/hbfsim-exp/eq3_thermal/generated/campaign-RC2MM-development/rc_grid.json \
  --events-output <d3-cap-2mm>/cap_events.txt \
  --receipt-output <d3-cap-2mm>/cap_receipt.json
```

启动 steady 前核对 receipt：17 组均存在，group/component/node 守恒，总 cap 正好
840 W，`blind_trajectory_read=false`；否则停止 D3 域分支。

4. 用同一 2 mm model/cap events 做一次 steady solve。三项 SHA-256 必须在 point
manifest 中先计算并原样传入，stdout 即唯一 steady receipt：

```text
<rc_runner_d3> \
  --model /root/hbfsim-exp/eq3_thermal/generated/campaign-RC2MM-development/model.txt \
  --events <d3-cap-2mm>/cap_events.txt \
  --step-s 0.5 --slot-s 0.5 --end-s 0.5 --sample-s 0.5 \
  --min-k 300 --max-k 400 --envelope-limit-k 380 \
  --model-sha256 <recorded-model-sha256> \
  --events-sha256 <recorded-cap-events-sha256> \
  --runner-source-sha256 <recorded-runner-source-sha256> \
  --domain-version EQ3-DOMAIN-V2-IN-RANGE-v1 --steady-envelope
```

这里只做 `L` 的一次分解、两个 RHS、四个 alpha；0.5 s 参数只满足既有事件调度
校验，不执行瞬态 workload。结果必须仍为 `reference_qualified=false`。若状态是
`DOMAIN_REDESIGN_REQUIRED` 或 residual/正网络检查失败，保留 receipt 并停止域内
轨迹分支，不另选更小 alpha。

5. receipt 为 `PREDICTED_ENVELOPE` 时，只派生 train/development：

```text
python3 -B tools/eq3_domain_v2_input.py \
  --power configs/eq3_thermal/research/calibration_power.json \
  --steady-receipt <d3-steady-2mm>/stdout.log \
  --trace train --trace development \
  --output <domain-v2>/calibration_power.json \
  --receipt-output <domain-v2>/input_receipt.json

python3 -B tools/eq3_layered_export.py \
  --profile configs/eq3_thermal/research/candidate_profile.json \
  --power <domain-v2>/calibration_power.json --trace train \
  --mesh-um 2000 --rc-mesh-um 2000 --step-s 0.02 --sample-s 0.1 \
  --output <domain-v2>/train

python3 -B tools/eq3_layered_export.py \
  --profile configs/eq3_thermal/research/candidate_profile.json \
  --power <domain-v2>/calibration_power.json --trace development \
  --mesh-um 2000 --rc-mesh-um 2000 --step-s 0.02 --sample-s 0.1 \
  --output <domain-v2>/development
```

核对两个新 normalized 输入只相对原 trace 统一缩放可变源，持续时间、窗口、源
映射与静态源不变；receipt 必须明确 `blind_trajectory_read=false`。

6. 为这两个新 normalized 输入创建独立的 domain-v2 scope/authorization 派生记录，
绑定 steady receipt、冻结 alpha、scaled `calibration_power.json`、各自 normalized hash
和新能量。不要修改正在使用的 `scope-v1.json`、`authorization-v1.json` 或 R03
manifest。既有 campaign gate 对 full `reference`/`rc` 可直接核对 scope 中的新
normalized hash/能量；本分支不需要泛化 gate，也不安排 prefix pilot。

7. 串行执行四点：train native reference、train RC、development native reference、
development RC。native 使用既有 `eq3_campaign_stream.py` 加 3D-ICE launcher：2 mm
网格为 32x32x63，train/development 分别 5000/3200 个 solver-step frame；RC 使用：

```text
<rc_runner_d3> --model <trace>/model.txt --events <trace>/events.txt \
  --step-s 0.02 --slot-s 0.5 --end-s <100-or-64> --sample-s 0.1 \
  --min-k 300 --max-k 400 \
  --model-sha256 <recorded> --events-sha256 <recorded> \
  --runner-source-sha256 <recorded> \
  --domain-version EQ3-DOMAIN-V2-IN-RANGE-v1 --run
```

每点从自己的空 cwd 启动，使 `rc_energy_receipt.json` 或失败诊断不会覆盖别点。
native 必须由完整 safety launcher 绑定 `package.stk`、63 层 floorplan、stream backend、
codec、raw 输出和 point manifest；不能用裸 3D-ICE 命令代替。P2 完整 reference/RC
可用 3600 s，单进程 12 GiB、任务 16 GiB、CPU/OMP/BLAS=1、GPU0；启动前按当前磁盘
重新给 point 输入/raw/临时/派生估算并保留至少 10 GiB，不机械继承旧 R06 的 600 s。

四点都保留每步 300--400 K 域检查、唯一 receipt、完整能量和失败证据。任一点越域、
输出不完整或 launcher gate 不通过就保留失败，不 clamp、不改 alpha、不解封 blind。
