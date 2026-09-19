# OCP v0.7.0 original-document verification

Read 2026-09-19 from the committed `docs/HBF_OCP` PDF, not merely old audit text.
Baseline SHA 5eb789d5f1a42f0c040ee6fb5a2cdb5ffa0951d5.
PDF SHA256 `307531eb8053f00cbeccbc907ddff0a9c4fe6f9d0066a077ce33b0ac99312da3`.
130 pages, dated 03 AUG 2026. Rendered p16 and p106 were visually inspected.
The failed current website download (HTTP403) does not invalidate this local source.

| Location | Verified definition | Configuration consequence |
| --- | --- | --- |
| p16 Table4 | Maximum user bandwidth0.384/1.536/3.072TB/s; x64; speed8/16/32GT/s; maximum stack height8/16/16; virtual AXI interfaces1,2,4 | Store maximum height separately from actual die_count; no32Hi inference. Use table decimal bandwidth, retain conflicts below. |
| p16 Table3 | Example16 dies/cube,16 banks/channel,4096B page,512GiB cube | Not a per-grade capacity guarantee; Sandisk512GB is separate. |
| p106 section9/Table33 | Core-die retention at85C, power on,24h; endurance/read-disturb product-specific | User's interpretation confirmed. Not a throttle threshold or a fitted temperature-aging law. |
| p106 section9.1 | HBF junction0–105C, IEEE1500 monitoring | HBF domain only, not GPU/HBM universal limit. |
| p107 | Severe drains/completes or errors in-flight commands before AXI Ready withdrawal; no credits until recovery | Future active adapter must preserve requests/completions; no blind discard. |
| p117 section11.4 | No general GC/active-data relocation; zone remapping requires invalid data before rewrite | MQSim SSD GC cannot silently stand in for OCP host maintenance. |
| p118 section11.5 | Periodic refresh typically24–48h, product-specific; read/refresh must not overlap on same die | Real maintenance scheduling/resources required; no fixed universal NAND period. |

Unresolved specification conflicts are retained: p15 says3.072TiB/s, while
p16 Tables2/4 use GB/s and TB/s. Sections5.1.2.2–3 give94/188GB/s per module,
whose16-module totals1504/3008GB/s differ from Table4's1536/3072.
The current profile field explicitly follows Table4, not a reconciliation or
an assertion that actual useful throughput achieves that limit.

P/E limits and read-disturb counts depend on product registers/datasheets;
the24h statement does not give a universal numeric P/E operating condition.
No absolute command energy, calibrated RC network, complete temperature-to-RBER
curve or silicon endurance prediction follows from these normative fields.
