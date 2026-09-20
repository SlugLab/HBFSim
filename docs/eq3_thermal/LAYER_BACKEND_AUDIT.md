# Pinned 3D-ICE backend and parse-only probe

DOC_DERIVED source audit, 2026-09-19. Backend source is 3D-ICE
`e0bb6850c5e446363e26936586d625270c87f224`; this is not new numerical
validation or experimental approval. No thermal equation was solved here.

## Expressible candidate and required mapping

The selected 64 mm package can be represented without changing its declared
geometry/material assumptions using a uniform lateral grid and the union of
all vertical component boundaries. A physical die may span several resulting
slabs. Give each powered slab a separate stack die with one source layer;
distribute component power among slabs by volume for the explicitly uniform
volumetric source assumption. Preserve each component's total energy. Recover
component means with volume weights and component hotspots with cell maxima.

Source evidence, relative to the pinned external source:

| Capability | Actual implementation / constraint |
| --- | --- |
| Multiple powered heights | `bison/stack_description_parser.y:1032` permits exactly one source per die definition; multiple die stack elements are permitted. `sources/power_grid.c:243` assigns a separate floorplan to each source height. |
| Heterogeneous material at one height | Named layer `layout` at parser line 779; `sources/layer.c:186` and `:248` use material-layout conductivity and heat capacity, with the declared layer material as background. Use this path for uniform grids, not merely floorplan material annotations. |
| Anisotropy | Parser line 320 accepts three conductivity components. `thermal_grid.c` uses axis-specific conductivities. Values must already use world x/y/z axes. |
| Top/bottom HTC | Parser line 386 and `thermal_grid.c:1199` support bottom convection alongside top convection for this multilayer uniform case. Single-layer simultaneous top and bottom sinks are not supported (`power_grid.c:505`); conversion must reject that special case. |
| Layer order | Stack declarations run top to bottom; offsets are assigned bottom-up at parser line 1232. |
| Background | Layer material fills locations not overridden by layout rectangles (`layer.c:220,274`). Converter coverage checks remain mandatory; fallback is not evidence that an omitted component was correctly represented. |
| Outputs | `Tflpel` average/maximum and `Tmap` target a stack die's source layer; parser lines 1995 and 2087. Collect all slices needed for a physical component; a single slice is not a whole-die maximum. |

Minimal structural grammar (lengths in micrometres):

```text
layer SLAB :
  height 35.75 ;
  material UNDERFILL ;
  layout "slab.layout" ;
die SLAB_DIE :
  source SLAB ;
stack:
  die S SLAB_DIE floorplan "slab.flp" ;
```

This snippet belongs inside a complete stack description, after material,
boundary and dimensions declarations and before solver/output sections.
Layout files group rectangles by material:

```text
SILICON :
  rectangle (16000, 48000, 12000, 16000);
  rectangle (36000, 48000, 12000, 16000);
```

Only one section per material is allowed; put all its rectangles together
(`layout_parser.y:260,446`). A floorplan source uses:

```text
REGION :
  position 16000, 48000 ;
  dimension 12000, 16000 ;
  power values 0, 1, 0 ;
```

Conductivity conversion is SI W/(m K) times 1e-6; volumetric capacity conversion
is SI J/(m3 K) times 1e-18. Non-grid-aligned geometry must be rejected or handled
by a separately validated discretization, not silently rounded. Nonuniform
bottom-boundary behavior is outside this audit. Nonzero residual contact
resistance is not silently supported by this grammar; the current candidate
already declares finite bond/TIM layers and ideal residual interfaces.

## Parsing is distinct from running the emulator

The stock emulator has no parse-only option. After parsing it generates output
headers, calls `thermal_data_build` and emulates (`bin/3D-ICE-Emulator.c:87–158`).
Do not use it for syntax-only verification.

`tools/eq3_3dice_parse_only.c` initializes the stock parser structures, invokes
`parse_stack_description_file`, prints a summary, and destroys the structures.
No thermal-data construction, factorization, output generation, or simulation
function is called. On parse failure the process exits without repeated cleanup
because some stock parser error actions already destroy these structures.

An important exception was found in the upstream parser: a pluggable heatsink
can call `initialize_pluggable_heatsink` during parsing (parser line 1808), which
loads and initializes arbitrary plugin code (`heat_sink.c:334`). The driver
therefore refuses any case-insensitive `pluggable` substring in the stack file
**before** invoking the parser. This conservative rule also refuses that word
in comments and paths. Use immutable generated inputs; this is a bounded probe
for the pinned backend, not a security sandbox or generic plugin validator.

For ordinary non-plugin inputs, the inspected chain opens stack, floorplan and
layout files for reading (`stack_file_parser.c:61`, `floorplan_file_parser.c:60`,
`layout_file_parser.c:61`). Parsing builds floorplan area mappings, power queues,
layer offsets and connection counts. These are allocations/input preprocessing,
not a thermal solve. Output declarations are retained in memory: the separate
output-header/file-generation functions are not invoked. Diagnostics can appear
on stdout/stderr; the driver's last stdout line is its JSON result.

## Portable isolated build and use

From the HBFSim source checkout, pass actual private build paths explicitly:

```sh
python3 -B tools/eq3_build_parse_only.py \
  --source tools/eq3_3dice_parse_only.c \
  --include ../reference/build/3d-ice-stock-e0bb685-gnu17-longint/include \
  --library ../reference/build/3d-ice-stock-e0bb685-gnu17-longint/lib/libthreed-ice-3.1.0.a \
  --link-arg=-lm --link-arg=-ldl \
  --output ../build/p2-parse-only/eq3_3dice_parse_only
```

The example paths are this workspace's inventory, not source requirements.
`--source`, repeatable `--include`/`--library`, `--cc`, `--link-arg` and `--output`
support other privately rebuilt locations. The builder refuses an existing
output. It compiles only the small driver, not 3D-ICE or HBFSim. Archive naming
`3.1.0` is an upstream naming artifact, not a replacement for the pinned commit.

Run from the generated input directory because stock relative floorplan/layout
paths resolve against the working directory:

```sh
/absolute/private/build/eq3_3dice_parse_only package.stk
```

Observed fixed software checks (no new research solve):

- Existing upstream `test/solid/transient/bothsink.stk`, with cwd `test`: exit 0,
  `PARSE_ONLY_PASS`, 4 layers, 50 rows, 200 columns, 40000 declared cells.
- Existing upstream `test/plugin/test_aligned.stk`: exit 2 before parsing,
  explaining the prohibited plugin initialization.
- External source checkout remained clean after the checks. Link succeeded
  against the existing private static archive plus libm/libdl, without SuperLU
  or BLAS linkage; no source, backend or simulator-core modifications.

Observed probe binary SHA256:
`d5750e0033f72c23487c89f64a06c07fa47829c16062a1246cfab83d8520a3ff`.
Linked private archive SHA256:
`7f8cd1e4f348c2dbdb249a6ed59bd268c8b007149da8c8c9472778ce9ff51615`.
These identify this build only; migration rebuilds must record new receipts.
Syntax PASS does not certify material fidelity, energy balance, numerical
accuracy, resource feasibility of solving, or any system-topology behavior.
