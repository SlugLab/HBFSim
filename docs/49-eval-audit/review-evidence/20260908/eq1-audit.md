# EQ1 定向实现与证据审计 — 2026-09-08

> 分享副本：审计事实、数值和原执行路径保持原记录；仅调整导航。大模型、完整 raw、部分本机清单与可执行二进制未随仓库发布，范围见 [审阅包说明](README.md)。

审计基线：`/root/hbfsim-exp/eval-base-integration`，`eval/eq1-eq4-implementation@254d65a66279fbaffc5c185d04fbe41dc8dbba44`。本文是本轮只读代码审计与 CPU 最小检查，不是 GPU/完整 EQ1 验收报告。审计时 tracked tree 干净，`figures/`、`results/` 未跟踪；本代理未更改仓库代码、canonical 文档、历史证据或 Git refs，未运行 GPU。未发现从 `/` 到 repo 的 AGENTS.md；已读取现有 async skill、evaluation audit、execution/claim/source ledger 与精确相关历史 review。

## 当前结论

当前不是早期 eval_base 的纯同步实现，也不是完整 SM120/TMA donor。当前增加了显式启用、默认关闭的 `timing_load_future_v1`：受限直线 scalar load 的 native issue 与 modeled wait 分离；它只接受 fast scalar TIMING、`time_scale=1`、非 empirical profile，保留共享 control ABI 4。它拒绝 capacity、一般 CFG、vector、atomic、普通 cp.async 与 TMA。当前存在 TMA 语法解析，不等于 TMA runtime 已接通。

19 项当前源码 CPU emitter/interpreter 检查通过，涵盖条件消费者、predicate 改写、执行/未执行 overwrite、native store/fence drain、混合 lane/page、错误状态、未知类型与 unsupported 形式拒绝。它支持“当前受限 emitter 已修复旧 donor 条件消费漏 wait”的工程判断；不能支持 GPU overlap、最终硬件 scoreboard、TMA 或 HBF 物理真实性。

新 EQ1 应同时冻结两个不同 oracle：`S1=[L1-W]+` 与 `DeltaS=[L1-W]+-[L0-W]+`。若 D 为额外延迟，`L1=L0+D`。本轮附件三例获得 `0/3/4.5 us`；把额外 D 代进旧 `[D-W]+` 会在后二例分别得到 `2.5/4.3 us`。旧 shadow CPU oracle 中 D 为 issue 相对总服务时间时可保留其局部语义，不能将它直接作为相对 native 的额外 stall oracle。

## 当前代码 ledger

表中 C 均为上述完整当前 SHA；S 为 `feature/sm120-exact-stage1@f4dc28b2671c01939d98e4a968e6fb37b2e364d9`。路径均相对 repo，行号绑定 C/S，后续修改需更新映射。

| ID | SHA / 文件 / 函数 / 行段 | 核实行为与证据 | 支持边界 / 缺口 |
|---|---|---|---|
| C01 | C `src/ptxpass_hbf/transform.cpp:199–237` `transform_ptx`；`CMakeLists.txt:8` | 显式 future mode、sm120 target gate、默认 OFF | 不是所有现有 kernel 自动获得异步支持 |
| C02 | C `src/ptxpass_hbf/plugin.cpp:351–395` | 固定 future bound、helper/控制 ABI/capability/module identity 合约；最多16静态producer/线程、1024线程/CTA | 不允许 caller 随意扩大资源声明；实际 launch admission 仍须匹配 |
| C03 | C `src/ptxpass_hbf/ptx_analysis.cpp:43–64,253–350,368–390` `supported_instruction/transfer_block/analyze_futures` | scalar ordinary load/store 与窄 ALU/fence子集；条件 consume 不无条件删除 pending；覆盖前 drain；早退/branch 不进入支持域 | predicate false→unconditional 在本轮 interpreter通过；一般 loop/diamond/vector/atomic未知，不转化为 PASS |
| C04 | C `src/ptxpass_hbf/future_transform.cpp:170–189,201–224,243–294` `Writer::wait/emit_timing_futures` | execution AND per-producer valid guard；wait 返回值重新构造目标寄存器；issue后保留原生 load；store前 guard | 输出的 raw→rawbits→nativebits 转换紧邻 native load（279–282），是否在最终 SASS 提前消费/损坏原生 overlap 必须检查；PTX顺序本身不能给出最终硬件结论 |
| C05 | C `src/cuda_runtime/device/hbf_device.cu:1140–1277` `__hbfsim_timing_future_issue_v1` | 1209明确只接受 range mode 1；按 warp同range/page分组；1232–1251原子预订 fast channel tail返回 issue-relative ready | **不是 host MQSim async ring**；没有实现 deferred capacity；硬件 MSHR/scoreboard占用并未随软件 ready 延长 |
| C06 | C `include/hbfsim/timing_future_abi.hpp:113–120,219–267,269–282` `derive_capabilities/poll_state/consume_state/scalar_reservation` | 仅 mode1/empirical0/time_scale1；ready=max(arrival+latency,transfer_end)；绑定与 deadline失败关闭 | 延迟+带宽 scalar模型，不是逐周期 backend资源模型；目标快于 native不可用简单正延迟注入复现 |
| C07 | C `src/cuda_runtime/device/hbf_device.cu:1001–1041,1280–1338` `future_header/future_liveness/future_poll/future_wait` | control generation/ABI/geometry绑定；poll/consume检测ready/deadline/daemon；native_bits真实call输入输出 | tight pending poll不是硬件memory stall；吞吐、occupancy与helper overhead必须独立测量 |
| C08 | C `src/cuda_runtime/device/hbf_device.cu:296–386` `wait_for_completion/resolve_leader` | 同步reference路径 reserve→host completion→issue相对模型目标等待→返回原生指令 | 原生读发生在软件wait后时可能额外支付 native尾延迟；issue stall 与 consume residual不能混淆 |
| C09 | C `src/host_service/capacity_page_service.cpp:88–186` `CapacityPageService::resolve`；`src/cuda_runtime/capacity_runtime.cpp:189–222` | mutex下lookup/eviction/writeback/read backing/copy/publish；HtoDAsync紧接cuStreamSynchronize | scalar page搬运的功能路径；不能证明整tile pinning、多stage buffer寿命、capacity TMA或在线重叠；迁移backing设备改变真实wall time |
| C10 | C `src/ptxpass_hbf/ptx_async_op.cpp:75–150` `parse_tma`；`src/ptxpass_hbf/transform.cpp:58–79,347–349` | 结构解析TMA方向/维度；默认transform将触及global的普通/bulk/tensor async记unsupported，同步-only不伪记内存 | C中无`async_object_analysis.cpp`、`tma_transform.cpp`；无host/device TensorMap generation runtime链，必须保持拒绝 |
| C11 | C `src/cuda_runtime/device/hbf_device.cu:897–939,962–997` `future_delay_prepare/future_delay_existing` | 现有诊断固定D为0或20000ns、K为0或4096，1 CTA×32 lanes | 不能直接执行附件D=0.5/1/2/5/10us或一般W/MLP/QD矩阵；新通用harness需独立补丁 |
| S01 | S `src/ptxpass_hbf/ptx_analysis.cpp:253–275`；`future_transform.cpp:324–440` | donor在可能consume后无条件erase pending；consumer wait却带predicate；本轮读取冻结Git blob确认 | 历史静态反例可复用；不再次编译未变donor，且不能据此称C仍有相同bug |
| S02 | S `src/ptxpass_hbf/async_object_analysis.cpp:79–105`；`tma_transform.cpp:584–846` | 线性descriptor/barrier/committed列表；static token bookkeeping | general CFG动态group守恒、循环reissue、conditional commit必须负测/拒绝 |
| S03 | S `src/cuda_runtime/device/hbf_device.cu:2444–2546` | barrier poll组合native/software readiness；barrier_wait无本层统一deadline；commit_group空体；read wait部分方向提前返回 | 空commit不单独证明直线程序错误；source-read-done、destination visibility和持久化必须分开，timeout/daemon loss仍需闭合 |
| S04 | S `src/cuda_runtime/device/hbf_device.cu:1864–2442`；已冻结source ledger S12–S13 | donor有TensorMap generation/hash/replace/copy/acquire与TMA issue候选 | 本轮仅按历史ledger定向定位，未重新完整审阅此稳定大段；动态跨注册区、opaque layout、capacity tile寿命均没有因此验收 |

## 历史 refs 当前关系

本轮只检查现有本地 refs/Git对象，未 fetch、merge、checkout。

| 引用 | 当前本地SHA | 与C关系 |
|---|---|---|
| eval_base | fc829992ecdc3ca68881656722b67a31067c5d33 | C祖先 |
| fix/mqsim-queue-depth-admission | 12ef13809dc13424981c123f13dc31e6d0456a80 | C祖先；不得被donor覆盖 |
| feature/sm120-exact-stage1 | f4dc28b2671c01939d98e4a968e6fb37b2e364d9 | 非C祖先；共同祖先0b34feb6459fb87eed8b40987e306d112118d363 |
| feature/thermal-reliability | 0069b2eec4d8b9d37cdbc9fbf84035d15572b0dd | 非C祖先；包含完整SM120 donor祖先链 |
| 全链路温度模拟 | fd11c3d98cde74977be7ec504c64429369b6fd3a | 与C和thermal分支无共同祖先；只能endpoint/tree比较 |
| Phase-II final source | 49f9b2daa60271b39085de8cbc98180b6aead297 | 对象在本地；与C无共同祖先，不能当C普通后继 |

可复用关系证据在 `eq1-history-refs.json`；固定donor片段与blob哈希在 `eq1-donor-snippets.json`。远端是否已前进不由这些本地refs证明。

## 历史结果再利用，保持原字节

* `results/gold/timing-future-unit/c6-future-delay-attempt-003/status.json` 为 `CAPTURED_UNVALIDATED`。相关 data-review manifest仍 `scientific_validation_passed=false`。review记录67次launch、输出/请求/trace算术检查，但20us deadline在work前已过；prework median约309/317us，K0和K4096并未制造实际W差异。它是识别失败的机制诊断，不是有效D/W曲线。这里复用冻结review，不本轮重哈希其全部原始数据，也不把历史采集标成本轮测量。
* `c6-future-lifecycle-data-review-attempt-001/actual-mapping-review.md` 对 `79dffe3efddf4949314b26212f15699d44795f85` 有optimized sm120结构路径映射；mode-dependent overwrite/consume/ret drain的映射有价值。对应producer仍未验证，manifest `formal_done=0`。不能升级到G5或硬件complete延迟正确。
* 旧 `docs/49-eval-audit/async-tma-audit.md` / `docs/skills/04-cuda-async-cpasync-tma.md` 混合了B/S历史说明与C6增补。canonical重规划应明确“B同步 / C受限fast TIMING future / S未整合TMA”三层，并修正额外延迟oracle。

## 新EQ1最小执行顺序与验收

每组先pilot，再冻结参数/误差/重复数；本轮只执行CPU组。下列预留预算是**硬停止上限，不是已测耗时**。GPU组须独占物理GPU，并避开复制/编译/存储压测。先单访问，随后MLP/occupancy正交扫描，最后代表流水kernel，不做全维笛卡尔积。

| 组 | 当前入口 / 状态 | 参数与依赖 | 观测 / 验收 / 停止 |
|---|---|---|---|
| Q1-CPU | `tests/integration/test_future_emitter.py` + 当前源编译driver；本轮19/19 | 1次确定性执行；CPU<=120s/1GiB/<30MiB | 支持子集值/predicate/issue-consume-drain守恒；不支持形式非零拒绝；无GPU主张 |
| Q1-ORACLE | `eq1-delta-stall-oracle.json`；本轮3/3 | L0=.5,L1=5us，W=6/2/.2us | DeltaS=0/3/4.5us；记录S0/S1/extraD；数学检查不当实测 |
| Q1-STRUCTURE | `scripts/eval/audit_sass_mapping.py`、现有C6 binding链；缺通用native LD→ALU→consume基准 | native/instrumented-zero/old issue-stall/deferred；每kernel独立原始PTX与优化cubin；CPU编译pilot<=5min/<1GiB | 映射native LD、首个真正SASS use、helper entry/exit、W、wait、consumer，记录寄存器/spill/shared；看不到或被编译器消除则STOP |
| Q1-LIFECYCLE | `scripts/eval/run_c6_future_lifecycle_correctness.py --execute ...`；受限历史入口真实存在 | `--out --build-dir --profile --ptx --cubin --gpu-uuid`，依赖同run mapping绑定；3个paired pilot，GPU<=120s/<128MiB；冻结后至少10独立paired launches | checksum全对，native/error/issued/consumed/drained/trace完整；失败原样保存；本轮不启动 |
| Q1-OVERLAP | `scripts/eval/run_c6_future_delay_overlap.py` 只支持D0/20us、K0/4096；**通用oracle矩阵BLOCKED_IMPLEMENTATION** | 目标D={0,.5,1,2,5,10,20}us；W覆盖L0/L1两侧，先校准actual W；time_scale1；3 paired pilot；GPU<=120s/<128MiB | 必须先证明有效W差异、deadline跨越W的三种情况；分别issue阻塞、consume residual、总exposed stall、native/zero wall；若D在所有W前都过期则NON_IDENTIFYING并停 |
| Q1-MLP | **BLOCKED_HARNESS_AND_BASE_GATE** | Q1-OVERLAP过门后，MLP/QD={1,2,4,8,16,32}与低/中/高occupancy，正交选点；3 paired pilot，<=5min/<256MiB | achieved occupancy/QD、访问/sector bytes、spill、queue/admission/completion、helper膨胀；低到高并发held-out，解释不了偏差则收窄claim |
| Q1-NATIVE-SLOW | **BLOCKED_PLATFORM_CAPABILITY_AND_HARNESS**；当前没有已验证mapped-host对照入口 | 先只读platform capability/最小allocation pilot；device vs mapped-pinned native LD；warm/cold、held-out W/MLP，<=120s/<128MiB | 原生SASS访问必须存活；host/PCIe路径只校验相应服务域，不外推host=HBF/TMA/atomic |
| Q1-TMA-GS | **BLOCKED_RUNTIME**；C10仅parser/rejection，S donor不是执行入口 | 独立producer/consumer oracle先行；合法phase/expect_tx/arrival、try_wait/test_wait、多warp、多stage、受支持multicast；每case5功能重复 | 联合native+model+data-ready gate；zero early/stale/missing bytes，timeout/daemon death非成功；不可软件改写硬件内部SYNCS |
| Q1-TMA-SG | **BLOCKED_RUNTIME** | `.wait_group.read N`后立即覆盖shared源；单独destination-visible测试；动态groups N=0/1、阶段复用 | source-read-done、destination visibility、durability分开；无全系统/持久化外推 |
| Q1-TMAP-CAP | **BLOCKED_RUNTIME_AND_LIFETIME** | host encode/replace + device replace/fence、A→B不同checksum、coords/stride/OOB、跨多range/page/mixed tier；capacity stage/pin/evict全tile | descriptor generation/范围账本与source fetch vs delivered multicast bytes分开；不解码未经稳定承诺的opaque layout；capacity TIMING证据不互换 |
| Q1-SIM | **BLOCKED_SM120_SUPPORT** | 外源代理统一提供Accel-Sim version/arch支持证据；最多少量代表kernel，不以移植LLM为前置 | Hopper校准不能当SM120；若无支持即停止可选安装路径，其余锚点继续 |
| Q1-PIPELINE | **BLOCKED_KERNEL_AND_SEMANTIC_GATE** | CUTLASS/Triton合法double-buffer GEMM/MoE；async vs forced-serial负对照，实际stage/transfer/fanout；pilot<=5min/<256MiB | buffer reuse、多outstanding与窗口机制成立；time run与profiler run分离；没有ready语义门禁不跑性能矩阵 |

误差验收：pilot分别估计native L0与instrumented-zero开销/波动，冻结绝对ns门槛与paired重复数；默认至少10独立paired launches，随机/ABBA次序并保留seed。若10对不足以达到预注册CI宽度，增加到预注册上限；达上限仍不足即INCONCLUSIVE，不删不利点。报告absolute bias/median/p95/paired CI；接近零用absolute error，不用MAPE或单一Pearson。不能把相同warp内lane当独立replicate。held-out W/MLP/QD的区间、阈值须在正式采集前写进run identity。

每条GPU结果必含issue origin/latency_kind(total或extra)/L0/L1/W定义、native与model时钟域、time_scale、GPU/存储身份、源/构建/环境/helper/profile/trace/renderer哈希、PTX/SASS/cubin映射、coverage分母与unknown bytes、失败原因、raw相对路径。独立ALU W是实测区间，不能以循环K代替。宿主时间戳与GPU globaltimer不得直接相减。

## 最小后续补丁（未实施）

1. **P-EQ1-01：两个oracle与字段，不改runtime ABI。** 在新EQ1分析器明确total/extra D、L0/L1、S0/S1/DeltaS、issue阻塞/consume残余/总暴露，并拒绝缺原生配对/单位/时钟域。用三例与L1<L0拒绝/独立模型分支验证。
2. **P-EQ1-02：可识别W与低扰动分段时间。** 将固定K predication基准拆成真实不同长度的independent序列；核实optimized SASS首use；把observer开销与helper机制分账。先证明W对比及deadline的三种关系，再考虑扩D；不简单增D掩盖坏基准。
3. **P-EQ1-03：受限native锚点与MLP/occupancy。** 加native device /条件mapped-host小基准，保存SASS和cache/访问计数；锁定held-out点。若current nativebits提前消费由SASS实证成立，再单独修最小数据依赖位置。
4. **P-EQ1-04：异步MQSim future桥接。** 仅在fast scalar门禁后设计issue→bounded request ring→model完成→GPU残余门；保留已集成queue-depth admission修复，错误ABI/timeout/daemon loss/finite trace负测。该路径当前不存在，不能称已完成。
5. **P-EQ1-05：TMA TIMING窄子集。** 先实现/验证所有软件可观察wait/poll的联合gate和动态group状态；未知CFG/descriptor/multicast仍拒绝。S源码只按独立patch选择，不整体merge thermal donor/升级ABI。
6. **P-EQ1-06：TensorMap生命周期与capacity tile租约。** generation/fence/range解析、全tile stage/pin/evict/source-reuse账本分两次可独立审查patch；与TIMING TMA分开验收。容量搬运的native尾延迟与真实backing设备耗时单列。

## 本轮可复现最小检查

原始命令、每源SHA256、耗时、stderr均在`eq1-build.json`、`eq1-build-complete.json`、`eq1-emitter-tests.json`。首次命令漏了已有`ptx_async_op.cpp`翻译单元，链接失败；已保留失败记录，补齐依赖后无代码修改编译成功，11.80s；测试1.73s/19项通过。CPU编译用-O0仅便于快速host工具检查，不是GPU优化证据。

```bash
g++ -std=c++20 -O0 -Isrc/ptxpass_hbf tests/cpu/ptx_future_emit_driver.cpp src/ptxpass_hbf/ptx_ir.cpp src/ptxpass_hbf/ptx_async_op.cpp src/ptxpass_hbf/ptx_analysis.cpp src/ptxpass_hbf/future_transform.cpp -o /root/hbfsim-exp/reports/eq-replan-20260908/eq1-future-driver
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/root/hbfsim-exp/reports/eq-replan-20260908/eq1-tmp python3 tests/integration/test_future_emitter.py /root/hbfsim-exp/reports/eq-replan-20260908/eq1-future-driver -v
```

三个真实runner的`--help`均exit0，输出在`eq1-entrypoints.json`，本轮未传`--execute`。`eq1-delta-stall-oracle.json`为附件三例数学输出。迁移后旧逻辑前缀仍保留时上述命令可通过兼容链接运行；若不保留，应由父任务的路径映射转换命令，不改写历史记录。
