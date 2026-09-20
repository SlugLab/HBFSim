# EQ3 parameter source audit

Audit date: 2026-09-19 UTC. This document is a source audit, not a design or
model freeze and not approval to run an experiment. Its machine-readable
companion is `configs/eq3_thermal/research/source_evidence.json`.

## Evidence rules used here

- **SPECIFIED** means the value is stated by the identified specification or
  vendor document under the stated conditions.
- **PROXY (MEASURED subtype)** means a public artifact reports measurements,
  but on a different device, generation, package, or platform from the target
  HBF/HBM4 system.
- **PROXY (CASE subtype)** means an input from a published numerical case. It
  is useful to reproduce that case; it is not a measured package property.
- **DERIVED** means arithmetic from cited inputs, with the formula and unit
  convention recorded.
- **SCENARIO_ASSUMPTION** is an explicit research choice to be sensitivity
  tested. It is not a device fact.
- **UNKNOWN_BLOCKING** means the source set supplies neither a target value nor
  a defensible target interval for an absolute claim.

All locations below are PDF page numbers or one-based text/source lines.
Hashes are SHA-256 unless explicitly identified as Git commits or blobs.

## 1. OCP HBF v0.7.0 original

Local original:
`eq3_thermal/worktree/docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf`,
130 pages, dated 03 Aug 2026, SHA-256
`307531eb8053f00cbeccbc907ddff0a9c4fe6f9d0066a077ce33b0ac99312da3`.

| Item | Exact original evidence | Decision-useful interpretation |
| --- | --- | --- |
| Organization example | PDF p.15: up to 16 host channels, 16-NAND-die stack support, 4 KiB NAND page; p.16 Table 3: 16 dies, 16 banks/channel, 4096 B page, 512 GiB cube | SPECIFIED example/configuration. Table 3's `512 GiB` is 549,755,813,888 B. It is not Sandisk's decimal 512 GB (512,000,000,000 B); the former is 7.3741824% larger. |
| Speed grades | PDF p.16 Table 4: grade 1/2/3 maximum user bandwidth 0.384/1.536/3.072 TB/s, UCIe 8/16/32 GT/s, x64, 1/2/4 virtual AXI interfaces per channel, maximum stack height 8/16/16 | The decimal bandwidth normalizations are 384/1536/3072 GB/s. “Maximum stack height” is not actual NAND die count. Grade 3 does not imply 32 dies. |
| Illustrative footprint | PDF p.16: “Approximate footprint” 10.975 mm × 16 mm × 775 um | Outer-envelope proxy only: area 175.6 mm² and rectangular-envelope volume 136.09 mm³ (DERIVED). It gives no die, bond, base-die, mold, or interface thicknesses. |
| Retention and endurance | PDF p.106, §9 and Table 33: powered-on retention 24 h at 85 °C; 10-year life is limited by reaching 100% endurance indicator; MAXPEC and AVGPEC are product-specific; read-disturb is product-specific | The only fixed retention condition is powered-on 85 °C/24 h. It does not identify activation energy, RBER, ECC strength, retry cost, P/E limit, or a policy threshold. |
| Junction range | PDF p.106 §9.1: operating junction temperature 0–105 °C, monitored using IEEE 1500 | SPECIFIED junction range. Ambient, cold-plate temperature, and controller Light threshold are different variables. |
| Thermal modes | PDF pp.106–107, Table 34 and §9.2 | Threshold temperatures are absent. Light preserves normal operation at reduced performance and auto-recovers. Severe asserts CATTRIP, backpressures new commands, and requires in-flight commands to complete normally or with status 0x9 before AXI Ready is deasserted. Table note says only maintenance commands remain supported. No fixed slowdown fraction is specified. |
| Refresh timing and contention | PDF p.118 §11.5: periodic data refresh at reliability-specification intervals, “typically every 24–48 hours”; time interval and read-count trigger are product-specific; read and refresh must not target the same die simultaneously | `24–48 h` is a typical interval, not a universal guaranteed setting. A refresh-contention model may enforce same-die mutual exclusion as SPECIFIED behavior, but its service time, energy, block size, read-count threshold, and exact deadline remain UNKNOWN_BLOCKING. |

The OCP document is internally a family specification: p.15 also says “up to
3.072 TiB/s,” while Tables 2/4 use GB/s and TB/s. Calculations in this audit
retain the Table 4 decimal interpretation rather than silently equating TB and
TiB.

### OCP/Sandisk maintenance conflict that must remain explicit

The Sandisk July 2025 fact sheet (local PDF SHA-256
`349f05372fb528702d2fe95ec8f3a9cb9b4dd976c7d115a3db49f26031b10111`)
states on p.2 / extracted text lines 53–54 that HBF is non-volatile and requires
“no refresh power” to prevent leakage or loss. OCP v0.7.0 p.118 later requires
periodic product-specific data refresh and same-die exclusion. These statements
have different dates, scopes, and meanings; neither proves zero maintenance
traffic for an OCP-style research profile. A maintenance-enabled mainline must
be labeled an OCP-derived combined research profile. A no-periodic-refresh
Sandisk boundary scenario must not simultaneously claim OCP refresh pressure.

## 2. HBM-Power pinned artifact

Repository: `CMU-SAFARI/HBM-Power`, pinned artifact commit
`4642c61f856cbd9d5d3f37d56214e5dbfa663703`. No root `LICENSE`, `COPYING`, or
`NOTICE` exists in the pinned tree. Licenses inside bundled subprojects do not
establish a license for the root measurement CSVs; redistribution/license status
of those data is therefore **UNKNOWN**. Values may be internally cited with
provenance, but this audit does not authorize republishing raw rows.

### 2.1 HBM2 FPGA currents and VDD power

`data/all_idd_measurements.csv` (SHA-256
`8beb9a4114ed88536ee03f0839696246a632d4dd27889fc0b08e6f9572797e38`)
contains 792,636 rows and 36 `chip_id` values across IDD0, IDD2, IDD3,
IDD3N1, IDD3N16, IDD4R, IDD4W, IDD5B, IDD0_3_CYCLE_ACTPRE, and IDD7.

The CSV header itself omits units. Unit provenance comes from pinned source
`sources/fpga/DRAMBender/sources/apps/Power_structural_variation/standardize/generate_standardized_csvs.py`,
Git blob `7f70fa37f6e73d577bb6f5e5a395a80cb6c81152`, SHA-256
`6c6a3598e156568124984e2e7360d8b3197d5196f9bb93170562bf036c60293d`:
lines 28 and 201–209 read `Current_Avg(mA)` as `idd` and compute `ipp` as
`Power_VPP_Avg(mW) / 2.5 V`. Therefore both `idd` and `ipp` columns are mA;
`temperature` comes from `Temp1_Ins(Temp)` and is °C by the measurement schema.
`ipp` is **not power** despite its name.

The standardized script maps a physical FPGA and `chip_in_fpga` to a `chip_id`
(lines 51–59) and selects channel groups 0–7 or 8–15 (lines 20–21, 70–77,
244–262). It does not justify treating a row as a per-die value. The safest
domain is the selected HBM2 chip/stack measurement rail for that channel group.

Do not derive total watts by adding `idd` and `ipp`. VPP power is recoverable as
`P_VPP[mW] = ipp[mA] × 2.5[V]`; VDD power additionally needs the applicable VDD
voltage or the original `Power_VDD_*` column. The selected local CSV and
standardizer do not freeze that VDD voltage. Temperature includes recorded zero
values and the artifact README warns that stack temperature and current-sensor
drift are uncontrolled (README lines 201–205), so zero values must be quality
filtered from a declared rule, not silently treated as 0 °C measurements.

The trace files `ground_truth_allzeros.csv` and
`ground_truth_random.csv` have SHA-256
`16020638216c5f143e0a103b29a9b781a9b573e77f070d1099f0a3a7e1136708`
and `3c1444634dbdbc9a4596c7ab05cc5d9049bacf27e9161912bdf079de4f134632`.
Pinned `aggregate_trace_ground_truth.py` lines 18–22, 77–78, and 139–161
(blob `b0499ab7160da37cf667aead57c6894a75fe0628`, SHA-256
`f546547708c59dfd9de702d896246949e72e2b778c3594f21e7b6cf1ae03a25f`)
show that `power_vdd_avg` is the mean of the final ten
`Power_VDD_Ins(mW)` rows. The associated BRAM trace summarizer lines 3–13 and
20–29 explicitly reports VDD/VPP power in mW and temperature in °C. These are
HBM2 VDD steady-state trace measurements, not total board power, per-die energy,
or HBM4 values.

### 2.2 H200/HBM3E aggregate memory rail

`data/HBM3E_measurements.csv` (SHA-256
`eddcd6bd5277a7870d794a7377107a7f89a11f7dce83a8da40c4933e4b822607`)
has only three rows. `sources/h200/log_power.py` lines 2–13 and 60–85 show that
`mem_avg_w` is NVIDIA SMI's **GPU Memory Power Readings / Average Power Draw**;
it is a GPU-level aggregate memory rail. `quick_measure_h200.py` lines 2–17,
77–117 define an idle baseline, sustained random-data read, and active power as
read minus idle. The logger samples at 0.5 s by default (lines 54–60); the quick
run uses 8 GiB per buffer ×3, 25 s stress by default (lines 51–59). No
temperature column is present.

For each recorded row, the exact derived active energy per delivered byte is

`E_active[pJ/B] = (total_power_W - idle_power_W) / read_GB_per_s × 1000`,

using decimal GB/s as named by the CSV:

| Sample | Total / idle / read | Active power | Active energy | Total-rail energy |
| --- | --- | ---: | ---: | ---: |
| tuning_read_rand | 255.22 W / 44.95 W / 4523.3 GB/s | 210.27 W | 46.485973 pJ/B | 56.423408 pJ/B |
| quick_145512 | 247.75 W / 35.55 W / 4520.6 GB/s | 212.20 W | 46.940672 pJ/B | 54.804672 pJ/B |
| quick_145735 | 241.71 W / 34.75 W / 4517.3 GB/s | 206.96 W | 45.814978 pJ/B | 53.507626 pJ/B |

Thus 45.814978–46.940672 pJ/B (mean 46.413874 pJ/B) is a defensible
**PROXY (MEASURED subtype)** only for the aggregate H200/HBM3E memory domain at these
three near-4.52-TB/s random-read operating points. It is not per stack, does not
separate DRAM core/refresh/PHY/base-die power, does not span temperature or low
bandwidth, and cannot be called HBM4 or HBF energy. Dividing it by a presumed
number of stacks would fabricate equal stack sharing. For a bounded power
scenario it may be applied only to aggregate physical-link bytes, with idle
power kept separate, and with a generation/platform transfer label.

## 3. MFIT public numerical cases

Repository: `AlishKanani/MFIT` commit
`4444336d95fe7e9a3bf440c96937831126c6a37f`, GPL-3.0. The material file
`material_prop.yml` has SHA-256
`d9686b55bf37898b21878e1cc0aeadaa748744f72f8eed30d25a53ac37179e10`.
`INPUT_FORMAT.md` lines 11–26 establishes units: density kg/m³, specific heat
J/(kg K), conductivity W/(m K), including anisotropic axes. The repository does
not attach material datasheets, temperatures, uncertainty, or measurement
conditions to the values. They are therefore **PROXY (CASE subtype)**, not MFIT-measured
properties and not HBF package facts.

| MFIT label | rho kg/m³ | cp J/(kg K) | k W/(m K), z where anisotropic | Derived rho*cp J/(m³ K) |
| --- | ---: | ---: | ---: | ---: |
| `link` | 1250 | 628.75 | 150 (kx=212, ky=kz=150) | 785,937.5 |
| `link_cu` / `lid` | 8960 | 385 | 398 | 3,449,600 |
| `link_si` / `interposer` / `chiplet` | 2330 | 710 | 150 | 1,654,300 |
| `ubump` | 2056 | 52 | 30.94 | 106,912 |
| `tim` | 2500 | 1000 | 10 | 2,500,000 |
| `substrate` | 1250 | 1300 | 0.3783 (kx=ky=20.68) | 1,625,000 |
| `c4` | 1250 | 1300 | 2.348 (kx=ky=0.6431) | 1,625,000 |
| `adhesive` | 2500 | 1000 | 1.9 | 2,500,000 |

The homogeneous example geometry (SHA-256
`c960f0693ae28b0ad7ab92097da586f129029c069063a1965818684ea9c53533`)
is a 9.5 × 9.5 × 1.855 mm package with four 1.5 × 1.5 mm chiplets, 1.5 mm
spacing, top/bottom HTC 1400/25 W/(m² K), and 300 K ambient (lines 1–13).
Its z sequence is 0.5 + 0.5 mm substrate, 0.08 mm C4, 0.1 mm interposer,
0.025 mm ubump, 0.1 mm chiplet, 0.05 mm TIM, and 0.5 mm lid (lines 15–141),
which sums exactly to 1.855 mm. This is internally consistent, but is neither
the OCP 10.975 × 16 × 0.775 mm outer envelope nor a stacked-memory cross-section.
The example power file assigns 3 W maximum to each of four 1.5 × 1.5 mm
chiplets (lines 7–34); the trace values are percentages of that maximum per
`INPUT_FORMAT.md` lines 112–120.

For a uniform one-dimensional slab, exact case-derived areal values are
`C_A = rho*cp*thickness` and `R'' = thickness/k_z`:

| Layer | C_A J/(m² K) | R'' m² K/W |
| --- | ---: | ---: |
| each 0.5 mm substrate layer | 812.5 | 1.321702353e-3 |
| 0.08 mm C4 | 130.0 | 3.407155026e-5 |
| 0.1 mm interposer | 165.43 | 6.666666667e-7 |
| 0.025 mm ubump | 2.6728 | 8.080155139e-7 |
| 0.1 mm chiplet | 165.43 | 6.666666667e-7 |
| 0.05 mm TIM | 125.0 | 5.0e-6 |
| each 0.25 mm lid layer | 862.4 | 6.281407035e-7 |

Using the full 9.5 mm-square surface only, the example HTC values imply ideal
convection resistances `1/(hA)` of 7.914127 K/W top and 443.213296 K/W bottom.
These derivations reproduce a simple MFIT-case interpretation; lateral paths,
partial layer footprints, interface/contact resistance, and temperature
dependence are omitted. They must not be copied as an HBF/HBM package RC.
MFIT's `FEM_models/README.md` says the Fluent reference cases are stored in an
external Drive folder and are too large for GitHub. They are not present here,
so these inputs alone do not constitute a validated FEM calibration dataset.

Recommended bounded use: reuse MFIT values only as a software regression case
or as explicitly labeled starting proxies for silicon, copper lid, TIM, and
package layers. Before a physical freeze, independently source target material
grades and temperatures; treat contact resistance and internal stack geometry
as separate unknowns. The MFIT case supplies no bond, mold/underfill, NAND die,
base die, TSV composite, or cold-plate property.

## 4. HeatWatch reliability proxy

Local original:
`eq3_thermal/worktree/docs/ref_article/luo2018-heatwatch-nand-temperature.pdf`,
HPCA 2018, 14 pages, SHA-256
`a1d2140c3c9c0ae2fedf7741f1e9d2af2fe777742509fc19875a5c559cc2f608`.

The experiment is not a generic NAND or HBF calibration:

- PDF p.4 §3.1 uses 30–40-layer 3D **charge-trap MLC** chips of one undisclosed
  model/vendor. PDF p.5 states exact numbers can vary by vendor and no
  chip-to-chip population study was performed.
- PDF p.5 §3.2 uses eight wordlines in eight blocks, 3000 P/E cycles, last-300
  dwell times 64–8192 s, primarily 70 °C, with a small trend check at 20 °C.
- PDF p.9 §4.2 fits activation energy using 20–70 °C, 1000–10000 P/E cycles,
  with 70 °C and 3600 s as the reference loss point.
- PDF p.10 reports `Ea = 1.04 eV`, 95% CI `1.01–1.08 eV`, and Arrhenius fit
  `R² = 0.76`; it explicitly notes activation energy varies with temperature
  and P/E count. The full SRRM work also spans room-temperature dwell 32 s–4.6
  h and retention up to 24 days (p.10), not OCP's 85 °C/24 h guarantee.

For mechanism-only retention-age sensitivity, a defensible **PROXY interval**
is Ea = 1.01–1.08 eV with 1.04 eV nominal, using

`rate(T;Tref) = exp[(Ea/kB)*(1/Tref - 1/T)]`,

temperatures in kelvin and `kB = 8.62e-5 eV/K` as stated on PDF p.4. At
`T=Tref` the rate is exactly 1; for positive Ea it increases monotonically with
T. With `Tref=20 °C`, the derived interval is:

| Actual T | Equivalent-age rate, Ea 1.01–1.08 eV | Nominal Ea 1.04 eV |
| --- | ---: | ---: |
| 0 °C (extrapolated) | 0.043746–0.053583 | 0.049122 |
| 25 °C | 1.954794–2.047747 | 1.994103 |
| 70 °C | 338.272032–506.479372 | 402.154384 |
| 85 °C | 1413.631885–2337.095581 | 1753.519819 |
| 105 °C | 7975.998474–14866.399836 | 10415.489082 |

The 0/85/105 °C rows are extrapolations beyond the paper's 20–70 °C Ea-fit
domain. They are useful only as declared stress/sensitivity scenarios. Applying
the same proxy to OCP's 85 °C/24 h point would yield 3.87–6.40 years of
20 °C-equivalent age, but this is a **cross-source hypothetical**, not an OCP
claim and not a Sandisk lifetime prediction.

Recommended reliability options, in decreasing evidentiary strength:

1. Use OCP's powered 85 °C/24 h as a hard test condition/deadline only; report
   maintenance traffic and conditional equivalent age, not RBER or lifetime.
2. Use HeatWatch 1.01/1.04/1.08 eV as a three-model sensitivity for generic
   3D charge-trap retention aging, label all outputs `CONDITIONAL_SIMULATED`,
   and prohibit absolute Sandisk RBER/ECC/lifetime claims.
3. Keep planar 1.1 eV, cited by HeatWatch p.4, only as a visibly separate
   out-of-family comparator; it must not be merged into the 3D interval.
4. Before absolute reliability claims, obtain target NAND cell type/level,
   layer generation, device-specific Ea versus temperature/P/E, RBER response,
   ECC limit, retry distribution, read-disturb threshold, and maintenance
   service/energy. Those parameters remain UNKNOWN_BLOCKING.

## 5. Freeze implications and consistency checks

The following quantities can be frozen now as source facts or labeled proxies:

- OCP speed-grade maxima, illustrative organization/footprint, 0–105 °C
  junction range, powered 85 °C/24 h retention condition, qualitative thermal
  state semantics, typical 24–48 h maintenance range, and same-die exclusion.
- HBM2 current/VDD-power units and measurement domains; not target values.
- H200/HBM3E aggregate random-read active-energy proxy
  45.814978–46.940672 pJ/B at 4.5173–4.5233 TB/s; not per stack or HBM4/HBF.
- MFIT case material/geometry values and the derived areal quantities, only as
  numerical-case proxies.
- HeatWatch Ea 1.01–1.08 eV, nominal 1.04 eV, only as a 30–40-layer 3D
  charge-trap MLC mechanism proxy, with 20–70 °C as its fitted temperature
  domain.

Before accepting any parameter freeze, automated/static checks should enforce:

1. capacities retain GB/GiB and TB/TiB distinctions;
2. currents are never consumed as watts without a cited voltage;
3. H200 memory-rail power is never divided into per-stack power without an
   independently justified allocation model;
4. idle and active energy are separate and denominators identify delivered
   link bytes versus physical NAND bytes versus useful bytes;
5. MFIT geometry thicknesses sum to the declared case height, but are rejected
   as target geometry if the selected outer envelope differs;
6. Arrhenius uses kelvin, equals one at the reference temperature, and is
   monotonic for positive Ea;
7. any HeatWatch use below 20 °C or above 70 °C is marked extrapolated;
8. no-periodic-refresh and OCP-maintenance scenarios are mutually exclusive;
9. Light/Severe thresholds and performance fractions remain policy/scenario
   fields until a product source exists;
10. absolute HBF power, junction temperature, RBER, ECC margin, retry latency,
    and lifetime claims remain blocked until target data or a validated
    calibration closes the relevant inputs.
