# Offline capacity trace replay on eval_base

This directory contains the minimal offline dependency closure selected from
`exp/hbm-hbf-capacity-qwen3-30b-a3b` at
`37144843906b3bd71f3fbac1fecc6b5080d82b95`.

It provides model inventory validation, CLOCK/LRU/Belady policies, routed-expert
trace validation, and capacity-ratio replay. It requires frozen model manifests
and traces as input. It does not load vLLM, stage live weights, collect new GPU
traces, or add a runtime statistics ABI. `stats_v2` here is an offline report
object, not the public runtime API.

See [capacity methodology](../../docs/eval/capacity_qwen3_30b_a3b.md) and the
[runbook](../../docs/eval/runbooks/mechanism_validation.md). The original live
capture/staging workflow stays at the pinned source branch and is UNVERIFIED
on eval_base. A toy trace test proves the replay implementation only.
