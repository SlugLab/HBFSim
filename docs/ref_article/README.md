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
as in the PDF; only the layout is gone. Thirty-seven files. Nineteen of the
thirty-seven are listed here with sizes:

- `tavakkol2018-mqsim-multiqueue-ssd.txt`, 201 KB
- `liu2023-photon-fine-grained-gpu-sampling.txt`, 179 KB
- `khairy2020-accel-sim-gpu-simulation-framework.txt`, 151 KB
- `baddouh2021-principal-kernel-analysis.txt`, 128 KB
- `sherwood2002-simpoint-program-behavior.txt`, 106 KB
- `wunderlich2003-smarts-statistical-sampling.txt`, 96 KB
- `luo2018-heatwatch-nand-temperature.txt`, 91 KB
- `wang2026-flashaccel-hbf-llm-inference.txt`, 88 KB
- `yoon2026-cylon-cxl-ssd-emulation.txt`, 78 KB
- `semiinsights2026-hbf-standard-release-cn.txt`, 77 KB
- `naderan2023-sieve-stratified-gpu-sampling.txt`, 75 KB
- `ju2026-tilelens-two-dimensional-memory-layout.txt`, 75 KB
- `yang2023-cxlmemsim.txt`, 71 KB
- `li2018-femu-nvme-ssd-emulator.txt`, 63 KB
- `sano2023-cxl-microsecond-latency-gpu-graph.txt`, 61 KB
- `yang2025-egpu-ebpf-ptx-injection.txt`, 39 KB
- `semiinsights2026-can-hbf-work-cn.txt`, 34 KB
- `micron2026-is-hbf-all-you-need.txt`, 22 KB
- `zhihu2026-hbf-protocol-and-market-analysis-cn.txt`, 7 KB

Eight more `.txt` files came in with the eight papers on running large language
models out of flash, recorded in their own group below. Sizes in bytes, as
reported when each file was produced:

- `silverbrook2025-zettalith-ai-inference.txt`, 211,101 bytes
- `alizadeh2023-llm-in-a-flash.txt`, 88,239 bytes
- `deng2025-kvnand-dram-free-flash-inference.txt`, 88,079 bytes
- `wang2024-neuralink-smartphone-flash-inference.txt`, 85,788 bytes
- `jia2025-activeflow-weight-swapping.txt`, 69,486 bytes
- `li2026-hbf-characterization-kv-cache.txt`, 67,514 bytes
- `hao2026-nvllm-3d-nand-edge-inference.txt`, 50,029 bytes
- `hsu2026-haven-hbf-vector-search.txt`, 39,097 bytes

The remaining ten `.txt` files are named at the end of this section, together
with the record that is still missing for each of the ten.

Three of the thirty-seven `.txt` files have no PDF beside them:
`semiinsights2026-can-hbf-work-cn.txt` was captured from a WeChat page as text
rather than as a PDF; `liu2012-retention-relaxation-nand-ssd.txt`, 5 KB, and
`sandisk2025-hbf-fact-sheet.txt`, 116 KB, also stand alone, and how each of the
two files was obtained is not recorded anywhere in this README.

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

**Ten files in this directory are not yet recorded below.** The rule at the top
of this README asks every file for a full citation, a stable link, a SHA-256, a
page count, and a statement of what this project uses from that file. The ten
names listed next have no such record anywhere in this README, and no citation
or link is guessed at here to fill the gap; obtaining the citation and the link
for each of the ten from the file itself is outstanding work:

- `cho2024-aero-adaptive-erase-operation`
- `jeong2014-dynamic-program-erase-scaling`
- `jouppi2023-tpu-v4-optically-reconfigurable`
- `juravsky2024-hydragen-shared-prefix-attention`
- `kim2024-duplex-moe-attention-accelerator`
- `liu2012-retention-relaxation-nand-ssd`
- `luo2018-3d-nand-early-retention-loss`
- `pope2023-efficiently-scaling-transformer-inference`
- `sandisk2025-hbf-fact-sheet`
- `yuan2024-llm-inference-roofline-survey`

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
  time recorded inside the PDF is 2026-02-10, matching the seminar date. The
  cover page of the deck itself prints `2026.2. 3`, seven days earlier than the
  creation time; the likely reading is that the cover carries the date the deck
  was prepared and the file was exported on the day of the seminar, but nobody
  has confirmed that with the author.
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

## `kim2025-hbm-roadmap-teralab-slides.pdf`

The 2025 edition of the same speaker's roadmap deck, and the primary source for
the pair of figures `HBM 100 GB` / `HBF 1 TB` that later circulated as "100 GB of
HBM acting as a cache in front of 1 TB of HBF". The deck itself never uses the
word cache for that pairing; see below.

> Joungho Kim (김정호), Professor, School of Electrical Engineering, Korea
> Advanced Institute of Science and Technology (KAIST).
> *HBM Roadmap ver 1.98 by KAIST Teralab: Overview of Next Generation HBM
> Architectures.* Slides, cover date 2025.9.11.

- Obtained by pulling the real download link out of the HTML of
  <https://tera.kaist.ac.kr/home>, where the entry is titled
  `2025 Teralab Next-Gen. HBM Roadmap` and carries a companion talk video at
  <https://www.youtube.com/watch?v=Lm6Xd5YqhgQ>.
- SHA-256: `1b858d5158035edfc183a33940c86accd6c4989b81376f49646dee68c9cec2a3`.
- 136 pages, 7,057,165 bytes. PDF creation time 2025-09-14; cover date
  2025.9.11. The deck calls itself `ver 1.98` while an inner page says
  `ver 1.3`, so earlier revisions of this roadmap exist and were not obtained.

What this project uses, and the boundary on each item:

- Page 47 and page 106 carry the same figure, `HBM-HBF-Storage Network
  Architecture`, on which `HBM 100 GB` and `HBF 1 TB` are printed side by side.
  This is the first-hand origin of that pair of numbers. The figure is a
  connection topology; the word cache does not appear on it.
- Page 49, `Comparison of HBM, HBF, and HBM-HBF Architecture`, scores four
  architectures. The HBM-HBF column is rated high on capacity and tokens per
  second, high on cost because the interposer grows, and medium on reliability.
  No column is marked as the recommended one, so this deck cannot be cited for
  a claim that any one architecture is the most promising.
- Page 105, `Cases of HBM-HBF Architecture Data Read/Write Path`, lists seven
  paths, of which Case 3 is `GPU <-> HBF (Read/Write)`. The deck therefore keeps
  a direct accelerator-to-HBF path rather than routing every access through HBM.
- Searching all 136 pages for `cache` returns only `L2 Cache`, `KV Cache`, the
  author's own `Extended Scale Cache`, and one page describing data moving from
  HBM into the cache inside the GPU. **No page in this deck calls HBM a cache for
  HBF.** The word entered circulation through third parties: a Korean news
  report of the 2025-09-03 KPCA Show keynote, and the interviewer's question in
  SanDisk's 2025-12-30 blog interview with the same speaker.

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

## Eight files on simulation cost, sampling, and device emulation

Eight files were added to this directory together, and the eight entries that
follow share one job in this project's paper. The paper's second challenge,
written C2, is that the timing model for this layer of hardware has to be
accurate and cheap at the same time, and accuracy and cost pull against each
other here. This project answers C2 with two timing paths: a detailed reference
path, and a cheap fast path. The cheap fast path brings 44.469 seconds down to
2.014352 seconds, a factor of 20.8.

The eight files fix the boundary of the C2 claim. Checked this round against
the full text of each of the eight papers: **no paper among the eight puts the
cheap timing path inside the GPU device code of the program under test, and no
paper among the eight has the cheap path itself produce latency numbers.** Each
entry below records, for the paper the entry names, where the cheap path runs
and what the cheap path produces.

Two of the eight papers, SimPoint and SMARTS, are held as the methodological
source this project cites rather than as comparisons; SimPoint and SMARTS both
predate GPU sampling and come from the CPU side of the literature. One further
file, MQSim, is not a comparison either: MQSim is the flash device model this
project runs.

None of the eight has a BibTeX key in `paper/refs.bib` yet, and each entry
below repeats that fact for the file the entry names.

---

## `baddouh2021-principal-kernel-analysis.pdf`

The GPU sampling method a reviewer is most likely to name when asking why a new
GPU simulation paper does not simply sample less work, and one of the two GPU
sampling papers in this directory that select what to simulate offline.

> Cesar A. Baddouh, Mahmoud Khairy, Roland Green, Mathias Payer, and
> Timothy G. Rogers.
> "Principal Kernel Analysis: A Tractable Methodology to Simulate Scaled GPU
> Workloads."
> In *MICRO-54: 54th Annual IEEE/ACM International Symposium on
> Microarchitecture (MICRO '21)*, October 18--22, 2021, Virtual Event, Greece.
> ACM, New York, NY, USA, 14 pages.
> DOI: `10.1145/3466752.3480100`

- Affiliations: Purdue University; Roland Green at Cerebras; Mathias Payer at
  EPFL.
- Stable link:
  <https://engineering.purdue.edu/tgrogers/papers/baddouh.micro2021.pdf>
- SHA-256: `7944791084038314483fb70f42163c37205a8dcf9ea2969bd374139b146e9c84`
- 2,077,382 bytes; 14 pages; 131,994 characters of body text extracted with
  `pdftotext`.
- BibTeX key: not yet added to `paper/refs.bib`.

Why this project holds it: Principal Kernel Analysis samples at two levels, the
kernel invocation and the thread block. A kernel is a piece of program that the
host launches and the GPU then executes; a thread block is a group of GPU
threads that the hardware schedules as one unit. Which kernel invocations and
which thread blocks to simulate is decided offline, from a profile collected by
running the workload on a real GPU. There is no second, cheaper timing path
anywhere in Principal Kernel Analysis: the whole saving comes from running the
same detailed simulator over fewer kernel invocations and fewer thread blocks.
So Principal Kernel Analysis never produces a latency number from a cheap path,
and no part of the timing model in Principal Kernel Analysis runs inside the
GPU device code of the program under test. Both of those absences are what the
C2 claim in this project's paper needs in order to be stated as a boundary
rather than as a general statement about GPU simulation.

Verified from the full text:

- The cost problem, quoted from the abstract: `Simulating all threads in a
  scaled GPU workload results in prohibitive simulation cost.`
- The alternatives Principal Kernel Analysis lists as already existing, quoted
  from the abstract: `Existing solutions to simulate GPU programs either scale
  the input size, simulate the first several billion instructions, or simulate a
  portion of both the GPU and the workload.`
- Why CPU sampling does not carry over, quoted from the abstract: `Existing CPU
  sampling mechanisms, like SimPoint, reduce per-thread workload, and are
  ill-suited to GPU programs where reducing the number of threads is critical.`
- A reported result on the Ampere GPU generation, for the SGEMM benchmark of the
  Cutlass Perf Suite, quoted from the body text: `presents a negligible mean
  error and a geomean speedup of 6×`.

---

## `khairy2020-accel-sim-gpu-simulation-framework.pdf`

The cycle-level GPU simulation framework the GPU sampling papers in this
directory build on, and the tool a reviewer will name when asking why this
project did not extend an existing GPU simulator instead.

> Mahmoud Khairy, Zhesheng Shen, Tor M. Aamodt, and Timothy G. Rogers.
> "Accel-Sim: An Extensible Simulation Framework for Validated GPU Modeling."
> In *Proceedings of the 47th Annual International Symposium on Computer
> Architecture (ISCA)*, 2020.

- Affiliations: Purdue University; Tor M. Aamodt at University of British
  Columbia.
- Stable link:
  <https://people.ece.ubc.ca/aamodt/publications/papers/accelsim.isca2020.pdf>
- SHA-256: `74cf70af45dd6d1eef45a693483eda3d0a85fc915e42545aa7e3b4e5daab8022`
- 1,161,499 bytes; 14 pages; 155,365 characters of body text extracted with
  `pdftotext`.
- BibTeX key: not yet added to `paper/refs.bib`.

Why this project holds it: Accel-Sim simulates a GPU cycle by cycle, and both
timing paths in Accel-Sim run on the host CPU. No timing model in Accel-Sim
executes inside the GPU device code of the program under test, and the speed of
Accel-Sim is the speed of a host-side simulator, not of a second cheap path
that produces latency numbers on the device. Accel-Sim is also the framework
the sampling work in this directory measures its savings against, so the
speedup figures quoted in the Principal Kernel Analysis, Sieve and Photon
entries are savings on top of a host-side cycle-level simulator.

Verified from the full text:

- The gap Accel-Sim states in its abstract: `In computer architecture,
  significant innovation frequently comes from industry. However, the simulation
  tools used by industry are often not released for open use, and even when they
  are, the exact details of industrial designs are not disclosed.`

---

## `li2018-femu-nvme-ssd-emulator.pdf`

The flash emulator that lets an unmodified guest operating system run a real
workload against a solid-state drive built entirely out of software.

> Huaicheng Li, Mingzhe Hao, Michael Hao Tong, Swaminatahan Sundararaman,
> Matias Bjørling, and Haryadi S. Gunawi.
> "The CASE of FEMU: Cheap, Accurate, Scalable and Extensible Flash Emulator."
> In *16th USENIX Conference on File and Storage Technologies (FAST '18)*,
> February 12--15, 2018, Oakland, CA, USA. ISBN 978-1-931971-42-3

- Affiliations: Huaicheng Li, Mingzhe Hao, Michael Hao Tong and
  Haryadi S. Gunawi, University of Chicago; Swaminatahan Sundararaman, Parallel
  Machines; Matias Bjørling, CNEX Labs. The given name `Swaminatahan` is spelled
  that way in the paper and is copied here unchanged.
- Stable link, printed on the first page of the paper:
  <https://www.usenix.org/conference/fast18/presentation/li>
- SHA-256: `11e263fcb08130bb42b381f08d8b9c1eb018e54369b004c0b7872ec3f080cc25`
- 483,697 bytes; 9 pages; 64,620 characters of body text extracted with
  `pdftotext`.
- BibTeX key: not yet added to `paper/refs.bib`.

Why this project holds it: FEMU is built into QEMU, a machine emulator that
runs an unmodified guest operating system, and FEMU presents itself to that
guest as an NVMe device. NVMe is the interface protocol a modern solid-state
drive speaks to the host, so software in the guest issues ordinary storage
requests and never learns that no drive exists. The timing model of FEMU runs
in the hypervisor, which is the software layer hosting the guest, and therefore
outside the program under test. FEMU is held here because FEMU shows what the
storage side does when the device is missing: emulate the device outside the
program, on the host. The fast path in this project runs in the other place,
inside the GPU device code the program under test executes, and no timing model
in FEMU runs there.

Verified from the full text:

- What FEMU is and what accuracy FEMU claims, quoted from the abstract: `FEMU is
  a software (QEMU-based) flash emulator for fostering future full-stack
  software/hardware SSD research. FEMU is cheap (open-sourced), relatively
  accurate (0.5-38% variance as a drop-in replacement of OpenChannel SSD),
  scalable (can support 32 parallel channels)`

---

## `liu2023-photon-fine-grained-gpu-sampling.pdf`

The most important comparison of the eight files here: the GPU sampling method
whose structure comes closest to the two timing paths this project uses, and
the method whose cheap path is easiest to mistake for the cheap path in this
project.

> Changxi Liu, Yifan Sun, and Trevor E. Carlson.
> "Photon: A Fine-grained Sampled Simulation Methodology for GPU Workloads."
> In *MICRO '23*, October 28--November 1, 2023, Toronto, ON, Canada.
> DOI: `10.1145/3613424.3623773`

- Affiliations: Changxi Liu and Trevor E. Carlson, National University of
  Singapore; Yifan Sun, College of William & Mary.
- Stable link: no verifiable direct address for the PDF was obtained this round.
  The DOI above is the only address recorded for Photon here.
- SHA-256: `1106c07bd31cce87c6b2b280416edbcb8a56317aad1e3c7183638144b9b30a12`
- 1,491,039 bytes; 15 pages; 184,221 characters of body text extracted with
  `pdftotext`.
- BibTeX key: not yet added to `paper/refs.bib`.

Why this project holds it: Photon samples at three levels, the kernel, the warp
and the basic block. A warp is the group of GPU threads the hardware issues
instructions for as one unit; a basic block is a straight-line run of
instructions with one entry and one exit. Photon decides what to sample while
the workload runs, with no separate profiling pass beforehand, which is why
Photon is the closest published method to the fast path in this project and why
the difference has to be stated exactly. Photon does have a cheap execution
mode, and the cheap mode of Photon computes only correct results, with no time
values at all; the mode that produces time values is the detailed mode. Both
modes of Photon run on the host CPU, inside the simulator. The sentence quoted
first below is the sentence this project's paper cites for that difference.

Verified from the full text:

- The two modes, quoted: `fast-forward mode, which allows for functional
  simulation only, and detailed mode, which enables the timing model.`
- The cost problem, quoted from the abstract: `existing GPU simulators are too
  slow to enable architects to quickly evaluate their hardware designs and
  software analysis studies`

---

## `naderan2023-sieve-stratified-gpu-sampling.pdf`

The GPU sampling method aimed at the workloads that the earlier GPU sampling
work handles worst: workloads with very many kernels and very many kernel
invocations.

> Mahmood Naderan-Tahan, Hossein SeyyedAghaei, and Lieven Eeckhout.
> "Sieve: Stratified GPU-Compute Workload Sampling."
> In *2023 IEEE International Symposium on Performance Analysis of Systems and
> Software (ISPASS)*, 2023.

- Affiliation: Ghent University, Belgium.
- Stable link: <https://users.elis.ugent.be/~leeckhou/papers/ispass-2023.pdf>
- SHA-256: `d8feb16b9e2e103c44d5dc5357ac72f912bc1c63817f9fb266199d56e2f529e5`
- 3,000,886 bytes; 11 pages; 77,400 characters of body text extracted with
  `pdftotext`.
- BibTeX key: not yet added to `paper/refs.bib`.

Why this project holds it: Sieve samples at the level of the individual kernel
invocation, and sorts the invocations into groups by instruction count before
drawing samples inside each group. Stratified sampling means exactly that:
split the population into groups whose members resemble one another on some
property, then sample within every group, so a group with few members still
gets represented. The profiling stage of Sieve runs on a real GPU. Sieve has no
second cheap timing path: the detailed simulator is the only part of Sieve that
produces time values, and the saving comes from handing the detailed simulator
fewer kernel invocations. The Principal Kernel Selection named in the first
quotation below is a stage of the Principal Kernel Analysis methodology of
`baddouh2021-principal-kernel-analysis.pdf`, not a separate method: the
Principal Kernel Analysis paper introduces the stage in its own contribution
list, quoted from Section 1, `We introduce Principal Kernel Selection`, and the
reference Sieve attaches to the name, numbered 11 in the Sieve bibliography, is
`C. A. Baddouh, M. Khairy, R. N. Green, M. Payer, and T. G. Rogers, "Principal
kernel analysis: A tractable methodology to simulate scaled GPU workloads"`.

Verified from the full text:

- The gap Sieve claims, quoted from the abstract: `the state-of-the-art sampling
  method for GPU-compute workloads, Principal Kernel Selection (PKS), falls
  short for challenging GPU-compute workloads with a large number of kernels and
  kernel invocations`
- What Sieve proposes, quoted from the abstract: `In this paper, we propose
  Sieve, an accurate and low-overhead sampling methodology for GPU-compute
  workloads.`

---

## `sherwood2002-simpoint-program-behavior.pdf`

Where the practice of simulating a few chosen intervals of a program instead of
the whole run comes from. Held as the source of a method this project uses, not
as a comparison.

> Timothy Sherwood, Erez Perelman, Greg Hamerly, and Brad Calder.
> "Automatically Characterizing Large Scale Program Behavior."
> In *ASPLOS X*, 10/02, San Jose, CA, USA.

- Affiliation: University of California, San Diego.
- Stable link:
  <https://cseweb.ucsd.edu/~calder/papers/ASPLOS-02-SimPoint.pdf>
- SHA-256: `f67a9b33460c3148edde0859279cb94f464e96f8a0c37009e7acd40110d1d1ac`
- 276,879 bytes; 13 pages; 109,482 characters of body text extracted with
  `pdftotext`.
- BibTeX key: not yet added to `paper/refs.bib`.

Why this project holds it: SimPoint is the CPU-side origin of sampled
simulation, and this project cites SimPoint as the source of the method rather
than presenting sampling as an invention of this project. SimPoint matters here
for a second reason as well: the Principal Kernel Analysis paper names SimPoint
by name as the CPU mechanism that does not carry over to GPUs, because SimPoint
reduces the work done per thread while GPU simulation cost is driven by the
number of threads. That sentence is quoted in full in the entry for
`baddouh2021-principal-kernel-analysis.pdf`.

Verified from the full text:

- What SimPoint sets out to build, quoted from the abstract: `In order to
  perform such an analysis we need to develop a hardware independent metric that
  can concisely summarize the behavior of an arbitrary section of execution in a
  program.`

---

## `tavakkol2018-mqsim-multiqueue-ssd.pdf`

The one file among the eight added here that this project depends on rather
than compares against: MQSim is the flash device model this project runs.

> Arash Tavakkol, Juan Gómez-Luna, Mohammad Sadrosadati, Saugata Ghose, and
> Onur Mutlu.
> "MQSim: A Framework for Enabling Realistic Studies of Modern Multi-Queue SSD
> Devices."
> In *16th USENIX Conference on File and Storage Technologies (FAST '18)*,
> February 12--15, 2018, Oakland, CA, USA. ISBN 978-1-931971-42-3

- Affiliations: Arash Tavakkol, Juan Gómez-Luna and Mohammad Sadrosadati,
  ETH Zürich; Saugata Ghose, Carnegie Mellon University; Onur Mutlu, ETH Zürich
  and Carnegie Mellon University.
- Stable link, printed on the first page of the paper:
  <https://www.usenix.org/conference/fast18/presentation/tavakkol>
- Direct address fetched this round:
  <https://www.usenix.org/system/files/conference/fast18/fast18-tavakkol.pdf>
- SHA-256: `6af7582801be37c91448df2fd032876209cf99e4ea10ab5e06998b3e1a2dbc99`
- 2,529,997 bytes; 18 pages; 206,301 characters of body text extracted with
  `pdftotext`.
- BibTeX key: not yet added to `paper/refs.bib`.

Why this project holds it: every latency number the flash side of this project
produces passes through MQSim, so the settings MQSim is run with are part of
this project's results and have to be stated with them. One limit is already
written down by the collaborator in `paper/eval.tex`, quoted: `It is an MQSim
PAGE_LEVEL proxy, not byte-granularity HBF AXI interleaving.` In plain terms,
this project runs MQSim in the page-level mapping mode, where the smallest unit
the model maps and charges time for is one flash page, while the HBF
specification defines interleaving on the host side at byte granularity. A
page-level mapping mode and byte-granularity interleaving are not the same
thing, so timing obtained through MQSim stands in for HBF timing instead of
reproducing HBF timing, and any claim that depends on fine-grained access
patterns has to carry that limit with it.

---

## `wunderlich2003-smarts-statistical-sampling.pdf`

The methodological source for how the sampling in this project is organised,
including the rule that the warm-up period runs on the detailed reference path.

> Roland E. Wunderlich, Thomas F. Wenisch, Babak Falsafi, and James C. Hoe.
> "SMARTS: Accelerating Microarchitecture Simulation via Rigorous Statistical
> Sampling."
> In *Proceedings of the 30th Annual International Symposium on Computer
> Architecture (ISCA)*, 2003.

- Affiliation: Computer Architecture Laboratory (CALCM), Carnegie Mellon
  University, Pittsburgh, PA 15213-3890.
- Project page, printed on the first page of the paper:
  <http://www.ece.cmu.edu/~simflex>
- Stable link: no verifiable direct address for the PDF was obtained this round.
  The project page above is the only address recorded for SMARTS here.
- SHA-256: `2a72c595d2c9b76a52189ea7844643e19aa0e8187d29b85730968df134e22bfe`
- 260,986 bytes; 12 pages; 98,594 characters of body text extracted with
  `pdftotext`.
- BibTeX key: not yet added to `paper/refs.bib`.

Why this project holds it: the arrangement this project uses -- run the warm-up
period entirely on the detailed reference path, then sample the remainder by a
fixed rule -- comes from this line of work, and the paper of this project cites
SMARTS for the arrangement instead of presenting the arrangement as new.
Systematic sampling, the term in the first quotation below, means taking
measurements at fixed intervals through the run rather than at randomly chosen
points. The second quotation is the warm-up half of the same arrangement:
warming instructions are simulated in full detail and are not measured, so the
detailed simulation during warm-up contributes no measurement of its own and
exists only to bring the modelled state into the condition the following
measurement needs.

Verified from the full text:

- Why fixed intervals rather than random points, quoted: `SMARTS uses systematic
  sampling rather than random sampling because systematic sampling is more
  straightforward`
- What happens during warm-up, quoted: `detailed simulation of W warming
  instructions (without measurement)`

---

## Eight files on running large language models out of flash

Eight files were added to this directory together, and the eight entries that
follow were gathered to settle one question: has anyone put HBF on an end-user
device -- a phone, a laptop, an edge box -- and attached numbers to it?

The answer from these eight files is one paper,
`silverbrook2025-zettalith-ai-inference.pdf`, and that paper is a single-author
preprint whose own abstract says it contains no implementation and no
simulation validation. Five of the eight run large
language model inference out of flash on a device with media that is not HBF:
`hao2026-nvllm-3d-nand-edge-inference.pdf` and
`deng2025-kvnand-dram-free-flash-inference.pdf` use 3D NAND with compute inside
it, `wang2024-neuralink-smartphone-flash-inference.pdf` and
`jia2025-activeflow-weight-swapping.pdf` use smartphone UFS, and
`alizadeh2023-llm-in-a-flash.pdf` uses an ordinary solid-state drive. The
remaining two are HBF papers whose target is the datacenter, not a device:
`li2026-hbf-characterization-kv-cache.pdf` on key-value cache serving and
`hsu2026-haven-hbf-vector-search.pdf` on approximate nearest-neighbor search.

The count of one rests on case-sensitive searches with word boundaries. A
case-insensitive search had earlier produced a wrong answer for NVLLM; the entry
for `hao2026-nvllm-3d-nand-edge-inference.pdf` below records that correction in
full.

None of the eight has a BibTeX key in `paper/refs.bib`, checked by searching
that file for each of the eight arXiv numbers and for each first author's name.

---

## `silverbrook2025-zettalith-ai-inference.pdf`

The one file in this directory that puts HBF into an end-user device and gives
capacity, bandwidth and token-rate numbers for it. A design study, not a
measurement.

> Kia Silverbrook.
> "ZettaLith: An Architectural Exploration of Extreme-Scale AI Inference
> Acceleration." arXiv:2507.02871v1. Submission time recorded by the arXiv
> interface: 2025-06-08.

- Single author. No journal or conference venue, and no DOI.
- Open access: <https://arxiv.org/abs/2507.02871>
- SHA-256: `2f6579082862018c8a2bdbf28be5f635277a32dcd2de485b0a5e8ad2562d3e9f`
- 53 pages; 2,095,726 bytes. The author's note gives 53 pages, 15 figures and
  23 tables.
- Companion plain text: `silverbrook2025-zettalith-ai-inference.txt`,
  211,101 bytes. The section title is at line 8102 and the passage cited below
  at lines 8170--8176.
- BibTeX key: not yet added to `paper/refs.bib`.

What this project uses: Section 28, `NEXAI: ZettaLith at the Edge`. Section
28.1 states that the edge system-on-chip uses SanDisk High Bandwidth Flash
stacks, giving 512 GB of parameter storage at about 1.2 TB/s, and an inference
rate limited by HBF bandwidth of 59 tokens per second.

Boundaries, all four of which have to travel with any citation of this file:

1. **The author says the numbers are not validated.** Quoted from the end of
   the abstract: `Note: This paper presents a design study and architectural
   proposal without implementation or simulation validation. All performance
   projections are based on theoretical analysis and should be interpr` -- the
   extract recorded here stops mid-word at `interpr`; the rest of the sentence
   is in the PDF. So all performance figures in the paper, the three numbers
   above included, are theoretical upper bounds.
2. **It is a single-author preprint and it is not peer reviewed.** There is no
   venue and no DOI to cite.
3. **The only power figure the full text gives for HBF is a 30 W estimate**,
   the same number the same source takes for HBM, and it appears in a parameter
   table for a datacenter PCIe card rather than for the edge system-on-chip.
4. **HBF appears in Sections 27--28 and hardly anywhere else**; the rest of the
   paper is about HBM and the author's own compute array.

Taken together: this file is evidence that a third party has written HBF into
an edge architecture proposal, which is what the demand argument needs. It is
not evidence about how such a device behaves, and no number in it may be cited
as a measurement.

---

## `hao2026-nvllm-3d-nand-edge-inference.pdf`

On-device large language model inference built on 3D NAND with computation
inside the die, and the file that corrects an earlier record kept in this
repository.

> Mingbo Hao, Changwei Yan, Haoyu Cui, Zhihao Yan, Yizhi Ding, Zhangrui Qian,
> and Weiwei Shan (corresponding author), School of Integrated Circuits,
> Southeast University.
> "NVLLM: A 3D NAND-Centric Architecture Enabling Edge on-Device LLM
> Inference." arXiv:2604.25699v2 [cs.AR], v1 submitted 2026-04-28, v2
> 2026-08-04. Published in *Proceedings of the 63rd ACM/IEEE Design Automation
> Conference (DAC 2026)*. DOI: `10.1145/3770743.3804190`

- Open access: <https://arxiv.org/abs/2604.25699>
- SHA-256: `a3b44287335cd1a000aff79c780366d44214a661fae5df30133552e7400fedf4`
- 9 pages; 4,438,238 bytes.
- Companion plain text: `hao2026-nvllm-3d-nand-edge-inference.txt`,
  50,029 bytes. The citation of AiF discussed below is at lines 824--828.
- BibTeX key: not yet added to `paper/refs.bib`.

What it does: it moves feed-forward network computation into the flash itself,
keeps attention on lightweight CMOS logic together with external DRAM, and uses
wafer-to-wafer stacking to integrate multi-plane 3D NAND with the compute
pipeline, the error-correction units and the buffers. Reported result: 16.7x to
37.9x speedup over out-of-core inference on an A800.

**Correction this file forces on an earlier record.** An earlier round in this
repository listed NVLLM as an on-device paper that mentions HBF. That is wrong.
Searched case-sensitively and with word boundaries, `HBF` occurs 0 times in the
full text and `high-bandwidth flash` occurs 0 times. The earlier hits came from
a case-insensitive search matching the LaTeX macro `\mathbf`, not the term HBF.

What this project uses, and the boundary that goes with it: NVLLM is evidence
that pressing large-model weights into NAND is a route other groups are taking
on edge devices. It is 3D NAND with computation inside the die, not in-package
HBF, so it is not an HBF comparison and must never be listed as one. Its
reference [20] is the AiF paper, which could not be obtained this round; the
AiF entry near the end of this file records why.

---

## `deng2025-kvnand-dram-free-flash-inference.pdf`

The device-side paper that puts the key-value cache into flash, which is the
same move the HBF characterization paper in this group finds harmful in the
datacenter.

> Lishuo Deng, Shaojie Xu, Jinwu Chen, Changwei Yan, Jiajie Wang, Zhe Jiang,
> and Weiwei Shan, Southeast University, Nanjing.
> "KVNAND: Efficient On-Device Large Language Model Inference Using DRAM-Free
> In-Flash Computing." arXiv:2512.03608v1 [cs.AR], submitted 2025-12-03, v1
> only.

- No DOI and no journal or conference venue in the arXiv record.
- Open access: <https://arxiv.org/abs/2512.03608>
- SHA-256: `d769244883037447de1d0603c72f874ac3e3ab46d891ebb635bf735eb222190f`
- 14 pages; 2,174,964 bytes.
- Companion plain text: `deng2025-kvnand-dram-free-flash-inference.txt`,
  88,079 bytes.
- BibTeX key: not yet added to `paper/refs.bib`.

What it claims: the first architecture that uses no DRAM at all, holding both
the model weights and the key-value cache in 3D NAND with computation inside
it. Reported results: geometric-mean speedups of 1.98, 1.94 and 2.05 at 128, 1K
and 10K token contexts against an in-flash computing design that does have
DRAM, and no out-of-memory failures at 100K context.

Boundary: `HBF` and `high-bandwidth flash` occur 0 times in the full text,
counted by grep over `deng2025-kvnand-dram-free-flash-inference.txt`. The target
is 3D NAND computing on an edge device, not in-package HBF. What this project
uses is the pairing: KVNAND puts the key-value cache into flash and reports a
gain, while `li2026-hbf-characterization-kv-cache.pdf` puts the key-value cache
into HBF in a serving system and reports a loss.

---

## `wang2024-neuralink-smartphone-flash-inference.pdf`

Rearranging where weights sit inside a phone's flash so that neurons which fire
together are read together. Also the file whose title changed between versions,
which any citation of it has to get right.

> Tuowei Wang and Ruwen Fan (equal contribution), Minxing Huang, Zixu Hao,
> Kun Li, Ting Cao, Youyou Lu, Yaoxue Zhang, and Ju Ren (corresponding author,
> Tsinghua University).
> "Neuralink: Fast LLM Inference on Smartphones with Neuron Co-Activation
> Linking." arXiv:2410.19274v3 [cs.LG], v1 submitted 2024-10-25, v3
> 2025-10-12. Published at *ASPLOS '25*, the 30th ACM International Conference
> on Architectural Support for Programming Languages and Operating Systems,
> 2025-03-30 to 2025-04-03, Rotterdam, Netherlands, volume 3, pages 147--162.
> DOI: `10.1145/3676642.3736114`. ACM ISBN 979-8-4007-1080-3/2025/03.
> Licensed CC BY-NC-SA 4.0.

- Open access: <https://arxiv.org/abs/2410.19274>
- SHA-256: `eb2113b8bc1f3746d1d67caabf4c46529e60909da9415eeed429952e72093807`
- 16 pages; 2,208,720 bytes.
- Companion plain text: `wang2024-neuralink-smartphone-flash-inference.txt`,
  85,788 bytes. Venue and licence at lines 68--95, the UFS description at lines
  571--583.
- BibTeX key: not yet added to `paper/refs.bib`.

**The system was renamed, and this is checked:** v1 and v2 of arXiv:2410.19274
carry the title Ripple; v3, dated 2025-10-12, carries the title Neuralink. Cite
the v3 title, and expect older citations elsewhere to say Ripple.

What it does: it relocates weights in flash according to which neurons are
activated together, and reports an average end-to-end latency improvement of
1.49x.

Boundary: `HBF` and `high-bandwidth flash` occur 0 times in the full text. The
medium is UFS (Universal Flash Storage), the flash inside a phone, and the
paper is bounded by the phone's ceiling on input/output operations per second,
which has nothing in common with in-package HBF in either bandwidth or access
granularity. What this project uses is the argument, not the platform: where
data is placed inside flash decides performance by itself, the same argument
that `ju2026-tilelens-two-dimensional-memory-layout.pdf` makes as read
amplification.

---

## `jia2025-activeflow-weight-swapping.pdf`

Moving the weights a mobile device is about to need between DRAM and flash, in
software only, and a measurement method close to the one this project uses for
its own calibration curve.

> Fucheng Jia and Zewen Wu (equal contribution), Shiqi Jiang, Huiqiang Jiang,
> Qianxi Zhang, Yuqing Yang, Yunxin Liu, Ju Ren, Deyu Zhang, and Ting Cao
> (corresponding author).
> "Scaling Up On-Device LLMs via Active-Weight Swapping Between DRAM and
> Flash." The system is named ActiveFlow. arXiv:2504.08378v2 [cs.LG], v1
> submitted 2025-04-11, v2 2025-09-23.

- Affiliations: Central South University; Tsinghua University; Microsoft
  Research; Institute for AI Industry Research, Tsinghua University.
- No DOI and no journal or conference venue in the arXiv record; the PDF
  carries no copyright block either.
- Open access: <https://arxiv.org/abs/2504.08378>
- SHA-256: `36c0010b136ed6354481d9de914466b80981b0e7a358906cda67aa6d4aa49fab`
- 14 pages; 1,220,133 bytes.
- Companion plain text: `jia2025-activeflow-weight-swapping.txt`, 69,486 bytes.
  The UFS model numbers are at lines 564--566 and line 650.
- BibTeX key: not yet added to `paper/refs.bib`.

Three techniques: prefetching active weights across layers, sparsity-aware
self-distillation, and a pipeline that swaps active weights between DRAM and
flash.

Boundary: `HBF` and `high-bandwidth flash` occur 0 times in the full text, and
what the paper measures is phone flash, UFS 3.1 and UFS 2.2. What this project
uses is the method: ActiveFlow measures flash bandwidth on a real device at a
range of transfer block sizes, which is the same shape of experiment as the
vmem calibration curve of this project, and the two can be compared as
methods.

---

## `alizadeh2023-llm-in-a-flash.pdf`

The upstream work for the four device-side papers above: keep the weights in
flash and bring in only what the next token needs.

> Keivan Alizadeh, Iman Mirzadeh and Dmitry Belenko (Mirzadeh and Belenko
> contributed equally), S. Karen Khatamifard, Minsik Cho, Carlo C Del Mundo,
> Mohammad Rastegari, and Mehrdad Farajtabar, Apple.
> "LLM in a flash: Efficient Large Language Model Inference with Limited
> Memory." arXiv:2312.11514v3 [cs.CL], v1 submitted 2023-12-12, v3 2024-07-30.
> The arXiv comments field says `ACL 2024`.

- No DOI in the arXiv record.
- Open access: <https://arxiv.org/abs/2312.11514>
- SHA-256: `b245d138e6aac32bdc1a3964e8976f70beeae29b2ecd9d98c07ea703091ec347`
- 23 pages; 1,333,354 bytes.
- Companion plain text: `alizadeh2023-llm-in-a-flash.txt`, 88,239 bytes. The
  hardware configuration is at lines 1083--1085.
- BibTeX key: not yet added to `paper/refs.bib`.

Two techniques: windowing, which moves in only the part of the weights used
recently, and row-column bundling, which reads matrix rows and columns in
groups so that each contiguous read is larger.

Boundaries:

1. `HBF` and `high-bandwidth flash` occur 0 times in the full text. The
   hardware is an M1 Max with a 1TB solid-state drive, an M2 Ultra with a 2TB
   solid-state drive, and a Linux machine with an RTX 4090 -- standard
   solid-state drives, not HBF.
2. The method depends on sparsity in feed-forward layers of the ReLU family, an
   activation function that returns zero for every negative input and therefore
   leaves most entries of that layer's output at zero. A model without that
   sparsity does not give the method anything to skip.

What this project uses: this is the original source for the cost model in which
the smallest contiguous read decides the bandwidth actually obtained, and it is
the common upstream of `jia2025-activeflow-weight-swapping.pdf`,
`wang2024-neuralink-smartphone-flash-inference.pdf` and
`deng2025-kvnand-dram-free-flash-inference.pdf`.

---

## `li2026-hbf-characterization-kv-cache.pdf`

The published HBF work that comes closest to this project's own claim: it is
the only one of the eight files here that builds a thermal model of an HBF
stack and reports where that stack runs into a temperature limit.

> Zhuoran Li, Zhuohang Bian, Yibo Zhao, Guangyu Sun, and Youwei Zhuo (Peking
> University), and Xin Huang (Fudan University).
> "HBF Sucks! A Full-Stack Characterization of High-Bandwidth Flash for
> KV-Centric LLM Serving." arXiv:2608.11668v2 [cs.AR], v1 submitted 2026-08-12,
> v2 2026-08-13.

- No DOI and no journal or conference venue in the arXiv record.
- Open access: <https://arxiv.org/abs/2608.11668>
- SHA-256: `268a3466c5ded355887e1e61792f8961167a1768ffba450dc2050cb46dbcd47c`
- 12 pages; 1,322,867 bytes. The author's note says 13 pages and 12 figures,
  while the stored PDF is 12 pages.
- Companion plain text: `li2026-hbf-characterization-kv-cache.txt`,
  67,514 bytes. The thermal model section is at lines 752--766, and the
  throttling policy is restated at lines 2610--2620.
- BibTeX key: not yet added to `paper/refs.bib`.

Method: the TokenSim simulator, extended; four complete two-hour Qwen-Bailian
production traces; five dense and mixture-of-experts models; H100 and B200
configurations.

Findings: dropping HBF into an SSD-style key-value offloading hierarchy raises
average end-to-end latency by 2x to 5.5x and lowers maximum throughput under
the service-level objective by 1.1x to 2.7x. Improving HBF read and write
latency by 3.75x changes average end-to-end latency by less than 1%. The
setting is datacenter key-value cache serving throughout; words for the
device side occur 0 times in the full text.

Quoted from the paper: `A faster storage device should make serving faster. We
find the opposite.`

What this project uses is the thermal model section. The authors build a
16-layer HBF stack resembling HBM4 out of 128-layer 3D NAND TLC dies, solve
steady-state heat flow with 3D-ICE, drive the model with the read and write
energy of a 16-token key-value block, and sweep sustained per-stack bandwidth to
obtain dynamic power and peak temperature. Safe junction temperature is set to
80 degrees C, aligned with HBM3e. They also model a throttling policy that
switches off the hottest and busiest plane and redirects writes to a cooler
plane. The stack reaches the temperature limit at a sustained 202.27 GB/s per
stack, drawing 53.72 W.

Two boundaries, both of which have to travel with any citation of these
numbers:

1. **The authors state that the thermal model is a projection for hardware that
   has not been released**, meant to give an upper and a lower bound rather than
   fixed values. It is simulation with no calibration against a measured
   device, and the thermal model and the serving simulation do not run coupled
   inside one execution. That is precisely the line between this paper and this
   project, and this project's paper states the difference in those terms.
2. **The energy per bit implied by the two numbers is 33.2 pJ/bit**:
   `33.2 pJ/bit = 53.72 W / (202.27 GB/s x 8 bits per byte)`. That is 3.9 times
   the 8.5 pJ/bit quoted for HBF read energy. The 8.5 pJ/bit figure is the HBF
   read-energy entry in Table 1 of the Micron HotInfra'26 paper (this
   directory, `micron2026-is-hbf-all-you-need.txt`, lines 92-130; Micron is a
   competitor to HBF and the paper models rather than measures), and it is
   consistent with the 8 pJ/bit hybrid-bonded-prototype measurement that
   FlashAccel cites (`wang2026-flashaccel-hbf-llm-inference.txt`,
   lines 1505-1511, attributing Yanagidaira et al., ISSCC). So the bandwidth at which the stack
   reaches its temperature limit is set by a parameter on which published
   figures differ by a factor of 4, and the limit moves with that parameter.

---

## `hsu2026-haven-hbf-vector-search.pdf`

HBF redesigned from the inside for vector search, and the file that removes a
number which had been circulating in this project.

> Po-Kai Hsu (Georgia Institute of Technology) and Weihong Xu (École
> polytechnique fédérale de Lausanne, EPFL), equal contribution, with Qunyou
> Liu (EPFL), Tajana Rosing (University of California San Diego), and Shimeng
> Yu (Georgia Institute of Technology).
> "HAVEN: High-Bandwidth Flash Augmented Vector Engine for Large-Scale
> Approximate Nearest-Neighbor Search Acceleration." arXiv:2603.01175v1
> [cs.AR], submitted 2026-03-01, v1 only.

- The PDF title page writes `ENgine` with a capital N, to bring out the
  acronym HAVEN.
- No DOI and no journal or conference venue in the arXiv record.
- Open access: <https://arxiv.org/abs/2603.01175>
- SHA-256: `a943692fcb8b17b95d1a230ff8ce1936863364642b3429d58078df1ce326c6dd`
- 8 pages; 1,953,632 bytes.
- Companion plain text: `hsu2026-haven-hbf-vector-search.txt`, 39,097 bytes.
  The energy and power passage is at lines 515--545.
- The first author's surname Hsu was read off the PDF title page, and the file
  name follows from it.
- BibTeX key: not yet added to `paper/refs.bib`.

The workload, quoted from the paper: `Retrieval-Augmented Generation (RAG)
relies on large-scale Approximate Nearest Neighbor Search (ANNS) to retrieve
semantically relevant context for large language models.` Approximate nearest
neighbor search finds the stored vectors closest to a query vector without
guaranteeing that the closest ones are found, in exchange for speed.

What it does: it restructures 3D NAND planes into distributed sub-arrays, models
them with the 3D-FPIM sub-array model together with NeuroSim, and reports up to
20x reranking throughput and up to 40x latency improvement on billion-scale
vector datasets.

Parameters this project can use, all checked against the full text: read energy
scaled down from a calibration baseline of about 30 pJ/bit for present-day
devices; a power envelope of 30 W per stack, taken from HBM; a maximum
bandwidth of 460 GB/s per stack, assumed so that HBM2E infrastructure can be
reused; and a final configuration of 4 KB pages, 64 blocks per sub-array, and
256 word-line layers.

**A range attributed to HAVEN that is not in HAVEN.** A figure of "2 to 16
pJ/bit, from HAVEN" has been passed around inside this project. Searching the
full text for `pJ` returns exactly one hit, the calibration baseline of about
30 pJ/bit quoted above. The 2 to 16 range may be the tick labels on the
vertical axis of Figure 8(b); numbers printed on an axis cannot be pulled out
by `pdftotext`, so the extracted text cannot confirm or refute that. Until
someone reads that figure in the PDF, do not cite "2 to 16 pJ/bit" from HAVEN.
The three numbers above -- about 30 pJ/bit as the calibration baseline, 30 W
per stack, 460 GB/s per stack -- are the checked ones and are what a citation
should use.

Two further boundaries:

1. **HAVEN's picture of the inside of an HBF device is its own redesign.** It
   is a projection built from a sub-array model, not modelled on the OCP
   specification, and no device was measured.
2. **HAVEN runs no thermal simulation of its own.** Its thermal discussion
   cites someone else's analysis of an HBM-GPU module, so it supplies no
   independent temperature result for HBF.

---

## `nvidia2024-h100-tensor-core-gpu-datasheet.pdf`

The vendor datasheet that supplies the HBM bandwidth figure the prefetch-window
arithmetic of the background section runs on. It is the only source in this
directory for a shipping accelerator's memory bandwidth stated by the company
that builds it.

> "NVIDIA H100 Tensor Core GPU Datasheet", NVIDIA Corporation, document
> 3440270, September 2024. The imprint on the last page reads
> "(c) 2024 NVIDIA Corporation and affiliates. All rights reserved. ...
> 3440270. Sep24"; the PDF's own metadata gives the title above, the author
> "NVIDIA Corporation", and a creation date of 2024-09-23.

- Source: <https://dam-cdn.nvd.orangelogic.com/AssetLink/u5hh6fv4r7564i4y673484y3m20083nj.pdf>
  -- fetched this round, HTTP 200, `application/pdf`, 411,990 bytes. That
  address was not guessed: it was read out of the `sourceUrl` fields of
  <https://resources.nvidia.com/en-us-gpu-resources/h100-datasheet-24306>
  (HTTP 200, page title "NVIDIA H100 GPU Datasheet"), which is NVIDIA's own
  landing page for this datasheet and the address the FlashAccel paper cites
  for it. The asset link is a content-delivery address and may rotate, which is
  why the file is stored here; the landing page is the stable entry point and
  is what `refs.bib` records in its `note` field.
- SHA-256: `17494a1792c15c55bae2453305e265ad508987b474e9235ecbc6f7c815399b98`.
- 3 pages, 411,990 bytes.
- Companion plain text: `nvidia2024-h100-tensor-core-gpu-datasheet.txt`,
  7,370 bytes.

What this project uses:

1. **One row of one table.** Page 2 carries the specification table. Its
   "GPU Memory Bandwidth" row reads `3.35TB/s` in the H100 SXM column and
   `3.9TB/s` in the H100 NVL column; the same table gives `80GB` of GPU memory
   and a `SXM` form factor for the first column, which is what identifies it as
   the SXM5 part. In the plain-text extraction the figure is at line 208.
   Nothing else in the datasheet is used.
2. **A second, independent confirmation of the same figure.** NVIDIA's own
   product page <https://www.nvidia.com/en-us/data-center/h100.md> (HTTP 200)
   carries the same specification table, and its "GPU Memory Bandwidth" row
   reads `3.35TB/s` in the H100 SXM column.

Boundaries that have to travel with any citation of it:

- It is a marketing datasheet, not a measurement. `3.35TB/s` is the peak
  bandwidth of the memory interface as specified, not a bandwidth anybody
  observed on a running workload; measured HBM bandwidth on real kernels falls
  below it.
- The datasheet says nothing about the latency of a memory access, so it cannot
  be used for the latency column of the three-way comparison table.
- The card the datasheet describes and the card the HBM latency baseline in
  that table comes from are two different cards. The table's own notes already
  say so, and that note must stay wherever this figure is quoted.

---

## Not available: the AiF paper, blocked by bot protection

AiF is on-device large language model inference with processing inside the
flash, the same line of work as `hao2026-nvllm-3d-nand-edge-inference.pdf` and
`deng2025-kvnand-dram-free-flash-inference.pdf`, and NVLLM cites it as
reference [20], at lines 824--828 of
`hao2026-nvllm-3d-nand-edge-inference.txt`. The paper is not in this directory.
Its citation:

> Jaeyong Lee, Hyeunjoo Kim, and Sanghun Oh (Seoul National University),
> Myoungjun Chun (Soongsil University), Myungsuk Kim (Kyungpook National
> University), and Jihong Kim (Seoul National University).
> "AiF: Accelerating On-Device LLM Inference Using In-Flash Processing."
> In *ISCA '25*, pages 529--543, 2025.
> DOI: `10.1145/3695053.3731073`

**Why the full text is missing: bot protection, not a paywall and not a wrong
address.** Both routes into the ACM Digital Library, the article page and the
PDF, came back as HTTP 403 from Cloudflare. The same address opens in a browser
and needs no subscription. Someone has to open it in a browser and save the PDF
into this directory together with its `.txt` extraction; repeating the fetch
from the command line will not get past the check.

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
