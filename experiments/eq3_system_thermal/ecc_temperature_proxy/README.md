# Conditional temperature retry proxy

This default-off provider explores whether a favorable recoverable-read
temperature cost can alter the controller tradeoff. It does not predict target
HBF RBER, UBER, UECC, or data loss. The frozen HBF hardware, bandwidth, energy,
topology, and thermal parameters remain unchanged.

The candidate family and evidence classes are recorded in
`candidate_family_v1.json`. The OCP target material is used first for the
hardware contract, but it provides no numerical temperature/error curve. The
1.04 eV value is therefore retained only as an older-NAND proxy. The `p85`
values and two expected extra attempts per recoverable read are explicit
scenario assumptions. Every evaluated candidate, including the exact null,
must remain in exploratory outputs; no favorable result promotes a candidate
to a calibrated physical parameter.

Select one candidate by copying the common fields into
`hbf_read_cost_proxy.profile`, setting
`recoverable_read_probability_at_85c` to `0`, `0.01`, `0.1`, or the bounded
stronger diagnostic `0.3`, and configuring:

```json
{
  "hbf_read_cost_proxy": {
    "mode": "conditional_temperature_retry_v1",
    "profile": {
      "classification": "CONDITIONAL_TEMPERATURE_RETRY_SCENARIO_NOT_MEASURED_HBF_RBER",
      "recoverable_read_probability_at_85c": 0.1,
      "expected_extra_attempts_per_recoverable_read": 2.0,
      "activation_energy_ev": 1.04,
      "boltzmann_ev_per_k": 0.0000862,
      "temperature_reference_k": 358.15,
      "temperature_domain_k": [300.0, 400.0],
      "ecc_decoder_headroom_over_fresh_media": 2.0
    },
    "initial_by_stack": {
      "hbf0": {"temperature_k": 300.0}
    }
  }
}
```

`initial_by_stack` must cover every actual HBF stack exactly. The latest
completed 20 ms thermal observation supplies the next cost. Each job freezes
that cost at first service allocation, so active work never changes
retroactively. Extra attempts consume the existing media and base/ECC service
and energy paths; the valid useful payload crosses the external fabric once.
Maintenance and dynamic migration remain rejected because this memoryless
proxy does not add the missing data identity.

The optional feedback overlay uses
`temperature_retry_feedback.mode=conditional_temperature_retry_feedback_v1`.
It requires `strategy=guard_only`, observes every 20 ms, evaluates every
200 ms, changes future admission by 5%, and reviews a candidate after 1 s.
The existing severe and shutdown protections retain immediate authority and
clear an incomplete candidate. After three cool, backlogged evaluations with
observed admission gating, the overlay can recover one 5% step; no demand never
causes an increase. Retry load is the ratio of internal expected-work bytes to
completed useful bytes over an evaluation cohort. Request completions can cross
cohort boundaries, so small changes include boundary noise rather than a
measured instantaneous retry probability.
