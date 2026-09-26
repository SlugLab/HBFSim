# Root draft validator review — 2026-09-25

The draft is not GPU admission or model evidence. Sol6 is correcting these
findings and adding focused synthetic negative fixtures before final review.

1. Real successful epoch6704 `logs/correlation.jsonl` shows the original
   function as EXACT with durable d15cf249... FATBIN_BYTES. Its paired patched
   function is TOPOLOGY_ONLY with empty module_candidates. Requiring the
   original FATBIN in the patched callback would falsely reject valid evidence.
   Keep distinct joins: original function/image identity; paired selected agent
   original/patched/context/token; patched actual symbol/API/CBID/thread plus
   staged module/service identity. Never relabel restored PTX as original image.
2. An existential patched callback match is insufficient for multiple storages
   sharing a function. Require one non-reused actual patched callback for each
   selected decision, in observed per-thread order. Original immutable image
   proof may be reused for the same live original identity. Synthetic request
   IDs or API names are not evidence.
3. Validate context/original/patched strings as positive actual pointer values;
   `(nil)` and missing values must fail. Keep per-storage base/extent/profile
   and selected-order/decision-ordinal links.
4. Verify supplied plan against actual run plan and actual controller/owner
   consumed plan hash; use real schema fields. Require guard return code zero.
   Anchor correlation/coverage paths to this run and check actual combined-host
   activation/two-profile config, not only Python plugin activation.

The accepted6603 standard remains: each old98 storage needs a fresh addressed
modeled row in the allowed seven old modules and active COMPLETE module service
closure; declared inactive modules need not all become active. Exclude both
Li6 and Li7 from the old-module set. New targets need their exact Li6/Li7 module
and individual selected output/dispatch joins. Aggregate service counts alone
cannot fill a missing consumer. No new counter/checkpoint API is requested.
