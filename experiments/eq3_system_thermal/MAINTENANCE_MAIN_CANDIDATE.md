# Maintenance-main candidate contract

`prepare_maintenance_main.py` is a default-off input generator. It neither
freezes nor launches a run. Its destination must be a new directory named
`maintenance-main-v1`, so pilot receipts and earlier raw data cannot be
overwritten.

## Pilot gate

Generation requires exactly one pilot for each of `mixed_direct`, `relay`,
`dash`, and `all_hbf_direct`. Each pilot must have:

- a `DONE.json` with `COMPLETED` status;
- a completed `analyze_extensions.py` result with matching point identity;
- passing timeline, byte-conservation, and energy-to-thermal checks;
- exact completion identities rather than only grouped hashes.
- at least one terminal maintenance operation, proving that the pilot exercised
  maintenance rather than only foreground traffic.

It also requires a separate root review receipt with status
`APPROVED_FOR_MAIN_INPUT_GENERATION`. That receipt binds the pilot-index hash,
every analysis hash, and a positive measured resource budget. This is an
engineering review gate under the already authorized four-topology plan; it is
not represented as a new user signature.

The measured budget records point/stage time and output limits, address-space
limit, CPU thread count, and host RAM/disk reserves. GPU count remains zero.

## Candidate matrix

The generator creates 19 inputs, all at 1.536 TB/s offered per stack for 20 s,
followed by 10 s recovery:

- 12 shared-resource points: four topologies by the three existing policies;
- three feedback-policy ideal-independent resource points for mixed, relay,
  and DASH;
- four mixed-topology shared-resource Ea endpoints: 1.01 and 1.08 eV by
  guard-only and feedback.

Ea 1.04 eV is already present in the 12-point shared matrix and is not
duplicated. Ideal-independent changes maintenance resource contention only;
it retains the actual controller states, admission guards, age, workload, and
energy assumptions.

The generated inputs copy the reviewed pilot's initial wall/equivalent age,
4 GiB aged subset per HBF stack, channel-owned spare geometry, program/erase
proxies, foreground energy profile, and topology service parameters. The
generator rejects pilot templates if these invariants or the offered bandwidth
differ.

## Output and next gate

The output contains `inputs/`, `CANDIDATE_INDEX.json`, and preflight candidate
documents. Its status is
`PILOT_REVIEW_PASSED_PREPARED_NOT_FROZEN_NOT_LAUNCHABLE`. A later root-owned
freeze must bind current source, model, thermal-binary, input, and resource
identities into a runnable index. No launcher consumes the candidate directly.

Results remain `CONDITIONAL_SIMULATED`: the service is an aggregate fluid
model rather than native MQSim NAND timing, maintenance validity is metadata
only, program/erase parameters are engineering proxies, and input generation
does not establish a scientific PASS.
