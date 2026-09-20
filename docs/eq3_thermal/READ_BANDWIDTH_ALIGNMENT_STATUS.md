# Current read-bandwidth alignment audit

Status: DOC_DERIVED from current production consumers of the isolated experiment, 2026-09-20. No running experiment parameters changed.

The current profile is an engineering composition, not a complete Sandisk or OCP speed-grade implementation. `campaign_inputs.py::configuration` supplies the following values:

| Layer | Actual parameter | Consumer | Qualification |
|---|---|---|---|
| HBF fabric fill/direct/relay stage | 1,600,000,000,000 B/s, each configured stage;10ns startup | BasicFabric stage duration | Sandisk Gen1 target magnitude; not calibrated sustained NAND delivery |
| Native NAND bus | 16 channels/stack,8bits/channel,1600MT/s | isolated online engine sets Flash_Channel_Width/Transfer_Rate, native NVDDR2 PHY | Engineering NAND proxy, not OCP UCIe host bus |
| Native page read | 10,000ns | profile defaults each Page_Read_Latency field, native NAND | Engineering assumption, not target HBF calibration |
| Whole-engine completion envelope | 512,000,000,000 B/s shared across all HBF stacks | dispatch_to_device callback bandwidth_cursor_ns | Existing engineering aggregate cap; not per stack. Raw/reported-completion equality composition guard remains active |
| Active workload | model payload /20,000s synthetic scan period, finite bursts | actual request generator, future byte gate | Sparse engineering replay, not a saturation test or token trace |

Sandisk July2025 fact sheet gives Gen1 1.6TB/s per16-die stack,512GB decimal. OCP v0.7.0 Table4 gives speed grades0.384/1.536/3.072TB/s. Table2 derives3072GB/s from16 host channels×8B×32GT/s×75% interface efficiency. Its page15 prose uses TiB/s while these tables use decimal GB/s/TB/s; table calculation is the explicit normalization basis. These are distinct profiles;1.6TB/s is not silently interchangeable with1.536TB/s.

Nominal internal configured bus capacity is16×1B×1600MT/s=25.6GB/s perstack before command/service/arbitration overhead. This is a calculated engineering interface ceiling, not measured throughput. The 1.6TB/s fabric value cannot create bytes that the native NAND proxy does not supply. Page/bank geometry alignment therefore does not establish bandwidth alignment.

Existing rolling-write diagnostic includes an actual subsequent read interval:16,384×4096 bytes delivered by native MQSim over1,402,880ns, about47.836GB/s aggregate over4 stacks. This is one finite warm mapped read trace, not a proven maximum, full-capacity OCP qualification, fabric-delivered throughput or a new run. Raw: `points/STARTUP-WRITE-ROLLING-W256-N16384-01/raw/summary.json`.

The thermal campaign may establish interface, sharing and control behavior under the recorded engineering assumptions. It cannot establish product bandwidth, target bandwidth utilization, or thermal steady-state at advertised sustained throughput. No operation duration, width, queue, power or timing is silently changed to force a product-rate match. Selecting a complete speed-grade proxy requires distinct source/version, native saturation and scaling evidence, causal energy accounting, and a separately registered comparison to these preserved engineering results.

Sources:
- https://documents.sandisk.com/content/dam/asset-library/en_us/assets/public/sandisk/collateral/company/Sandisk-HBF-Fact-Sheet.pdf
- https://www.opencompute.org/documents/ocp-hbf-architecture-specification-v0-7-0-final-pdf (Table2/Table4,p16)

## Fixed-parameter feasibility bound

For4KiB requests and10us media latency, Little's-law minimum simultaneous page service is ceil(B×10us/4096):938/3750/3907/7500 for .384/1.536/1.6/3.072TB/s perstack. This optimistic lower bound excludes command, transfer and arbitration overhead and does not claim queue entries equal independent media slots. Current queue256 is shared across the engine. Even an optimistic256 truly independent plane operations perstack gives104.8576GB/s at10us; the actual MQSim die/multiplane rule may impose tighter limits. Increasing queue or fabric bandwidth alone cannot establish these targets.

User requested feasibility on2026-09-20. No new speed-grade profile or internal-parallelism mapping is approved or applied by this audit. A target-rate abstraction must expose source assumptions, physical-to-service mapping and energy consequences; numeric rate matching alone is insufficient evidence.
