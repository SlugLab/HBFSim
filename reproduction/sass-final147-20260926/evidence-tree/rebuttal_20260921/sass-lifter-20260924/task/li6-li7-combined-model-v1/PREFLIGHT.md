# Combined mixed3 engineering preflight

Status: root scope reviewed; fresh resource admission and concrete live plan pending. No START.

**experiment_id**

li6-li7-combined-mixed3-v1

**authority**

USER_CONFIRMED SASS continuation: representatives then expansion then final regression; routine Astra root decision, not fabricated per-point user approval

**research_question**

Can the final combined host/provider and one model adapter execute layer0 QKV, o_proj and router sequentially in one real scheduled decode with correct outputs, binding and service closure?

**evidence_type**

Engineering model integration, not a calibrated simulator performance or paper speedup result

**environment**

Reuse frozen workspace-freeze-copy-v5/runtime-v3 and env-restore-v1/runtime-v2 plus separately built combined-host build-v5 agent/provider and frozen Li7 gate. Actual joins in BUNDLE_JOIN.json and CPU_BUILD_RECEIPT.json; no dependency changes.

**mechanism**

Immutable independent Li6/Li7 profiles and per-entry live identity query preserve exact model weight selection across three sequential consumers.

**controls**

[
  "Individual representative passes reused, no unchanged old98/6603 rerun",
  "Same model, request input2/output2/seed0, frozen staged PTX/helper, hybrid profile",
  "Native cloned input/output comparison before each selected consumer; final tokens versus native baseline"
]

**variables**

{
  "changed": "combined host/provider and unified model adapter",
  "fixed": "model/checkpoint/profile/input/helper/output acceptance",
  "measured": "actual storage, per-entry Driver callback, output equality, service accounting, walltime"
}

**scope**

Exactly layer0 qkv_proj,o_proj,mlp.gate; three registered storages. Old98 staged but unregistered. Head representative dependency passed6706; head not selected in this pilot.

**analytical_estimate**

INFERRED: about170k reference services at conservative240/s suggests~710s plus model setup. Different consumer grouping and service dynamics may change this; worker1800 is safety budget, not ETA. Head6706 completed3314.621s despite earlier4284s projection, reinforcing uncertainty.

**resources**

{
  "gpu": "one configured device; expected targetpeak26200MiB, fresh reservation sizing and5%free margin required",
  "cpu": "one model process family, no competing project build or experiment",
  "ram": "at least5%available, fresh measurement beforelaunch",
  "disk": "50GiBfree floor; no full cache mirroring",
  "pcie_io": "local model load plus existing reference service transport; no concurrent bulk transfer",
  "thermal": "single projectGPUworker; no calibrated performance claim",
  "budget_seconds": {
    "worker_seconds": 1800,
    "outer_seconds": 2100,
    "collection_seconds": 600,
    "startup_cleanup_seconds": 300,
    "full_admission_seconds": 3000
  },
  "request_timeout_seconds": 480
}

**output_contract**

Fresh runs/li6-li7-combined-mixed3-v1; START/controller/guard/raw plugin/actualmaps/env/provider/registration/accounting/finalresult; validate_combined_model.py strict acceptance

**pass**

Clean controller/guard, three exact currentrun selected consumers with unique callbacks, independent output equality, finaltokens, exactstorage/module modeledservice closure

**stop**

Worker1800/outer2100, resource guard or identity/error; preserve failure, no live budget extension

**confounders**

[
  "Configuration names alone do not prove actual entry",
  "Staged old98 does not imply coverage",
  "Shared module counters not individual storage latency",
  "CPU synthetic fixtures do not prove GPU behavior"
]

**interpretation**

Pass validates combined three-consumer mechanism only; remaining45 expansion and final147 regression still required. Failure diagnosed by identity/ABI/path/lifetime before any device change.

**figure_contract**

No performance figure; categorical evidence ledger and diagnostic walltime only

**observation**

No completion callback configured; one check near estimated completion, if running long then ~30min cadence; internal watchdog remains active

**reviewer**

root, Astra decision reviewer

**created_utc**

2026-09-26T00:55:18.279270+00:00
