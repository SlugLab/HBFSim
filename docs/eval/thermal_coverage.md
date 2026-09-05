# Thermal evaluation status

Thermal integration decision: **DEFER**. This branch retains hybrid's existing calibration/LogP utilities, profiles and historical proofs. These do not establish a live closed loop between temperature, retention, refresh, MQSim and GPU performance.

The optional runtime reliability subsystem on feature/thermal-reliability and the separate full-chain package/ROM snapshot are not imported. There is no thermal runtime switch to enable here. See [assessment](thermal_integration_assessment.md) for ABI, lineage, dependency and gate evidence. Future work starts from eval_base on `eval/thermal-reliability`; physical GPU telemetry and modeled HBF junction temperature must remain separate evidence classes.
