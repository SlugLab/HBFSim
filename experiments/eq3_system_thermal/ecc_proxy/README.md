# HBF conditional reliability cost proxy v1

Status: isolated optional software consumer; no measured HBF RBER or ECC calibration.
USER_CONFIRMED: the requested device is HBF, and NAND/OCP/SanDisk conditional proxies
may be used. HBM remains DRAM. Default existing runners remain proxy-disabled.

## Sources and assumptions

| Input | Basis | Actual consumer / limits |
|---|---|---|
| Base-managed retries and valid/invalid read status | OCP HBF v0.7.0, local original pp.56–60 | Internal media work; useful external payload only once. No claimed UECC prediction. |
| Mean retry 19.9 at 365 days at 30°C and 2000 P/E | Park et al., ASPLOS 2021, https://arxiv.org/html/2104.09611 | Old 48-layer TLC scenario anchor; NOT HBF measurement. |
| 90-day retry summary bounds | Same paper | Constrain an illustrative monotone age/wear interpolant. Below 90 days and intermediate values are assumptions, not measured retry points. |
| Ea=1.04 eV | HeatWatch HPCA 2018, https://research.ece.cmu.edu/safari/pubs/heatwatch-3D-nand-errors-and-self-recovery_hpca18.pdf | Old MLC retention proxy. Fit 20–70°C; time outside this fit range explicitly reported. Not target HBF calibration. |
| Transfer factor 0 / 0.1 / 1 | SCENARIO_ASSUMPTION | Null / weak / full old-NAND transfer; not a statistical confidence interval. No best-factor selection based on controller victory. |
| Decoder throughput twice fresh media | SCENARIO_ASSUMPTION | Explicit shared per-stack resource, not official ECC throughput. |
| 40 pJ/B array + 10 pJ/B base | USER_CONFIRMED original 80 W envelope | Each physical read attempt; base covers proxy ECC work, no duplicate ECC energy. |
| No instantaneous hot-read penalty | Source limitation / measured contrary effects in old NAND | Temperature history accelerates age. Cooling never erases existing age. |
| Initial P/E and uniform per-stack age | SCENARIO_ASSUMPTION | Fixed input, not dynamic FTL wear. Hottest array die drives conservative stack age; not per-page history. |

The 72 bits/1 KiB ECC strength in the paper is provenance only and has no target
HBF decoder-strength consumer. Sandisk's public thermal-stability claims do not
supply a numerical ECC curve; no such curve is attributed to Sandisk.

## Actual chain and safety

`run_causal_point.execute` opt-in `hbf_read_cost_proxy.mode=conditional_nand_history_v1`
constructs `ReadCostProxy` and `ReliabilityCausalService`. Prior observed array
hotspots integrate equivalent age. At first service resource allocation, a job
snapshots expected extra read effort. That effort competes on existing media and
a shared decoder resource, contributes actual phase energy, and delays real DAG
completion. It never rewrites completed timestamps. Receipts contain profile,
age state and per-admission cost decisions. Default original/native MQSim is not
modified or loaded by this equivalent service.

HBM data, static retries combined with this proxy, and simultaneous maintenance
with unmapped per-stack ages are rejected. Refreshing a subset must not reset
all stack ages. A future per-extent mapping consumer is needed before claiming
refresh improves this ECC model. Existing maintenance tests remain independent.
No RBER, uncorrectable failure probability, decoded data bytes, or calibrated
HBF reliability is simulated. Successful reads are conditional on recoverability.

## Short-window feasibility

At constant 85°C, 20 real seconds correspond to about 0.104 days at 30°C under
the selected Ea. This is not 24 hours at 85°C. The weak scenario interpolant from
fresh data predicts about 0.047 expected retries there, but this is an unmeasured
short-age extrapolation, not experimental evidence. With 90-day initial age the
same extra age changes cost only slightly. Thus a 20-second policy comparison
may mostly measure throttling cost, even with a correctly connected proxy.

Null/weak/full transfer comparisons and both fresh/aged states must remain
separate. No acceptance criterion requires positive policy benefit. Actual
thermal jobs require frozen inputs and the existing serial CPU/resource gate.

Cross-source transfer is explicit: the ASPLOS paper's example converts roughly
13 hours at85°C toone year at30°C, whereas retained Ea=1.04eV converts about19.4
hours. The implementation keeps the original HeatWatch-based Ea consistently
for age integration; it does not pretend these different devices share a fitted
law. This discrepancy and the assumed early-age interpolant are reasons for
sensitivity analysis, not reasons to change Ea until a controller wins.
