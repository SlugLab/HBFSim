# Shared HBM endpoint policy adapter

Status: fixed software evidence complete; future sensitivity/diagnostic use is
prepared but not launched.

The base system runner creates the same read-rate feedback policy for HBF
foreground sources and HBM route endpoints.  A route endpoint can have zero
local foreground bytes while still carrying relay traffic.  After a Light
window reduces its budget to one half, the legacy feedback branch sees
insufficient local demand and holds that reduced budget after the endpoint
returns to Normal.  This is an adapter/caller mismatch, not evidence about the
thermal solver or backend service.

`endpoint_policy.EndpointAwarePolicy` is default-disconnected and selected
only by `run_endpoint_guard_point.py`.  It delegates HBF decisions to the
existing `ReadRatePolicy` without modification.  For an HBM shared endpoint it
uses only the observed endpoint thermal guard: Normal restores the configured
baseline, Light applies the configured half cap, and Severe/Shutdown apply the
configured severe/zero cap.  It neither fabricates HBM delivered bytes nor
changes the service scheduler, resource ownership, thermal solver, or base
runner.

The fixed reproducer records the old `INSUFFICIENT_DEMAND` half-cap hold and
the adapter's Normal recovery.  It also checks Light, Severe, Shutdown and exact
HBF legacy decision equality.  The isolated entry point adds its source hashes
and capability statement to each point manifest.

The already frozen/running base matrix remains on its original runner.  Four
pilots peaked only about 310--321 K for HBM and therefore did not enter Light;
their behavior is unaffected.  Completed base raw must still be checked for
any HBM Light transition before it is reused as a comparison.
