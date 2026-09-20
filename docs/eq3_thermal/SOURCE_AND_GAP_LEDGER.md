# P2 source and gap ledger — 2026-09-19

Per-file SHA256/size and exact upstream commits are in
[public_source_manifest.json](public_source_manifest.json). Raw external inputs
are stored in workspace `eq3_thermal/reference/{MFIT,HBM-Power}`; they are not
modified or executed. Source acquisition and arithmetic import are not a formal
experiment or complete physical calibration.

| Field / units | Source / evidence | Adopted use / missing information |
| --- | --- | --- |
| Geometry mm, temperature K, HTC W/(m² K) | MFIT 4444336d95fe7e9a3bf440c96937831126c6a37f, three example folders, INPUT_FORMAT.md; DOC_DERIVED numerical-model inputs | All three geometry/power YAML and CSV sets acquired; input-format and method reference, not HBF measurement |
| rho kg/m³, cp J/(kg K), anisotropic k W/(m K) | MFIT material_prop.yml, per-file hash in manifest | Source-specific material examples; e.g. Si 2330/710/150, not universal package constants |
| FEM reference | MFIT FEM_models/README.md | Links external ANSYS Fluent case archive; actual FEM outputs and commercial rerun NOT_ACQUIRED/NOT_RUN. No claim of ANSYS reproduction |
| Material table | 3D-ICE paper 2512.05823v1 Table II / Fig.8, p5 | Case-specific chip/PCB grouping and bump anisotropy; not blanket PCB material. Large paper case exceeds task RAM; not run |
| Numerical solver | Stock 3D-ICE e0bb6850c5e446363e26936586d625270c87f224 | NUMERICAL_REFERENCE; source/build hashes retained; not a physical gold standard |
| Memory power W; bandwidth decimal GB/s | HBM-Power artifact 4642c61f856cbd9d5d3f37d56214e5dbfa663703, HBM3E_measurements.csv; published aggregate measurements | 3 rows, 241.71–255.22 W loaded, 34.75–44.95 W idle, 4517.3–4523.3 GB/s; no time/temperature/per-stack samples |
| Marginal read energy pJ/B | Derived `(loaded-idle)/GB_s*1000` from those same three rows | 45.815–46.941 pJ/B at this operating region only; no static/dynamic/IO extrapolation to HBM4/HBF |
| HBM2 characterization | all_idd_measurements.csv, ground_truth_allzeros.csv, ground_truth_random.csv acquired | Preserve raw IDD/IPP/power_vdd columns, sample/chip/temp fields; units/rails must be tied to upstream model before converting. Not HBF data |
| GPU/HBM product anchor | NVIDIA H200 official product page: 141 GB HBM3e, 4.8 TB/s | Alternate SPEC anchor aligned with the public HBM3E data; not measured here, not a claim of 8-stack H200 or HBM4 |
| GPU/GDDR proxy | RTX 5090 official: 32 GB GDDR7, cc12.0; local enumeration succeeds with targeted access | Separate physical-proxy platform. Memory temp/power unavailable in current telemetry; no workload collected |
| HBF interface/height | Local OCP v0.7.0 PDF p16 Table4, SHA256 307531eb8053f00cbeccbc907ddff0a9c4fe6f9d0066a077ce33b0ac99312da3 rechecked | 384/1536/3072 GB/s, max height8/16/16; actual die count separate. 64-bit interfaces, 8/16/32 GT/s and virtual AXI multiplicity remain per existing field ledger |
| HBF capacity/page | OCP p16 Table3 example | 16 dies, 512 GiB, page4096B example; not every grade/product |
| Retention / temperature | OCP p106: powered85 C/24h, junction0–105 C | Normative condition, not generic Light threshold, RBER curve, Ea or lifetime |
| Maintenance | OCP p107,117–118 | In-flight completion/error semantics; constrained relocation; product-specific typically24–48h maintenance and same-die read/refresh exclusion; NOT_IMPLEMENTED runtime |
| Sandisk product target | July2025 fact sheet | 1.6 TB/s,16×256Gb,512GB decimal separate from OCP1536/512GiB; no absolute RC/power |
| HBM4 device | Micron HBM4 official page | 36GB/12H,48GB/16H,2048bit, >11Gb/s target; existing fixture's8Gb/s is assumption. Do not silently update frozen fixtures |
| Retention activation mechanism | Local HeatWatch HPCA2018 original; existing registry | Literature constraints on older tested NAND, not acquired raw HBF measurements; no generic lifetime claim |
| Geometry/contact/cooling/HBF joules | Research scenario inputs | INFERRED explicit assumptions; full vendor floorplan, contact resistance, command energies, per-die sensors remain missing |

## Conflicts and transfer boundaries

Sandisk fact sheet states no refresh power; OCP describes longer-period retention
maintenance. Product/date/terminology differ and no vendor reconciliation is
available. Keep both statements; OCP-like and Sandisk-target maintenance profiles
must remain distinct. DRAM refresh and NAND retention maintenance are not assumed
equivalent. OCP page15 TiB/s vs page16 GB/s and module bandwidth totals remain
unresolved source discrepancies (see ocp-verification.md).

H200/HBM3E is a defensible source anchor for memory-domain proxy data, while
5090 is a local telemetry platform. Neither determines custom GPU–HBM4–HBF
package geometry. Do not join their measurements into a fake synchronous trace,
split aggregate memory power equally among stacks without an explicit hypothesis,
or add memory power on top of a board-power number that already contains it.
Official capacities/bandwidth are not active heat; no official watts inferred.

MFIT license is GPL-3.0. HBM-Power root dataset license is UNKNOWN; component
licenses do not establish redistribution rights for all data. Keep original
datasets external and register hashes. No top-level artifact, FPGA, Docker,
cloud-rental or instance-destruction script was executed; no cloud key used.

Primary links: [MFIT](https://github.com/AlishKanani/MFIT),
[HBM-Power](https://github.com/CMU-SAFARI/HBM-Power/tree/artifact),
[3D-ICE paper](https://arxiv.org/html/2512.05823v1),
[H200](https://www.nvidia.com/en-us/data-center/h200/),
[Sandisk fact sheet](https://documents.sandisk.com/content/dam/asset-library/en_us/assets/public/sandisk/collateral/company/Sandisk-HBF-Fact-Sheet.pdf),
[Micron HBM4](https://www.micron.com/products/memory/hbm/hbm4).

### HBF read-cost proxy source addition (2026-09-20)

Park et al., *Reducing Solid-State Drive Read Latency by Optimizing Read-Retry*,
ASPLOS2021, https://arxiv.org/html/2104.09611 (primary paper reviewed2026-09-20).
Actual old48-layerTLC retry data constrains an explicitly assumed age/wear
interpolant; it is not HBF calibration. HeatWatchEa1.04 retains the original
cross-device proxy, with its temperature fit/extrapolation distinguished. OCP070
§5.3.2 supports base-managed retry; §9 distinguishes HBF NAND endurance from HBM.
No official numerical ECC latency/J/byte curve was inferred. Consumer, parameters,
source discrepancy and transfer factors are documented in the isolated ECC README.
