# Li7 host adapter pause checkpoint

Paused at user/root request on 2026-09-25 UTC so the session can restart with
new review settings. No GPU action or resource reservation action occurred.
There is no active Li7 CPU build/stage process at this checkpoint.

## Completed and durable

- Approved boundary read from
  `task/resume-giga-20260925-v1/LI7_HOST_DECISION.md` and the archived
  `task/router-li7-host-interface-audit-v1/REPORT.md`.
- Implementation plan: `IMPLEMENTATION_PLAN.md`, SHA-256
  `1986b3a817f310a717d53a5db6c6e40a09c51034191d3152d155bd1e73c3b39a`.
- Existing unchanged HBF pass staged the Li7 quarantined PTX successfully;
  stage rc 0, empty stderr. Source PTX SHA-256 `e70c7c4b...`, unchanged pass
  SHA-256 `0ab365ca...`, staged PTX `stage-v1/staged.ptx` SHA-256
  `7ac0e8a61283376711e6b13e8af9e4d7e2e5621ab1cb6896e6791280476c689a`.
  Pass manifest SHA-256
  `21a18f62209eff935b4876866ffc4013f1345fb9628f58ea8b84eb0c9dc169a5`;
  it records 19 exact parameters, 243 rewritten instructions, zero unsupported.
- Isolated, partial C++ source edits exist and are **not built or reviewed**:
  - `source/nv_attach_impl_frida_setup.cpp` SHA `d1b065a6...`: adds exact
    `router_li7` target pins, own source/staged/map hashes and router geometry.
  - `source/nv_attach_impl_router_scoped.cpp` SHA `4ea38ae3...`: retains Li6
    exact suppression and adds the exact Li7 name.
  - `source/launch_gate.cpp` SHA `7b38e7eb...`: adds the exact Li7 allowlist.
  - `source/provider_router_exact.cpp` SHA `20bcba59...`: adds explicit Li7
    symbol selection beside Li6.

## Important incomplete state

- No provider, agent, gate, archive, DSO or plugin was compiled. No CPU
  acceptance or dynamic-resolution check exists for the partial source edits.
- `router_model_adapter/__init__.py` is still the mechanically copied Li6
  plugin, SHA `9c7d5aa0...`; an attempted replacement patch failed atomically,
  so no router plugin implementation was installed. Its dist-info files are
  likewise still Li6 copies.
- Constant-bank/parameter-offset evidence and actual captured driver arguments
  have not yet been combined into a pointer-role reuse decision. Candidate
  widths alone must not be used.
- Exact manifest/sidecar path join package has not been created.
- The newly requested pure-output Li7 native-versus-uninstrumented-recovered
  harness package has not been prepared. `LIFT_OUTPUT_PASS` remains absent.
- No tier-2 or tier-3 evidence exists; no model coverage claim changed.

## Resume order

1. Re-read the decision and this checkpoint; inspect the partial diffs before
   deciding whether to retain or revise them.
2. Finish the default-off router plugin and pure-output harness preparation.
3. Complete pointer-role evidence without inferring semantics from widths.
4. Build provider/agent/gate in isolated paths and record the full
   source-command-object-archive-DSO chain, exports, resolution and CUDA domains.
5. Run CPU-only default-off and exact-join preflights; freeze for root review.
   Do not launch GPU work from this builder.
