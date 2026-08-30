#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import csv
import json
import pathlib
import subprocess
import sys
import tempfile


KINDS = ["square_wave", "burst", "mixed_gpu_hbm_hbf",
         "write_heavy_hbf", "read_heavy_hbf"]


def run(command: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    assert result.returncode == expected, (command, result.stdout, result.stderr)
    return result


def dataset(path: pathlib.Path, trace_id: str, kind: str, seed: int) -> None:
    names = ["gpu", "hbf.base"]
    state = [30.0, 30.0]
    samples = []
    for index in range(48):
        power = [((index * (seed + 2)) % 11) / 10.0,
                 ((index * (seed + 3) + 2) % 13) / 12.0]
        samples.append({"time_ns": index * 1000, "power_w": power,
                        "temperature_c": state[:]})
        # Stable coupled dynamics with ||A||_inf > 1. This catches fitters that
        # scale A based on a row norm without preserving the 30 C equilibrium.
        state = [0.7 * state[0] + 0.4 * state[1] +
                 0.12 * power[0] + 0.03 * power[1] - 3.0,
                 -0.2 * state[0] + 0.5 * state[1] +
                 0.02 * power[0] + 0.16 * power[1] + 21.0]
    path.write_text(json.dumps(
        {"schema_version": 1, "trace_id": trace_id, "trace_kind": kind,
         "sample_period_ns": 1000, "input_names": names,
         "output_names": names, "samples": samples,
         "provenance": {"evidence_label": "synthetic_fixture"}},
        sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    assert len(sys.argv) in {3, 4}
    root = pathlib.Path(sys.argv[1]).resolve()
    rom_check = pathlib.Path(sys.argv[2]).resolve()
    offline = (pathlib.Path(sys.argv[3]).resolve() if len(sys.argv) == 4 else
               root / "plugins/package_thermal/offline")
    scripts = ["build_3dice_model.py", "run_3dice_sweeps.py",
               "extract_step_responses.py", "assemble_transfer_matrix.py",
               "fit_reduced_model.py", "fit_era_model.py",
               "validate_reduced_model.py",
               "decompose_rom.py"]
    for script in scripts:
        run([sys.executable, str(offline / script), "--help"])

    with tempfile.TemporaryDirectory(prefix="hbfsim-offline-") as directory:
        work = pathlib.Path(directory)
        geometry = work / "geometry.json"
        provenance = work / "parameter-provenance.csv"
        provenance.write_text(
            "parameter,value,unit,class,source,locator,dataset_sha256,calibration_sha256,note\n"
            "synthetic,1,ratio,C,unit-test,fixture,,,sensitivity only\n",
            encoding="utf-8")
        geometry.write_text(json.dumps({
            "schema_version": 1, "evidence_label": "synthetic_fixture",
            "three_d_ice_version": "4.0", "three_d_ice_commit": "e0bb685",
            "material_config": {"label": "synthetic"},
            "solver_settings": {"analysis": "transient"},
            "grid_settings": {"label": "synthetic"},
            "geometry": {"label": "synthetic plumbing only"},
            "parameter_provenance_csv": provenance.name},
            sort_keys=True) + "\n", encoding="utf-8")
        profile = root / "configs/package_thermal/synthetic-8hi.json"
        node_names = json.loads(profile.read_text(encoding="utf-8"))["topology"]["node_names"]
        template = work / "template"
        template.mkdir()
        floorplan = []
        for index, name in enumerate(node_names):
            floorplan.extend([f"{name} :", f"position {index}, 0 ;",
                              "dimension 1, 1 ;",
                              f"power values {{{{POWER_VALUES:{name}}}}} ;"])
        (template / "package.flp.in").write_text("\n".join(floorplan) + "\n",
                                                  encoding="utf-8")
        (template / "package.stk.in").write_text(
            "# synthetic dry plumbing fixture; not a calibrated 3D-ICE model\n",
            encoding="utf-8")
        sweep = work / "sweep"
        run([sys.executable, str(offline / "build_3dice_model.py"),
             "--package-profile", str(profile), "--geometry", str(geometry),
             "--template-dir", str(template), "--output", str(sweep),
             "--samples", "16"])
        plan = json.loads((sweep / "sweep-plan.json").read_text(encoding="utf-8"))
        assert len(plan["cases"]) == len(node_names) + 5
        assert all("\\" not in path for path in plan["cases"])
        assert all("\\" not in item[0] for item in plan["template_sha256"])
        assert (sweep / "parameter-provenance.csv").is_file()

        provenance.write_text(
            "parameter,value,unit,class,source,locator,dataset_sha256,calibration_sha256,note\n"
            "synthetic,1,ratio,M,unit-test,fixture,,,not measured\n",
            encoding="utf-8")
        bad_provenance = run([
            sys.executable, str(offline / "build_3dice_model.py"),
            "--package-profile", str(profile), "--geometry", str(geometry),
            "--template-dir", str(template),
            "--output", str(work / "bad-provenance-sweep"),
            "--samples", "16", "--dry-run"], 2)
        assert "measured provenance needs a dataset SHA-256" in \
            bad_provenance.stderr

        unit_cases = []
        for relative in plan["cases"]:
            case_path = sweep / relative
            case = json.loads(case_path.read_text(encoding="utf-8"))
            assert case["evidence_label"] == "synthetic_fixture"
            assert "\\" not in case["stack_file"]
            assert "\\" not in case["power_trace"]
            if case["trace_kind"] == "unit_step":
                unit_cases.append((case_path, case))
        legacy_case_path, legacy_case = unit_cases[0]
        del legacy_case["evidence_label"]
        legacy_case_path.write_text(
            json.dumps(legacy_case, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_manifest = work / "successful-runs.json"
        run_manifest.write_text(json.dumps({
            "schema_version": 1,
            "solver_identity": "synthetic extractor fixture",
            "runs": [{"trace_id": case["trace_id"], "returncode": 0}
                     for _, case in unit_cases]},
            sort_keys=True) + "\n", encoding="utf-8")
        extracted = []
        for case_path, case in unit_cases:
            power_path = sweep / case["power_trace"]
            with power_path.open("r", encoding="utf-8", newline="") as stream:
                power_rows = list(csv.reader(stream))
            temperature_path = work / f"{case['trace_id']}-temperature.csv"
            with temperature_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(["time_ns", *node_names])
                for sample, row in enumerate(power_rows[1:]):
                    writer.writerow([row[0], *[
                        30.0 + node * 0.01 + sample * 0.001
                        for node in range(len(node_names))]])
            output = work / f"{case['trace_id']}-dataset.json"
            run([sys.executable, str(offline / "extract_step_responses.py"),
                 "--case", str(case_path), "--power", str(power_path),
                 "--temperatures", str(temperature_path),
                 "--run-manifest", str(run_manifest), "--output", str(output)])
            assert json.loads(output.read_text(encoding="utf-8"))[
                "provenance"]["evidence_label"] == "synthetic_fixture"
            extracted.append(output)
        transfer = work / "transfer"
        assemble = [sys.executable,
                    str(offline / "assemble_transfer_matrix.py")]
        for path in extracted:
            assemble.extend(["--dataset", str(path)])
        assemble.extend(["--run-manifest", str(run_manifest),
                         "--geometry", str(geometry), "--output", str(transfer)])
        run(assemble)
        transfer_manifest = json.loads(
            (transfer / "manifest.json").read_text(encoding="utf-8"))
        assert transfer_manifest["source_ordering"] == node_names
        assert len(transfer_manifest["sources"]) == len(node_names)

        bad_temperature = work / "bad-temperature.csv"
        bad_temperature.write_text("wrong_header\n0\n", encoding="utf-8")
        failed_extract = run([
            sys.executable, str(offline / "extract_step_responses.py"),
            "--case", str(unit_cases[0][0]),
            "--power", str(sweep / unit_cases[0][1]["power_trace"]),
            "--temperatures", str(bad_temperature),
            "--run-manifest", str(run_manifest),
            "--output", str(work / "bad-extract.json")], 2)
        assert "header does not match" in failed_extract.stderr

        solver_manifest = work / "solver.json"
        solver_manifest.write_text(json.dumps({
            "schema_version": 1, "project": "esl-epfl/3d-ice",
            "version": "4.0", "commit": "e0bb685",
            "executable_sha256": "0" * 64,
            "source_url": "https://github.com/esl-epfl/3d-ice"},
            sort_keys=True) + "\n", encoding="utf-8")
        missing_solver = work / "missing-3D-ICE-Emulator"
        run([sys.executable, str(offline / "run_3dice_sweeps.py"),
             "--solver", str(missing_solver), "--solver-manifest",
             str(solver_manifest), "--plan", str(sweep / "sweep-plan.json"),
             "--dry-run"])
        failed = run([sys.executable, str(offline / "run_3dice_sweeps.py"),
                      "--solver", str(missing_solver), "--solver-manifest",
                      str(solver_manifest), "--plan", str(sweep / "sweep-plan.json")], 2)
        assert "missing or not executable" in failed.stderr

        training = []
        for index in range(3):
            path = work / f"training-{index}.json"
            dataset(path, f"training-{index}", "unit_step", index + 1)
            training.append(path)
        held = []
        for index, kind in enumerate(KINDS):
            path = work / f"held-{kind}.json"
            dataset(path, f"held-{kind}", kind, index + 10)
            held.append(path)
        model = work / "rom.json"
        fit = [sys.executable, str(offline / "fit_reduced_model.py")]
        for path in training:
            fit.extend(["--training", str(path)])
        for path in held:
            fit.extend(["--held-out", str(path)])
        fit.extend(["--model-id", "synthetic-offline-test",
                    "--geometry-sha256", hashlib.sha256(geometry.read_bytes()).hexdigest(),
                    "--solver-identity", "synthetic_fixture_not_3d_ice",
                    "--output", str(model)])
        run(fit)
        model_value = json.loads(model.read_text(encoding="utf-8"))
        payload = model_value["payload"]
        assert payload["evidence_label"] == "synthetic_fixture"
        a = payload["a"]
        bias = payload["bias"]
        left00, left01 = 1.0 - a[0], -a[1]
        left10, left11 = -a[2], 1.0 - a[3]
        determinant = left00 * left11 - left01 * left10
        equilibrium = [
            (bias[0] * left11 - left01 * bias[1]) / determinant,
            (left00 * bias[1] - bias[0] * left10) / determinant,
        ]
        assert max(abs(value - 30.0) for value in equilibrium) < 1.0e-6
        rejected_upgrade = run(
            fit[:-2] + ["--evidence-label", "measured", "--output",
                        str(work / "upgraded-rom.json")], 2)
        assert "would upgrade source evidence" in rejected_upgrade.stderr
        validation = work / "validation.json"
        validate = [sys.executable, str(offline / "validate_reduced_model.py"),
                    "--model", str(model), "--output", str(validation),
                    "--max-rmse-c", "0.001",
                    "--max-steady-hotspot-error-c", "0.001"]
        for path in held:
            validate.extend(["--held-out", str(path)])
        run(validate)
        assert json.loads(validation.read_text(encoding="utf-8"))["accepted"] is True
        run([str(rom_check), "rom_file", str(root), str(model)])

        decomposition = work / "decomposition.json"
        run([sys.executable, str(offline / "decompose_rom.py"),
             "--model", str(model), "--dataset", str(held[0]),
             "--output", str(decomposition)])
        decomposition_value = json.loads(
            decomposition.read_text(encoding="utf-8"))
        assert decomposition_value["accepted"] is True
        assert decomposition_value["maximum_superposition_error_c"] < 1.0e-8
        assert set(decomposition_value[
            "contribution_delta_c_at_full_hotspot"]) == {
                "gpu", "hbm", "hbf_self"}

        manifest = work / "manifest.json"
        run([sys.executable, str(root / "scripts/package_thermal_manifest.py"),
             "--output", str(manifest), "--repo", str(root),
             "--device-profile", str(root / "configs/profiles/nominal.json"),
             "--package-profile", str(profile), "--thermal-model", str(model),
             "--model-kind", "rom", "--thermal-mode", "package_rc",
             "--thermal-stage", "read_only", "--thermal-clock",
             "model_time_replay", "--three-d-ice-version", "4.0",
             "--three-d-ice-commit", "e0bb685",
             "--command", "synthetic package thermal manifest test"])
        manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
        assert manifest_value["repository"]["branch"] == "codex/package-thermal"
        assert len(manifest_value["repository"]["mqsim_patches"]) == 3
        assert manifest_value["inputs"]["thermal_model"]["sha256"] == hashlib.sha256(
            model.read_bytes()).hexdigest()
        assert manifest_value["evidence"]["hbf_temperature"] == \
            "model_based_projection"

        malformed = work / "malformed.json"
        malformed.write_text("{}\n", encoding="utf-8")
        bad = [sys.executable, str(offline / "fit_reduced_model.py"),
               "--training", str(malformed)]
        for path in held:
            bad.extend(["--held-out", str(path)])
        bad.extend(["--model-id", "bad", "--geometry-sha256", "0" * 64,
                    "--solver-identity", "bad", "--output", str(work / "bad.json")])
        run(bad, 2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
