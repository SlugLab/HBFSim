# 《HBF Sucks!》这篇论文的核实与已发现问题

**这份文档给两条线共同使用:HBFSim 模拟器论文,以及 HBF 边缘部署论文。** 两条线在正文、大纲、投稿材料里引用《HBF Sucks!》时,以这份文档记录的核实结果和第六节的引用规矩为准,不另起一份平行的核实记录。文档同时供另一个正在写 HBF 模拟器论文的模型使用,因此每一条指控都带核实等级标记,没有标记的句子一律视为未核实,不得下传。

## 核实等级的三种标记,含义先说清楚

- **【本轮核过】** 指本轮打开本地原文 `docs/ref_article/li2026-hbf-characterization-kv-cache.txt` 逐字读到的句子或数字,附录第一节给出该句在这份文本文件里的行号。原件是 `docs/ref_article/li2026-hbf-characterization-kv-cache.pdf`。
- **【第三方提出,未核】** 指出自用户提供的第三方分析(一次 ChatGPT 对话的分享页面,标题《论文贡献与流量评估》),本项目没有独立打开对应的源头去核实。引用前必须先核,核实办法写在附录第三节。
- **【推算】** 指由已核实的数字算出来的结果,不是原文印出来的数。凡是标这个记号的地方,算式一律写在同一句里。

## 整篇结论

**《HBF Sucks!》的核心测量是可信的,不可信的是把那个头条倍数当成 HBF 这种器件本身的判决。** 这篇论文最硬的一段是介质敏感度实验:把 HBF 的读写延迟从 30/300 微秒改善到 8/80 微秒(3.75 倍),端到端平均延迟只动了 0.75% 与 0.88%,由此反解出真正暴露在二级层输入输出上的时间占比约 1%(【本轮核过】)。这一段是单变量的,结论站得住。而那个被写进摘要的「平均端到端延迟上升 2 到 5.5 倍」不是单变量对照——论文自己在正文里写明了这一点,原句是 `Two comparisons locate it, and neither is a single-variable control.`(【本轮核过】)。

本轮一共整理出三处方法学缺口和另外八处此前独立核出的问题。三处缺口里,第一处(头条对比不是单变量)和第二处(模型权重那一端是解析处理的,不是测量出来的)本轮已在原文里逐字核到;第三处(混合专家模型的权重放不进近端层容量)的算术本轮已复核,但论文里对应的说明本轮没有找到——找不到不等于没有,附录第四节把这一条列为待核。

**这篇论文有两件事必须替它说清楚,否则我们自己引用时会失准。** 一,它的结论不是「HBF 这种器件不行」,而是「把 HBF 当成一块更快的固态盘、拿去承接活不过一次请求的键值缓存,这个部署方式不行」——摘要末句原文是 `The device is fine; the drop-in deployment is not.`(【本轮核过】)。二,它对自己的器件参数来源是有声明的,原文写 `HBF remains projected hardware, so its service parameters are sourced or swept, not claimed as measured silicon.`(【本轮核过】)。把这篇论文说成「不严谨」是不成立的批评,能成立的批评只有下面逐条列出的那些。

## 第一节 《HBF Sucks!》做了什么

**先给这一节的结论:它做的是一次配置层面的对照实验,问的是「把固态盘换成 HBF,大模型服务会不会变快」,答案是不会,而且明显变差。** 下面的事实全部标【本轮核过】,除非另有标注。

它针对的现状是这样的:Mooncake 这一类大模型服务系统会把可以复用的键值缓存(KV cache,大模型生成每个词元时都要读的中间状态,前缀相同的请求之间可以复用)从 GPU 的高带宽内存搬到外部固态盘上,腾出显存。既然 HBF 的读延迟比固态盘低、带宽比固态盘高,直觉上把固态盘直接换成 HBF 应该更快。《HBF Sucks!》把这个直觉写成摘要的头两句去否定:`A faster storage device should make serving faster. We find the opposite.`

方法:扩展 TokenSim(一个离散事件的大模型服务模拟器,按事件推进时间、不真的跑模型),加入 HBF 这一层、键值缓存的存取事件、逐层的读写字节计数器、排队与服务等级目标(SLO,对请求延迟的承诺上限)的统计,然后回放四条完整的两小时阿里云 Qwen-Bailian 生产轨迹。模型五个:Qwen3-4B、Qwen3-32B、DeepSeek-V3.2(685B 总参数、37B 激活参数)、GLM-5.2(753B/40B)、Kimi-K2.7-Code(1.1T/32B),后三个是混合专家模型(MoE,每次只激活一小部分专家网络参与计算,总参数远大于激活参数)。平台两套:八卡 H100 与八卡 B200。论文对自己扩展的部分列出了回归检查项:块与字节守恒、存取顺序、确定性回放、逐层计数器。

它比较的三种配置是这样的。固态盘基线:每张 GPU 96 GB、3.0 TB/s 的高带宽内存,加一个外部固态盘池。HBF-1:近端层从高带宽内存退化成 12 片 GDDR7、合计 48 GB。HBF-2:近端层是 3 堆高带宽内存、合计 48 GB,再加 HBF。

**它的贡献里最有价值的一块是把「HBF 什么时候值得用」归纳成三个必要条件。** 条件一(论文记作 C1):远端层的读必须真的落在请求的关键路径上。条件二(C2):每写一次要换回足够多次的读。条件三(C3):交付带宽要可持续,也就是能长时间维持,不被热限制或别的因素压下去。这三条是可证伪的框架,值得正面引用。

主要结果两组。第一组是介质敏感度:把 HBF 的读写延迟从 30/300 微秒改善到 8/80 微秒(3.75 倍),端到端平均延迟只变化 0.75%(HBF-1)与 0.88%(HBF-2),反解出暴露在二级层输入输出上的时间占比约 1%。第二组是完整架构对比:平均端到端延迟上升 2 到 5.5 倍,能满足服务等级目标的最大吞吐下降 1.1 到 2.7 倍。

## 第二节 三处方法学缺口

**先给这一节的结论:三处缺口都不影响「换成 HBF 之后服务变慢」这个观察本身,影响的是「慢在哪一部分」这个问题能不能从论文里读出来。** 三处都读不出来,原因分别是对照设计、建模边界、容量记账。

### 缺口一:头条那个 2 到 5.5 倍不是单变量对照,论文自己承认了

【本轮核过】原文逐字如下(所在行号见附录第一节):

> `Two comparisons locate it, and neither is a single-variable control. The HBF/SSD pair changes near-tier capacity and bandwidth together, halving both (96 to 48 GB and 3.0 to 1.5 TB/s on H100), so it prices the package trade without separating its two parts.`

含义:HBF 与固态盘这一对比较里,近端层的容量和带宽是一起砍半的(96 GB 降到 48 GB,同时 3.0 TB/s 降到 1.5 TB/s),所以那个 2 到 5.5 倍衡量的是整包交易的代价,无法拆成「容量损失贡献了多少、带宽损失贡献了多少」。

【本轮核过】同一段还写了另一半对照的结果:

> `The HBF-1/HBF-2 pair instead holds near-tier capacity fixed, and HBF-1 is 16–64% slower end to end despite carrying twice the flash capacity and bandwidth, so a strictly better flash tier does not recover the loss.`

也就是说,把近端层容量固定住之后,HBF-1 尽管带着两倍的闪存容量和带宽,端到端仍然慢 16% 到 64%;论文据此把变慢的原因归到近端层——近端层决定服务器一次能同时准入多少请求,原句是 `What the swap degrades is the near tier that governs how many requests the server admits at once.`

**这一条对我们两条线的含义是:引用那个 2 到 5.5 倍时必须同时说明它不是单变量对照,否则同一条攻击会原样打到我们自己身上。** 论文自己把话说在前面了,我们转述时把限定丢掉,等于替它制造了一个它没有主张的强结论,审稿人一查原文就是我们失分。

### 缺口二:模型权重那一端是解析处理的,不是测量出来的

【本轮核过】原文逐字如下:

> `The weights and decode-KV endpoints remain analytical, since decode KV is never content-addressed here and weights`

这句话在本地文本文件里被后面插进来的图表框打断,句子没有续完,但已经够读出建模边界:模型权重这一端和解码阶段私有键值缓存这一端都是解析处理的,不由模拟出来的事件产生。配合另一句一起读,【本轮核过】原文是 `HBM holds weights and active, write-heavy state while HBF holds long-context KV`——权重放在高带宽内存里,不走键值缓存那条路径,所以论文那些逐层读写字节计数器里不含权重字节。

**作为「HBF 这一层收到多少流量」这个问题,把权重排除在外是正确的方法学选择,这一点要替《HBF Sucks!》说清楚,不要写成它做错了。** 权重根本不落到 HBF 上,把权重算进 HBF 的流量计数器才是错的。

但换成「完整 HBF 架构为什么变慢」这个问题,同样的处理就不够了。近端层带宽从 3.0 TB/s 砍到 1.5 TB/s,直接冲击的正是权重的流式读取——每生成一个词元都要把当前层的权重从近端层读一遍。论文没有给出权重、键值缓存、激活值三者各占多少带宽的分解,因此外部读者无法审计那 2 到 5.5 倍主要来自哪一部分。这与缺口一是同一个洞的两面:缺口一是对照设计合并了两个变量,缺口二是建模边界让人无法事后把两个变量拆开。

【第三方提出,未核】第三方分析补充了一条使这个洞更难补的理由:《HBF Sucks!》依赖的 TokenSim 里,负责算计算算子延迟的那个部件 `TransformerRoofline`(屋顶线模型,用算力上限和带宽上限估算一个算子要跑多久)没有开源,只提供预编译的 `.so` 二进制文件,第三方分析称 TokenSim 官方仓库自己说明了这一点。若属实,外部就无法核实每个解码步实际计入了多少权重字节、批处理之后权重开销如何被摊薄、混合专家模型的专家权重如何处理、权重按什么量化位宽计算。这条本项目尚未去 TokenSim 上游仓库核实,引用前必须先核。

### 缺口三:混合专家模型的权重容量对不上账

这是第三方分析提出的最严重一条。**算术本轮已复核;论文里对应的说明本轮做过定向检索,结果如下,原来的待核状态已经消解一半。**

【本轮核过】**权重精度论文没有交代。** 对本地 `.txt` 全文检索 `FP16`、`INT4`、`INT8`、`precision`、`quantiz`、`bf16`,命中共五处,没有一处是在说被评估模型的权重按什么位宽计入容量:第 107 行与第 2803 行是背景与相关工作里把量化列为已有的省容量办法;第 2882 行与第 2903 行是参考文献标题;第 2168 行的 `24 FP16 MAC arrays at 600 MHz` 说的是近存处理引擎那 24 个乘加阵列的数据格式,不是模型权重。**所以「论文没有说明权重精度」这一条从【第三方提出,未核】升级为【本轮核过】。**

【本轮核过】**专家放置只交代了并行方式,没有交代驻留策略。** 第 786 行原文是 `We model eight-GPU H100 and B200 systems with tensor parallelism 8, pipeline and data parallelism 1, and expert parallelism for MoE models.`,即混合专家模型采用专家并行;第 785 行原文 `For MoE models, the parameter count reports both total parameters and parameters activated per token.`,即参数表同时报总参数与每词元激活参数。**但全文没有说明未被激活的专家权重放在哪、是不是只有激活的专家常驻近端层、有没有专家换入换出。** 专家并行是一种把专家分散到多张卡上的并行方式,它解释了权重如何在八张卡之间切分,不解释八张卡合计 384 GB 如何装下 1.1T 参数。**所以容量矛盾这一条依然成立,而且现在有了更准确的表述:不是论文算错了,是论文交代的信息不足以让人重建这笔容量账。**

【推算,输入已核】八卡 H100 配置下近端层合计是 8 乘 48 GB 等于 384 GB。384 GB 折合 3.072 乘 10 的 12 次方比特。若模型权重全部驻留近端层:DeepSeek-V3.2 的 685B 参数,3.072e12 除以 685e9 等于每个参数 4.48 比特;GLM-5.2 的 753B 参数,得 4.08 比特;Kimi-K2.7-Code 的 1.1T 参数,得 2.79 比特。而且这三个数还没有给键值缓存、激活值和运行时缓冲留任何空间,实际可用于权重的比特数只会更少。上述除法是推算,输入的两项都已核实:48 GB 这个近端层容量【本轮核过】,五个模型的参数量与激活参数量【本轮核过】。

这个矛盾之所以尖锐,是因为论文自己写了权重放在高带宽内存里(前面缺口二引的 `HBM holds weights` 那一句)。把权重放到 HBF 那 1.5 TB 上去是一条出路,但论文正文没有这样写。

论文没有说明、而外部读者要判断这笔账就必须知道的几件事:权重用什么精度存放;混合专家模型的专家权重放在哪一层;是不是只有被激活的那些专家常驻近端层;有没有专家的换入换出,换入换出的流量算不算进 HBF 的读写计数器;未被激活的专家权重放在哪里;权重字节按什么规则计入近端层的容量占用。

【第三方提出,未核】第三方分析另补一条:公开版 TokenSim 默认按 FP16 计算权重占用,依据是代码里 `model_param_size ... * 2 # sizeof fp16 is 2 bytes` 这一行,并且先从加速器内存容量里扣掉权重、再计算剩下能放多少键值块。但《HBF Sucks!》显然扩展了 TokenSim 并加入了混合专家模型,所以不能断定它沿用了这段逻辑。**真正能成立的批评不是「它算错了」,而是「扩展后的实现没有给出足够信息,让人确认这个容量矛盾是怎么解决的」。**

## 第三节 本项目此前独立核出的另外八处

**先给这一节的结论:这八处与第三方分析无关,全部集中在器件侧——热、耐久度、能耗——而这一侧恰好是《HBF Sucks!》用来支撑「HBF 撞热限制」和「HBF 比固态盘先磨损坏」两个结论的那一侧。** 八处里有两处本轮做了修正,修正内容写在对应条目里。

**第一处,热模型与服务模拟不在同一次运行里耦合,论文自己写明了。** 【本轮核过】原句是 `Two findings need device models the serving simulator does not provide.`,以及 `we build a 16-Hi HBF stack similar to HBM4, with 128-layer 3D-NAND TLC dies, and solve its steady-state heat flow with 3D-ICE [18]`(3D-ICE 是一个芯片堆叠热仿真工具,这里求的是稳态解,也就是热量流动达到平衡之后的温度分布)。它报出来的结果是:16 层堆在单堆持续带宽 202.27 GB/s、功耗 53.72 W 时撞到 80 摄氏度上限。这个上限从未回灌进端到端的服务数字——热模型算出来的带宽天花板没有反过来限制服务模拟里的传输速率。

**第二处,决定那个温度上限位置的参数,也就是每比特访问能耗,全文一次都没有印出来,也没有被扫描。** 【推算】只能从已核的两个数反推:53.72 W 除以 202.27 GB/s 等于 0.2656 焦耳每 GB,除以每 GB 的 8 乘 10 的 9 次方比特,得约 33.2 皮焦耳每比特。【前轮核过,本轮未复核原始出处】这个值高于 HAVEN 给当代 3D NAND 的约 30 皮焦耳每比特,远高于 Micron 表格给 HBF 的 8.5 皮焦耳每比特,也远高于 FlashAccel 转述的混合键合原型实测 8 皮焦耳每比特;33.2 除以 8.5 等于 3.9 倍,33.2 除以 8 等于 4.15 倍。含义:【推算】既然论文自己写了动态功耗随带宽线性增长,那么在同一个 53.72 W 的功耗预算下,每比特能耗取 8.5 皮焦耳时对应的带宽会是 202.27 GB/s 的 3.9 倍——这道温度上限的位置,随着一个在文献里分歧接近 4 倍的参数移动接近 4 倍。**但要同时写明这不是算错,是场景不同**:《HBF Sucks!》模拟的是写占多数的流量,写比读费电,把写能耗混进来之后合成出 33.2 皮焦耳每比特是合理的。

**第三处,3D-ICE 的稳态解被用在一个 394 到 721 毫秒的窗口上。** 【本轮核过】那张图的标注逐字是 `Throttle zoom: 394-721 ms`。而封装级的热时间常数是秒这个量级:本项目实测 GPU 是 13.1 秒、CD8P 固态盘是 12.4 秒(出处 `docs/proofs/2026-08-11-hybrid-complete.md` 与 `docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md`)。【推算】13.1 秒除以 0.721 秒等于 18.2 倍,窗口本身的长度 327 毫秒更是比这两个时间常数短 38 到 40 倍。稳态解描述的是热量流动已经达到平衡之后的温度,在远小于热时间常数的窗口里,器件根本还没走到那个平衡点。**不要在远小于热时间常数的窗口上使用稳态解**——这一条对我们自己同样成立,是我们做热模型时要守的线。

**第四处,耐久度对比的两边口径不对称。** 【本轮核过】固态盘那一侧用的是厂商保证值:四块 KIOXIA CM7-V 3.2 TB 组成的容量对等池,按厂商标称的每日全盘写入次数 3 次(DWPD,一天可以把整块盘写满几次)算出 38.4 TB 每天。HBF 那一侧用的是写放大等于 1 的理想假设(写放大是实际写入介质的字节数与主机请求写入字节数之比,等于 1 表示没有任何额外写入),算出 21.7 TB 每天的预算;论文自己标注了这个假设是刻意取的有利假设,原句写 `any real NAND management would only lower it`。结论是四条轨迹都写超了两条预算(48 到 140 TB 每天),HBF 那一层的寿命恒为固态盘池的 0.56 倍。**恒定 0.56 倍、四条轨迹全都一样,这个现象本身就说明它是两个常数相除的结果**,21.7 除以 38.4 等于 0.565(【推算】),与每条轨迹写了多少字节无关。

**第五处,热模型只有一条过原点的直线。** 【本轮核过】原文写 `Dynamic power scales linearly with bandwidth`。【前轮推算,本轮复核了其中一点】按功耗除以带宽逐点折算,得到的都是 0.264 到 0.270 焦耳每 GB;本轮复核了 202.27 GB/s 与 53.72 W 这一点,得 0.2656 焦耳每 GB,落在这个区间里。逐点折算得到同一个常数,意味着模型里没有与带宽无关的静态功耗项,也没有把读和写分成两个不同的系数。而论文正是据此断言写比读费电、所以是写占多数的流量把器件推到极限——一条不区分读写的直线,支撑不了一个关于读写差异的结论。公平起见要补一句:【本轮核过】论文写了它是用一个 16 词元键值块的读能耗与写能耗共同驱动这个模型的(`drive the model with the read and write energy of a 16-token KV block`),所以读写在输入侧是分开的;问题出在报出来的功耗与带宽关系被压成了单一常数,读者拿不到读与写各自的系数,也就无法把这个热结论重算一遍。

**第六处,扩展后的模拟器没有给出仓库地址,全文也没有可复现声明。本轮对这一条做了修正。** 【本轮核过】修正的内容是:《HBF Sucks!》确实给了一个仓库链接,在第 4 节第 1 小节的脚注一,地址是 `https://github.com/pku-lemonade/TokenSim`——但这是基础版 TokenSim 的仓库,不是这篇论文自己那个扩展版本的。论文对扩展部分只列了做过哪些回归检查(块与字节守恒、存取顺序、确定性回放、逐层计数器),没有给出扩展代码、配置或轨迹的获取途径,全文也没有 artifact 或 availability 这类可复现声明。**所以这一条要按修正后的说法转述:不能写「没有给任何仓库地址」,要写「只给了基础版模拟器的仓库,扩展部分不可获取」。**

**第七处,它唯一的正向产出是一张没有刻度的定性图。** 【本轮核过】那张图是 Fig. 12,图题写的是把「暴露的停顿时间(条件 C1)」对「每次写换回多少次读(条件 C2)」画成一张放置图,再由「可持续带宽(条件 C3)」移动分界线;作者自己承认不给出标定过的边界。也就是说,三条必要条件作为框架是清晰的,但把它变成一条可以照着做部署决策的定量分界线,这篇论文没有做。

**第八处,它没有数据保持期模型,也没有刷新模型。本轮对这一条的措辞做了收紧。** 【本轮核过】全文检索 `refresh` 零命中、`Arrhenius` 零命中(Arrhenius 方程是把温度换算成失效速率的那个公式,是数据保持期建模的标准入口)。`retention` 一共出现四处,其中只有一处是数据保持期的含义,在相关工作一节,原句是 `and NAND retention depends on wear and temperature [1].`——这是一句指路,不是一个模型。另外三处不能算:一处是 `selective retention`,讲的是保留哪些词元、与数据保持期无关;一处是参考文献的标题;一处是 DOI 字符串。结论不变:热的后果在《HBF Sucks!》里只走到温度和吞吐,没有走到数据保持期限和为守住数据必须补写的刷新流量。

## 第四节 这篇论文写法上值得借鉴的地方

**先给这一节的结论:《HBF Sucks!》最值得学的是它处理负面结果的办法——先把对手的最强主张替对手说出来,再去测它,最后不停在「不行」上,而是交出一条放置规则。** 下面八条,前两条【本轮核过】,其余各条是本轮通读全文形成的判断。

一,摘要的头两句先给一句所有人都同意的常识,再立刻否定它:`A faster storage device should make serving faster. We find the opposite.` 两句话之内读者就知道这篇论文的立场。

二,引言第一段不出现任何存储器件的名字。【本轮核过】第一段从应用侧的容量问题起笔——每个保留下来的词元都要存一份键和值,键值缓存随上下文长度、批大小、活跃请求数增长,分页分配、量化、选择性保留能省字节但不解决容量上限;整段没有出现 HBF、NAND、固态盘中的任何一个,只在末尾落到一句「工作集超过 GPU 显存时,必须把键值缓存放到更大的一层」。

三,在背景一节而不是评估一节加强基线,并且自己写下这是一个强的、还在被持续优化的基线。等到评估一节才说基线强,读者会认为是在为负面结果找台阶。

四,替对手把最强的主张说出来,再去测这个主张。它不是去测一个自己搭的弱版本 HBF 方案,而是先承认「直接替换是最自然的做法」,然后把这个做法完整实现出来测。

五,把一堆负面数字收敛成一个成本模型,再从模型导出编号的条件(C1、C2、C3)。数字会过期,条件不会;后来的人换一组参数仍然能用这三条去判断。

六,把不确定的参数扫描一遍,再用扫描结果反解出一个物理量。介质延迟那次 3.75 倍的扫描,反解出的是「暴露在二级层输入输出上的时间占比约 1%」——这个占比比任何单点数字都更难被推翻。

七,每一个数值都标注为三者之一:模拟器输出的测量值、有出处的器件参数、建模的推算值。它对自己的器件参数专门写了一句声明,原句是 `HBF remains projected hardware, so its service parameters are sourced or swept, not claimed as measured silicon.`(【本轮核过】)。

八,以一条正向的放置规则收尾,不以「不行」收尾。摘要末尾给的是「在什么条件下 HBF 在大模型服务里有位置」——按复用感知放置、按写入预算约束、与热协同调度。

## 第五节 对我们两条线各自的含义

**先给这一节的结论:《HBF Sucks!》对我们不是竞争关系,是佐证——它自己写下的那句「服务模拟器提供不了器件模型」,正好是我们两条线共同瞄准的空位。**

### 对 HBFSim 模拟器论文那条线

引用《HBF Sucks!》时,那个 2 到 5.5 倍必须带上「非单变量对照」的限定,理由见第二节缺口一。它归纳的三条必要条件(C1、C2、C3)是可以正面引用并致敬的框架,不要把三条必要条件也一并批评掉——被批评的是数字的可分解性,不是这个框架。

最有用的一句是它自己承认的那句 `Two findings need device models the serving simulator does not provide.`(【本轮核过】):在服务模拟这一侧,器件模型是外挂进来的,两者不在同一次运行里耦合。这正好说明「在同一次执行里把器件模型与上层执行耦合起来」是一个真实存在的方法学空位,而不是我们自己找出来的麻烦。写进论文时按这个顺序写:先陈述《HBF Sucks!》怎么做的、它自己怎么说的,再说明这样做会漏掉什么(热的后果无法回灌到端到端数字上,见第三节第一处),最后才说我们怎么做。

### 对 HBF 边缘部署论文那条线

第二节那三处缺口分别对应我们的三处优势,一一对上:

权重流量这一项,在我们这里是真实内核真的去读的字节数,不是一个闭源屋顶线模型算出来的估计值——缺口二说的正是这一点无法被外部审计。

单变量对照这一项,我们做得到,所以要立一条硬规矩:任何进论文的头条数字都不许来自两个变量同时变的对比;要报整包交易的代价,就另外补一组只变一个变量的对照放在旁边。

混合专家模型的专家放置与读取这一项,是在真实执行过程中施加效应的模拟器能直接测到的——哪些专家被激活、专家权重什么时候被读、读了多少字节,不需要靠假设。缺口三之所以成为缺口,原因就是这些量在离散事件模拟里必须先假设才能算。

## 第六节 引用《HBF Sucks!》时的硬规矩

**这一节的六条对两条线共同生效,写成可以照抄的短句。**

一,引「平均端到端延迟上升 2 到 5.5 倍」时,同一句里必须带上非单变量对照的限定:近端层的容量与带宽是一起砍半的(96 GB 到 48 GB,3.0 TB/s 到 1.5 TB/s),论文自己声明了这不是单变量对照。

二,引 202.27 GB/s 与 53.72 W 时,必须带三项限定:这是 3D-ICE 的稳态热求解结果;热模型与服务模拟不在同一次运行里耦合,这个上限没有回灌进端到端数字;它隐含每比特访问能耗约 33.2 皮焦耳,与 Micron 表格给 HBF 的 8.5 皮焦耳每比特相差 3.9 倍。

三,引它的耐久度结论(HBF 那一层寿命为固态盘池的 0.56 倍)时,必须说明两边口径不对称:固态盘那侧用厂商的每日全盘写入次数保证值,HBF 那侧用写放大等于 1 的理想假设。

四,引介质敏感度那一组(3.75 倍改善只换来 0.75% 与 0.88% 的端到端变化,反解出占比约 1%)可以不加限定——这一组是单变量的,是这篇论文最硬的一段。

五,不许把《HBF Sucks!》的结论表述成「HBF 不行」。它自己的结论是问题出在部署方式:把 HBF 当成一块更快的固态盘、拿去承接活不过一次请求的键值缓存。原句是 `The device is fine; the drop-in deployment is not.`

六,凡是本文档标【第三方提出,未核】的两条(TransformerRoofline 是否开源、公开版 TokenSim 的 FP16 权重记账),在核实之前不许写进任何论文正文、大纲或投稿材料,也不许在与合作者的讨论里当成已确立的事实转述。

## 附录

### 附录第一节 全部逐字引文与行号

行号一律指本地文本文件 `/root/hbfsim/HBFSim/docs/ref_article/li2026-hbf-characterization-kv-cache.txt`。这份文本是从 PDF 抽取出来的,表格和图注在抽取过程中被打散,所以有几句在文本文件里是断开的,断点已在下面注明。

第 79 行,摘要开头:`A faster storage device should make serving faster. We find the opposite.`

第 91 行,摘要里的介质敏感度结论:`while HBF’s own read/write latency barely matters: scaling it 3.75× moves latency less than 1%`

第 93 至 96 行,摘要结论句:`The device is fine; the drop-in deployment is not. HBF sucks as an SSD replacement for transient KV, but earns its place in LLM serving when used selectively with reuse-aware placement, write budgeting, and thermal coordination.`

第 106 至 109 行,引言第一段全文(不含任何存储器件名)。

第 391 至 392 行,三种配置的近端层容量:`12 GDDR7, 48 GB`(HBF-1)与 `3 HBM, 48 GB`(HBF-2)。同一张表第 393 至 394 行给闪存侧:`6 stacks, 3 TB` 与 `3 stacks, 1.5 TB`。

第 577 至 578 行,权重的放置:`HBM holds weights and active, write-heavy state while HBF holds long-context KV`

第 598 至 604 行,模拟器与扩展项,以及器件参数来源声明:`We use an extended version of TokenSim1 [21].` 直到 `HBF remains projected hardware, so its service parameters are sourced or swept, not claimed as measured silicon.`

第 613 行,脚注一给出的仓库地址:`1 https://github.com/pku-lemonade/TokenSim`

第 657 至 669 行,五个模型与参数量:Qwen3-4B(Dense 4B/4B)、Qwen3-32B(Dense 32B/32B)、DeepSeek-V3.2-685B(MoE 685B/37B)、GLM-5.2-753B(MoE 753B/40B)、Kimi-K2.7-Code-1.1T(MoE 1.1T/32B)。

第 754 至 765 行,热模型的定位与做法:`Two findings need device models the serving simulator does not provide.` 直到 `we report it as such and use it to bound, not fix, the thermal limits of an HBF tier.`;其中 `solve its steady-state heat flow with 3D-ICE [18]` 在第 758 行,`drive the model with the read and write energy of a 16-token KV block` 在第 759 至 760 行。

第 909 至 918 行,介质敏感度扫描:读写点从 `30/300` 移到 `8/80` 微秒,`A k = 3.75× improvement in raw media latency shifts mean end-to-end latency by 0.75% (HBF-1) and 0.88% (HBF-2)`。

第 1066 至 1071 行,单变量对照那一段全文,含 `Two comparisons locate it, and neither is a single-variable control.` 与 `What the swap degrades is the near tier that governs how many requests the server admits at once.`

第 2350 行,建模边界(句子在此处被后面插入的图表框打断,未续完):`The weights and decode-KV endpoints remain analytical, since decode KV is never content-addressed here and weights`

第 2465 至 2466 行,图 10(a) 上的两个标注:`202.27 GB/s` 与 `53.72 W`。第 2616 行是正文里的同一组数:`hits the 80◦ C limit at only 202.27 GB/s and 53.72 W, far below interface peak, so it cannot run continuously above that point (Fig. 10(a))`;第 2615 行是 `Dynamic power scales linearly with bandwidth`。

第 2525 行,那张放大图的标题:`Throttle zoom: 394-721 ms`

第 2725 至 2735 行,耐久度两侧口径:`whose vendor 3-DWPD rating gives 38.4 TB/day`、`we adopt a labeled, deliberately favorable TLC assumption with unit write amplification, which yields a 21.7 TB/day budget for a five-year target; any real NAND management would only lower it`、`the nominal HBF tier lasts a constant 0.56× the SSD pool’s life across all four traces (Fig. 11)`。

第 2776 行,图 12 的图题:`Figure 12: Placement map implied by the findings: exposed stall (C1) against reads-per-write (C2), with sustainable bandwidth (C3) shifting the boundary.`

第 2828 行,相关工作里唯一一句关于数据保持期的话:`and NAND retention depends on wear and temperature [1].`

### 附录第二节 第三方分析的落盘位置

第二节缺口二与缺口三里标【第三方提出,未核】的两条,来源是用户提供的一次 ChatGPT 对话分享页面,标题《论文贡献与流量评估》,artifact 编号 `c6e1131843d1`。落盘位置:

- 抽取出的纯文本:`/root/hbfsim/tmp/other-ai-hbf-edge-analyses/text/c6e1131843d1.txt`,其中关于 `TransformerRoofline` 未开源的那几句在第 997 行与第 1006 行,关于公开版 TokenSim 按 FP16 记账权重的那句在第 1125 行。
- 只保留与 HBF 相关部分的节选:`/root/hbfsim/tmp/other-ai-hbf-edge-analyses/text/c6e1131843d1-hbf.txt`
- 原始网页:`/root/hbfsim/tmp/other-ai-hbf-edge-analyses/raw/c6e1131843d1-6a88ef44-c2e0-83ea-a76f-00816663ec75.html`
- 同一目录下另有两份第三方分析(`d9cff4aa10e0` 与 `bbed497e05f1`),本文档没有引用这两份的任何内容。

**这个目录在 `/root/hbfsim/tmp/` 下,不在 git 仓库里。** 需要长期保留其中任何一条结论时,先把它核实,再把核实结果写进本文档,不要指望这个目录一直在。

### 附录第三节 本项目尚未核实的清单,以及核实办法

四条,前两条来自第三方分析,后两条是本轮读原文时没能确认的。

一,TokenSim 上游仓库里 `TransformerRoofline` 的开源状态。核实办法:打开 `https://github.com/pku-lemonade/TokenSim`(这个地址是《HBF Sucks!》第 613 行脚注给的,已核),查这个部件是不是只提供预编译的 `.so` 二进制文件,以及仓库自己有没有声明这一点。核实之前,不许在任何论文材料里写「它依赖一个闭源的延迟模型」。

二,公开版 TokenSim 按 FP16 计算权重占用、并先从加速器内存容量里扣掉权重的那段逻辑。核实办法:在同一个仓库里搜 `model_param_size` 与 `sizeof fp16`,确认代码原文与所在文件。即使核实属实,也只能说明基础版是这样,不能推断《HBF Sucks!》的扩展版沿用了它。

三,《HBF Sucks!》有没有在正文、表格或附录里交代权重精度。本轮通读没有找到,但表格在文本抽取过程中被打散,不能排除数字落在某一格里。核实办法:直接看 PDF 原件 `docs/ref_article/li2026-hbf-characterization-kv-cache.pdf` 的第 2 张表(评估工作负载、模型与系统),以及第 3.2 节讲配置的那几段。

四,《HBF Sucks!》有没有交代混合专家模型的专家权重放在哪一层、有没有专家换入换出。同样按上一条的办法直接看 PDF 原件。**这一条是缺口三成不成立的关键:如果论文其实交代了,缺口三就要改写成「交代了但没有给出容量对账」;如果确实没有交代,缺口三按现在的写法成立。**

### 附录第四节 这篇论文的完整出处

标题:《HBF Sucks! A Full-Stack Characterization of High-Bandwidth Flash for KV-Centric LLM Serving》。作者:Zhuoran Li、Zhuohang Bian、Yibo Zhao、Guangyu Sun、Youwei Zhuo(北京大学)与 Xin Huang(复旦大学)。编号:arXiv:2608.11668v2 [cs.AR]。篇幅 12 页。本地原件 `/root/hbfsim/HBFSim/docs/ref_article/li2026-hbf-characterization-kv-cache.pdf`,SHA-256 是 `268a3466c5ded355887e1e61792f8961167a1768ffba450dc2050cb46dbcd47c`;配套纯文本 `/root/hbfsim/HBFSim/docs/ref_article/li2026-hbf-characterization-kv-cache.txt`,67,514 字节,本文档所有行号都指这一份。这篇论文在 `docs/ref_article/README.md` 里已有登记条目。

