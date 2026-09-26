## Actual final147 single-run PASS — 2026-09-26

DOC_DERIVED：epoch6712 `runs/li6-li7-final147-v1/MODEL_VALIDATION.json` 验收 MODEL_CONNECTED_PASS_COMBINED_ADDED49。同一最终组件真实run覆盖147storage注册13,838,323,712B，旧98实际地址/模块服务闭合，新49选中原生/候选输出均一致，最终tokens[[70,13]]。Controller6359.627s，worker0/guardclean；641,170,176次/5,026,111,488B modeled admission/completion闭合、记录错误0。新不可变 `task/full-coverage-ledger-v1/FINAL_SINGLE_RUN_147_ACCEPTED_v1.json` 与历史union分开；完整报告 `reports/final147-20260926-v1/REPORT.json`。注册extent不等于全部字节访问，shared module计数不证明逐storage延迟；selected49仅scheduled decode，精确expert/lanes、全部prefill及unsupported消费者仍UNKNOWN/NOT_MEASURED，已观察nonmodeled分类另列。保留全部旧失败。root资源释放收据确认reserve/watchdog均退出，GPU compute-app为空。下方早期pending状态为历史。

## Remaining45模型接入通过；147历史union，最终单轮待验收（2026-09-26）

DOC_DERIVED：epoch6711 `runs/li6-li7-remaining45-v1/MODEL_VALIDATION.json` 为 MODEL_CONNECTED_PASS_COMBINED_REMAINING45，层1–15 QKV/o_proj/router共45storage/507,248,640B均通过真实scheduled decode选中输出、请求/线程/Driver/地址及精确模块服务闭合。Controller2180.826370213s、worker0/guard0/clean。与先前102合计147/147、13,838,323,712B，见 `task/full-coverage-ledger-v1/MODEL_COVERAGE_AFTER_REMAINING45_v1.json`，状态明确 UNION147_FINAL_SINGLE_RUN_PENDING。该历史union不替代最终同组件旧98+新49（含各层之后的head）单轮回归；原失败保留，任务未完成。root已选最终epoch6712有限预算10800/11400/600/300/full12300，但实际START/PASS只能由新运行证据建立。

## 同组件 Li6/Li7 mixed3 实际通过（2026-09-26）

DOC_DERIVED：epoch6710 `../runs/li6-li7-combined-mixed3-v1/MODEL_VALIDATION.json` 为 `MODEL_CONNECTED_PASS_COMBINED_MIXED3`，真实层0 QKV→o_proj→router三次选中调用在同一最终combined host组件内通过。三份实际storage地址与请求/线程、Li6/Li7 profile、原/候选Driver API/CBID/上下文/关联token、独立原生/候选输出和各自精确模块服务闭合由冻结validator核实；无旧98/head注册。本轮证明默认关闭双profile组件的三类交错路径已GPU验证，不将共享模块计数解释成逐storage延迟证明。候选耗时75.727/14.624/0.751s，controller841.639s，worker0/guardclean。仅三块已接受层0 storage，不新增102/147覆盖；其它45与最终147需各自真实结果。历史下方的combined CPU候选状态已被本段的此范围GPU结果更新，未扩大到head兼容。

# 权重接入：接口、绑定与路径约定

## lm_head 模型代表实际通过（2026-09-25 15:36UTC）

DOC_DERIVED：epoch6706 ../runs/li6-head-progress-v1/MODEL_VALIDATION.json 验收MODEL_CONNECTED_PASS_LM_HEAD。实际compute_logits后scheduled decode选中lm_head.weight整块206,045,184B；原生/候选各100,608B相同、select/end均0、最终token[[70,13]]、Li6 exact module 103,022,592 modeled admitted/completed accesses与206,045,184B闭合，错误0。candidate持续3314.621s，原~4284s仅早期速率条件外推。接口仍沿独立Li6真实152B aggregate→19槽148B及原绑定顺序；此成功不证明两selector同运行或合并Li6/Li7 host。详 ../task/li6-head-progress-v1/TERMINAL_EVIDENCE_INTERPRETATION.md 与 ../task/full-coverage-ledger-v1/MODEL_COVERAGE_AFTER_HEAD_v1.json。旧6701/6703/6705失败未改写。

## Li6/Li7 combined host CPU candidate（2026-09-25；尚未部署）

`task/li6-li7-combined-host-v1/`是隔离的默认关闭候选，仅完成CPU编译及mock。新增`HBFSIM_QKV_COMBINED_V1=1`需启动前同时给定两份source/staged路径和map/stage SHA；配置首次使用时捕获，缺任一行或与旧`HBFSIM_QKV_TARGET_KIND`冲突则拒绝。Li6沿已成功QKV实际152B aggregate→19槽（9/14/18槽4B，其余8B）、`6db074...`原source→`7219c1...` staged；Li7沿router代表相同已验证参数布局，但独立`e70c7...` source→`7ac0e8...` staged路径join。两个profile各自绑定original/patched/context/module/token；模型线程仍用原V1 select/end一次性scope、一次gate inspect。provider新增`hbfsim_qkv_live_identity_for_entry_v1(function,context,exact_symbol,QkvLiveIdentityV1*)`，输出仍104B V1布局，精确Li6/Li7白名单及实际关联名称校验；原V1查询语义不变。agent决策日志增加实际selected base/extent/profile、线程与本进程call ordinal；request_id留给模型插件收据join，API/CBID由provider真实回调提供，不靠名称猜。原98 name fallback及gate二入口分流未改。CPU产品/完整源码→编译命令→对象→原archive成员→新archive→DSO链见`task/li6-li7-combined-host-v1/CPU_BUILD_RECEIPT.json`及`build-v5/`；provider是双源码直链DSO，无archive，gate复用冻结Li7产品。此候选**没有GPU/模型验证**；epoch6706正在独立运行时不得触碰其工件，head代表PASS之前不得启动combined/扩层GPU验证。完整147仍需地址行与每storage真实选中/输出/调度join及exact module终态闭合；共享module计数不宣称每storage独立延迟。

此文档记录本次实际接口和故障，供后续接入与排错复用。状态以当前运行收据和 `../CURRENT_HANDOFF.md` 为准；历史地址、PID、CUDA 句柄不是新运行配置。新增实现必须同步更新本文件中受影响的接口及其验证状态。

各类旧权重及剩余权重的复用边界、注册、metadata与路径对齐见[权重接入对照表](weight-integration-matrix.md)。扩层先查该表与真实成功工件，避免对每一层重复处理已经定位的同类错误。

## 当前状态与问题归属

| 问题 | 定位与修复 | 已验证范围 |
|---|---|---|
| 副本 Fill、Embedding、norm 绑定返回自定义 10 | 复制生成器修改了 manifest 的 staged_path，却保留 sidecar 的旧 module_joins.staged_path。独立修复三份 sidecar 的路径及其 manifest 引用，保留精确匹配规则和 PTX 内容 | 6306 代表8份、6307 完整98份均实际通过，root已核原始结果；见copied-all98-relocation-root-review.json |
| 原生 QKV 参数与恢复入口不同 | 原生为一个152B aggregate；候选为19个参数槽。host v2在真实入口限定转换，通过一次性局部桥接入已有gate | 6611短真实调用及6603真实模型layer0选中decode均通过；其余权重/消费者不据此视为已接入 |
| provider 来源信息原先只写日志，agent 无法实时查询 | 独立provider增加精确函数身份只读查询，并在模块、父library、context失效时撤销记录 | 6603真实身份查询、patched函数与模型storage关联及输出/服务闭合通过；见ROOT_TERMINAL_REVIEW.json |
| gate 比 agent 更早读取参数及真实Driver入口绕行 | 在外层inspect前进入精确aggregate adapter，转换后执行既有gate；host v2使用真实libcuda入口及独立trampoline防止递归 | 6610失败保留；6611与6603新组件成功。实际二进制/路径以各自manifest为准，不用旧版源码解释新结果 |
| 结果收据完成后退出139 | 6510栈定位provider晚到的卸载回调使用静态映射表；原源码CPU对照复现析构后访问。独立修复延长该路径host状态有效期 | 6511实际GPU输出一致、计数闭合、worker0/guardclean，root已核原件；原失败保留。属于本项目provider生命周期缺陷 |

副本路径问题属于本项目打包元数据，不是 hetGPU 的 SM120 指令转换缺陷。QKV lifter 的六项语义修复另见 `lifter-repair-attribution.md`。

6603的成功仅覆盖旧98份加一份QKV的已验证消费者。当前扩展目标为147份，剩余48份见[分层验收与扩展计划](all-weights-expansion.md)。下方带6602/6610编号的故障定位段保留当时状态；其中“正在实现/未验证”不覆盖本表已注明的后续6611/6603结果。

## 调用顺序与职责

o_proj与lm_head代表适配须沿固定vLLM实际接口。已收`task/new-category-interface-source-v1/{olmoe,linear,logits_processor,vocab_parallel_embedding}.py`：o_proj是`RowParallelLinear.forward`，lm_head却不走其forward（该方法明确raise），而由`compute_logits → logits_processor._get_logits → quant_method.apply`消费权重；后续gather/词表裁剪不能被绕过。旧QKV仅包`_model_forward`的scope在输出头计算前已经结束，新head需要匹配实际请求及compute_logits阶段。源码观察不代表这些代表已经接入成功。

新增剩余权重发现路径独立于HBF模型接入：固定原生`GPUModelRunner.load_model()`返回None，加载后从`self.model`枚举实际storage；`execute_model`包含后续`compute_logits`，不能只用QKV forward区间判断输出头是否被调用。固定runner关闭V1多进程并在启动后清理后续子进程的preload；provider只装入明确的模型目标进程。观测库实际产品为`task/all-weights-provider-observability-v1/build-provider-v2/libprovider_qkv_production.so`，CPU验证通过，尚无该路径GPU证据。

provider的BLAS `begin_ns/end_ns`来自`CLOCK_MONOTONIC_RAW`；模型插件必须另记同域的`monotonic_raw_ns`作请求区间关联，不能直接与普通`time.monotonic_ns()`混比。新原生路径不依赖旧HBF runner收尾，必须显式调用实际导出的`hbfsim_provider_trace_flush()`，再通过`hbfsim_provider_trace_health_v3`读取五项uint64计数，记录最终`process_exit`收尾；验收要求flush返回0、ready为1、五计数为0。单独模型退出0不能替代这份日志完整性证据。

原native `start.json.environment`是筛选快照，并非全部实际环境。旧`REPRODUCE_PUBLISHED.py`的环境构造还设置`CUDA_LAUNCH_BLOCKING=1`和`PYTHONUNBUFFERED=1`，但收据筛选没有包含它们。独立发现启动器复用该快照时必须依据原构造函数补齐这两项；不能将“记录里没有”解释成“成功运行时没有”。本轮在新GPU启动前定位并修正，未为此重跑旧native。

1. 离线准备精确 QKV 的恢复 PTX、原 pass 生成的 staged PTX、参数映射及来源清单；不在模型 launch hook 内运行 lifter 或编译器。
2. provider 从实际成功的 CUDA 函数解析回调记录 `function → module → parent library/image`，复制名称、context 和关联 token；不保存稍后失效的 callback 参数指针。
3. 模型调用处先核精确入口、来源 image、当前 context、关联 token、参数元数据和支持的 launch 形式；不能仅凭名称或数值句柄匹配。
4. 将原152B参数转换为19槽后，再供 gate 检查及候选调用使用。不得先激活别名，再把未转换的原聚合参数发送给候选。
5. gate 检查身份、模块就绪和地址范围；agent 的精确 driver hook 将 original CUfunction 换成 patched CUfunction，再调用真实驱动。gate 的 canonical alias 本身不执行句柄替换，也不转换参数。
6. 一次模型调用只执行一次选中的候选，不以原生计算后再 replay 双写来宣称替换成功。保留 stream、context、launch geometry 和 device storage 生命周期。

6508 对应 hook 源码审查见 `../task/qkv-real-model-abi-readiness-v1/REPORT.md`。该次日志证明精确绑定、instrumented PTX 活动和输出一致，但未单独记录每次 original/patched 句柄对。新模型适配应在实际 dispatch 处记录这两个句柄及结果，不能以“注册成功”代替“候选已执行”。

## 实际接口

| 接口/位置 | 输入与输出 | 必须遵守的边界 |
|---|---|---|
| provider `hbfsim_qkv_live_identity_v1(uint64_t function, uint64_t expected_context, QkvLiveIdentityV1 *out)` | 调用者初始化 `out.struct_size`；返回1表示精确活跃身份，0表示没有满足条件的身份，-1表示参数/结构大小错误 | 当前独立 v2 实现；不调用 CUDA，仅在锁内读取已有记录。不得将0当作允许名称回退 |
| `QkvLiveIdentityV1` | 64位平台上104B：struct_size、reserved、context、module、association_token、65字节 image_sha256 | 实际布局以 `../task/qkv-real-model-adapter-interface-v2/qkv_live_identity_v1.hpp` 为准；规划文档的伪签名不替代此头文件 |
| `bpftime_nv_bind_ptx_variant` / `nv_attach_impl::bind_ptx_variant` | 精确 original handle、恢复 PTX 身份、入口；成功后发布 original→patched alias | patched module 的 timing binding 必须已就绪；失败不写成功缓存。不得把自定义返回码解释为 CUDA 错误码 |
| `hbfsim_approve_original_cuda_function` | original handle、转换后的 kernelParams、extra | 原生/候选/拒绝决策按当前 gate 实现解释；新模型路径只接受所需候选决策，不把 fallback 计入覆盖 |
| `cu_launch_kernel_common` | 已核身份和ABI的调用参数 | 精确调用真实驱动一次；default-off 分支保留旧行为。模型适配实现尚在准备，不能当成已部署 |

provider v2 仅支持本次已观察的 `cuLibraryGetModule → cuModuleGetFunction` 来源链；`cuKernelGetFunction` 路线尚不支持此查询。历史 launch 的 `library_generation=0` 指 function 通过 module 路线关联，不能因此误判为没有来源。新查询独立核 parent-library generation 与 function-association token。

context 销毁使用 CUDA12.8 的 `CUPTI_CBID_RESOURCE_CONTEXT_DESTROY_STARTING` 撤销新查询记录，不在回调内调用 CUDA。实际 `CUpti_CallbackData.context` 可以为空；为空时拒绝，不能凭静态声明伪造当前 context。[NVIDIA CallbackData 文档](https://docs.nvidia.com/cupti/api/structCUpti__CallbackData.html)要求在回调之外使用的数据进行复制，并说明该 context 是线程当前 context。

绑定接口的实际 C 签名为 `int bpftime_nv_bind_ptx_variant(CUfunction original, const char *original_ptx, size_t original_ptx_size, const char *kernel_name)`。`original_ptx` 是恢复后、pass 前的 **PTX 内容字节**，不是文件名、SHA字符串或pass后的staged PTX；实现对这些内容取摘要寻找已准备好的 variant。当前恢复PTX身份为 `6db07471…`，pass后staged内容身份为 `7219c1e8…`，两者角色不能互换。此实现返回0成功、-1参数错误、1 agent未启用/尚未late bootstrap、2精确variant未找到、3原句柄已有不同绑定、4gate发布拒绝。外层 native auto-bind 可再加10，故应保留每层原始返回值。

`hbfsim_approve_original_cuda_function` 的调用类型为 `int(CUfunction, void **kernelParams, void **extra)`。此外，旧 `bpftime_nv_strict_direct_cu_launch_kernel_v1` 入口自身会在进入共同dispatch函数前调用gate；模型适配必须检查这层以及外层gate，确保任何按候选ABI读取参数的地方都在转换之后，不能只在最深的driver hook中转换。

上述外层顺序已从 `../task/qkv-hbf-replay-v1/candidate/src/cuda_runtime/launch_gate.cpp` 的 `driver_launch` 核实。拟新增 `bpftime_nv_qkv_exact_aggregate_launch_v1` 由 opt-in 精确目标分支在普通 inspect 前调用；最终签名和构建身份须在实现冻结后补入，不将当前拟定入口当成已有导出。provider 的锁不得跨 CUDA/gate 调用持有，也不能经新入口递归启动两次。

## QKV 参数布局

仅适用于精确 v7 目标，映射文件为 `../task/qkv-hbf-replay-v1/qkv-io-abi-map-v7.json`。原参数区域152B，候选参数区域148B；槽 i 读取原区域偏移 `8*i`，其中 i=9、14、18 各4B，其余各8B。原区域的 `[76,80)`、`[116,120)`、`[148,152)` 不作为候选输入。

运行前核原函数确为一个 offset0/size152 参数，候选的19项 offset/size 与映射相符。不得通过读取 `kernelParams[1]` 猜测原指针数组长度。只接受已支持的 kernelParams 形式；extra 参数缓冲、Graph 和其它 launch API 不因名称相同自动获准。

19份 host 参数值与指针数组至少保持到 launch API 返回；device tensor、module、context 与 stream 按模型原有顺序保持到GPU完成。当前受控目标 slots0/2/4/6 对应 weight/input/output/output，真实模型需核 live storage，不能照抄捕获地址。首个实际模型目标为 `model.layers.0.self_attn.qkv_proj.weight`，BF16 `[6144,2048]`、25,165,824B；新运行时重新取得地址。详 `../task/qkv-model-target-selection-v1/REPORT.md`。

## 路径与版本对应

恢复工具的输入镜像范围也必须对齐：对fatbin使用官方`cuobjdump --function`可能仍输出大量无目标函数的镜像头。Li7首轮已解析1984条目标指令，却因后附1347个空镜像段被单镜像检查拒绝。后续优先对已证明来源的具体sm120 cubin导出；若复用已有dump，则独立保存唯一目标image的原字节切片及边界，核其余段无指令，保留完整原件。不能以放宽解析断言或删除真实指令处理此问题。

本地资料根为 `E:/project/ssd/hbfsim-exp/sass-lifter-20260924`；giga 任务根为 `/root/hbfsim-exp/rebuttal_20260921/sass-lifter-20260924`。两者是资料收集与运行位置的对应关系，不能把本地绝对路径写进远端 manifest。公共接入代码应通过配置接收根路径。

| 内容 | 当前路径与约束 |
|---|---|
| 旧副本原 stage | `runs/copied-regression-v1/prepared/stage/`；PTX 不因修复 metadata 而改写 |
| 修正的来源 sidecar/manifest | `task/copied-provenance-relocation-fix-v1/package-v1/`；manifest.staged_path 必须与所选 sidecar.module_joins.staged_path 精确一致且唯一 |
| 6306/6307 的 pre-resource config | `task/copied-norm-relocated-v1/runtime-config.pre-resource-relocated-v1.json`；运行配置在各自 run 中绑定当次资源，不覆盖旧文件 |
| 6508/6510 冻结 provider | `task/qkv-io-validation-v1/build-capture-v1/`；source `06ddf3f9…`，DSO `b1d11462…` |
| 6602 provider 接口 v3 | `task/qkv-real-model-adapter-interface-v3/`；source `63cb58b7…`，DSO `27745d83…`，将身份v2与已验证退出修复合并。实际用于6602，原件确认加载；模型选中dispatch失败 |
| 6602 agent 参数适配 | `task/qkv-real-model-agent-abi-v2/build-agent-v4/`；DSO `3992e186…`，setup与impl两个编译单元正确组合、五个新接口已导出。实际用于6602，保留该失败版本 |
| 配套 gate | `task/qkv-real-model-agent-abi-v2/build-gate-v1/`；DSO `94fa045d…`，实际用于6602；转换后检查/dispatch和权重注册查询接口不因新host路由修复而重建 |
| 待GPU验证的host路由修复 | `task/qkv-model-dispatch-repair-v1/`；setup `81064d87…`，agent `build-agent-v1/libbpftime-agent.so` 为 `6cf8b7db…`；provider source `c464f694…`，`build-provider-v1/libprovider_qkv_production.so` 为 `b495ef88…`。独立CPU构建通过，尚无实际GPU成功证据 |

短哈希仅便于阅读；完整身份以各构建/运行收据为准。必要时检查发生变化或首次使用的关键文件，不机械重复全量哈希、逐字节比较或大文件补传。复制的库不能称为新构建库，另一版本的源码不能用来解释冻结二进制。

框架 CUDA12.8、agent/HBF 私有 CUDA13 编译域保持各自配置；核实际加载库，而不是仅看目录名称。模型运行使用的 provider、agent、gate、pass、helper、PTX、ABI map 与 profile 必须来自同一个经过审查的运行包。

### 局部重链接的编译单元对应

独立目录部署不能只在profile中核验旧目录的头文件。实际`#include "qkv_exact_abi_adapter.hpp"`/live identity头从新源码目录解析，而既有`build_incremental_v2.py`不会自动复制这些头或添加对应`-I`；因此新setup目录须携带原同版本头，profile绑定实际被包含的副本。Li6类别适配审查在编译前发现并要求修正此问题，避免重复repair-v2初次缺头失败。该构建脚本实际重编setup和impl两成员，即便只有setup源码发生变化，也应如实记录两条编译命令。

agent同时有 `nv_attach_impl.cpp` 和 `nv_attach_impl_frida_setup.cpp`，不能互换对象角色。6511精确绑定agent `8f7ac0c2…` 的实现源码为 `77d2aa0a…`，setup源码为 `a94b2cb0…`，基准archive为 `9ebcce0a…`。新增152B→19槽适配在setup编译单元，因此应使用其真实编译命令并替换 `nv_attach_impl_frida_setup.cpp.o`，保留已有精确绑定的 `nv_attach_impl.cpp.o`。

模型ABI构建v1的脚本错误地把setup源码编译成 `nv_attach_impl.cpp.o` 并替换该成员，且起点为更早的archive；最终库缺少精确绑定逻辑，被检查拒绝，未运行GPU。独立 `BUILD_AGENT_CPU_V2.py` 已改为读取真实setup编译命令，从6511对应archive复制后仅替换setup对象。随后发现旧 `agent.version` 的 `local: *` 隐藏了新接口，v3只增加五个明确导出名称；v4再加入QKV限定的名称回退保护。实际v4及gate构建/链接、动态解析均通过，旧导出保留。

应记录每个被替换对象的真实角色、基准archive和版本导出脚本。单纯“编译、链接成功”或某字符串存在不能证明组合正确；在对象内可见的函数也不等于 `dlsym(RTLD_DEFAULT, ...)` 可见。

当前实际编译器角色也不同：agent沿基准Ninja图使用GCC13，gate/provider局部构建使用GCC15。可迁移构建入口须核对实际命令里的编译器，而非仅在收据写一个统一编译器名称；profile明确分列角色。CUDA12.8 CUPTI provider与CUDA13 agent/HBF依赖边界保持不变。

增量重链接还要区分两个archive身份：Ninja原图中的archive路径，与复制来承载已验证修改的基准archive可能不同。实际link图出现同一图archive三次，其中有绝对和相对两种写法；须按明确的图archive身份替换全部三处，而不是把输入基准archive路径当作原图路径。异路径构建v1在链接前因这一角色混淆被计数检查拒绝，保留失败；修复要显式记录两种角色并保持精确三处核对，不能放宽为任意同名文件。

### 模型开关与接口组合

既有 `BPFTIME_CUDA_EXACT_BIND_ONLY=1` 仍表示全局禁止名称回退，不能悄悄改成只影响QKV。组合运行使用该开关为0、`HBFSIM_QKV_RECOVERED_ABI_V1=1`：新impl只阻止精确QKV入口的名称回退，旧98内核保留原行为；QKV必须通过实时身份查询和原句柄精确绑定。配套Python插件另由 `HBFSIM_QKV_MODEL_PLUGIN_V1=1` 启用。

五个agent接口为 `bpftime_nv_qkv_select_weight_storage_v1(CUdeviceptr,size_t)`、`bpftime_nv_qkv_end_selected_call_v1()`、`bpftime_nv_qkv_pin_bound_identity_v1(CUfunction)`、`bpftime_nv_qkv_is_exact_original_v1(CUfunction)` 和 `bpftime_nv_qkv_exact_aggregate_launch_v1`。前两个必须在实际模型执行的同一线程成对调用；end返回0才证明恰好一次选中调用成功，早期拒绝也必须使该scope失败。aggregate入口及gate的 `hbfsim_qkv_converted_launch_v1` 使用 `cuLaunchKernel` 对应的函数、三维grid/block、动态共享字节、stream、kernelParams、extra参数顺序。前者接收原152B aggregate，后者只接收已转换19槽；不能互换。

模型启动器清理并重建环境后，还必须显式传入以下四项；只在父进程设置会被清除。`HBFSIM_QKV_ABI_MAP_SHA256` 绑定ABI map `20754dd201073fb033f724c0a61ee0177b39eb2c920807c870e5825136096ed9`；`HBFSIM_QKV_STAGED_PTX_SHA256` 绑定staged PTX `7219c1e8f58fdca32bc520a3a8ccdb1055aaccc33d12ff7935d7ef7baf786d67`。`HBFSIM_QKV_SOURCE_PTX_PATH` 必须指向原始恢复PTX（内容SHA `6db07471…`），`HBFSIM_QKV_STAGED_PTX_PATH` 指向本次stage内变换后PTX（内容SHA `7219c1e8…`）。两条路径不可互换；stage文件名仍可能是原始PTX的SHA。组合包审查发现四项曾全部漏传，尚未启动GPU即已要求修复；最终准入要检查实际worker环境而非只看config字段。

模型插件使用task-local `vllm.general_plugins` 入口，通过worker的 `PYTHONPATH` 发现，另需 `HBFSIM_QKV_MODEL_RECEIPT_DIR` 指向本次新建目录。真实storage注册完成后才安装layer0 QKV包装；关联单请求prefill/decode，原生参考与候选各自产生独立输出，只将候选输出交给后续层。CPU插件发现与模拟边界通过不等于真实模型验证。验收将插件weight地址与本次registration直接连接；CUDA默认stream句柄0合法，不能用非零检查误拒绝。

## 扩展权重时的接入验收

用户要求全部原未覆盖类别接入后做一次全覆盖运行。每类记录`LIFT_OUTPUT_PASS`、`INSTRUMENTED_KERNEL_PASS`及`MODEL_CONNECTED_PASS`，只有真实模型消费者的精确函数、参数/地址、输出和服务闭合才能进入覆盖表。6603已满足layer0选中decode QKV的第三层；其它层/投影/router/lm_head不能仅因名称或形状接近而继承此状态。第一层已通过、第三层失败时直接定位入口/ABI/绑定/生命周期/服务，避免反复试跑未变化的仅恢复路径。具体扩展清单见[全部权重扩展](all-weights-expansion.md)。

## 错误诊断与修复入口

6611独立候选使用 `task/qkv-model-dispatch-repair-v2/build-agent-v2/libbpftime-agent.so`（de11e670…）。实际CPU构建收据绑定profile-v3 7a1bd13a…、impl dfabe82c…及setup 50154ad8…；原缺头文件构建失败保留，两个既有头文件同版本补齐后编译/链接通过。该候选额外从实际libcuda句柄解析普通cuLaunchKernel，记录全局入口与真实入口各自地址/DSO；若地址相同则复用已保存trampoline，否则为QKV设置独立真实Driver hook和trampoline。非QKV直接调用该原始trampoline，已转换QKV也使用它完成最终发射，避免再次进入gate或重复转换。旧全局hook保留。此为host入口修复，PTX/pass/helper未变；CPU通过不证明6610根因或GPU修复成功。

6611运行包 `task/qkv-real-route-v2/` 绑定上述agent，provider仍b495、gate94fa、corea3db、pass0ab及原短harness141f；使用新RUN/epoch，保留6610。该短运行实际PASS并由root验原件：worker0、输出12,288B零差异、NATIVE_UNSELECTED及PATCHED_SELECTED恰当关联原/新句柄，真实provider callback同OS线程，19槽coverage匹配实际weight地址，目标模块及访问/服务闭合。首次live maps含这些组件实际路径。target过滤runtime未含安装地址行，不把缺失日志补推为6610历史hook的具体地址；完整模型回归仍单独验收。

6610短真实调用仍未消费selected scope。原件已明确普通`cuLaunchKernel`/CBID307/同一OS线程，不能再将该次失败归于Ex/PTSZ或跨线程。需核具体入口地址及所属模块：全局同名符号、gate预加载导出、真实libcuda导出及Driver查询返回指针并不因名字相同而自动证明走同一hook。CUDA12.8的[Driver Entry Point Access文档](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-driver-api/group__CUDA__DRIVER__ENTRY__POINT.html)规定返回地址由请求版本及flags选择，函数指针类型应匹配对应ABI。该文档支持核查这些区别，不能代替本次实际调用来源证据；当前正在定位，不把cuGetProcAddress绕过写成已证根因。

6602后的独立host修复将opt-in exact QKV的普通Frida入口送入aggregate适配器；未arm仍沿原生分支。转换后的原函数句柄与19槽数组由同线程RAII作用域绑定，按Armed→DirectEntered→CommonEntered只消费一次，离开即清除。只有该已核身份/ABI的局部桥获得strict检查，全局partial及旧98行为保持。选中的Ex入口明确拒绝，不丢弃其属性后强转普通API。provider新增exact QKV的API、CBID和OS线程字段；Python线程标识与OS线程号分别记录，不能混用。

短验证通过原函数与patched函数句柄关联实际callback、选中决策、权重地址和服务计数。恢复PTX的函数可能属于另一个image，不能要求它仍属于原fatbin；coverage检查日志行数也不是实际kernel发射次数。最终结论仍须实际GPU终态、独立输出比较和地址服务闭合，不能从上述源码结构推定运行通过。

- `QKV selected dispatch failed: -1`（6602）：plugin已进入真实decode并保存输入，candidate Python forward正常返回，但agent选中scope未以成功状态收尾。此码不是CUDA错误码，也不能直接解释成恢复PTX数值失败。原件显示两次原exact QKV函数的provider callback、无QKV ABI decision文件、QKV模块modeled计数0。冻结代码把aggregate入口放在gate `driver_launch`，Frida替换后的Driver入口另有调用链，不能用ELF preload顺序证明该入口被经过。同时实际旧98为`HBFSIM_INSTRUMENTATION_POLICY=partial`，而`strict_launch_bridge_enabled()`只认全局strict，新增aggregate和转换桥又依赖它；只补路由仍会被该策略条件拒绝。独立host修复进行中，不能把全局改strict破坏旧路径，也不能删选中调用成功检查来放行原生输出。
- 自定义10：检查精确 provenance join 数量。6305 为路径不一致导致0匹配；不放宽正则、删断言或清零计数。独立元数据修复及验证见 `../task/copied-provenance-relocation-fix-v1/`。
- 自定义14：不能仅按数值认定 handle/generation 失效；它也可能是 `10 + bind_ptx_variant` 的内部4。6306原日志证明早期 gate 内部4，稍后相同目标绑定成功；完整代表回归通过，不额外重跑追逐已消失的早期拒绝。
- 控制器 `inner staged owner/mode/scope mismatch`：6601的配置和worker使用 `all98_plus_qkv`，旧controller v2却在 `attest_inner` 将scope写死为 `all98`，实际拒绝发生在内层启动身份核对。更新范围须同时连接plan命令、worker start收据和controller允许值；不能只更新模型选择正则。修复仅扩展明确支持的scope并与plan精确相等，保留PID/start/PGID/parent、mode、epoch、timeout、config与wrapper核对。6601属于运行控制接口失败，没有模型验证成功。
- 查询返回0：按 context、关联 token、module/parent-library、精确 image/entry 分层定位；缺少真实回调记录是未验证，不是接口成功。
- 输出/服务通过但进程139：仍记完整生命周期失败，检查退出阶段真实栈。HBF context destroy 与 CUDA context destroy 分开记录，不用 `_exit` 或信号吞掉掩盖失败。

6510 的实际退出栈已将故障定位到冻结 provider 的 `unload_library → libraries().erase(lh)`，上游为 cuBLASLt 退出时调用 `cuLibraryUnload` 触发的 CUPTI 回调。对应实际 provider 是 source `06ddf3f9…` / DSO `b1d11462…`，并非新身份接口 v2。独立修复位于 `../task/qkv-provider-exit-lifecycle-fix-v1/`，source `1b93044b…` / DSO `c97011b7…`：将该卸载路径使用的host映射表及关联状态保存至进程结束。实际源码的CPU退出顺序对照已复现原版map析构后的use-after-free，修复版正常返回。6511仅换provider的受控GPU验证已通过：12,288B输出零差异、12,582,912次/25,165,824B访问闭合、服务提交/完成126,814、所有API成功、worker0及guardclean。证据见 `../manifests/qkv-provider-exit-6511-root-review.json`。运行包绑定了新库及精确target-only preload，未保存独立的进程maps快照，不声称有此证据。此结果解决当前受控退出故障，不等于真实模型适配已验证。

回调接口必须记录状态和代码各自的有效期。当前 provider 作为进程预加载库使用，回调可达 host 状态需覆盖退出阶段的库卸载；它不是支持任意动态 `dlclose` 的插件。若未来支持提前卸载，需要另行实现所属订阅的停用、活动缓冲完成及在途回调排空协议，不能把 host 状态延寿误认为动态卸载协议已完成。

改变接口后，先运行与修改直接相关的构建和小范围接口验证；再由唯一 execution owner 在已批准运行包中验证实际调用、输出及地址/服务闭合。未改动的旧工件不机械重跑。完整旧集合回归、受控内核验证和真实模型闭环分别报告。

最终v7源码的[异路径构建入口](../task/qkv-output-source-delivery-v1/README.md)还记录了工具入口角色：giga的`cargo`与`rustc`均指向rustup代理。可对解析后的文件核身份，但执行须保留声明入口名并显式传递RUSTC；直接执行解析后的rustup路径会改变工具角色。此问题在首次构建START前修正，不虚构失败运行。实际新目录生成PTX与冻结v7相同；新CUBIN异字节且没有追加GPU验证，不替换原工件。
# 原生发现插件的进程边界（2026-09-25）

vLLM 即使关闭 V1 engine 多进程，模型架构检查仍启动 registry 子进程，并再次注册 general plugins。目标进程加载 provider 后会清理后续子进程的 LD_PRELOAD；因此插件 register 只能安装包装，不可要求子进程具有 provider 导出。实际 GPUModelRunner.load_model 入口才绑定当前 PID 的 provider、检查 ready 与输出目录并注册最终 flush；真实模型缺少 provider 必须失败。发现 v1 在此边界失败，尚未加载模型，不是 SASS/PTX 正确性失败，也不计任何新增覆盖。独立 v2 修复保持 provider 和设备算法不变。

## Giga恢复准备中的接口核验（2026-09-25，GPU待运行）

Li6真实目标进程是wrapper启动的模型子进程；controller的worker-start记录对应shell进程组leader，不能拿它的PID或maps冒充模型进程。启动采集须核精确runner命令、父子链、PID/start和同组关系，再采集目标库与筛选环境。环境中人为写入CUDA域标签不能证明实际加载库域，仍以实际maps路径为准。

Li6输出头验收源码须来自实际PYTHONPATH选中的vLLM overlay。固定CUDA的unquantized GEMM路径到torch.nn.functional.linear；logits processor只gather/crop，无float转换。因此本次模型配置50304词表/BF16的最终logits合同为[1,50304]、100608B，不能只凭模型weight dtype推导输出dtype。o_proj为[1,2048]/4096B，均要求原始独立输出证据及同请求/线程/实际storage关联。

Li7独立候选必须让回放脚本消费Li7自身symbol、CUBIN及byte-map schema；仅换命令行文件路径而保留Li6 EXPECTED_SYMBOL/旧cubin/map常量不能执行。恢复期CPU审查发现此问题，GPU尚未运行，不能归类为数值失败。

当前Li7 host候选用process-global HBFSIM_QKV_TARGET_KIND选择类别，仅准备单独router代表；不是最终Li6+Li7同一模型联合接入。最终147版本仍需精确逐类别选中与联合回归。此项不得以两次独立代表成功替代。

2026-09-25 Li7 tier-1 首次root真实启动在输出`runs/router-li7-lift-output-v1`止于启动层：worker rc1，`CUPTI correlation initialization is not ready`，guard清理成功，未到原生/候选输出。对应实际provider源码`task/router-li7-host-adapter-v1/source/provider_router_exact.cpp`的`init_correlation`先以`O_EXCL`打开correlation log，再由provider独占`mkdir(HBFSIM_PROVIDER_MODULE_DIR)`；v4 `target_entry.py`在target-only `execve`前预建该目录，使构造函数遇到`EEXIST`并提前返回。v5仅将target setup改为创建父目录，保持模块目录不存在并交给provider；CPU目录所有权测试1/1通过。`TIER1_PLAN_ROOT_v5.json`绑定更新的entry哈希及全新v2输出；旧v4和失败raw保留。此修复是CPU验证，GPU/数值/服务状态仍待root后续运行收据，不能视作`LIFT_OUTPUT_PASS`。详见`task/router-li7-host-adapter-v1/TIER1_STARTUP_DIAGNOSIS_v5.md`。


## Li6 v3 启动收据（2026-09-25）

运行包 `task/all-weights-category-representatives-v1/prepare_and_start_li6_v3.py` 使用冻结 runtime-v2 Python、实际 nominal profile 与两个精确 storage selector；日志在 run/logs，插件原件在 run/result/li6-plugin。捕获仅接受 wrapper 的唯一直接 runner 子进程，核真实 PPID/start/PGID，读取 maps/env 后再次核身份。staged PTX 文件名使用源 PTX SHA6db074…，内容 SHA7219…；二者不能混用。已实际启动6701，尚无 GPU PASS；验收器为同目录 validate_li6_v2.py，技术准入与计划保存在 preflight-v3/ 及 ROOT_REVIEWED_LI6_GPU_STAGE_v3.json。

2026-09-25 Li6第二次选中调用诊断准备：原6701的provider `driver_launch`在CUPTI_API_ENTER记录第二patched函数相关ID100982；原agent的`PATCHED_SELECTED`及strict bridge日志分别在converted调用/真实Driver调用返回后才写，均仅有第一次。原计划`CUDA_LAUNCH_BLOCKING=1`，因此第二调用无返回既可能是持续执行内核，也可能是host/回调等待，不能直接判死锁；原运行没有完成服务收据。隔离`task/li6-wait-boundary-v1/`只复制原Li6 plugin，加fsync主机阶段标记及候选前独立原生输出保存；改动会调整诊断运行的host顺序，不能与原6701比较性能。epoch6703仍保留o_proj→lm_head两selector、原agent/gate/provider/PTX，controller900s、请求780s、外部observer600s仅记录截止不杀进程。CPU检查通过，GPU未运行；实际地址/进程/模型服务仍须新运行收据，见该包`CPU_DIAGNOSTIC_PLAN.md`。

2026-09-25 Li6独立exact-head tier-1准备：`task/li6-exact-head-lift-v1/`复制原Li6 provider并只扩`lm_head-m1` case；原`qkv-m1`分支不变。实际C ABI仍为`hbfsim_provider_trace_set_case(const char*,const void*,size_t,long long)`（成功0；head错误extent/m返回自定义-7），`hbfsim_qkv_capture_write(const char*)`及`hbfsim_qkv_blas_capture_write(const char*,const char*)`（成功0，缺唯一有效捕获-2）；不要把这些自定义码当CUDA码。精确head门要求BF16 GEMM m50304/n1/k2048、A=weight、B=input、C=输出、transA/T transB/N、lda/ldb2048、ldc50304、compute68/algo99、grid12576/block16×4/shared272及原Li6 symbol/image。target-only preload先由`target_entry.py`只建provider父目录，provider构造函数独占创建modules；capture在native之后保存原始152B聚合、C prestate和native100608B输出，随后同context按旧Li6 19槽map启动未插桩recovered cubin并恢复native C。provider直接编译链接为DSO，精确命令、源码和产物见其`build-provider-v1/compile.argv.json`与`BUILD_RECEIPT.json`，无archive成员。当前仅CPU已构建/AST验证/导出检查，GPU未启动，尚无head `LIFT_OUTPUT_PASS`，更无`MODEL_CONNECTED_PASS`；冻结root计划及验证边界见该包`README.md`。

2026-09-25 单类别隔离模型包接口：`task/li6-oproj-only-v1/`在真实scheduled decode调用`RowParallelLinear.forward`时以C导出`bpftime_nv_qkv_select_weight_storage_v1(uint64_t,size_t)`选择完整8MiB storage，调用后`bpftime_nv_qkv_end_selected_call_v1()`；返回码0为接受，非0按agent自定义拒绝，不能当CUDA码。原生结果先保存，候选再调用同一原forward，保留原tuple。`task/li6-head-only-v1/`仅在真实`compute_logits`调用处选择206045184B lm_head storage，原生输出先保存，候选仍走原`compute_logits`并保留最终裁词表结果。两包均以request/thread/actual storage绑定独立输出，不修改C ABI、context或helper。head只读observer按`first-fault-compact-source-v2/src/host_service/control_layout.hpp` ABI5/header384检查daemon子进程、`--control-fd`、`--report-dir`及memfd；mode1的GPU端计数不能由host header推断队列闭合。此为CPU准备，只有实际run终态+validator可升级模型层证据。

2026-09-25 14:04UTC o_proj接口实际验收：`runs/li6-oproj-only-v1/MODEL_VALIDATION.json`对冻结单selector版本返回`MODEL_CONNECTED_PASS_OPROJ_LAYER0`。实际请求内原生/候选4096B相同，选中storage地址与registration/coverage关联，普通`cuLaunchKernel` CBID307、原/补函数、线程与模块服务闭合均匹配；controller/guard干净。该成功证明本o_proj真实调用链，不改变上述C ABI，也不证明head或两selector串接；head包仍只CPU准备。
`task/li6-li7-combined-model-v1/`新增默认关闭的单一模型插件，现仅CPU契约验证，未部署GPU。插件沿用冻结agent的`bpftime_nv_qkv_select_weight_storage_v1(uint64_t base, size_t extent)`与`bpftime_nv_qkv_end_selected_call_v1(void)`，两者返回`int`，成功码为0；它们是本项目选择/结束返回码，不按CUDA错误解释。每个实际模型子层保存原callable、验证BF16完整storage和activation后，以clone相同数值先调用原生、保存原生输出，再对真实base/extent执行select，原activation调用候选，并在`finally`执行end；候选独立输出随后与原生输出逐字节比较。QKV/o_proj/router保持原二元tuple及router bias身份，head保持原tensor返回并在`compute_logits`里执行。请求ID/OS线程/调用顺序由插件收据给出；真实CUDA入口API/CBID由provider/agent运行收据关联，不能由插件函数名推断。插件用manifest SHA固定3/45/49 scope，`prepared-v2/STAGE_JOIN_RECEIPT.json`记录Li6/Li7各自source-SHA文件名→staged内容SHA、旧98与Li7 sidecar/native-binding路径。这里的CPU检查未证明实际context、函数句柄、module绑定、动态解析或模型服务；这些须在后续独立GPU运行中验证。

该combined包新增`vllm.general_plugins`入口，CPU通过`importlib.metadata`发现且默认关闭时无需torch/vLLM导入；不是GPU加载证明。`bundle-combined-v1/BUNDLE_JOIN.json`用symlink组装已建的combined agent/provider、冻结Li7 gate与原运行库，并保存configured→resolved路径和device/inode。混合3模板保留原480s单次服务等待定时器；worker1800/outer2100/collect600/startup300/full3000是待root审查的独立有限运行预算。`root_mixed3_launcher.py`未来prepare要求真实head代表MODEL_CONNECTED_PASS、实时有限lease及配置GPU索引；启动前核PID/start/boot与所选GPU资源，启动后核目标实际映射和所选GPU占用。隔离controller/guard按配置索引检查本GPU，记录当时UUID，不将历史UUID当硬门槛；本worker组若落在其它GPU亦判错误。当前只通过CPU mock，无LIVE_PLAN、没有启动GPU。
