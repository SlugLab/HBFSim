# P2 4 mm to 2 mm static diagnostic

Status: read-only diagnosis, 2026-09-20. Existing source, generated inputs,
receipts, and retained sensor CSVs were inspected. No solver, build, or large
postprocessing job was run. Findings are `DOC_DERIVED` unless marked otherwise.

## Exact failing observable

`eq3_thermal/plans/campaign-v1/R01-R02-comparison.json` compares the full 100 s
R01 4 mm result against the full 100 s R02 2 mm result at the registered 0.1 s
sensor observations. Its global maximum absolute difference is **3.253 K**.
The exact maximum is:

| Field | R01 4 mm | R02 2 mm |
| --- | ---: | ---: |
| time | 31.0 s | 31.0 s |
| sensor | `component:hbf3.die15:hotspot` | same |
| observable | maximum cell temperature over that physical component | same |
| temperature | 312.007 K | 315.260 K |
| reported hotspot cell | `n60_3_11` | `n60_7_23` |

The cell identities differ because the lateral grids differ; both identify a
cell in the same component and z slab. The comparison is a maximum over all
registered **sensor/time** pairs, not a cell-by-cell field norm. The comparator
requires identical sensor sets and timestamps, then computes absolute error
(`tools/eq3_campaign_compare.py:28-46`). Both retained CSVs contain the same
275 sensor identities. This remains `NUMERICAL_ACCURACY_LIMIT`; this audit did
not find evidence that the 3.253 K value is caused by a mapping bug.

## Controlled inputs

The normalized inputs copied into the R01 and R02 run directories are exactly
equal as JSON. Both use 100 s duration, 20 ms solver step, 0.1 s observation,
1365 J input energy, and 21.1572437888 J/K total reference capacity. The only
intended generated-grid difference is:

- R01: `16 x 16 x 63`, 16,128 cells, 4 mm lateral pitch.
- R02: `32 x 32 x 63`, 64,512 cells, 2 mm lateral pitch.

Both retained observations contain all 5,000 solver frames. Their energy
residuals are small but nonzero because output temperature is quantized to
0.001 K and boundary energy is reconstructed with backward-Euler endpoint
flux: R01 `2.47e-6`, R02 `1.91e-6` relative. These receipts support completed
and internally accounted runs; they do not establish spatial convergence.

## Static consistency audit

| Contract | Static finding | Classification |
| --- | --- | --- |
| Geometry and material ownership | Uniform x/y planes must align every component edge; no snapping is allowed. Every cell has exactly one component or declared background owner, and every component's cell volumes must sum to its physical volume (`eq3_layered_export.py:23-89`). R01/R02 retain the same 63 z interfaces. | No inconsistency found. |
| Material units | Conductivity is exported from W/(m K) to W/(um K) by `1e-6`; volumetric heat capacity from J/(m3 K) to J/(um3 K) by `1e-18` (`:141-146`). Each z layer uses a material layout, so heterogeneous cells are not inferred from floorplan power labels. | No unit/mapping inconsistency found. |
| Internal interfaces | The independent RC/network audit uses two half-cell resistances in series, `dx/(2 k_left) + dx/(2 k_right)`, including material boundaries (`:92-103`). Stock input preserves every z material slab explicitly. Additional area contact resistance must be exactly zero or export fails (`:210-216`); the shared normalized input declares zero residual contact and represents finite bond/TIM layers explicitly. | Consistent with the declared ideal residual-interface assumption; nonzero contact remains unsupported. |
| Top/bottom Robin boundaries | Stock export converts HTC from W/(m2 K) to W/(um2 K) by `1e-12` and preserves ambient temperatures (`:147-150`). The independent network combines half-cell conduction and `1/h` in series (`:104-112`). Lateral boundaries must be explicitly adiabatic. A single-z-layer model with two HTC faces is rejected (`:135-140`). | No boundary translation inconsistency found. |
| Power/source weights | Each stock floorplan rectangle is exactly one cell. Component watts are split by `cell_volume/component_volume`; geometry volume conservation therefore makes component source weights sum to one across x/y/z (`:153-174`). Power transitions must align to slots, with no time averaging (`:116-132`). Both run receipts retain the same 1365 J source. | No duplication or omitted-source evidence found. |
| Layer and output order | Stack layers are emitted in reverse z declaration order as required by the pinned backend, while one `Tmap` is requested for every z slice at every solver step (`:175-181`). The reader opens fields in ascending z, reads exactly `nx*ny` finite values per frame, and concatenates slices in the same z/y/x order used to assign cell indices (`eq3_layered_observe.py:55-67,145-166`). | No ordering mismatch found. |
| Sensor reduction | Weighted means distribute each declared component weight by cell volume and assert total sensor weight one. Hotspots take the maximum over the exact component-cell index union and retain the winning cell ID (`eq3_layered_observe.py:70-96`). | The failing observable is correctly a component-cell maximum, not a mislabeled mean or request occupancy. |
| Boundary-energy observation | Boundary flux is integrated from every solver-step full field before 0.1 s sensor decimation (`eq3_layered_observe.py:133-180`). | Consistent with receipts; not an independent proof of the stock solver equation. |

## Interpretation boundary

The evidence supports a real sensitivity of the component hotspot observable to
4 mm versus 2 mm lateral discretization. Hotspots can change more than
volume-weighted means when a finer grid resolves a localized maximum; that is a
plausible mechanism, but attribution of the full 3.253 K difference remains
`INFERRED` until a same-window finer reference establishes a convergence trend.

No physical parameter, boundary, source weight, sensor definition, threshold,
or old PASS criterion should be changed to remove this failure. The original
`NUMERICAL_FAIL` remains valid and P2 remains unfrozen.
