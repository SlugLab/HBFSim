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

Web captures are stored as PDF plus a `.txt` of the same name, so the body text
stays greppable without opening a viewer.

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

## Not available: the OCP HBF specification itself

The normative document -- the first HBF technical specification, released
through the Open Compute Project on 2026-08-03/04 by Sandisk and SK hynix --
is **not** in this directory. `opencompute.org` sits behind bot protection that
returns HTTP 403 to command-line fetches and shows a "Performing security
verification" page to reader proxies, and neither the Sandisk nor the SK hynix
press release links a downloadable file. Getting it needs a browser session and
most likely an OCP account.

Until someone drops the real PDF here, the two Chinese articles above are the
best secondary account we have, and every number taken from them should be
marked as second-hand in the paper.

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
