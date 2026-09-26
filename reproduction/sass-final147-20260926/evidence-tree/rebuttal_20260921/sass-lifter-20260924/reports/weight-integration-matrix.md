## Actual final147 single-run PASS — 2026-09-26

DOC_DERIVED：epoch6712 `runs/li6-li7-final147-v1/MODEL_VALIDATION.json` 验收 MODEL_CONNECTED_PASS_COMBINED_ADDED49。同一最终组件真实run覆盖147storage注册13,838,323,712B，旧98实际地址/模块服务闭合，新49选中原生/候选输出均一致，最终tokens[[70,13]]。Controller6359.627s，worker0/guardclean；641,170,176次/5,026,111,488B modeled admission/completion闭合、记录错误0。新不可变 `task/full-coverage-ledger-v1/FINAL_SINGLE_RUN_147_ACCEPTED_v1.json` 与历史union分开；完整报告 `reports/final147-20260926-v1/REPORT.json`。注册extent不等于全部字节访问，shared module计数不证明逐storage延迟；selected49仅scheduled decode，精确expert/lanes、全部prefill及unsupported消费者仍UNKNOWN/NOT_MEASURED，已观察nonmodeled分类另列。保留全部旧失败。root资源释放收据确认reserve/watchdog均退出，GPU compute-app为空。下方早期pending状态为历史。

## Remaining45模型接入通过；147历史union，最终单轮待验收（2026-09-26）

DOC_DERIVED：epoch6711 `runs/li6-li7-remaining45-v1/MODEL_VALIDATION.json` 为 MODEL_CONNECTED_PASS_COMBINED_REMAINING45，层1–15 QKV/o_proj/router共45storage/507,248,640B均通过真实scheduled decode选中输出、请求/线程/Driver/地址及精确模块服务闭合。Controller2180.826370213s、worker0/guard0/clean。与先前102合计147/147、13,838,323,712B，见 `task/full-coverage-ledger-v1/MODEL_COVERAGE_AFTER_REMAINING45_v1.json`，状态明确 UNION147_FINAL_SINGLE_RUN_PENDING。该历史union不替代最终同组件旧98+新49（含各层之后的head）单轮回归；原失败保留，任务未完成。root已选最终epoch6712有限预算10800/11400/600/300/full12300，但实际START/PASS只能由新运行证据建立。

当前增量（2026-09-26，DOC_DERIVED）：epoch6710 `../runs/li6-li7-combined-mixed3-v1/MODEL_VALIDATION.json` 为 `MODEL_CONNECTED_PASS_COMBINED_MIXED3`。同一次真实scheduled decode依次选层0 QKV、o_proj、router，Li6/Li7独立profile及真实storage/output/Driver/地址/服务闭合；old98/head在该轮未注册。候选实测QKV75.727s、o_proj14.624s、router0.751s，controller841.639s。三块已在旧102接受集合，覆盖不加计，仍102/147、13,331,075,072B；剩45仅层1–15三类，最后还需一次旧98+新49的147联合回归。remaining45有限模板预算由root按此实测选定，但无新GPU结果。下方早期状态为历史。

# 权重类别接入对照与错误复用记录

当前状态（2026-09-26，DOC_DERIVED）：epoch6706 lm_head 真实scheduled decode compute_logits代表已MODEL_CONNECTED_PASS，参见 ../runs/li6-head-progress-v1/MODEL_VALIDATION.json 及 ../task/li6-head-progress-v1/TERMINAL_EVIDENCE_INTERPRETATION.md。与旧98、QKV layer0、o_proj layer0、router layer0合计102/147、13,331,075,072B；剩45/507,248,640B。账本 ../task/full-coverage-ledger-v1/MODEL_COVERAGE_AFTER_HEAD_v1.json。此前6701/6703/6705失败保留，统一mixed3、扩45及最终147回归未执行。旧保留已过期且GPU由Qwen vLLM占用，root须重新核资源身份后才可决定启动；下方13:50状态为历史。

当前状态（13:50UTC）：接受模型覆盖100/147。router layer0 MODEL_CONNECTED_PASS；lm_head 精确profile LIFT_OUTPUT_PASS，但6701/6703插桩候选超时，未计模型覆盖；o_proj局部BYTE_EQUAL尚缺独立整轮服务闭合。下一步隔离o_proj-only和head-only真实模型代表，仍保留最终组合147回归目标。实际进程/计划入口 `../task/resume-giga-20260925-v1/OWNER.json`。下方有时间戳的准备段属于历史，不能覆盖实际终态。

最新验收（2026-09-25 13:14UTC）：Li7 layer0 router epoch6702 已通过完整模型接入验收，唯一storage262144B，固定decode消费者。接受覆盖100；尚未证明其余router15层。实际工件与路径见 `../task/router-li7-model-representative-v1/LIVE_PLAN.json`、`BUNDLE_JOIN.json`、`MODEL_TARGET_MANIFEST.json`、`model-sidecar.json` 和运行 `MODEL_VALIDATION.json`。其余未通过类别优先，以下13:13记录为验收前历史。

历史CPU准备记录（已被6703实际超时终态取代）：Li6 o_proj/lm_head epoch6703仅备隔离诊断包`../task/li6-wait-boundary-v1/`，未运行，不增加已接受覆盖。原6701 o_proj局部输出BYTE_EQUAL但无整轮完成/服务闭合；lm_head第二patched Driver入口无返回，`CUDA_LAUNCH_BLOCKING=1`使长内核与host等待均可能。诊断保留两selector先后、原agent/gate/provider/PTX，仅添加host阶段标记及候选前原生输出保存；其host顺序变化不能当性能比较。仅新运行的实际storage地址/输出/服务闭合可改变该类MODEL_CONNECTED状态。

历史CPU准备记录（已被head LIFT_OUTPUT_PASS取代）：2026-09-25 CPU包更新：`../task/li6-exact-head-lift-v1/`已冻结独立head m50304/grid12576原生对recovered cubin输出比较计划，使用6701真实4096B activation与原checkpoint BF16 `[50304,2048]` head权重。候选为原Li6 qkv-output-repair CUBIN和其自身v7 ABI map，provider只增加精确`lm_head-m1`捕获；原Li6 agent/gate/PTX/helper不变。`TIER1_PLAN_ROOT_v1.json`仅CPU准备，root尚未启动。此点若通过只增加该head profile的`LIFT_OUTPUT_PASS`，不增加HBF服务或模型覆盖；6703及旧失败保留。

当前增量（2026-09-25 13:13UTC）：Li7 tier1 已 `LIFT_OUTPUT_PASS`，证据 `../runs/router-li7-lift-output-v2/DONE.json`；真实 layer0 router 模型 epoch6702 已启动，验收未完成，不能计覆盖。Li6 epoch6701 已 worker_timeout，o_proj局部BYTE_EQUAL但缺整体终态/服务闭合，lm_head第二次patched Driver调用未记录返回；原raw保留，见 `../task/all-weights-category-representatives-v1/LI6_TIMEOUT_DIAGNOSIS_v1.md`。接受模型覆盖仍99，以下CPU准备/未运行文字为对应时间的历史记录。

本表用于扩层前核对已经成功的接口，配合 [接口契约](interface-binding-contract.md) 和 [三级验收](all-weights-expansion.md)。表中成功只指对应收据的消费者范围。新层的地址、句柄、context及生成器身份必须取自本次模型；未知项不由名称、形状或调用顺序推断。

下列运行产品的 `task/…` 路径以 giga `/root/hbfsim-exp/rebuttal_20260921/sass-lifter-20260924/` 为根；本地只收集必要证据，不保证存在全部远端产品。不能把这些相对运行路径直接解释为本地文件或复制成Windows绝对路径写入远端配置。

| 类别 | 已有依据与参数边界 | 注册、路径及扩层注意事项 |
|---|---|---|
| MoE 专家权重 | 6307旧98、6603保留旧98；沿成功 Triton `fused_moe_kernel` 生成器和真实入口 | 以实际storage去重注册；复用对应module的生成器和profile，不因名称为MoE更换专家实现；模块级计数不证明每位专家均访问 |
| norm | 6306/6307及6603；普通指针metadata与Embedding/Fill聚合参数不同 | 使用该module自己的metadata profile；副本的manifest与sidecar路径须同时迁移，不能只改manifest |
| Embedding；相关Fill内核 | 6306/6307及6603；聚合参数处理沿实际成功生成器 | Fill是相关执行内核，不额外冒称一类模型权重。逐module保留精确scoped/direct入口；不能套norm普通指针profile |
| QKV | 6603只证明layer0选中decode；精确目标原152B aggregate→19槽148B，槽9/14/18为4B，其余8B | 按真实storage选中，在同一线程配对begin/end；转换须早于任何gate按候选ABI读取。其余15层先核image/entry/context/ABI/geometry能否复用；不能复制layer0一次性插件即称全部接入 |
| attention o_proj（16份） | layer0 epoch6704 MODEL_CONNECTED_PASS，其余15份待证 | 先从实际BLAS输入地址关联storage及driver入口，再决定恢复与参数适配；不默认沿QKV布局 |
| router gate（16份） | layer0 epoch6702 `MODEL_CONNECTED_PASS`，其余15层待证 | 实际ReplicatedLinear→FusedMoE、[64,2048]BF16 storage262144B、输出[1,64]/128B；Li7独立152B→19槽148B ABI，grid16/block32x4/shared528；证据 `../runs/li7-router-layer0-model-v1/MODEL_VALIDATION.json`。模型注册与真实patched Driver/address/service闭合；不可把路由器权重与HBF launch gate接口混为同一对象 |
| lm_head（1份） | 精确m50304/grid12576 LIFT_OUTPUT_PASS；epoch6706 单storage真实decode MODEL_CONNECTED_PASS；6701/6703/6705失败保留 | 观测必须包含execute_model后续compute_logits；仅观察model forward可能漏掉真实消费者 |

## 同类扩层必须携带的接入记录

当前优先级按用户最新要求：先使o_proj、router、lm_head每个尚未覆盖类别都有MODEL_CONNECTED_PASS代表，再推广至各层。QKV已有layer0代表，扩16层暂缓。发现记录的phase只有LOAD_OR_PROFILE/SCHEDULED_EXECUTE；不得单凭CUTLASS名称将某条调用判为prefill，实际阶段需模型请求或输入证据关联。

旧98份的逐module原始证据及实际metadata形状已整理在[复用审查](../task/weight-interface-reuse-audit-v1/REPORT.md)：MoE四种Triton变体为23/24参数；norm两个入口共享普通指针module；Embedding的indexSelect为三个216B聚合参数；Fill另含2B/8B聚合参数，不能互换。该报告直接链接6307配置/注册、6603 pass行与绑定、修复后sidecar。原始每module生成命令/profile文件名尚未从本地收据证明，明确留缺，不按类别编造。

原生发现v2已实际通过：147份storage清单中剩余48份均建立实际消费者关联，见[发现验收](../task/all-weights-discovery-run-v2/VALIDATION.json)。这只是确定后续适配入口；prefill与decode使用不同内核的情况分别保留，不提升为MODEL_CONNECTED_PASS。

每个实际module/内核记录：权重别名与去重storage字节范围；消费者阶段；原image/entry及生成器；原参数布局→候选布局；metadata/pass profile；原PTX→staged PTX→manifest→sidecar精确引用；provider/agent/gate实际库与CUDA域；真实original→patched发射证据；输出和逐地址服务验收。引用已通过工件即可，不重复全量哈希或复制大文件。

注册只证明范围已登记。计入模型覆盖还须真实模型消费该storage、运行对应插桩函数，并完成输出与地址/服务闭合。全局非零计数、原生fallback或单独恢复内核成功都不能填补接入缺口。

## 已发生错误及复用修复

新类别的真实调用接口已从固定运行版本源码核实，原件位于`task/new-category-interface-source-v1/`：o_proj实际调用`RowParallelLinear.forward`，内部`quant_method.apply`后保留原通信及tuple返回；lm_head的`ParallelLMHead.forward`直接抛异常，真实路径是`OlmoeForCausalLM.compute_logits → LogitsProcessor._get_logits → lm_head.quant_method.apply`，之后还有gather及词表padding裁剪。不能复制QKV的child.forward包装来接输出头，也不能在_model_forward结束时撤掉后续compute_logits仍需要的请求选中状态。接入时须明确原始线性输出和最终logits的比较范围。

| 错误 | 已定位原因 | 后续同类检查 |
|---|---|---|
| Fill/Embedding/norm副本绑定自定义10 | manifest.staged_path改了，sidecar.module_joins.staged_path仍指旧位置 | 同时更新两端及manifest所引用sidecar，保持唯一精确匹配。成功包 `task/copied-provenance-relocation-fix-v1/package-v1/`；配置 `task/copied-norm-relocated-v1/runtime-config.pre-resource-relocated-v1.json` |
| QKV恢复内核正确但模型未替换 | 真实Driver函数指针绕过原hook，或gate在aggregate转换前读取候选参数 | 沿6611/6603实际 `task/qkv-model-dispatch-repair-v2/` 路由修复，核真实libcuda入口、trampoline与original/patched对；不重跑未变化的lift验证 |
| 绑定接口传错内容角色 | 原PTX内容、文件路径和staged PTX可被误混用 | `bpftime_nv_bind_ptx_variant` 接受pass前PTX内容字节；实际签名及返回值见接口契约 |
| 成功结果后退出139 | provider卸载回调访问已析构host状态 | 沿实际通过的独立生命周期修复；保留进程终态检查，不归咎于SASS转换 |
| 原生发现在架构检查子进程失败 | registry再次注册插件，但没有target provider preload | register仅安装包装；真实load_model才绑定provider并注册flush，真实目标缺provider仍失败 |

这些条目不授权更换PTX访存改写或device helper算法；新增算法变化仍按既有审批边界处理。最终各类均接入后再执行一次完整147份回归。

## 2026-09-25 giga恢复增量

Li7 v2已经在固定六修复lifter下完成CPU离线汇编，并从自己的候选ELF提取ABI：19槽、bank148B，槽9/14/18为4B，其余8B；原函数仍是152B单聚合。证据 `../task/router-li7-lift-v1/run-v2/CPU_RECEIPT.json`、独立 `li7-byte-abi-map.json`。13条同步诊断与quarantine身份不变，v1原失败保留。此项只解决候选产物与布局准备，不是LIFT_OUTPUT_PASS、INSTRUMENTED_KERNEL_PASS或MODEL_CONNECTED_PASS。Li7 host精确接口准备遵 `../task/resume-giga-20260925-v1/LI7_HOST_DECISION.md`，保留Li6。

Li6当前CPU准备要求：真实env构造与147清单上两selector检验；o_proj输出[1,2048]/BF16/4096B；lm_head比较实际compute_logits后的最终有效logits，关联org_vocab、dtype、字节数；installed/selected/registration地址和进程身份一致；模块与全局错误分别验收。审查入口 `../task/resume-giga-20260925-v1/ASTRA_REVIEW.md`。当前模型覆盖仍99，尚无新GPU代表终态。


2026-09-25 Li6代表6701已实际启动，尚待终态；CPU准备已不再是最新状态。精确147-storage目标清单由原生inventory及6603 registration生成于 `../task/full-coverage-ledger-v1/TARGET_LEDGER.json`，其中历史通过99、待证48，地址不可复用。最终Li6/Li7同组件绑定缺口见同目录COMBINED_INTERFACE_GAP.md；分类通过不得替代最终回归。


## 2026-09-25 同类原生消费者复用审查

`../task/full-coverage-ledger-v1/NATIVE_PROFILE_REUSE.json` 从已完成原生发现的真实 storage 输入指针、BLAS/CUPTI correlation、线程/请求时段及唯一持久 module 关联派生；未启动新GPU。QKV16、o_proj16、router16各自均只有一种已观测scheduled n=1完整profile，含BLAS尺寸/类型/leading dimensions和exact image/symbol/API/CBID/grid/block/shared。因此可以在各类代表MODEL_CONNECTED通过后准备同类扩层，但它们当前仍不是新增HBF覆盖。

lm_head存在两次scheduled n=1调用，均为相同profile；它说明n=1不足以判断decode，必须保留插件请求阶段关联。最初CPU派生脚本误假定每storage只有一次n=1，原断言失败已保存在 `NATIVE_PROFILE_INITIAL_FAILURE.json`，修订后保留全部调用而非删重遮蔽。地址/context/function句柄仅为该原生run证据，不作为扩层配置。


2026-09-25 13:46UTC exact-head standalone证据：`../runs/li6-exact-head-lift-v1/DONE.json`及raw native/replay输出均100608B、零差异；原Li6 CUBIN SHA0e154ab5...、map20754dd2...、host provider7ee37c...。只算恢复计算层，后续不重复。真实模型接入仍需地址/函数/服务/终态全部闭合。

2026-09-25 单类别模型代表CPU准备：`../task/li6-oproj-only-v1/`选o_proj层0一块8MiB、真实decode输出4096B；`../task/li6-head-only-v1/`仅选lm_head一块206045184B、真实compute_logits最终输出100608B。两者独立epoch/输出与有限资源门槛，插件保持其余权重原生并保存独立native/candidate原始输出。head-only观察器只读daemon memfd ABI5 host header，mode1 GPU独占计数不可按host队列闭合解释。此处仅记录CPU待root审查/运行，不能计为MODEL_CONNECTED或两selector组合修复。

2026-09-25 14:04UTC o_proj代表状态更新：epoch6704 `../runs/li6-oproj-only-v1/MODEL_VALIDATION.json` 为`MODEL_CONNECTED_PASS_OPROJ_LAYER0`；真实scheduled decode仅选`model.layers.0.self_attn.o_proj.weight`完整8,388,608B storage，native/candidate各4096B一致，最终token与原生基准同，实际目标DSO/path/inode、Driver入口、权重地址、模块访问/接纳/完成闭合。controller worker0/guardclean。账本升101/147及13,125,029,888B，剩46份713,293,824B，详见`../task/full-coverage-ledger-v1/MODEL_COVERAGE_AFTER_OPROJ_v1.json`。仅此层此decode消费者成功，其他15层o_proj与原生prefill不借此计入；lm_head仍为`LIFT_OUTPUT_PASS`、head-only模型待run，旧6701/6703失败不覆盖。

2026-09-25 head-only更新：epoch6705 `../runs/li6-head-only-v1` worker900s timeout/rc143，native100608B与select0留存但候选未返回，不计MODEL_CONNECTED。只读host服务30–120s同一daemon/memfd参考完成+32992/90s，表明该区间有真实服务进展；后期速率未知。源码/条件采样估计见`../task/li6-head-only-v1/REFERENCE_WORK_RATE_DIAGNOSIS.md`，不能直接把超时归于死锁。独立6706有限进展包在`../task/li6-head-progress-v1/`仅CPU就绪、root待审，未改原profile/采样/PTX/helper或接受判据，尚无GPU START。覆盖101/147不变。
