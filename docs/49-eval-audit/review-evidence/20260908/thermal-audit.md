# EQ2 thermal audit and executable boundary — 2026-09-08

> 分享副本：审计事实、数值和原执行路径保持原记录；仅调整导航。大模型、完整 raw、部分本机清单与可执行二进制未随仓库发布，范围见 [审阅包说明](README.md)。

本报告只读核查当前实现和冻结历史工件，并执行小型 CPU 检查。没有启动 GPU、热负载、完整矩阵、重编译或合并；原始结果保持原字节。路径采用迁移兼容逻辑前缀 `/root/hbfsim-exp`，物理路径以父任务迁移 manifest 为准。本文是 canonical 文档的审计依据，不另立规范源。

## 1. 决策与源码边界

- 当前 evaluation tree：`eval-base-integration`，`eval/eq1-eq4-implementation@254d65a66279fbaffc5c185d04fbe41dc8dbba44`。它没有 package thermal 或 reliability runtime 开关；`docs/eval/thermal_coverage.md`、`thermal_integration_assessment.md` 和 `EVAL_BASE_INTEGRATION.md` 的 DEFER 与当前代码相符。当前树不能直接跑新 EQ2 live thermal curve。
- 独立 prototype 入口：`prototype-revalidation/sources/diagnostics/cell-c-phase2-adaptation-001@49f9b2daa60271b39085de8cbc98180b6aead297`，当前只读检查 HEAD/已跟踪状态干净。其 package/ROM + MQSim device-only controller 反馈可作为窄机制证据；不能视作与当前 admission/async/runtime 合并验证。
- `phase3/source/hbfsim-vllm015@fd11c3d98cde74977be7ec504c64429369b6fd3a` 现场有 retention model 和 HBM 宏观能量映射，Git 包含这两文件且状态干净。该独立快照不能按普通后继分支直接合并；它的新增能力已核查，不能继续写成“完全没有”，也不能当当前树已集成。
- 取消旧 EQ1-B 作为独立生产 HBF 物理真实性论证。MQSim 调度/映射复用有工程和验证价值；现有 layer mapping、observer 和 ROM 是具体适配，但未证实 shared TSV/base-die 数据链路仲裁，也无生产 HBF 温度校准。保留数值/守恒/极限退化和文献约束在方法/附录，必要时为新 EQ2 提供参数依据。

## 2. 本轮验证输出和边界

| 工件 | 实际结果 | 不能支持 |
|---|---|---|
| `thermal-check.py`, `.json`, `.log` | 13 个指定首层结果计算 SHA256；45 个明确引用路径/hash 匹配；0 mismatch、0 missing；哈希读取 95,774,517 B，约 0.44 s | 两个大型 bpftime 共享库因 128 MiB 单次读取上限标 DEFERRED；没有重哈希全部模型权重或每个历史二进制 |
| `thermal-golden-check.json` | 冻结 8/16Hi 各 5 held-out dataset、ROM、fit、decomposition hash 与 summary/validation 全吻合；20 检查，读取 23,587,477 B | 未重跑 3D-ICE；数值 golden 不是硅片校准 |
| `thermal-timeline-test.log` | 既有 `test_phase2_timeline_analysis.py` 的 240 行 synthetic fixture 检查 exit 0，0.27 s，19,488 KiB RSS | 只验证分析器处理 synthetic 样本；不是新闭环测量 |
| `thermal-rom-eigencheck.json` | 单线程 NumPy 2.2.6 eigenanalysis，两个 224-state ROM 稳定，0.107 s | 数值慢模态不是测量的器件热常数 |
| `thermal-phase3-check.json` | 13 个选定文件核查无不匹配；两组 24h HBM envelope CSV 独立积分与目标能量相等 | 守恒不等于保留真实 transient/command 时间结构 |

`thermal-check.json` 中每个 result/hash、引用 source/profile/ROM/raw artifact 都保留绝对路径和核对结果。报告中的历史 PASS 仍属于 2026-08-30/31 的原作用域，不能表述为本轮 GPU 或物理测量 PASS。

## 3. G1 指定历史工件再利用表

“直接复用”限于表中明确的旧证据作用域；新主图仍须满足新三臂、单位、有用 completion 和物理参数门禁。

| 指定路径（均在 `prototype-revalidation/results/`） | 现场证据和再利用分类 | 新 claim 的缺口/禁止用途 |
|---|---|---|
| `final-requirement-audit-002/result.json` | 直接复用：旧两项任务 completion audit，science_status=PARTIAL；报告 manifest 路径/hash 可核对 | `COMPLETE_WITH_DOCUMENTED_LIMITATIONS` 不是新 EQ2 完成，更不是物理 thermal validation |
| `final-campaign-audit-001/result.json` | 直接复用：历史汇总与证据索引、失败/限制保留 | PASS 字符串不能替代下面的原始工件链 |
| `vllm-2x2-001/result.json` | 直接复用：A/C prototype vLLM 0.15.1 PASS、B/D current-stack FAIL 的兼容性诊断 | 不能说明 vLLM 0.26 永不可移植；当前栈适配独立任务，不改 thermal core 掩盖平台问题 |
| `cell-a-023/core-provenance.json` | 仅机制/集成证据：`dc4ef72c...` 历史小适配的输出正确性、环境/工具链绑定 | 历史 dirty vLLM 原字节不可重建；当前模型字节有记录但历史字节一致性未证明；非 per-access GPU fidelity |
| `cell-c-002/core-provenance.json` | 仅机制/集成证据：`49f9b2d...` off/read_only/shadow/active stage 正确性与 tokens 一致 | 没有证明当前 254d65 的 thermal-off parity；host-launch fallback 无法支持严格 per-access LLM 热性能 |
| `app0-002/app0-provenance.json` | 仅机制：所有 stage checksum 一致；`host_launch_mqsim` 每 modeled kernel 一请求，8 MiB logical、4096 iterations、seed 21001 | 不能与历史 6144 per-access 请求 APP0 相称；time_scale=100，不能把 wall time 当目标 HBF 时间 |
| `golden-0-001/result.json` | 直接复用：冻结 8/16Hi 的 ROM 对 3D-ICE 数值 held-out 交叉验证，各 5 traces；原 dataset/model hash 现场吻合 | 只覆盖该 geometry/material/cooling/power/ROM；不是生产 HBF junction 温度校准；几何修改需新 held-out |
| `cl-0-002/result.json` | 仅机制：C-class/test-only 8Hi 4 s controller 闭环；400 原始 bins，530 ms gate、560 ms recovery；350/382 完成、32 排队 | 91.7504 MB/s 是该 4s 窗口 useful completion 均值，不是稳定服务边界；缺 off/shadow 正式配对与 nominal 参数 |
| `bw-0-001/result.json` | 需补跑新 claim；原 16Hi 确认 replay 可作窄机制/数值回归：8s、573/573 完成、无队列 | 整窗 useful=75.104256 MB/s；旧尾窗 physical=75.105616609 MB/s，两数统计窗口与定义不同；不能作产品带宽或 universally sustainable |
| `hbm-0-001/` | 直接复用失败证据；真实 transient claim 不能使用：50,021 ns trace 对 10,000,000 ns ROM step，约 199.916× | 缺真实 workload-time mapping；Phase3 新宏观 envelope 不能解除 transient BLOCKED。constant-power/scheduled-envelope 只能 PROJECTED |
| `workload-matrix-001/` | 仅机制/探索：W0 weight-like、W1 expert-like、W2/W3 KV proxy，单独 provenance hash 匹配 | 不是 real MoE routing/live serving/实际 KV residency；不能直接成为新 EQ3/4 曲线 |
| `lit-1-001/` | 不能用于新物理 claim；保留 PARTIAL/QUALITATIVELY_MIXED 与文献受限比较 | 不能声称公开论文温度曲线被独立复现，command energy 不可辨识；写密集并非普遍更热/更早 throttle |
| `lit-2-001/` | 仅机制/负结果：8/16Hi 下各访问混合相同 sustainable boundary，trend unresolved | 不能改写为“write 必然更差”；缺有证据的 per-command energy/访问混合与外部 held-out |

补充已核查更新：Phase3 T6 `OFFLINE_TEMPERATURE_DRIVEN_PROJECTION` 有 Arrhenius age、refresh demand/bytes/energy 及 statistical latency；不存在由该 CLI 向 MQSim 注入刷新并再加热的实测链。T7 的两组 HBM2/HBM3E 24h 宏观 envelope 单次积分源数据再结合四段 activity/residency schedule，1/6/60 requests/min 是 Class-C 假设，batch/context intensity 不匹配且不保留 command correlation。二者可在新方案复用为 PROJECTED 附录，不能升级 live claim。

## 4. 可追审的代码 ledger

以下 P 指 prototype source `.../cell-c-phase2-adaptation-001@49f9b2d...`；C 指当前 `eval-base-integration@254d65a...`；F 指 Phase3 `.../hbfsim-vllm015@fd11c3d...`。

| 层/行为 | 文件/函数/精确行段 | 已实现与缺口 |
|---|---|---|
| 当前 NAND mapping/admission/completion | C `src/mqsim_adapter/mqsim_online.cpp:112–157,272–335` | profile→channels/dies/planes/timing；queue_depth device admission；completion 上 aggregate bandwidth cursor；该限速不能当 shared-stack arbitration 的物理实现 |
| 当前 request 生命周期 | C `src/host_service/request_dispatcher.cpp:283–332,406–463` | 按 completion 聚合并发布；无 package controller 集成 |
| 冻结 prototype observed activity | P `cmake/MQSimPatchedBuild.cmake:3–7`, `patches/mqsim/0003-physical-media-observer.patch`; 实际 build `_deps/mqsim-hbf-src/src/nvm_chip/flash_memory/Flash_Chip.cpp:123–178` | MQSim command issue 时提供预计 end-time、每 plane address 发一 media observation；不是读取原 submodule 就能看到 patch。多 plane 的 `command_j` 当前按 plane event 计费，物理参数必须说明其是 per-plane activity 还是整条命令 |
| 物理坐标→runtime | P `src/mqsim_adapter/mqsim_online.cpp:258–270,350–385`; `thermal_load_runner.cpp:196–209` | channel/chip/die/plane/block/page/bytes 坐标传播；observer 错误失败，不 silently bypass；热 node 映射不是数据 TSV resource arbitration |
| activity→energy/power | P `src/package_thermal/power_clock.cpp:262–285,325–375,378–411,450–466` | `E=command_j+joules_per_byte*bytes`，按跨 bin duration 分摊；idle base 加入；NAND energy 守恒检查；已有 emitted bin 不允许晚到活动 |
| GPU/HBM accounting | P `src/package_thermal/runtime.cpp:191–219` | legacy board total 单独扣 HBM；新 schema GPU compute + explicit HBM；CL/BW synthetic=GPU 30W + HBM 5W，不能混成测量 board telemetry |
| ROM/policy | P `src/package_thermal/runtime.cpp:137–154,180–240`; `model_policy.cpp:666–767` | model time 单调校验，`model_->step(power)`→hotspot→hysteresis/debounce；shadow保留raw policy但effective正常；active轻度scale/重度关admission |
| controller→future activity | P `experiments/package_thermal/phase2/thermal_load_runner.cpp:263–345` | token admission rate受 service_scale；gate关时不submit，已有MQSim可完成；每bin仍 advance，可冷却恢复。CPU replay 确有 controller→MQSim activity→power 反馈 |
| 真 off 缺口 | P `thermal_load_runner.cpp:173–194,333–363`; `model_policy.cpp:718–726` | runner 即使 stage=off 仍构造ROM/采样/advance，只令 policy正常；不能算新定义“热子系统关闭”的off。需要窄 runner bypass补丁 |
| 历史 daemon 真 off | P `src/host_service/main.cpp:86–100,281–329,373–429` | `--thermal off` 跳过依赖/runtime构造；`--thermal package_rc --thermal-stage shadow|active ...` 可用。但应用入口含平台 fallback；不能借 daemon off 拼接不同 device-only path 做配对 |
| useful vs physical metric | P `thermal_load_runner.cpp:313–325,365–383`; `analyze_phase2_timeline.py:254–277` | runner summary 从 completed request 计算 served_bytes；旧stationarity把MQSim read+program bytes称served_physical。新T1必须用完成的有用bytes；refresh/write amplification后两者不等 |
| model time边界 | P `thermal_load_runner.cpp:313–345`; analyzer `:198–205` | run_next_completion可能越过bin target；runtime按max(target,engine time)推进，多bin可能共享service snapshot。新严格time-series需per-completion timestamp/bin accounting；不能臆称当前每bin精确所有completion |
| refresh/retention | F `src/retention_refresh.cpp:93–177`; `experiments/retention_refresh/retention_refresh_cli.cpp` | 已有离线age/bytes/energy/projection；没有与P controller runner连接，更无C组合队列证据。不能把逻辑counter写成实际刷新请求 |
| HBM宏观映射 | F `experiments/external/hbm-power/energy_conserving_mapper.py:153–157,173–238,248–269` | 有严格source/schema/积分/能量守恒；manifest明示macro envelope，不能恢复原trace时序 |

## 5. 单位、守恒和 ROM 时长

CL0 read/program `command_j=0.01 J`，`joules_per_byte=0`，base idle=0.5W；第一因果窗口 6400 plane events/s × 0.01J + 0.5W = 64.5W。关 gate 后活动=0，power=0.5W。1MiB请求有64个16KiB media activity，不能将0.01J误写成0.01uJ。100 MB/s=100,000,000 B/s，1 MiB=1,048,576B，200 MB/s 是 runner token peak，仅Class-C controlled参数，非测量 HBF接口带宽。

BW0全窗：573 × 1,048,576B / 8s=75,104,256B/s；尾窗 `stationarity.json` 2.89s 的 media bytes统计=75,105,616.609B/s。raw CSV整窗physical/useful相等仅对此 workload及无refresh/放大数据成立；不能推广。

已核查的模型身份：

| 模型 | runtime文件SHA256 | dt | 重算τ / 5τ |
|---|---|---:|---:|
| 8Hi | `cc935171db2725b351cd7adc057d46c34e7472d6ba48656f2426839c14792524` | 10ms | 0.283248410s / 1.416242052s |
| 16Hi | `7634f3fa010a22262a4330b6a438a8222fd8005a06c7b5354bb09b64a3759d65` | 10ms | 1.021309821s / 5.106549104s |

数值重算与旧 validated_override 一致；4s/8s对此ROM可覆盖5τ加窗口，旧BW尾窗温度斜率0.0335788°C/s、queue slope0、relative served error0.00140822，仅通过当时Class-C `max_temp_slope=0.2°C/s`。新几何、power/policy或约12s慢模态profile不可沿用旧duration/override；须新算τ、≥5τ加预先冻结统计窗，检测队列漂移和completion守恒。τ不稳定/缺少可信来源则 BLOCKED，不手写短τ促成PASS。

## 6. 新 EQ2 三臂可执行映射

已存在的独立CPU runner：

```text
/root/hbfsim-exp/prototype-revalidation/builds/cell-c-phase2-adaptation-001/phase2_thermal_load_runner
  --device-profile FILE --package-profile FILE --model FILE --output DIR
  --duration-ns N --offered-byte-rate N --peak-byte-rate N
  --request-bytes N --queue-depth N --seed N
  --arrival-mode periodic|poisson|closed_loop
  --workload read|read_heavy|mixed|write_heavy|write
  --pattern sequential|random
```

stage由package JSON给出，没有`--thermal-stage` runner flag。CPU runner自身不接触GPU；进入正式性能任务仍需父任务空间guard/无存储压测与CPU重拷贝互扰约束。

| cell族 | 入口/输入现状 | 当前状态/依赖 |
|---|---|---|
| test-only 8Hi shadow/active复现pilot | 上述runner；`phase2-runtime-inputs-20260827T095943Z/g1-8hi-cl2-{shadow,active}/` 中device、package、ROM真实存在；device/ROM hash相同，package仅name/stage不同 | READY_NOT_RUN；只能controller机制检查，不进入nominal主图。先0.1s modeled/10bins/单seed pilot，再按预算4s；本轮未启动 |
| test-only 16Hi active确认回放 | 上述runner；`g1-16hi-bw-active/{device-16hi.json,package-16hi-active.json,rom-16hi-runtime.json}` | READY_NOT_RUN；原8s结果已足够旧证据，不必无变化重跑 |
| device-only true off | 现runner名义off仍做热计算 | BLOCKED_EQ2_TRUE_OFF_RUNNER；最小补丁P-T1 |
| nominal off/shadow/active，8Hi/16Hi | 新冻结物理输入应保存parameter-source与不确定区间；没有可核验输入时没有真实command可执行 | BLOCKED_EQ2_PHYSICAL_INPUTS + P-T1 + P-T2；完整三臂paired源码环境相同后才能画主图 |
| EQ2 live LLM throughput/tokens/s | C当前tree无thermal；P daemon平台/host_launch granularity已被限定 | BLOCKED_EQ1_APP_TIMING + BLOCKED_THERMAL_COMBINATION_PARITY；不以device replay换名为live |
| HBM transient反事实 | 原50.021us trace/10msROM不匹配 | BLOCKED_REAL_TRANSIENT_TIME_MAPPING；Phase3 T7仅可作为独立PROJECTED宏观能量敏感性 |
| refresh causal反馈 | F已有离线需求；C/P runner无物理刷新闭环 | BLOCKED_REFRESH_DISPATCH_COMPLETION_ENERGY；P-T3独立补丁 |

8Hi active参数：4,000,000,000ns，100,000,000B/s offered，200,000,000B/s peak，1,048,576B/request，queue_depth128，seed21001，periodic/read/random；shadow配对只切package stage。active package SHA `af55f556048e6220a1e648d200a3ce92f5cb8eaab5b87f17c1fc3680dd90f9ee`，shadow `c6a143b6be10de240804314d6fe4fd1a87464a9c056318bab167a47a69cdeb52`；device `6cb05ce7623bc9e8620c2f7639e2272201352d94ff79b6644369c20c2cc37335`。没有创建新profile，没有在历史目录运行finalizer（该脚本会重写分析输出）。

## 7. 正式实验协议、主图与门禁

先冻结来源支持的geometry/material/cooling/energy/policy区间。分别选未触限负对照、接近边界、可能触限持续负载；不以降低nominal阈值制造曲线差。8Hi/16Hi各三种条件×off/shadow/active=18个首pilot cells，每cell一个seed；估算后确认集每cell至少3独立seed/replicate，配对arm顺序预先随机/平衡，共54cells上限，不扩成所有参数笛卡尔积。若deterministic重复完全一致，报告确定性与参数敏感性，不把零方差当物理CI。

配对冻结source/build/env/profile/ROM/geometry、初始状态、seed、原始需求与capacity；off–shadow比较额外工具开销，shadow–active比较反馈因果。periodic/poisson固定arrival replay和closed-loop arrivals分开：后者completion决定新arrival，本就允许反馈改变后续到达。actual arrival不强制相同。

Pilot上界初始建议：单CPU进程、120s wall timeout、2GiB RSS、100MiB输出、≤0.1s modeled/10 bins；这是尚未运行的预算，不是测量耗时。失败则保留输出，记录OOM/unsupported/timeout，停止该cell扩张。通过后以实测wall/model-time、RSS、bytes/bin外推≥5τ+统计窗口，预留2×pilot增长误差与父任务磁盘低水位；总campaign申请≤30min CPU wall、≤2GiB artifacts只是候选上限，超限需重排任务。不准无界trace或秒级轮询。模型输出受bin/资源界限限制时不丢样本冒充完成。

T1：模型时间秒，100ms固定窗口的已完成useful HBF bytes/s；保留10ms原始服务序列。旧media activity吞吐另列physical_bytes/s及读放大；tokens/s仅通过live GPU计时门禁后使用。正文off/active，shadow作为因果/开销对照。没有true off配对不做正式renderer输出。

T2：同一模型时间轴的MODELED/PROJECTED HBF hotspot、层max/min；注明GPU/HBM输入类型；画nominal阈值/hysteresis与首次crossing/recovery。off不计算温度时留空，shadow为无反馈预测温度。宿主GPU物理telemetry单独时间轴/图层，不能称目标同时刻HBF。

补表：初始/尾窗useful throughput、累积completed useful bytes、offered/admitted/pending/inflight/completed守恒、queue slope、temperature slope、首次gate/recovery、duty cycle、Tmax、power、physical read/program/refresh/read-retry bytes、J/useful byte（app门禁后J/token）、参数不确定性。未实现的refresh/retry记UNSUPPORTED，不能填0伪装已测无流量。

因果验收：controller改变实际未来submit/活动，活动与能耗下降后温度斜率变化；无请求/关gate仍持续按model time冷却恢复。当前CL0只证明一个窄controlled案例。nominal没有差距是允许的负结果，claim写成“在已定义条件下忽略反馈高估多少/错判何种边界”；不得预设所有场景thermal必需。

## 8. 最小独立补丁设计

| Patch | 可独立审查范围 | 必须验证 |
|---|---|---|
| P-T1 true off runner | 在独立工具中可选thermal对象和media sink；off不加载ROM/采样；保留相同arrival/MQSim/token/queue代码及统一service时间序列；不要升级ABI/合并大分支 | 不可用ROM路径下off仍可跑，shadow失败关闭；未触限off/shadow completion序列相同；独立计量工具CPU开销 |
| P-T2 completion/bin accounting | 按每completion真实model timestamp累计useful bytes与inflight；延迟跨bin/跨终点的completion不能提前记到早bin；每个bin快照对应自身时刻；renderer检查单位和三臂pair | 人工跨bin/多bin/终点案例；有read amplification/refresh时physical≠useful；守恒、单调与未完成请求显式保留 |
| P-T3 refresh boundary adapter | 复用F需求模型，通过显式背景请求接口向MQSim提交read/program，range/media offset一致；完成后更新寿命/PEC并把实际activity送回未来power bins | queue_depth saturation与当前admission组合、刷新完成/失败/重试配对、不中途双计能量、关gate冷却时仍合法推进；保持reliability与controller作用可拆 |
| P-T4 optional shared-stack path | 若source ledger确证且影响结论，再提供host/base/TSV共享资源domain及带宽/仲裁/能耗；明确与MQSim原channel的瓶颈分工 | 单层/平面退化，同容量不同stack，固定path改plane并行、固定plane改path；不以数值一致替代外部held-out |
| P-T5 isolated live integration | thermal核心和平台lifecycle分开；先当前tree thermal-off baseline，再设计窄service接口/必要ABI过渡，单独测试所有producer/consumer | EQ1 first-consumer/async覆盖、真实app计时、off parity、exclusive GPU、source/environment一致；未通过则仅device-only证据 |

## 9. 下轮无需重复的工作

未改变的原13组工件/兼容测试不重跑。冻结hash的3D-ICE/ROM数值证据直接引用；只有geometry、power coupling、ROM、clock/bin或控制语义改变时才重做相应held-out/机制检查。历史CL0/BW0、HBM0失败与LIT负结果保持不可变。主图新配对不能复用host-launch APP0、旧Class-C数值或T7 envelope冒充同一物理运行。
