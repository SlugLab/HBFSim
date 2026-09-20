"""Read one physical layer/frame from two completed EQ3 reference fields."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from eq3_campaign_nativecodec import decoded_lines


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bounds(cells):
    return [[min(cell["xyz_m"][axis] for cell in cells),
             max(cell["xyz_m"][axis] + cell["size_m"][axis] for cell in cells)]
            for axis in range(3)]


def planar_distance(left, right):
    gaps = [max(left[axis][0] - right[axis][1],
                right[axis][0] - left[axis][1], 0.0) for axis in range(2)]
    return math.hypot(*gaps)


def extract_frame(run, layer, frame, library):
    grid = json.loads((run / "reference_grid.json").read_text())
    nx, ny, _ = grid["shape"]
    encoded = run / f"field_{layer}.txt.tmk"
    plain = run / f"field_{layer}.txt"
    if encoded.exists():
        stream_receipt = json.loads((run / "stream_receipt.json").read_text())
        if stream_receipt.get("status") != "PASS":
            raise ValueError("field transport is not PASS")
        expected = next(item for item in stream_receipt["fields"]
                        if item["path"] == encoded.name)
        lines = decoded_lines(encoded, library=library)
        expected_sha = expected["uncompressed_sha256"]
        expected_bytes = expected["uncompressed_bytes"]
        expected_frames = expected["frames"]
        source_path = encoded
        transport = "EQ3TMK1_LOSSLESS_DECODE"
    elif plain.exists():
        done = json.loads((run / "DONE.json").read_text())
        expected = next(item for item in done["output_file_sha256"]
                        if item["path"] == plain.name)
        expected_sha = expected["sha256"]
        expected_bytes = expected["size_bytes"]
        receipt = json.loads((run / "generation_receipt.json").read_text())
        expected_frames = round(receipt["duration_s"] / receipt["step_s"])
        lines = plain.open("rb")
        source_path = plain
        transport = "REGISTERED_PLAIN_RAW"
    else:
        raise FileNotFoundError(f"missing field layer {layer}")
    digest = hashlib.sha256()
    byte_count = numeric_rows = 0
    selected = []
    try:
        for line in lines:
            digest.update(line)
            byte_count += len(line)
            stripped = line.strip()
            if not stripped or stripped.startswith(b"%"):
                continue
            numeric_rows += 1
            values = [float(value) for value in stripped.split()]
            if len(values) != nx or not all(map(math.isfinite, values)):
                raise ValueError("invalid decoded field row")
            current_frame = (numeric_rows - 1) // ny + 1
            if current_frame == frame:
                selected.extend(values)
    finally:
        lines.close()
    if (digest.hexdigest() != expected_sha or
            byte_count != expected_bytes or
            numeric_rows != expected_frames * ny or
            len(selected) != nx * ny):
        raise ValueError("decoded layer identity/frame coverage mismatch")
    return selected, {"source_path": source_path.name,
                      "source_sha256": sha(source_path),
                      "transport": transport,
                      "decoded_sha256": digest.hexdigest(),
                      "decoded_bytes": byte_count, "decoded_rows": numeric_rows,
                      "selected_frame": frame, "layer": layer,
                      "complete_stream_identity_and_eof_checked": True}


def component_distribution(grid, values, component):
    output = []
    for index in grid["component_cells"][component]:
        cell = grid["cells"][index]
        x, y, z = cell["xyz_index"]
        output.append({"cell_id": cell["id"], "xyz_index": [x, y, z],
                       "center_m": cell["center_m"], "size_m": cell["size_m"],
                       "volume_m3": cell["volume_m3"],
                       "temperature_k": values[y * grid["shape"][0] + x]})
    return output


def aggregate(distribution):
    total_volume = math.fsum(row["volume_m3"] for row in distribution)
    hottest = max(distribution, key=lambda row: row["temperature_k"])
    return {"volume_weighted_mean_k": math.fsum(
                row["temperature_k"] * row["volume_m3"] for row in distribution) /
                total_volume,
            "hotspot_k": hottest["temperature_k"],
            "hotspot_cell_id": hottest["cell_id"],
            "hotspot_center_m": hottest["center_m"],
            "hotspot_minus_mean_k": hottest["temperature_k"] - math.fsum(
                row["temperature_k"] * row["volume_m3"] for row in distribution) /
                total_volume}


def coarsen(coarse, fine):
    projected = []
    tolerance = 1e-12
    for cell in coarse:
        x0, y0, _ = [cell["center_m"][axis] - cell["size_m"][axis] / 2
                     for axis in range(3)]
        x1, y1 = x0 + cell["size_m"][0], y0 + cell["size_m"][1]
        members = [row for row in fine
                   if x0 - tolerance <= row["center_m"][0] - row["size_m"][0] / 2 and
                   row["center_m"][0] + row["size_m"][0] / 2 <= x1 + tolerance and
                   y0 - tolerance <= row["center_m"][1] - row["size_m"][1] / 2 and
                   row["center_m"][1] + row["size_m"][1] / 2 <= y1 + tolerance]
        if len(members) != 4:
            raise ValueError("fine-to-coarse footprint is not exactly 2x2")
        volume = math.fsum(row["volume_m3"] for row in members)
        temperature = math.fsum(row["temperature_k"] * row["volume_m3"]
                                for row in members) / volume
        projected.append({"coarse_cell_id": cell["cell_id"],
                          "coarse_center_m": cell["center_m"],
                          "coarse_temperature_k": cell["temperature_k"],
                          "fine_member_ids": [row["cell_id"] for row in members],
                          "coarsened_fine_temperature_k": temperature,
                          "coarse_minus_coarsened_fine_k":
                              cell["temperature_k"] - temperature})
    return projected


def source_context(normalized, grid, component, time_s):
    interval = next(row for row in normalized["power"]["intervals"]
                    if math.isclose(row["end_s"], time_s, abs_tol=1e-12))
    component_bounds = {name: bounds([grid["cells"][index]
                                      for index in indices])
                        for name, indices in grid["component_cells"].items()}
    target = component_bounds[component]
    selected = {}
    for name in ("hbm3.die0", "hbm2.die0", "gpu"):
        selected[name] = {"bounds_m": component_bounds[name],
                          "planar_distance_to_target_m":
                              planar_distance(target, component_bounds[name]),
                          "power_w_in_interval_ending_at_frame":
                              interval["power_w"].get(name, 0.0)}
    hbm2 = {name: watts for name, watts in interval["power_w"].items()
            if name.startswith("hbm2.die") and watts != 0}
    return {"completed_interval_s": [interval["start_s"], interval["end_s"]],
            "target_bounds_m": target, "selected_neighbors": selected,
            "active_hbm2_die_count": len(hbm2),
            "active_hbm2_die_total_w": math.fsum(hbm2.values()),
            "gpu_power_w": interval["power_w"].get("gpu", 0.0),
            "hbm3_base_power_w": interval["power_w"].get("hbm3.base", 0.0),
            "hbm3_die_total_w": math.fsum(
                watts for name, watts in interval["power_w"].items()
                if name.startswith("hbm3.die"))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse-run", type=Path, required=True)
    parser.add_argument("--fine-run", type=Path, required=True)
    parser.add_argument("--codec-library", type=Path, required=True)
    parser.add_argument("--component", default="hbm3.base")
    parser.add_argument("--time-s", type=float, default=15.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    grids = [json.loads((run / "reference_grid.json").read_text())
             for run in (args.coarse_run, args.fine_run)]
    receipts = [json.loads((run / "generation_receipt.json").read_text())
                for run in (args.coarse_run, args.fine_run)]
    layers = []
    for grid in grids:
        values = {grid["cells"][index]["xyz_index"][2]
                  for index in grid["component_cells"][args.component]}
        if len(values) != 1:
            raise ValueError("target component spans multiple z layers")
        layers.append(values.pop())
    if layers[0] != layers[1]:
        raise ValueError("target component layer differs between grids")
    frames = []
    identities = []
    for run, receipt, layer in zip((args.coarse_run, args.fine_run), receipts, layers):
        frame = round(args.time_s / receipt["step_s"])
        if not math.isclose(frame * receipt["step_s"], args.time_s, abs_tol=1e-12):
            raise ValueError("requested time is not a solver frame")
        values, identity = extract_frame(run, layer, frame, args.codec_library)
        frames.append(values)
        identities.append(identity)
    distributions = [component_distribution(grid, values, args.component)
                     for grid, values in zip(grids, frames)]
    summaries = [aggregate(distribution) for distribution in distributions]
    projection = coarsen(distributions[0], distributions[1])
    coarsened_peak = max(projection, key=lambda row: row["coarsened_fine_temperature_k"])
    field_differences = [abs(row["coarse_minus_coarsened_fine_k"])
                         for row in projection]
    total_peak_difference = summaries[1]["hotspot_k"] - summaries[0]["hotspot_k"]
    sampling = summaries[1]["hotspot_k"] - coarsened_peak["coarsened_fine_temperature_k"]
    solved_field = coarsened_peak["coarsened_fine_temperature_k"] - summaries[0]["hotspot_k"]
    normalized = json.loads((args.fine_run / "normalized.json").read_text())
    result = {
        "schema_version": "eq3-p2-local-field-diagnostic-v1",
        "status": "READ_ONLY_DIAGNOSTIC_COMPLETE_REFERENCE_UNQUALIFIED",
        "component": args.component, "time_s": args.time_s,
        "z_layer": layers[0],
        "z_span_m": bounds([grids[1]["cells"][index]
                             for index in grids[1]["component_cells"][args.component]])[2],
        "coarse": summaries[0], "fine": summaries[1],
        "peak_difference_fine_minus_coarse_k": total_peak_difference,
        "decomposition": {
            "fine_cell_sampling_above_coarsened_fine_peak_k": sampling,
            "coarsened_fine_peak_minus_coarse_peak_k": solved_field,
            "sum_k": sampling + solved_field,
            "identity_residual_k": total_peak_difference - sampling - solved_field,
        },
        "coarse_vs_coarsened_fine": {
            "mean_abs_k": math.fsum(field_differences) / len(field_differences),
            "max_abs_k": max(field_differences),
            "max_abs_cell": max(projection,
                key=lambda row: abs(row["coarse_minus_coarsened_fine_k"])),
            "coarsened_fine_peak": coarsened_peak,
        },
        "source_context": source_context(normalized, grids[1], args.component, args.time_s),
        "coarse_distribution": projection,
        "fine_distribution": distributions[1],
        "identity": {
            "coarse": identities[0], "fine": identities[1],
            "codec_library_sha256": sha(args.codec_library),
            "coarse_grid_sha256": sha(args.coarse_run / "reference_grid.json"),
            "fine_grid_sha256": sha(args.fine_run / "reference_grid.json"),
            "normalized_sha256": sha(args.fine_run / "normalized.json"),
        },
        "reference_qualified": False, "model_freeze": False,
        "limitations": [
            "One completed frame and one physical layer only.",
            "2x2 volume projection separates sampling from resolved-field difference but does not prove which grid is physically accurate.",
            "No Richardson order is inferred from moving hotspot cells."
        ]
    }
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps({"status": result["status"],
                      "peak_difference_k": total_peak_difference,
                      "sampling_k": sampling, "field_k": solved_field}))


if __name__ == "__main__":
    main()
