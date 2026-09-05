调查完成。以下为结果。

## 抓取受阻说明
- `usenix.org` 会议页对自动化访问返回 HTTP 403(被挡,人在浏览器里可打开)。所有 USENIX 官方页原文都改从 arXiv、作者主页、GitHub 或 web.archive.org 存档副本取得。
- WORT(FAST '17)PDF 取自 web.archive.org 对 USENIX 官方 PDF 的存档快照(2017 年首次收录的 camera-ready 副本;论文定稿内容不随时间变化)。

---

## 问题 1:Optane 上市前(2011–2016)持久内存论文的模拟方法

### 1a. Intel PMEP:定制微码硬件平台,按 LLC 停顿周期比例注入额外停顿

机制原句(PMFS,EuroSys '14,§4.1,一手核实,从 PDF 逐页读到;本地副本 `/root/.claude/projects/-root-hbfsim/871e3c1d-1655-4458-a7df-90c0d504c243/tool-results/webfetch-1788523440881-rnwd4i.pdf`,来源 https://community.intel.com/cipcp26785/attachments/cipcp26785/processors/6753/1/(1-2)%20System%20software%20for%20persistent%20memory%20(2014)%20(2).pdf):

> "System-level evaluation of PM software is challenging due to lack of real hardware. Publicly available simulators are either too slow and difficult to use with large workloads [36] or too simplistic and unable to model the effects of cache evictions, speculative execution, memory-level parallelism and prefetching in the CPU [10]. To enable the performance study of PM software for a range of latency and bandwidth points interesting to the emerging NVM technologies, we built a PM performance emulator: PM Emulation Platform (PMEP)."

> "PMEP partitions the available DRAM memory into emulated PM and regular volatile memory, emulates configurable latencies and bandwidth for the PM range..."

延迟注入机制:"we exploit the fact that the number of cycles that the CPU stalls waiting for data to be available on a LLC miss (C_stalled) is proportional to the actual memory access latency (L_dram). ... The CPU microcode emulates the desired PM latency (L_pm) by injecting (C_stalled * (L_pm/L_dram)) additional stall cycles for each interval."

带宽机制:"PMEP supports bandwidth throttling, by using a programmable feature in the memory controller [16] that can limit the maximum number of DDR transactions per usec on a per-DIMM basis. Maximum sustained PM bandwidth (B_pm) for the entire platform is set to 9.5GB/s, about 8x lower than available DRAM bandwidth on the unmodified system."

参数交代方式:默认单点 + 声明可变。"For the evaluations in the paper, L_pm is always set at 300ns (per Table 1), unless specified otherwise. Latency to local DRAM memory is 90ns."(一手核实)

### 1b. NOVA(FAST '16)用 PMEP:两点式取值,覆盖快慢两端

一手核实(PDF 逐页读到,§5.1;本地副本 `.../tool-results/webfetch-1788523444852-lddm9g.pdf`,来源 https://cseweb.ucsd.edu/~swanson/papers/FAST2016NOVA.pdf):

> "To emulate different types of NVMM and study their effects on NVMM file systems, we use the Intel Persistent Memory Emulation Platform (PMEP) [21]. ... PMEP supports configurable latencies and bandwidth for the emulated NVMM, allowing us to explore NOVA's performance on a variety of future memory technologies."

> "To emulate different NVMM technologies, we choose two configurations for PMEP's memory emulation system (Table 1): For STT-RAM we use the same read latency and bandwidth as DRAM, and configure PCOMMIT to take 200 ns; For PCM we use 300 ns for the read latency and reduce the write bandwidth to 1/8th of DRAM, and PCOMMIT takes 500 ns."

Table 1 标题原句:"STT-RAM emulates fast NVMs that have access latency and bandwidth close to DRAM, and PCM emulates NVMs that are slower than DRAM." —— 即不猜一个"正确值",而是用两个命名档位把未知器件夹在快慢两端之间,评估问题之一明写 "How do underlying NVMM characteristics affect NOVA performance?"。

### 1c. Mnemosyne(ASPLOS '11):软件 TSC 自旋延迟 + 明说参数是投影 + 三点延迟扫描

一手核实(PDF 逐页读到,§6.1 与 §6.4;本地副本 `.../tool-results/webfetch-1788523485248-nzgjiu.pdf`,来源 https://pages.cs.wisc.edu/~swift/papers/asplos11_mnemosyne.pdf):

承认参数不可知:

> "Because real memory systems based on PCM are not available, we developed a simple performance emulator based on DRAM to evaluate performance of our system. There are a wide variety of projections for PCM's performance, and the specific design of the memory system can have a great impact on performance [39]. So, we limit our emulation to the most important aspect of performance: slow writes."

机制与自校准:"we introduce a delay after each write into the hardware access macros. ... In all cases we implement the delay with a loop that reads the processor's timestamp counter (TSC) in each iteration. ... In calibration tests, we found that inserted delays are at least equal to the target delay, and that our bandwidth model is accurate to within 4%."

单点默认值 + 出处:"All tests add 150 ns of extra latency and are limited to 4GB/s of write bandwidth unless otherwise noted. We estimated write bandwidth based on projections provided by Numonyx [21]."

敏感性扫描(§6.4 Sensitivity to Memory Performance):"Figure 7 shows the relative performance of Mnemosyne over Berkeley DB as a function of data element size for three different latencies: 150 ns, 1000 ns, and 2000 ns." 结论写成条件式:"Thus, Mnemosyne is most useful when SCM latencies are close to those of DRAM... For larger latencies, SCM may best be treated as a disk and accessed through the file system."

### 1d. NV-Heaps(ASPLOS '11):Pin 内存层次模拟 + 参数取自已发表模型与"业界讨论" + 校准数字

一手核实(pdftotext 提取全文后按行读到;本地文本 `/tmp/claude-0/-root-hbfsim/871e3c1d-1655-4458-a7df-90c0d504c243/scratchpad/nvheaps.txt`,来源 https://goto.ucsd.edu/~rjhala/papers/nvheaps.pdf):

> "NV-heaps aim to support systems with many gigabytes of high-performance non-volatile memory, but mature products based on those memories will take several years to appear. In the meantime, we use two emulation systems to run applications for many billions of instructions while simulating the performance impact of using advanced non-volatile memories."

> "For PCM we use the performance model from [35] which gives a PCM read time of 67 ns and a write time of 215 ns. We model STTM performance (29 ns reads and 95 ns writes) based on [59] and discussion with industry."

自身校准:"To calibrate our system we used a simple program that empirically determines the last-level cache miss latency. We ran the program with the simulated PCM and STTM arrays and its estimates matched our target latencies to within 10%." 块设备模拟一侧 "the emulation is accurate to within about 2%"。

### 1e. Quartz(Middleware '15):纯商品硬件——性能计数器 + epoch 末注入自旋延迟 + 内存控制器热控寄存器限带宽

一手核实(pdftotext 提取论文全文;本地文本 `.../scratchpad/quartz.txt`,来源 http://www.jahrhundert.net/papers/middleware2015.pdf;GitHub README 原文另经 curl 直接取得 https://raw.githubusercontent.com/HewlettPackard/quartz/master/README.md):

> "We emulate bandwidth by utilizing the DRAM thermal control feature available in commodity processors [6, 24] to limit (throttle) available memory bandwidth ... The key idea is to dynamically inject software created delays to account for higher PM latency at boundaries of specially defined time intervals, called epochs. We derive delays based on an analytic model that leverages hardware performance counters to achieve low overhead and good accuracy."

延迟公式用 LDM_STALL(处理器等内存的停顿周期计数)修正内存级并行:"Memory stall cycles (denoted as LDM_STALL) naturally capture memory-level parallelism"。

模拟器自身的验证方式(直接回应"保真度如何向审稿人交代"):

> "The accuracy of the proposed approach is validated by running these programs both on our emulation platform and a multi-socket (NUMA) machine that can support a range of memory latencies. We show that Quartz can emulate a range of performance characteristics with low overhead and good accuracy (with emulation errors 0.2% - 9%)."

即:用一台真实存在、延迟确实更高的参照机器(跨 NUMA 访问)当"真值",验证注入机制本身复现已知延迟的能力,再外推到不存在的器件。

### 1f. Quartz 被 FAST 论文使用的实例:WORT(FAST '17)

一手核实(web.archive.org 2017 年存档的 USENIX camera-ready PDF,pdftotext 提取;本地文本 `.../scratchpad/wort.txt`;原始地址 https://www.usenix.org/system/files/conference/fast17/fast17-lee.pdf,自动化访问被挡,经存档取得):

> "To observe the effect of PM latency on the performance of the data structure, we emulate PM latency using Quartz [1, 18], a DRAM-based PM performance emulator."

> "Since write memory latency emulation is not yet supported in the publicly available Quartz implementation [1], we emulate PM write latency by introducing an additional delay after each clflush and mfence instructions, as in previous studies [7, 9, 19]."

参数交代:多档扫描。"As PM read and write latency is generally expected to be comparable or slightly worse than those of DRAM, we set the latency to various values as shown in the figure."(默认 DRAM 100ns,横轴扫多个延迟值)

PMEP 使用者中经我一手核实的 FAST 论文是 NOVA(FAST '16);PMFS(EuroSys '14)是平台的出处论文。其他声称使用者未逐一核对,不列。

---

## 问题 2:FAST '20 Empirical Guide——真器件与此前模拟假设的差别

以下全部一手核实(arXiv v1 PDF 逐页读到,arXiv:1908.03583,本地副本 `.../tool-results/webfetch-1788523544443-n29rbj.pdf`;发表版在 usenix.org,自动化访问被挡)。作者 Jian Yang、Juno Kim、Morteza Hoseinzadeh、Joseph Izraelevitz、Steven Swanson,UCSD。

此前十年的方法被归为四类(p.2):

> "The widely expressed expectation was that NVDIMMs would have behavior that was broadly similar to DRAM-based DIMMs but with lower performance (i.e., higher latency and lower bandwidth). These assumptions are reflected in the methodology that research studies used to emulate NVDIMMs, which include specialized hardware platforms [21], software emulation mechanisms [49, 53, 12, 36, 41], exploiting NUMA effects [20, 19, 31], and simply pretending DRAM is persistent [44, 9, 8]."

真器件差在哪(p.1–2):

> "The data we have collected demonstrate that many of the assumptions that researchers have made about how NVDIMMs would behave and perform are incorrect."

> "We have found the actual behavior of 3D XPoint DIMMs to be more complicated and nuanced than the 'slower, persistent DRAM' label would suggest. 3D XPoint DIMM performance is much more strongly dependent on access size, access type (read vs. write), pattern, and degree of concurrency than DRAM performance."

具体机制差异(§2.1.1):器件内部介质访问粒度是 256 字节("the 3D XPoint physical media access granularity is 256 bytes (referred as XPLine in this paper), the XPController will translate smaller requests into larger 256-byte accesses, causing write amplification as small stores become read-modify-write operations.")——由此得出的四条最佳实践(§5,原文加粗列表):1. Avoid random accesses smaller than < 256 B;2. Use non-temporal stores when possible for large transfers, and control of cache evictions;3. Limit the number of concurrent threads accessing a 3D XPoint DIMM;4. Avoid NUMA accesses (especially read-modify-write sequences)。

对模拟方法论的判决原句:

> "We find that all of these emulation methodologies are inaccurate, suggesting that it is unwise to assume that previously published results based on those methodologies reflect performance on real hardware."(p.2)

> 图 7 标题:"The emulation mechanisms used to evaluate many projects do not accurately capture the complexities of 3D XPoint performance."

结论被反转的实证(§4.2,RocksDB 案例):

> "The study used DRAM as a stand-in for 3D XPoint, and found that fine-grained persistence offered 19% better performance. We replicated these experiments on real 3D XPoint media. ... With real 3D XPoint, the result is the opposite: FLEX performs better than fine-grained persistence by 10%."

教训原话(§4.2 开头与 §4.3):

> "In this section, we revisit prior art in NVM programming in order to demonstrate that emulation is generally insufficient to capture the performance of 3D XPoint DIMMs and that future work should be validated on real hardware."

> "First, the differences between emulated and real persistent memory are large enough to alter the conclusions that researchers might draw from their experiments. Second, there does not appear to be any simple relationship between emulated results and results on real 3D XPoint hardware."

> "We conclude that basing future designs (and design decisions) on the results of emulation-based studies may cause system designers to overlook superior options. It also demonstrates the value of re-evaluating previously considered (and perhaps discarded) ideas on new hardware."

顺带一条与本项目直接相关:§5.1.2 点名 NOVA——"The original NOVA studies used emulated NVMM for their evaluations, so NOVA has not been tuned for 3D XPoint."(NOVA 当年的 PMEP 方法没有让它躲过真器件的 256B 粒度问题。)

---

## 问题 3:CXL 硬件稀缺期(2022–2023)的跨 NUMA 模拟

### Pond(ASPLOS '23)

一手核实(pdftotext 提取 arXiv PDF 全文;本地文本 `.../scratchpad/pond.txt`,来源 https://arxiv.org/pdf/2203.00241):

机制:

> "First, we emulate a single-socket system with a CXL pool on a two-socket server by disabling all cores in one socket, while keeping its memory accessible from the other socket. This memory mimics the pool."

延迟取值的推导与两点扫描:

> "Further, analysis of CXL topologies lead us to estimate that CXL will add 70-90ns to access latencies over same-NUMA-node DRAM with a pool size of 8-16 sockets, and add more than 180ns for rack-scale pooling."

> "we evaluate 158 workloads under two scenarios of emulated CXL access latencies: 182% and 222% increase in memory [latency]"(图 4 注明具体值:"Local: 78ns, remote: 142ns (182%)" 与 "Local: 115ns, remote: 255ns (222%)")

即:先用拓扑分析推出目标器件的延迟区间,再选两个可在真实 NUMA 硬件上实现的档位把区间夹住,158 个负载全量跑,报告分布("43% and 37% of 158 workloads are within 5% of the performance on same-NUMA-node DRAM ... more than 21% of workloads suffer a performance loss above 25%")。

### TPP(ASPLOS '23,Meta)

一手核实(pdftotext 提取 arXiv PDF 全文;本地文本 `.../scratchpad/tpp.txt`,来源 https://arxiv.org/pdf/2206.02878):

论证"NUMA 模拟成立"的依据:

> "From a software perspective, CXL-Memory appears to a system as a CPU-less NUMA node where its memory characteristics (e.g., bandwidth, capacity, generation, technology, etc.) are independent of the memory directly attached to the CPU."

> "CXL-Memory access latency is also similar to the NUMA access latency. CXL adds around 50-100 nanoseconds of extra latency over normal DRAM access."

实验设置,包括"功能验证用真 FPGA 卡、性能评估用 NUMA 模拟、并向 CPU 厂商求证"的三层交代:

> "We deploy a number of pre-production x86 CPUs with FPGA-based CXL-Memory expansion card that support CXL 1.1 specification. ... Although our current FPGA-based CXL cards have around 250ns higher latency than our eventual target, we use them for the functional validation. We have confirmation from two major x86 CPU vendors that the access latency to CXL-Memory is similar to the remote latency on a dual-socket system. For performance evaluation, we primarily use dual-socket systems and configure them to mimic our target CXL-enabled system's characteristics (one memory node with all active CPU cores and one CPU-less memory node) according to the guidance of our CPU vendors."

### 事后清算:MICRO '23 "Demystifying CXL Memory"

一手核实(arXiv abs 页 HTML 直接抓取并解析,arXiv:2303.15375 v4):

> "However, since CXL memory devices have not been widely available, they have been emulated using DDR memory in a remote NUMA node."

> "This reveals important differences between emulated CXL memory and true CXL memory, some of which will compel researchers to revisit the analyses and proposals from recent work."

即 CXL 一代重演了 Optane 一代的剧本:NUMA 模拟支撑了 Pond/TPP 这批被顶会接收的工作,真器件到手后又有论文专门指出模拟与真实的偏差。

---

## 问题 4:关于"模拟不存在的硬件如何验证保真度"的方法论段落

系统会议没有一篇专门的"方法论宣言"论文(在本次检索范围内未发现);方法论散在四处,各自可引:

1. **用已存在的可变延迟硬件当真值,验证注入机制本身。** Quartz 摘要原句(见 1e,一手核实):在 NUMA 机器上跑同一组程序,证明模拟器能以 0.2%–9% 误差复现已知的更高延迟,再外推到不存在的器件。这是"机制保真"与"参数正确"分离的写法:机制可验证,参数明说是投影。
2. **模拟器自校准并报告误差数字。** Mnemosyne 4% 以内(带宽模型)、NV-Heaps 10% 以内(延迟)与 2% 以内(块设备),都在正文给出(见 1c、1d,一手核实)。
3. **事后验证类论文给出的反面教材。** FAST '20 Empirical Guide §4.2–4.3 与 MICRO '23 Demystifying CXL(见问题 2、3,一手核实):失真主要不在参数取值错,而在"器件就是慢一点的 DRAM"这个结构性假设错——真实器件有自己的内部粒度、缓冲、不对称与并发行为。
4. **同组参照:CXLMemSim(HPDC '26)。** 转述自本地判读文档 `/root/hbfsim/34-CXLMemSim与合作者论文的启示-2026-08-11.md`(第 74、220、222 行):它同样以 "is not yet widely available for empirical evaluation" 开场,用真实 CXL 硬件加 gem5 当两个参照,报每种拓扑准确率 86.81%–92.15%、应用级平均预测误差 7.3%–12.8%。

---

## 对 HBF 模拟这篇 FAST 投稿可借用的论证套路

1. **开门见山承认器件不存在,并给一句"为什么现在就要研究"。** Mnemosyne 与 NV-Heaps 都在方法一节第一句写明真硬件不存在("Because real memory systems based on PCM are not available..."/"mature products based on those memories will take several years to appear"),没有一篇因此被挡在顶会外。HBF 的对应句现成:规范 2026-08-03 才发布、首批器件 2027 年初送样。
2. **把"机制保真"与"参数正确"分开验证,各给一个数字。** 机制一侧学 Quartz:用一个真实存在、行为可实测的参照路径验证注入机制能复现已知时序(HBFSim 的 CD8P 实测标定路径正是这个角色,且比 Quartz 的 NUMA 参照更强——它是端到端实测曲线);参数一侧学 Mnemosyne/NV-Heaps:每个取值给出处(OCP 规范条文、厂商投影、实测锚点),并报告校准误差的数字上界。留几个没参与拟合的实测点做交叉验证(这一条同时消掉项目已知弱点第 1 条,见 `/root/hbfsim/CLAUDE.md` 第 5 节)。
3. **参数不确定时用两点夹逼或多点扫描,把结论写成条件式,不赌单点。** NOVA 用 STT-RAM/PCM 两档夹住快慢两端;Mnemosyne 扫 150/1000/2000 ns 并明写"延迟接近 DRAM 时才有优势";Pond 用 182%/222% 两档并报告 158 个负载的敏感度分布;WORT 扫多档延迟。HBF 的三个合成 profile(conservative/nominal/aggressive)天然是这个结构,但要按 OCP 公布数字重新锚定(项目已知弱点第 8 条)。
4. **主动回答"真器件到来后,这篇论文哪些结论会先倒"。** FAST '20 的判决("all of these emulation methodologies are inaccurate")和 MICRO '23 的重演说明,审稿人现在预期模拟类论文自己列出假设清单:哪些量是实测的、哪些是外推的、结论对哪个假设最敏感。HBFSim 的温度维度可以反过来当动机用:Optane 一代模拟失真的根源正是"器件行为比'慢一点的 DRAM'复杂"——访问粒度、不对称、并发、热——其中热致行为变化是 HBFSim 实测到的(冷机 379.117 对热机 348.427 TFLOP/s,-8.10%),这是把"我们为什么把温度放进模型"接到已发表教训上的现成链条。
5. **模拟平台随论文开源,机制写到可复算。** Quartz、Pond(原文明写 "Pond artifacts are open-sourced at https://github.com/vtess/Pond")都公开了模拟层;WORT 因 Quartz 公开版缺写延迟功能而能明说自己怎么补(clflush/mfence 后加延迟)。反例是私有定制平台:PMEP 需要特殊微码与固件,他人无法复现,后续社区转向了 Quartz 这类商品硬件方案。HBFSim 的注入机制(PTX 改写、四类时间分开统计、拒绝不支持指令)属于可开源、可复算的一侧,对 FAST 的 artifact evaluation 直接有利。

**来源汇总(URL)**:https://arxiv.org/abs/1908.03583 、https://arxiv.org/pdf/1908.03583 、https://cseweb.ucsd.edu/~swanson/papers/FAST2016NOVA.pdf 、https://pages.cs.wisc.edu/~swift/papers/asplos11_mnemosyne.pdf 、https://goto.ucsd.edu/~rjhala/papers/nvheaps.pdf 、http://www.jahrhundert.net/papers/middleware2015.pdf 、https://raw.githubusercontent.com/HewlettPackard/quartz/master/README.md 、https://arxiv.org/pdf/2203.00241 、https://arxiv.org/pdf/2206.02878 、https://arxiv.org/abs/2303.15375 、web.archive.org 存档的 https://www.usenix.org/system/files/conference/fast17/fast17-lee.pdf(2017 年快照)、https://community.intel.com/cipcp26785/attachments/cipcp26785/processors/6753/1/(1-2)%20System%20software%20for%20persistent%20memory%20(2014)%20(2).pdf 。本地文件:上文各条标注的 tool-results 与 scratchpad 路径,及 `/root/hbfsim/34-CXLMemSim与合作者论文的启示-2026-08-11.md`。除 CXLMemSim 一条标为转述外,所有引句均一手核实(逐页读 PDF 或 pdftotext 提取原文后按行读到)。