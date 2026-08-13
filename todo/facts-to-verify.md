# Facts to verify before they go into the paper

Each item records what we currently say, what it rests on, why that is not
enough, and what would close it. Items 1 to 4 affect claims the paper's argument
depends on; items 5 to 9 are narrower.

Throughout, three kinds of statement are kept apart: a first-hand fact (measured
here, or read verbatim in a primary source), a second-hand opinion (an industry
analysis article or an unattributed commentary piece), and our own judgment.

## 1. The OCP HBF specification text has not been obtained

**What we say now.** Every spec number in the paper — the four thermal states
and their thresholds, the 0 to 105 °C junction temperature window, 24-hour data
retention at 85 °C, command codes 0x08 and 0x0A, MMIO registers 0x150 and
0x14C — is stated as spec content.

**What it rests on.** A Chinese-language secondary article reproducing the spec:
`docs/ref_article/semiinsights2026-hbf-standard-release-cn.pdf`.

**Why that is not enough.** The whole thermal and reliability argument rests on
the 85 °C / 24-hour figure. If that number is wrong, the section does not stand.

**What would close it.** Obtain the specification text itself. `opencompute.org`
returns HTTP 403 to command-line requests and a bot-check page to reading
proxies; the SanDisk and SK hynix press releases contain no download link. A
browser session, possibly with an OCP account, is needed. Then check each of the
values above against it, line by line.

## 2. How strong a "first" claim we may make

**What we say now.** The wording fixed for the paper is: the first open standard
that defines stackable NAND as an in-package memory layer for an xPU.

**What it rests on.** The official wording is from the SanDisk press release of
2026-08-03: `As one of the first technical standards of its kind in the memory
and storage industry`. That is a claim about being among the earliest standards
of this kind, not about being the first device.

**Why that is not enough.** An industry opinion piece says HBF is the first time
NAND enters the accelerator package as part of the accelerator. That is an
industry judgment, and we cannot verify whether an unstandardized precedent
exists.

**What would close it.** For any stronger wording, a verifiable device-history
source. Absent that, keep the standard-level wording and do not upgrade it.

## 3. The two order-of-magnitude ratios are our own arithmetic

**What we say now.** Relative to an NVMe SSD, HBF has roughly two orders of
magnitude more bandwidth and roughly one order of magnitude lower latency.

**What it rests on.** The bandwidth ratio is the spec's three points, 0.4 to
3.0 TB/s, divided by our own measured Dell CD8P sequential read of 7.577 GB/s,
giving roughly 50x to 400x. The latency ratio is HBF at about 10 µs against an
SSD 4 KiB random read of tens to a hundred-plus µs, giving roughly one order of
magnitude.

**Why that is not enough.** Both ratios are arithmetic we performed, not results
quoted from a source, and one input on each side has no citation.

**What would close it.** Three citable sources: SSD 4 KiB random read latency;
the PCIe 5.0 x4 link ceiling of about 14 GB/s; HBM latency of about 100 ns.
Note also that our own vmem 4 KiB cold-read P50 of 11,133 ns may not be used as
evidence of SSD device latency — it includes page faults, driver, and cache
insertion, and describes a single-threaded software path only.

## 4. "8 to 16 times the capacity of HBM at comparable cost" has no first-hand basis on the cost side

**What we say now.** HBF offers 8 to 16 times the capacity of HBM at comparable
cost.

**What it rests on.** The capacity multiple is supported by the official press
material. The cost side is supported only by the judgment of an industry
analysis article: there is no verifiable figure for cost per GB, and no
statement of whether packaging and integration costs are included.

**What would close it.** Either a citable cost basis, or rewriting the sentence
to state the capacity multiple only and leaving cost to the discussion section.

## 5. FlashAccel's experimental setup has not been read line by line

**What we say now.** FlashAccel (arXiv:2607.10186) reports 2.54x throughput and
1.93x energy efficiency per GPU with 6 HBF stacks against an HBM-only GPU under
a 100 ms latency constraint, with 4.6 TB/s read bandwidth against 245.8 GB/s
peak write bandwidth. We also state that the words `thermal` and `temperature`
do not appear anywhere in it.

**What it rests on.** Text extracted with `pdftotext`: the abstract, the
evaluation-method paragraph in the original English, the endurance argument, and
a zero-hit search for those two words.

**Why that is not enough.** The full 16 pages have not been read. Two things are
still open: the complete experimental setup behind 2.54x and 1.93x, and which of
the two integration schemes — cascaded integration (CSI) and co-located
integration (CLI) — each of 4.6 TB/s and 245.8 GB/s belongs to, and at how many
stacks. Also, a text search cannot see content that exists only as an image, so
the zero-hit statement is only as strong as the extraction.

**What would close it.** Read the 16 pages and confirm both attributions.

## 6. The introduction needs a concrete KV cache size

**What we say now.** Model weights and the KV cache both grow and both have to
sit in HBM. The KV cache is the set of intermediate results kept for tokens
already generated, which every later step re-reads; it grows with context length
and with the number of concurrent requests.

**What it rests on.** Nothing numeric. The material at hand contains no figure.

**What would close it.** Pick one public model and one context length, compute
the KV cache size, and cite a source for the parameters used.

## 7. The architecture argument from the industry article is that article's speculation (new this round)

**What we say now.** In the background section and in evaluation question Q9, we
report that an industry analysis article speculates that a TPU-style statically
scheduled architecture suits HBF better than a GPU, because the two hide access
latency by different mechanisms.

**What it rests on.** That article only. It describes the idea in its own words
as a stray thought and a guess. Its supporting arithmetic is also its own and we
have not checked it: a 405B model at 8-bit weights is about 405GB over 126
layers, about 3.2GB per layer; 8 HBF stacks at 12.8 TB/s stream one layer of
weights in roughly 250 µs, so a single NAND read latency of 10 µs is about 4% of
that.

**Why that is not enough.** Wording in the paper must be "the article
speculates", never "research shows". Our measurement platform is an NVIDIA GPU;
we have no TPU, so this can only appear as an observation in background and
motivation.

**What would close it, if it is to enter the body as more than an observation.**
Either redo the arithmetic independently, or find a citable source for it.

## 8. Old ACM filename references — DONE

**What it was.** `docs/proofs/2026-08-11-hybrid-complete.md` line 79 and
`scripts/thermal/collect.py` line 74 cited the thermal-sampling paper by its ACM
identifier `2333660.2333670.pdf`, which says nothing about what the file holds.

**Resolved.** Both now point at
`docs/ref_article/ardestani2012-thermal-aware-sampling.pdf`, where the PDF lives
with a full citation in `docs/ref_article/README.md`. The DOI
`10.1145/2333660.2333670` still appears where it belongs, in citations.

## 9. The name `LogP` collides with an existing model

**Where.** `configs/thermal/gpu-cd8p-logp-live.json` and
`scripts/thermal/fit_logp.py`.

**Why it is a problem.** The name does not come from the cited paper: that paper
calls its own methods TASS and TAPS, and `LogP` does not appear in it. Separately,
LogP is an established name in parallel computing for a model of a completely
different kind, so a reader in that area will read the name as referring to
something we do not mean.

**Suggested action.** Rename both the configuration file and the script. This
touches the code repository, so it needs the repository author's agreement
before anything is changed.

