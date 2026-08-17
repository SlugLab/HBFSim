# Reference articles

Primary sources cited by HBFSim documentation and by the paper draft, stored
here so a reader can check a claim without leaving the repository.

## Naming convention

`<first-author-surname><year>-<topic>.pdf`, all lowercase, hyphen-separated,
matching the BibTeX key used in the paper. A file named after a DOI or an ACM
Digital Library ID tells a reader nothing about what it contains. For press and
trade articles with no academic author, use the outlet or handle in place of a
surname, and end the name with `-cn` when the source is Chinese.

Record every file below with its full citation, a stable link, its SHA-256, its
page count, and what this project actually uses from it.

## Text extractions

Every PDF in this directory has a `.txt` file of the same name beside the PDF:
the plain text of that PDF, extracted with `pdftotext`. The words are the same
as in the PDF; only the layout is gone. Eleven files, with sizes:

- `luo2018-heatwatch-nand-temperature.txt`, 91 KB
- `wang2026-flashaccel-hbf-llm-inference.txt`, 88 KB
- `yoon2026-cylon-cxl-ssd-emulation.txt`, 78 KB
- `semiinsights2026-hbf-standard-release-cn.txt`, 77 KB
- `yang2023-cxlmemsim.txt`, 71 KB
- `ju2026-tilelens-two-dimensional-memory-layout.txt`, 75 KB
- `sano2023-cxl-microsecond-latency-gpu-graph.txt`, 61 KB
- `yang2025-egpu-ebpf-ptx-injection.txt`, 39 KB
- `semiinsights2026-can-hbf-work-cn.txt`, 34 KB
- `micron2026-is-hbf-all-you-need.txt`, 22 KB
- `zhihu2026-hbf-protocol-and-market-analysis-cn.txt`, 7 KB

One of these eleven has no PDF beside it: `semiinsights2026-can-hbf-work-cn.txt`
was captured from a WeChat page as text, not as a PDF.

Web pages captured into this directory follow the same rule: the capture is
stored as a PDF, and the same-name `.txt` beside the capture keeps the body text
searchable with `grep` without opening a viewer.

The `.txt` files are committed rather than regenerated on demand because
documentation in this repository and the paper outline cite line numbers inside
the `.txt` files. Two examples: the Cylon sentence `Every guest access undergoes
address translation through Intel's Extended Page Tables (EPT)` is at lines
498--500 of `yoon2026-cylon-cxl-ssd-emulation.txt`, and the CXLMemSim sentence
`it's always a sampled model which may lose a lot of data` is at lines 340--343
of `yang2023-cxlmemsim.txt`. Different versions of `pdftotext` break lines
differently, so a regenerated file can shift every line number and leave those
citations pointing at the wrong text. Freezing the extraction keeps the
citations valid.

To produce one, run in this directory:

```text
pdftotext -q <name>.pdf <name>.txt
```

When adding a PDF, produce the same-name `.txt` and commit both files together.

The `.txt` files are not the primary source. To check whether a sentence really
appears in a source, the PDF settles the question; the `.txt` exists so the text
can be searched directly and cited by line. Extraction can also put text from a
multi-column page out of order, so read the PDF wherever a passage does not read
as coherent prose.

---

## `ardestani2012-thermal-aware-sampling.pdf`

Cited elsewhere in this repository as `2333660.2333670.pdf` -- the ACM Digital
Library filename -- in `docs/proofs/2026-08-11-hybrid-complete.md` and
`scripts/thermal/collect.py`. Those two references still use the old name;
search for either string to find them.

> Ehsan K. Ardestani, Elnaz Ebrahimi, Gabriel Southern, and Jose Renau.
> "Thermal-Aware Sampling in Architectural Simulation."
> In *Proceedings of the 2012 ACM/IEEE International Symposium on Low Power
> Electronics and Design (ISLPED '12)*, July 30 -- August 1, 2012,
> Redondo Beach, CA, USA, pages 33--38.
> DOI: `10.1145/2333660.2333670`

- Affiliation: Dept. of Computer Engineering, University of California Santa Cruz.
- Open access: <http://masc.soe.ucsc.edu/docs/islped12.pdf>
- SHA-256: `1e85f9c7965f3da6045895b13a9caea34a85817d3794d43ef692df425027a639`
- 6 pages.

What this project uses: Equation 1, the weighted moving average

```text
Theta_i = ( sum_{k=1..n} alpha_k * theta_{i-k} ) / ( sum_{k=1..n} alpha_k ),
alpha_k = 1 / 2^k
```

where `Theta` is the estimated value, `theta` the measured value, and `n` the
history size. Section 3.1.1 sets `n = 7` and uses measured power from intervals
`i` through `i - 7` to estimate power for interval `i`.

Two boundaries, because the citing text is easy to misread:

1. Equation 1 estimates power (and, via Equation 2, interval length) for a
   thermal sampling interval. It is not itself a first-order thermal response
   equation; the first-order fit in `scripts/thermal/fit_logp.py` is this
   repository's own addition on top of the sampling weights.
2. The paper names its methods TASS (Thermal-Aware Statistical Sampling) and
   TAPS. It does not use the name "LogP" anywhere. "LogP" is an established
   name for an unrelated parallel-computation model, so the `logp` naming in
   `configs/thermal/` and `scripts/thermal/` collides with existing
   terminology.

---

## `yang2023-cxlmemsim.pdf`

The full-length CXLMemSim preprint. Same group as this project.

> Yiwei Yang, Brian Zhao, Yusheng Zheng, Pooneh Safayenikoo,
> Tanvir Ahmed Khan, and Andi Quinn.
> "CXLMemSim: A pure software simulated CXL.mem for performance
> characterization." arXiv:2303.06153. Submitted 2023-03-10, v2 2025-06-17.

- DOI: `10.48550/arXiv.2303.06153` -- <https://arxiv.org/abs/2303.06153>
- SHA-256: `cb665333c9ecf5bc0ef08b3ee9d3d704bbbd44321f93b68657a6da27b4b55797`
- 9 pages (v2).
- Code: <https://github.com/SlugLab/CXLMemSim>
- An earlier short version appeared at the fifth Young Architect Workshop
  (YArch '23); the repository's own citation block records that.

The peer-reviewed short version is a separate entry:

> Yiwei Yang, Shri Vishakh Devanand, Brian Zhao, Yusheng Zheng,
> Pooneh Safayenikoo, Tanvir Ahmed Khan, and Andi Quinn.
> "CXLMemSim: Practical Performance Simulation and Characterization of
> CXL 3.0 Memory Systems." In *Proceedings of the 35th International Symposium
> on High-Performance Parallel and Distributed Computing (HPDC '26)*,
> pages 567--569, 2026. DOI: `10.1145/3806645.3820069`

The HPDC PDF is behind the ACM Digital Library paywall (`dl.acm.org` returns
HTTP 403 to unauthenticated requests), so only the arXiv full text is stored
here. Add the HPDC PDF if someone with access can export it.

What this project uses: it is the closest model for how to write and evaluate a
"the hardware does not exist yet, so we emulate it on unmodified applications"
paper. See `/root/hbfsim/34-CXLMemSim与合作者论文的启示-2026-08-11.md`.

---

## `yang2026-wio-cxl-ssd-computational-storage.pdf`

> Yiwei Yang, Yanpeng Hu, Yusheng Zheng, Estabon Ramos, Jianchang Su,
> Andi Quinn, and Wei Zhang.
> "WIO: Upload-Enabled Computational Storage on CXL SSDs."
> arXiv:2604.02442, submitted 2026-04-02.

- DOI: `10.48550/arXiv.2604.02442` -- <https://arxiv.org/abs/2604.02442>
- SHA-256: `3024a60ccfe0be64578b48eddf28ccbe84e8f62c863a96a31c6b5dcbe3ab6000`
- 9 pages.

What this project uses: Section 2.1 is measured evidence, from the same group,
that device thermal state changes storage behaviour under sustained load --
the argument HBFSim's thermal model rests on. Reported there: an NVMe
controller that throttles at 70 degrees C with 50% throughput loss, an FPGA
that reduces frequency at 93 degrees C, gates clocks at 97 degrees C and shuts
down at 100 degrees C, and a ScaleFlux device that throttles at 65 degrees C
with 60% degradation; enterprise SSDs draw 10--14 W while added device-side
compute raises the same form factor to 25--70 W.

---

## `semiinsights2026-hbf-standard-release-cn.pdf`

A Chinese-language walk-through of the first OCP HBF specification, published
the day after the spec itself. Useful because the OCP original is not
downloadable without an account (see the note at the end of this file).

> 半导体行业观察（编辑部）。"首个HBF标准，正式发布，130页完整版披露"。
> 微信公众号文章，发布于 2026-08-04 13:25 UTC。

- Source: <https://mp.weixin.qq.com/s/5gabSHNhy92TgcuoVlujcA>
- Captured by fetching the page and rendering it to PDF; all 116
  in-article images were downloaded and embedded (0 failures).
- SHA-256: `dabad55c6171651f8219626f20730428564344e8df569ed11d4c2f0397cc25fa`
- 132 pages, 194 embedded images, 32,791 characters of body text.
- Companion plain text: `semiinsights2026-hbf-standard-release-cn.txt`

What this project uses: it follows the specification's own structure --
简介, 产品描述, 系统组织结构, 基片结构 (base die), 主机通道定义,
UCIe 主机接口架构, 协议层, 适配器层, Flit 格式, 电气层, 边带消息支持,
链路初始化 -- and is the most detailed account of the HBF interface we
currently hold. Treat it as a secondary source: it is one outlet's rendering,
not the normative text.

---

## `zhihu2026-hbf-protocol-and-market-analysis-cn.pdf`

> "HBF协议-从芯片架构适配到摆脱周期股票的思考". 知乎专栏文章，作者署名
> @aiiiiii（图片水印）。

- Source: <https://zhuanlan.zhihu.com/p/2068137669847208978>
- Zhihu returns HTTP 403 to direct fetches, including its own article API, so
  this was captured through the `r.jina.ai` reader proxy with the
  user's explicit approval. Six images downloaded and embedded (0 failures).
- SHA-256: `a7dfde7e23e1b3247b16068974dabfda8b798b3b8e564dc2752c8867cbcb3980`
- 7 pages, 3,219 characters of body text.
- Companion plain text: `zhihu2026-hbf-protocol-and-market-analysis-cn.txt`

What this project uses: a side-by-side parameter table comparing HBF Gen1,
HBM4, HBM3E and a high-end eSSD (Kioxia GP1) on media type, per-die capacity,
stack height, per-stack capacity and read bandwidth. Also an opinion, clearly
labelled as such by its author, on why Sandisk and SK hynix each pushed this
standard. Secondary source; do not cite its numbers without checking them
against the specification.

---

---

## `hbf2026-five-questions-answered-cn.md`

A Chinese-language opinion article answering five public criticisms of HBF.
Supplied by the user; original outlet and author unknown (the text ends with a
WeChat contact note), so treat it as a question list and a pointer into the
specification, never as a normative source.

- Renamed from `HBf_业界的 5 个质疑解决了吗.md` per the naming
  convention above.

The five criticisms it addresses, which map directly onto evaluation questions
for this project: (1) in-package thermal stress vs NAND retention/BER/aging;
(2) limited P/E endurance vs memory-class write rates, and whether HBF failure
strands the co-packaged GPU; (3) ~10 us read latency and 4KiB granularity vs
sparse/irregular access; (4) refactoring and packaging cost vs the per-GB
advantage; (5) ecosystem risk and whether HBF stays read-only.

Mechanisms it attributes to spec v0.7.0 (cross-check against the spec
write-up before citing): 24-48 hour host refresh patrol; per-block read
counters with CECC (Status 0x5) / UECC (Status 0x4); four-stage thermal
ladder with LTT/STT thresholds, CATTRIP pin, AXI Ready deassert, Status 0x9;
per-channel scratchpad SRAM at 64B granularity; channel partitioning (e.g. 12
weights + 4 KV-cache); MAXPEC/AVGPEC wear registers; Zone Remapping (0x08);
REDCAP (0x0A) with a 1024-bit failure bitmap at MMIO 0x14C.

## The OCP HBF specification: in this repository, under `docs/HBF_OCP/`

The normative document -- the first HBF technical specification, released
through the Open Compute Project -- is now held in this repository. The user
downloaded it through a browser on 2026-08-13 and put it outside this
directory, in `docs/HBF_OCP/`. An earlier version of this section said the
specification could not be fetched because `opencompute.org` returns HTTP 403
to command-line requests; that is no longer the situation anyone should act on.

> *HIGH BANDWIDTH FLASH (HBF™) HIGH-LEVEL BASE DIE SPECIFICATION.*
> VERSION 0.7.0. DATE: August 3, 2026. Open Compute Project.
> (Copied from the title page.)

- File: `docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf`
- SHA-256: `307531eb8053f00cbeccbc907ddff0a9c4fe6f9d0066a077ce33b0ac99312da3`
- 4.67 MB; 130 pages of body text.
- Companion plain text: a `.txt` extraction of the same name sits beside the
  PDF in `docs/HBF_OCP/`, 216 KB.

**Citation discipline: every citation of the specification gives a section
number and a page number from the English original. No statement about what the
specification says is ever sourced to a Chinese retelling of it.** This is not a
preference for primary sources in the abstract. Checking the file this round
turned up at least four facts that are in the English specification and in none
of the Chinese accounts; which four they are is not recorded in this entry.

That leaves the Chinese articles above a narrower job: a map of the
specification's structure and a list of questions worth looking up. The
mechanisms listed in the `hbf2026-five-questions-answered-cn.md` entry above --
the 24-48 hour host refresh patrol, the thermal ladder, Zone Remapping,
REDCAP -- can now be checked against the file itself, and the paper cites the
file, not the article.

---

## `wang2026-flashaccel-hbf-llm-inference.pdf`

The comparison target for HBFSim's paper: an HBF systems paper from the
Institute of Computing Technology, Chinese Academy of Sciences, and the
University of Chinese Academy of Sciences, whose evaluation vehicle is an
event-driven simulator rather than live execution.

> Xinyu Wang, Yalong Xue, Xiaotian Sun, Xiaoyu Zhang, Chunmeng Dou,
> Xueqi Li, and Xiaoming Chen (corresponding).
> "FlashAccel: Leveraging High-Bandwidth Flash for High-Throughput LLM
> Inference." arXiv:2607.10186v1 [cs.AR], submitted 2026-07-11.

- DOI: `10.48550/arXiv.2607.10186` -- <https://arxiv.org/abs/2607.10186>
- SHA-256: `9a1021441048c570cb998bcef6b19c60ad1b8ae1a47867bd23df5ba864618fb3`
- 16 pages.

Verified against the full text (pdftotext):

- What it does: co-designs LLM inference for HBF-equipped GPUs --
  architectural support to hide access latency, data layouts for weights and
  KV cache, an HBF-aware storage management layer and programming model.
- Evaluation method (quoted): "We build an event-driven simulator on top of
  LLMCompass [69] ... LLMCompass searches tiling strategies to find efficient
  GEMM mappings on GPU hardware and uses ScaleSim [45] to estimate latency.
  We extend it with a NAND simulator that models page access latency at plane
  granularity." No live workload execution.
- Headline results: six HBF stacks give 2.54x throughput per GPU and 1.93x
  energy efficiency over an HBM-only GPU under a 100ms latency constraint;
  CSI read bandwidth reaches 4.6 TB/s while peak write bandwidth is
  245.8 GB/s.
- Endurance argument: citing prior work, "reducing retention time from
  3 years to 3 days can extend P/E cycles by up to 50x"; they conservatively
  assume 10x (P/E 100K to 1M), and estimate SLC flash sustains about 55 P/E
  cycles per day over a 5-year lifetime.
- The words "thermal" and "temperature" do not appear anywhere in the paper
  (checked against the extracted text). Retention appears only as an
  endurance lever, with no temperature dependence modeled -- retention time
  is in fact a strong function of junction temperature, which is exactly the
  coupling HBFSim's thermal model captures.

What this project uses: (a) demand evidence -- a systems paper already builds
LLM inference on HBF before silicon exists; (b) the gap statement -- its
evaluation is an isothermal, analytic, event-driven model, the category of
tool HBFSim complements with live execution, measured calibration, and a
thermal/reliability loop; (c) a concrete experiment hook -- its
retention-relaxation endurance assumption can be re-examined under HBFSim's
temperature-dependent retention model.

---

## `luo2018-heatwatch-nand-temperature.pdf`

The primary source for "NAND is temperature-sensitive." Grounds this project's
thermal model; read it before writing any retention or reliability claim.

> Yixin Luo, Saugata Ghose, Yu Cai, Erich F. Haratsch, and Onur Mutlu.
> "HeatWatch: Improving 3D NAND Flash Memory Device Reliability by Exploiting
> Self-Recovery and Temperature Awareness." In *2018 IEEE International
> Symposium on High Performance Computer Architecture (HPCA)*, pages 504--517.

- Affiliations: Carnegie Mellon University; Seagate Technology; ETH Zurich.
- Open access: <https://www.cs.cmu.edu/~yixinluo/index_files/heatwatch_hpca18.pdf>
- SHA-256: `a1d2140c3c9c0ae2fedf7741f1e9d2af2fe777742509fc19875a5c559cc2f608`
- 14 pages.

Verified from the full text (pdftotext). Equation 1, quoted:

```text
AF(T1, T2) = t1 / t2 = exp[ (Ea / kB) * (1/T1 - 1/T2) ]
```

"AF is the acceleration factor between t1 and t2, where t1 is the retention or
dwell time under temperature T1, and t2 is the retention or dwell time under
temperature T2. kB is the Boltzmann constant, which is 8.62 x 10^-5 eV/K. Ea is
the activation energy, which is a manufacturing-process-dependent constant. For
a planar NAND flash memory device, Ea = 1.1 eV. **To our knowledge, there is no
public literature that reports the value of Ea for 3D NAND flash memory.**"

Three facts this project relies on:

1. Time at high temperature is equivalent to a longer time at room temperature
   -- the paper calls this the *effective retention time*, and the same
   equivalence holds for *dwell time* (idle time between program/erase
   operations, during which some damage is repaired: the *self-recovery*
   effect).
2. Scale of the effect, quoted: "Based on Arrhenius' Law, the same experiment
   would take more than 11 years to finish had we performed it at room
   temperature (20 degrees C)."
3. The direction is not uniform: "higher temperature increases retention errors
   but reduces program variation errors."

Result reported: 3.85x flash lifetime improvement over a fixed read reference
voltage baseline, averaged across 28 real storage workload traces, within 0.9%
of an ideal read-reference-voltage selection mechanism.

**Correction this source forces on our earlier notes.** An earlier web-search
summary put NAND retention activation energy at "1.05--1.2 eV". The primary
source states Ea = 1.1 eV *for planar NAND* and says explicitly that no public
value exists for 3D NAND. Since HBF stacks 3D NAND, Ea is an unknown parameter
for our device, not a constant we may assume. The paper is also the reason to
run Ea as a sensitivity sweep rather than a fixed input.

---

## `yang2025-egpu-ebpf-ptx-injection.pdf`

The direct predecessor of this project's instrumentation path, and the reason
the coverage problem is a real research challenge rather than an engineering
detail. Same first author as this repository.

> Yiwei Yang, Tong Yu, Yusheng Zheng, and Andrew Quinn.
> "eGPU: Extending eBPF Programmability and Observability to GPUs."
> In *4th Workshop on Heterogeneous Composable and Disaggregated Systems
> (HCDS '25)*, March 30, 2025, Rotterdam, Netherlands. 7 pages.
> DOI: `10.1145/3723851.3726984`

- Affiliations: UC Santa Cruz; Eunomia Inc.
- Full text: <https://asplos.dev/pdf/bpftime_super.pdf>
- SHA-256: `8a25c18a0a6f9cd5efa1618e6b49062b12182cfcfadaad5f0074600ce96730eb`
- 7 pages.
- Code: <https://github.com/victoryang00/bpftime-super> (also merged into
  <https://github.com/eunomia-bpf/bpftime>)

Verified from the full text (pdftotext):

- It claims to be first at this: "we introduce eGPU, the first framework and
  eBPF runtime that dynamically offloads eBPF bytecode onto GPUs via dynamic
  PTX injection."
- Mechanism: eBPF bytecode is just-in-time compiled into PTX snippets and
  injected into running GPU kernels without interrupting them; shared memory
  registered as pinned (or pinned-like) lets the GPU and CPU sides observe the
  same data structures without repeated copying.
- Baseline it compares against is gpumemtrace, which instruments the `mov`
  instruction and reroutes it to the host; the paper notes NVBit's
  instrumentation overhead becomes a bottleneck once the hashmap value size
  grows past 32KB, because the data no longer fits the eBPF stack and must
  live in a global map.
- Evaluation platform: dual-socket Intel Xeon E5-2697-v2 (48 cores, 2.7 GHz,
  30 MB LLC), 256 GB DDR3.
- **Its own stated limitation (Section 7.2), quoted:** "While our
  micro-benchmark clearly demonstrates eGPU's low instrumentation overhead, we
  acknowledge that our current evaluation scope is limited. Specifically, we
  have not yet conducted extensive end-to-end performance evaluations on
  complex, real-world workloads such as large-scale machine learning models or
  HPC simulations."

What this project uses: two things. First, it is prior work on the exact
mechanism this project builds on -- rewriting PTX to instrument a running GPU
kernel -- so Related Work must cite it. Second, its stated limitation is
evidence for how new this path is: the step it has not taken (end-to-end
evaluation on a large-scale machine learning model) is the step this project
takes, and the coverage problem that follows -- production stacks ship
compiled cubins with no rewritable intermediate code -- is not something the
CPU-side or hypervisor-side simulators (CXLMemSim, Cylon) ever face.

---

## `villa2019-nvbit-gpu-binary-instrumentation.pdf`

The instrumentation framework that reaches the programs this project's PTX
route cannot reach, and the baseline the eGPU paper above compares against.

> Oreste Villa, Mark Stephenson, David Nellans, and Stephen W. Keckler.
> "NVBit: A Dynamic Binary Instrumentation Framework for NVIDIA GPUs."
> In *Proceedings of the 52nd Annual IEEE/ACM International Symposium on
> Microarchitecture (MICRO-52)*, 2019, pages 372--383.
> DOI: `10.1145/3352460.3358307`

- Affiliation: NVIDIA.
- Stable link:
  <https://d1qx31qr3h6wln.cloudfront.net/publications/MICRO_2019_NVBit.pdf>
- SHA-256: `0d16035cea56057c5ca9e6d58bb5532ae0bcf02037dd07089aa80e2dffd33d1c`
- 1,838,380 bytes; 73,036 characters of body text extracted with `pdftotext`.
  Proceedings pages 372--383.
- BibTeX key: `villa2019nvbit`, already added to `paper/refs.bib`.

Why this project holds it: NVBit works at the `SASS` level. `SASS` is the
machine instruction set the NVIDIA driver ultimately executes, and the file
that carries `SASS` is a `cubin`. That is exactly the class of program the PTX
rewriting route in this repository cannot modify, which is the coverage problem
recorded in the eGPU entry above.

Verified from the full text and from the NVBit repository's own README:

- Source is not required, quoted from the NVBit README: `Because NVBit does not
  require application source code, any pre-compiled GPU application should work
  regardless of which compiler (or version) has been used`.
- Supported hardware, quoted from the same README:
  `SM compute capability: >= 3.5 && <= 12.1`. The verification GPU here is
  compute capability 12.0, inside that range. The same README also states
  `CUDA driver version: <= 575.xx`, while the verification platform runs driver
  595.84, so the combination has not been tried here.
- How much execution sits inside pre-compiled libraries, quoted from Section
  6.1: `To understand the extent of this problem we have also applied an
  optimized version of the instruction count tool in Listing 1 and measured the
  percentage of instructions executed by these workloads inside pre-compiled
  libraries. This percentage ranges from 74% to 96%, and averages 88% of the
  total executed instructions across the various ML workloads.` Immediately
  after that: `NVBit instead does not require source code and it works on any
  application binary that makes use of these libraries, applying instrumentation
  at run-time only on the kernels that are actually invoked.`
- Cost: instrumenting every instruction is on average 36.4x slower than native
  execution and up to 112x in the worst case; sampling instead of full
  instrumentation brings the figure down to 2.3x.

---

## `ju2026-tilelens-two-dimensional-memory-layout.pdf`

The closest published comparison to this project: an HBF evaluation that runs
the same model this project's vLLM use case runs, on the same class of GPU, but
computes its timing in an offline simulator.

> Jae Hyung Ju, Euijun Chung, Hritvik Taneja, Anish Saxena, Shinnung Jeong,
> Hyesoon Kim, and Moinuddin K. Qureshi.
> "TileLens: Efficiently Using Large-Granularity Memory Systems with
> Transparent Two-Dimensional Memory Layout." arXiv:2607.04031.

- Affiliation: Georgia Institute of Technology.
- Stable link: <https://arxiv.org/abs/2607.04031>
- SHA-256: `0eb4570c8c1947617711c670ad1451edb6c0504c77ab781b150026db4d4b7670`
- 13 pages, 1,012 KB.
- Companion plain text: `ju2026-tilelens-two-dimensional-memory-layout.txt`

Verified against the full text:

- The problem it names: HBF and RoMe read at kilobyte granularity. Quoted from
  the Figure 1 caption: `HBF and RoMe both require a minimum access granularity
  of 4 KB, 128× coarser than HBM's 32 B sectors.` The tiled matrix
  multiplication that dominates large language model inference computes on
  two-dimensional tiles while memory is laid out one-dimensionally, and the two
  do not line up. Quoted: `Under one-dimensional (row- or column-major) memory
  layout, each 4 KB access fetches data mostly outside the compute tile,
  wasting bandwidth and stalling CTAs that await the same memory request.` (A
  CTA, cooperative thread array, is the group of GPU threads that NVIDIA's
  programming model schedules as one unit.)
- The name and the size of the effect: fetching bytes the computation does not
  want, because the smallest readable unit is larger than the piece wanted, is
  called *read amplification*. The abstract states that read amplification
  slows tiled matrix multiplication `by up to an order of magnitude`.
- Why cache does not absorb the extra bytes, quoted: `on an NVIDIA H200 GPU
  with 132 SMs, a 32× amplification factor over 128 KB of shared memory per SM
  produces 528 MB of amplified data per iteration, far exceeding the 50 MB L2
  cache size.` (An SM, streaming multiprocessor, is one of the GPU's
  independent compute units.)
- The fix: make one contiguous block of memory hold a two-dimensional rectangle
  of the matrix instead of a one-dimensional strip. `TileLens-SW` extends GPU
  domain-specific languages, where CUTLASS and FlashAttention need only their
  layout descriptors changed; `TileLens-HW` extends the batched tensor movement
  engine, so cuBLAS and DeepGEMM get the new layout with no source change.
- Evaluation method, quoted: `We use Macsim, a cycle-level GPU simulator,
  extended with DRAM, RoMe, and HBF memory models. Kernel traces are collected
  from an NVIDIA H200 GPU using a SASS-level tracer built on NVBit.`
- HBF page read latencies swept: 1, 2, 5, 10 and 20 µs.

What this project uses -- four separate things, written out in full because each
one touches a decision already open in this repository:

1. **The closest comparison work we have.** Of the four published HBF
   evaluation papers found so far (FlashAccel, H3, HAVEN, TileLens), TileLens is
   the only one that uses both Qwen3-30B and real H200 hardware, and Qwen3-30B
   is the model this project's vLLM use case runs. H3 and HAVEN are not stored
   in this directory.
2. **Its evaluation method sits between the two options this project is
   choosing between.** Traces are collected on a real GPU with NVBit, but the
   timing is computed offline in Macsim and never fed back to the real machine
   -- one variant of whole-machine simulation. The tracing tool it uses, NVBit,
   is the same tool at issue in this project's open question of whether the
   injector stays at the `PTX` level or moves to the `SASS` level.
3. **The effect TileLens measures is the state this project's implementation is
   in right now.** Every access that falls inside a registered range is charged
   for a whole page: in `src/cuda_runtime/device/hbf_device.cuh`,
   `media_descriptor` sets `bytes` to `range.page_bytes`. That is read
   amplification at its most extreme. The effect TileLens measures should
   therefore be reproducible here, which cuts two ways: the model being built
   has a consequence that shows up in measurements, and that consequence has
   already been measured by someone else.
4. **A set of parameters to line up against**: HBF page read latency of 1, 2,
   5, 10 and 20 µs.

---

## `sano2023-cxl-microsecond-latency-gpu-graph.pdf`

The only work found so far that adds controllable, per-access latency to a real
GPU's memory accesses while the program under test runs unmodified.

> Shintaro Sano, Yosuke Bando, Kazuhiro Hiwada, Hirotsugu Kajihara,
> Tomoya Suzuki, Yu Nakanishi, Daisuke Taki, Akiyuki Kaneko, and
> Tatsuo Shiozawa.
> "GPU Graph Processing on CXL-Based Microsecond-Latency External Memory."
> In *SC-W 2023*. arXiv:2312.03113.

- Affiliation: KIOXIA.
- DOI: `10.1145/3624062.3624173` -- <https://arxiv.org/abs/2312.03113>
- SHA-256: `99942318cffa700cbb7f7988f893724ee5eb6076653f8fd0fbba86a8a0127a43`
- 11 pages, 953 KB.
- Companion plain text: `sano2023-cxl-microsecond-latency-gpu-graph.txt`

What it does, verified from the full text: it evaluates graph processing on a
real GPU whose data is held in external memory with microsecond-scale latency.
The hardware is an NVIDIA RTX A5000 together with five Intel Agilex 7 FPGA
development boards. Three quotations, with line numbers in the companion `.txt`:

- Line 822: `The CXL interface has two instances of CXL.mem each connecting to
  latency bridges that we designed to introduce additional latency to the
  onboard DRAM`
- Line 824: `The behaviors of the latency bridges can be controlled by setting
  registers via CXL.io.`
- Line 920: `the graph processing code of EMOGI works on the CXL memory without
  any modification. The GPU performs zero-copy access in the same way as does
  to the host DRAM`

`pdftotext` breaks each of these three sentences across two to four lines, so
read a few lines either side of the number.

The appendix gives the mechanism: an incoming read request is timestamped; when
the data comes back from DRAM, the data and the timestamp are pushed into a
FIFO together; the entry is popped only once the current time passes that
timestamp plus the configured additional latency.

What this project uses: a fourth way of obtaining complete coverage that this
project's first challenge left out -- build a programmable stand-in device
outside the program under test, and let the real accelerator access that device
the way it normally would. The sentence in this project's argument saying there
are only three ways to obtain complete coverage does not hold as written. The
correction has to admit this fourth way and say why the fourth way does not
apply to HBF: the memory emulated by the FPGA boards in this paper hangs outside
the package, so every GPU read really does leave the chip and land on an FPGA,
whereas HBF is connected inside the package over UCIe, and that link offers no
socket into which a stand-in device could be inserted. This entry is also the
most direct support for narrowing the first challenge from "there is no place to
insert instrumentation" to "there is no place when three conditions hold at the
same time"; this entry does not enumerate the three conditions.

---

## `micron2026-is-hbf-all-you-need.pdf`

The published work that argues most directly against HBF, and the one the paper
from this project has to answer. It takes SK hynix's H3 as its opponent by name
and concludes that LPDDR placed inside the same package as the GPU matches or
beats HBF at every operating point it evaluates.

> "Is High-Bandwidth Flash All You Need?"
> In *HotInfra '26*, co-located with ISCA '26, 2026-06-28.
> Vinicius Petrucci, Felippe Zacarias, and Vishal Tanna, Micron Technology,
> San Jose, CA, USA. Read from page 1 of the PDF.

- Source: <https://hotinfra.org/2026/papers/hotinfra26-final83.pdf> -- fetched
  this round, HTTP 200, `application/pdf`, 348,270 bytes. The address was
  retrieved and used, not inferred from a naming pattern.
- SHA-256: `e0333b59a3a89a8ca6b9546c92d765db4bc46c4bd6297fd1945b2e5bc2e685b6`.
- 5 pages, 348,270 bytes.
- Companion plain text: `micron2026-is-hbf-all-you-need.txt`, 22,975 bytes.

What this project uses, and why this is the most important new file here:

1. **Its power numbers.** In-package LPDDR at 2 TB/s draws 95 W, against 549 W
   per GPU for HBF -- 5.8 times lower. Against HBM alone the factor reaches 5.6
   at most.
2. **Its result on when HBF is used at all.** Under the allocation policy
   production systems run by default, the one that places weights first, HBM
   plus HBF and HBM alone differ by less than 0.4% at every operating point.
   HBF only starts doing work once expert offloading is turned on -- that is,
   once the expert weight sets of a mixture-of-experts model are kept outside
   GPU memory and fetched when a request selects them.
3. **It turns the objections into numbers.** Its reasons are the same ones the
   Chinese commentary listed below uses -- swapping LoRA adapters (small
   fine-tuning weight sets loaded on top of a base model), model versions,
   drift in which experts a workload selects -- but where the commentary
   argues, this paper computes.

Boundaries, all four of which have to travel with any citation of it:

- 5 pages, a workshop paper, not a full evaluation.
- The method is a purely analytical roofline model: performance is bounded on
  paper by whichever runs out first, compute rate or memory bandwidth, with no
  code run.
- It is built on public information and carries its own disclaimer saying so.
- Micron sells both DRAM and NAND, so advocating in-package LPDDR is not a
  neutral position.

None of that makes it settled. It does make it a paper that must be cited and
answered before submission.

---

## `kim2026-hbf-workload-and-roadmap-slides.pdf`

Seminar slides on HBF workloads and the roadmap for the technology. Slides, not
a paper: the one page this project cares about carries curves with no numbers on
either axis, so nothing here can be used as a measurement.

> Joungho Kim (김정호), Professor, School of Electrical Engineering, Korea
> Advanced Institute of Science and Technology (KAIST).
> *HBF Technology: Workload Analysis and Roadmap.* KAIST online seminar,
> 2026-02-10.

- Original filename: `HBF_Workload_and_Roadmap_Joungho_Kim.pdf`; the creation
  time recorded inside the PDF is 2026-02-10, matching the seminar date.
- Obtained by pulling the real link out of the HTML of
  <https://tera.kaist.ac.kr/home> and downloading it from there.
- SHA-256: `7e4af8289b1a9fac887ba42e464bcb90149e84a3c97b130b5a923b020e49de9c`.
- 95 pages, 5,138,830 bytes.
- Press coverage calls the speaker the `father of HBM`; the source checked this
  round for that phrase is the English edition of 아시아경제, 2026-02-10.

What this project uses: page 71 onward is `Part6: Write Endurance and Signal
Integrity Designs`. Page 72, titled `HBF: Durability of Writing, NAND Flash`,
holds two curves that cross. Its horizontal axis is NAND process and type; it
has two vertical axes, write cycle count and NAND data retention time.

Two boundaries, because that page is easy to over-read:

1. **What the two curves trade against each other is write cycle count against
   retention time -- not write speed, and not performance.** A page that shows
   more write cycles bought with shorter retention says nothing about how fast a
   write completes.
2. **The page carries no numbers; it is a schematic.** Any figure taken from it
   would be read off a drawing.

Neither of the two slide decks from this speaker contains a page about
shortening the retention period to obtain faster writes or higher performance.
That claim, wherever else it shows up, is not supported from here.

---

## `kim2026-future-of-hbm-hbf-hbs-slides.pdf`

The second slide deck from the same speaker, held for completeness. The talk
title, the venue and the date for this deck are not recorded in this entry; the
file name is the only descriptor handed over with it.

> Joungho Kim (김정호), Professor, School of Electrical Engineering, Korea
> Advanced Institute of Science and Technology (KAIST). Slides, 2026.

- SHA-256: `e733aaee54d74cec592e53752782690ad1bfed91a9770a7955675543449c787e`.
- 130 pages, 12,317,276 bytes.

What this project uses: nothing on retention or endurance, and the useful part
of that is knowing not to look here. Searching the text of all 130 pages for
`retention`, `endurance`, `refresh` and `SLC` returns no occurrence of any of
the four. Their absence from this deck is a fact about the deck, not evidence
about HBF.

---

## `semiinsights2026-can-hbf-work-cn.txt`

A Chinese-language commentary arguing that HBF will not work, aimed at SK
hynix's H3 paper. Held for two reasons: it is the list of objections this
project's paper will be read against, and it ends with a full Chinese
translation of H3.

> 半导体行业观察。《下一个HBM：HBF，能行吗？》。微信公众号文章，2026-02-20。

- Source: <https://mp.weixin.qq.com/s/6xrtaV3wJ1qkZ1P1-IAO6g>
- Retrieval: `WebFetch` came back with WeChat's 「环境异常」 verification page
  instead of the article. Fetching again with `curl` carrying a WeChat client
  User-Agent returned the real page.
- SHA-256: `e5612856e6fcb2ffbc8437d985cb0810223ee366fc376ddfe483b7ceff561fa0`
- 35,072 bytes; 14,392 characters of body text.
- Plain text only. There is no PDF capture of this article in this directory,
  so this entry breaks the pattern where every `.txt` sits beside a PDF.

What this project uses:

1. **Its seven objections**, which are the questions a reviewer is likely to
   raise: the read-only assumption, the physical floor on latency, cost
   structure, yield and heat dissipation, market indifference, competing
   routes, and reliability validation.
2. **The H3 translation at the end.** We do not hold the H3 original (see the
   section below), so this translation is the only full-length rendering of H3
   in the repository. It is second-hand, and every sentence taken from it is
   marked second-hand.

Boundaries:

- **This is a commentary, not a paper.** It treats a 4-page IEEE Computer
  Architecture Letters short paper as if that paper were the complete case for
  HBF technology, then refutes that.
- **Three of its claims were checked this round and are wrong.** First, the
  20 µs figure it returns to repeatedly is not a physical property of HBF: H3
  took it as an input assumption from published SLC NAND read latency, and H3
  itself writes that the figure is expected to improve once the technology is
  commercialised. Second, it states that HBF 「还需要单独的 DRAM 来运行 FTL」 --
  that HBF needs separate DRAM to run the flash translation layer, the map from
  logical addresses to physical flash pages that an SSD controller keeps. The
  specification has neither on-die DRAM nor on-die garbage collection; wear
  levelling is the host's job, done through zone remapping. Third, its
  market-indifference verdict was published on 2026-02-20, while the first OCP
  specification came out on 2026-08-03 with Google and Tenstorrent taking part
  in it.

---

## Not available: the H3 paper itself, behind a subscription paywall

H3 is named as an opponent by the Micron paper above, translated in full by the
Chinese commentary above, and listed in the TileLens entry as one of the four
published HBF evaluations. The original is not in this directory. Its citation,
checked this round:

> Minho Ha, Euiseok Kim, and Hoshik Kim, all with SK hynix Inc., Icheon-Si,
> South Korea. *IEEE Computer Architecture Letters*, volume 25, issue 1,
> pages 49--52, January 2026.
> DOI: `10.1109/LCA.2026.3660969`
> IEEE Xplore document number 11371745.

The paper's title is `H3: Hybrid Architecture using High Bandwidth Memory and
High Bandwidth Flash for Cost-Efficient LLM Inference`.

**Why the full text is missing: a subscription paywall, not bot protection and
not a wrong address.** OpenAlex returns `oa_status = closed` and
`any_repository_has_fulltext = false` for this record, so no repository copy
exists to fall back on. Someone with an IEEE subscription has to export it from
a browser; nothing else will work, and no further command-line attempt is worth
making.

The abstract was retrieved. The only number in it is `up to 2.69x higher
throughput per power`. Six further numbers circulate through the Chinese
commentary above -- 40MB SRAM, 1.25x at 1M, 6.14x at 10M, 20 µs, 16x capacity,
and power up to 4 times that of HBM -- and all six are second-hand until the
paper itself is read. What the 1M and the 10M count is not recorded in this
entry either.
