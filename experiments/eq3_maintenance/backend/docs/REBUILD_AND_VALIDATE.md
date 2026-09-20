# Rebuild and fixed validation

Run from the integration checkout.  Use the system CMake explicitly because an
obsolete vendor CMake earlier failed before configuration; that preserved
launcher failure is `backend-build-v1`.

```sh
/usr/bin/cmake -S experiments/eq3_maintenance/backend \
  -B experiments/eq3_maintenance/backend/build-isolated -G Ninja
/usr/bin/cmake --build experiments/eq3_maintenance/backend/build-isolated \
  --parallel 2
```

Configuration verifies the upstream MQSim revision, creates a fresh writable
copy only inside `build-isolated/source/mqsim_eq3_maint`, checks and applies
patches 0001--0004 in order, and uses distinct library and executable names.
The resulting executable is:

`experiments/eq3_maintenance/backend/build-isolated/bin/hbf_mqsim_eq3_maint`

Fixed C++ validation:

```sh
experiments/eq3_maintenance/backend/build-isolated/tests/eq3_maintenance_backend_tests \
  configs/profiles/nominal.json
```

Process protocol validation requires a new artifact directory:

```sh
python3 experiments/eq3_maintenance/backend/tests/maintenance_service_test.py \
  "$PWD" \
  "$PWD/experiments/eq3_maintenance/backend/build-isolated/bin/hbf_mqsim_eq3_maint" \
  /absolute/path/to/new-test-artifact
```

These are small engineering checks.  They are not research campaign points and
do not validate the thermal model or full-capacity product geometry.

