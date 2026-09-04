核对完成:22 条引句全部通过 check_claims 脚本比对(1 条因 PDF 双栏抽取错位改用同句更短片段后通过)。落盘原件在 `/tmp/claude-0/-root-hbfsim/871e3c1d-1655-4458-a7df-90c0d504c243/scratchpad/tmp/storage-simulator-validation/` 与 `.../tmp/hbf-collision-search/`(manifest.tsv 内有每份的 URL、SHA-256、抓取判定)。全程没有遇到 403 或人机校验页。以下按任务报告。

## 第一件:FAST 系模拟器/仿真器论文的验证章节

### (a) NVMeVirt(FAST '23)【一手核实,读的是官方 PDF 全文】

- 出处:https://www.usenix.org/system/files/fast23-kim.pdf(落盘 id=35473c9bd09d);会议页 https://www.usenix.org/conference/fast23/presentation/kim-sang-hoon。作者 Sang-Hoon Kim (Ajou), Jaehoon Shim, Euidong Lee, Seongyeop Jeong, Ilkueon Kang, Jin-Soo Kim (SNU)。
- **真值来源**:四台真实设备——Samsung 970 Pro 512GB(文中称 "SSD")、Intel P4800X Optane 350GB(称 "Optane")、Samsung KVSSD、某公司提供的 ZNS SSD 评估原型(96 MiB zone,只能按 192 KiB 写)。原句:"Samsung 970 Pro SSD and Intel P4800X SSD based on the OptaneDC persistent memory technology"。模型参数不是拟合出来的,是查表加实测填的:"The values are obtained empirically from in-house microbenchmarks and device specification documents"。
- **误差指标与数字**:对四种设备逐个配置虚拟设备,与真机跑同一组 FIO/KVBench/KVCeph 负载比带宽与延迟。原句:"performance difference is by up to 12.2%, 11.8%, 3.3%, and 3.8% for Optane, SSD, ZNS SSD, and KVSSD, respectively"(误差以上限报告,不报平均)。另外三项是定性对齐:16 KiB 请求的延迟分布曲线与 970 Pro 相似;GC 触发时的性能下跌形状相符;ZNS 上跑 RocksDB YCSB-A 的吞吐随时间波动"can model the performance changes very closely"。
- **与 FEMU 的对比**(单独一节,放在真机验证之前):4 KiB 随机写延迟的百分位分布,重复 10 次。FEMU 的run间标准差为其均值的 28.7%–39.7%,99.99 分位均值 559.3 us、标准差 462 us;结论是 FEMU 峰值性能只略快于 Optane,没法用来外推未来低延迟设备——这个论证方式(先证明自己比现有仿真器快且稳,才有资格仿真未来设备)对 HBFSim 可直接借鉴。
- **评估章组织**:开头先列 4 个要回答的问题,然后 4.1 Setup → 4.2 Emulation Quality(对 FEMU,1 个实验)→ 4.3 Emulating a Real Device Performance(真机保真度,4 个实验:全设备带宽对比、延迟分布、GC、RocksDB/ZNS)→ 4.4 Supporting Various Storage Environments(NVMe-oF target、PCI P2P DMA/GPUDirect Storage,2 个)→ 4.5 数据库案例(MariaDB 对 PostgreSQL,sysbench OLTP,改 target 带宽扫参)→ 4.6 NVMe 接口扩展案例(compound command)。顺序是:仿真质量 → 保真度 → 能力展示 → 两个"用它做研究"的案例。

### (b) Amber / SimpleSSD 2.0(MICRO '18)【一手核实,读的是 arXiv 全文】

- 出处:https://arxiv.org/pdf/1811.01544(落盘 id=6e0158a1acc1);正式版 https://dl.acm.org/doi/10.1109/MICRO.2018.00045。Gouk 等,KAIST camelab。
- **真值来源**:四台真实设备——Intel 750、Samsung 850 PRO、Samsung Z-SSD 原型、Samsung 983 DCT 原型。参数怎么来的:拆机逆向,原句 "we disassemble an Intel 750 device and reverse-engineer the"(后接固件参数提取)。验证前把盘全部顺序写满进入稳态(STEADY-STATE)再测——这个前置条件他们明写。
- **误差指标与数字**:准确率(不是误差)按 accuracy = 1 − |real−sim|/real 报。带宽随队列深度:"the accuracy of Amber is in the range of" 72%–96%(Intel 750 写 88–93%、读 72–81%)。延迟随队列深度:"between 64% and 94% for all real devices"。块大小 4KB–1024KB 扫参:平均误差 "in still reasonable range of 6% for all sizes"(Intel 750 随机读例外,平均 14%,归因于未公开的厂商内部优化)。动机一节还给了反面基线:MQSim 对 Intel 750 稳态误差 3%–80%,其他仿真器更差。
- **评估章组织**:V. Evaluation 分 A. Methodologies → B. Validation(带宽-队列深度、延迟-队列深度、块大小扫参、over-provisioning 压力测试,共 4 组)→ C. Operating System Impacts → D. Handheld vs. General Computing(两个全系统案例研究);VI 单独一节 Simulator Comparisons and Related Work。

### (c) VSSIM(MSST '13)与 FEMU(FAST '18,OpenChannel/lightnvm 时代)【一手核实,均读全文】

VSSIM:
- 出处:https://oslab.kaist.ac.kr/wp-content/uploads/esos_files/publication/conferences/international/VSSIM_Yoo_MSST_2013.pdf(落盘 id=ba63184b576d)。
- **真值来源**:Intel X25-M 一台(10 channel、2 way),内部结构靠前人逆向研究(Yoo et al.)加厂商规格书。
- **误差指标与数字**:厂商规格顺序写 70 MB/s,VSSIM 配 900 us 编程延迟得 68.9 MB/s,摘要写成 "VSSIM models the sequential IO performance of X25M within 3% offset";实测手里 1.5 年旧盘只有 62 MB/s,于是把编程延迟从 900 调到 1100 us 去贴老化后的真机("We adjust the NAND programming delay of VSSIM from" 900 到 1100 us)——**对老化设备重标定参数**这个做法值得注意。延迟 CDF 均值差:写 5.4%、读 9%。
- 评估组织:验证只占一小节,其余是用模拟器做的设计空间研究(channel/way 扫参、用户场景负载)。

FEMU:
- 出处:https://www.usenix.org/system/files/conference/fast18/fast18-li.pdf(落盘 id=61c65d628577)。Huaicheng Li 等,芝加哥大学 + CNEX Labs。
- **真值来源**:真实 OpenChannel SSD(CNEX,由 LightNVM 管理),定位是它的替代品。
- **误差指标与数字**:摘要 "0.5-38% variance as a" drop-in replacement;误差公式明写 Error=(Lat_femu−Lat_oc)/Lat_oc。三组结果:微基准(16 线程随机读、扫 channel×plane 配置)误差 0.8–11.6%;filebench 六个负载,基础延迟模型误差 12–57%("With the basic" model),加入双寄存器 plane 与 MLC 上下页非均匀延迟的高级模型后降到 0.5–38%;残差分析:单线程单 plane 误差 0.7%,16 线程 16 plane 升到 38%,并解释为软件仿真在高并发下的排队噪声。
- 评估组织:短文,没有独立评估章,准确性结果嵌在设计章 3.2(Delay Emulation → Basic Model → Advanced OC Model,每步紧跟 Result)。**误差随负载复杂度增大而增大、且作者主动给出残差归因**,这个写法对 HBFSim 处理 164.70x 那类混合数字有参考价值。

## 第二件:撞车检索(2026-03 以来)

结论先行:**没有找到与 HBFSim 同机制(真实 GPU 执行中注入时序)的模拟器/仿真器工作;找到一篇在主张层面重叠最大的评估类工作(2608.11668,热与磨损进入 HBF 评估),和一篇承诺开源 HBF 仿真器的分析类工作(2608.13868),都属于 trace/事件驱动仿真,不做真实执行注入,也不做真实器件标定。**

用 arXiv API 枚举(`export.arxiv.org/api/query`,检索式 all:"high-bandwidth flash" OR all:"high bandwidth flash",按提交时间倒序,落盘 id=f85f55152e6d)共 10 条,2026-03 之后且不在已知相邻清单里的全部列出:

1. **arXiv:2608.11668 "HBF Sucks? A Full-Stack Characterization of High-Bandwidth Flash for KV-Centric LLM Serving"**(v1 2026-08-12,v3 2026-08-25;Zhuoran Li, Zhuohang Bian, Xin Huang, Yibo Zhao, Guangyu Sun, Youwei Zhuo;北大 pku-lemonade 组)【一手核实,读了 HTML 全文,落盘 id=372ecfa2c571】。**这是本轮找到的最接近的一篇。** 方法:"We use an extended version of TokenSim"(https://github.com/pku-lemonade/TokenSim),四条 2 小时 Qwen-Bailian 生产 trace,H100/B200 profile;**用 3D-ICE 建了 16-Hi HBF 堆叠的热模型加写磨损预算**,结论含 "3D-ICE model shows the stack hits its thermal" limit well below peak bandwidth。与 HBFSim 的重叠在主张层:温度与磨损限制 HBF 可用带宽/寿命,这正是 HBFSim 热模型一节要讲的话。差别有三,论文里可以直接用:它是 trace 驱动仿真,不在真实执行中注入;它的热模型自我声明 "The thermal model is a projection for" unreleased hardware(投影,非实测标定);它面向 KV offload 场景而非通用评估平台。它还批评 H3 "does not model the write, thermal, or endurance behavior of transient KV"——说明"热与磨损没人建模"这个空白已被人指出并部分填上,HBFSim 的相关工作一节必须引它并说清差别。**判定:定位相邻偏撞,机制不撞。**
2. **arXiv:2608.13868 "Exploring High-Bandwidth Flash for Modern LLM Inference: Opportunities and Challenges"**(2026-08-14;Dowon Son 等 8 人,含 Onur Mutlu、Jisung Park;POSTECH/ETH 系)【一手核实,读了 HTML 全文,落盘 id=09f36cff8f91】。方法:"We use an existing simulator for LLM-serving systems, called LLMSimulator",扩展点之一是 "we add an HBF-aware timing model that individually configures read and write bandwidths";并且 "We will open-source our extended LLM simulator"。**判定:定位上沾"HBF 仿真工具将开源"这一条,但时序模型只是读写带宽两个参数,无热、无标定、无真实执行;相邻。**
3. **arXiv:2608.14333 "Beyond Capacity: Scalable MoE LLM Inference via High-Bandwidth Flash with Direct GPU and HBM Paths"**(2026-08-14;Seeyeon Kim, Juhyeong Jin, Joo-Young Kim)【一手核实,读了摘要页,落盘 id=90b40ae8158e】。体系结构提案,评估用 "event-driven continuous-batching LLM serving simulator with empirically measured GPU compute latencies"(GPU 算子延迟实测、器件侧建模)。**判定:相邻。**
4. **arXiv:2608.13127 "Potential Applications of HBF in LLM Serving Systems"**(v1 2026-08-13;Yihan Yin 等)【一手核实,读了摘要页,落盘 id=046e65b4787d】。定位原句:"This report examines High-Bandwidth Flash (HBF) as a capacity-oriented extension to HBM-based serving systems",仿真分析报告。**判定:相邻。**
5. **arXiv:2608.25062 "FLINT"**(2026-08-25;Geraldo F. Oliveira 等 13 人,Mutlu 组)【一手核实,读了摘要页,落盘 id=e7559d6c211f】。"we propose FLINT, a workload-driven HBF substrate for capacity-scalable LLM inference"——硬件设计,含把刷新挪出关键路径的 phantom-plane refresh 与只读 FTL。**判定:相邻;但它处理 refresh 这一点与 HBFSim 的刷新流量回路话题相关,相关工作里值得点。**
6. **arXiv:2604.16007 "MemExplorer"**(2026-04-17)【一手核实,读了摘要页,落盘 id=924d4a56d64a】。NPU 异构内存设计空间探索,摘要页仅两处提到 HBF。**判定:弱相邻。**
7. Hot Chips 2026(2026-08)的三场工业界内容【转述,来源为报道,未抓原始讲稿】:SanDisk 教程(Anurag Agarwal / Radhakrishna Giduthuri,"No HBF products exist yet, so the talk focuses on simulations, projections",https://chipsandcheese.com/p/hot-chips-2026-applying-high-bandwidth);Oxmiq Labs 72-GPU 机架级仿真(https://www.servethehome.com/oxmiq-labs-hbf-in-ai-compute-at-hot-chips-2026/);SK hynix HBM+HBF 混合架构仿真、性能功耗比至 2.69 倍(https://www.tomshardware.com/pc-components/ssds/hot-chips-2026-high-bandwidth-flash-promises-massive-bandwidth-and-capacity-but-its-usability-is-extremely-limited-new-memory-format-strikes-a-balance-between-hbm-and-nand-flash)。均为内部仿真研究,没有发布工具。**判定:相邻。**

零命中的检索词(如实报告):"HBF simulator"/"HBF emulator"(除上述工业界仿真报道外无工具发布);"NAND in package" / "flash in package" + GPU simulation timing injection(无 2026 相关命中);"delay injection"/"latency injection" + real GPU execution + future memory device(无相关命中);HotStorage 2026 + HBF(未检到相关论文);MICRO/ISCA/ATC 2026 accepted + high-bandwidth flash(未检到,仅 H3 的 IEEE CAL letter,属已知相邻)。已知相邻清单中的 TileLens 与 HAVEN 在枚举中确认为 arXiv:2607.04031 与 arXiv:2603.01175,窗口内无新版本变化需要注意的迹象(HAVEN v1 2026-03-01,TileLens v1 2026-07-04)。

局限声明:arXiv 枚举只覆盖标题/摘要含 "high(-)bandwidth flash" 的论文;只用 "HBF" 缩写而全文不展开的论文、以及尚未放上 arXiv 的会议在投稿件,此法查不到。检索日期 2026-09-04,HBF 是热点方向(8 月单月 5 篇),下次动笔前建议按同一检索式重新枚举一次。