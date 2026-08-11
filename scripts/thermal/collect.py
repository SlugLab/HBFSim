#!/usr/bin/env python3
"""Collect fail-closed real-GPU and read-only Dell CD8P thermal telemetry."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time


EXPECTED_MODEL = "Dell DC NVMe CD8P E3.S 1.92TB"


def command_json(command: list[str]) -> dict:
    return json.loads(subprocess.run(command, check=True, text=True, capture_output=True).stdout)


def discover_cd8p() -> dict:
    devices = command_json(["lsblk", "-J", "-d", "-b", "-o", "NAME,MODEL,SERIAL,SIZE,TYPE"])["blockdevices"]
    matches = [item for item in devices if (item.get("model") or "").strip() == EXPECTED_MODEL]
    if len(matches) != 1 or matches[0].get("type") != "disk":
        raise RuntimeError(f"expected exactly one {EXPECTED_MODEL!r}, found {len(matches)}")
    item = matches[0]
    path = pathlib.Path("/dev") / item["name"]
    holders = list((pathlib.Path("/sys/class/block") / item["name"] / "holders").iterdir())
    mounts = subprocess.run(["findmnt", "-rn", "-S", str(path)], text=True, capture_output=True).stdout.strip()
    if holders or mounts:
        raise RuntimeError(f"refusing raw-device validation: holders={holders}, mounts={mounts!r}")
    return {"path": str(path), "model": EXPECTED_MODEL, "serial": item["serial"].strip(), "size_bytes": int(item["size"])}


def smart(device: str) -> dict:
    smartctl = subprocess.run(["bash", "-lc", "command -v smartctl"], text=True, capture_output=True).stdout.strip()
    if smartctl:
        raw = command_json([smartctl, "-j", "-a", device])
        log = raw.get("nvme_smart_health_information_log", {})
        temperature = raw.get("temperature", {}).get("current")
        return {"source": "smartctl", "temperature_c": temperature,
                "critical_warning": log.get("critical_warning", 0),
                "media_errors": log.get("media_errors", 0), "raw": raw}
    raw = command_json(["nvme", "smart-log", device, "-o", "json"])
    return {"source": "nvme-cli-smart-log", "temperature_c": int(raw["temperature"]) - 273,
            "critical_warning": raw["critical_warning"], "media_errors": raw["media_errors"], "raw": raw}


def gpu_sample() -> dict:
    fields = "name,uuid,driver_version,temperature.gpu,power.draw,utilization.gpu,clocks.sm,clocks.mem,memory.used"
    values = subprocess.run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                            check=True, text=True, capture_output=True).stdout.strip().split(", ")
    keys = ("name", "uuid", "driver", "temperature_c", "power_w", "utilization_pct", "sm_clock_mhz", "memory_clock_mhz", "memory_used_mib")
    result = dict(zip(keys, values))
    for key in keys[3:]:
        result[key] = float(result[key])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--heat-seconds", type=int, default=30)
    parser.add_argument("--cool-seconds", type=int, default=20)
    parser.add_argument("--sample-seconds", type=float, default=1.0)
    parser.add_argument("--matrix-size", type=int, default=8192)
    parser.add_argument("--fio-size", default="64G")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cd8p = discover_cd8p()
    initial_smart = smart(cd8p["path"])
    initial_gpu = gpu_sample()
    hardware = {"gpu": initial_gpu, "ssd": cd8p, "initial_smart": initial_smart,
                "read_only": True, "paper": "2333660.2333670.pdf"}
    (args.output / "hardware.json").write_text(json.dumps(hardware, indent=2) + "\n")

    gpu_trace = args.output / "gpu-performance.jsonl"
    heater = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).with_name("gpu_heat.py")),
                               "--duration", str(args.heat_seconds), "--matrix-size", str(args.matrix_size),
                               "--output", str(gpu_trace)])
    fio_output = args.output / "fio.json"
    fio_handle = fio_output.open("w")
    fio = subprocess.Popen([
        "fio", "--name=cd8p-read-only", f"--filename={cd8p['path']}", "--readonly", "--rw=read",
        "--bs=128k", "--iodepth=64", "--ioengine=io_uring", "--direct=1", "--time_based=1",
        f"--runtime={args.heat_seconds}", f"--size={args.fio_size}", "--offset=0", "--group_reporting=1",
        "--eta=never", "--output-format=json",
    ], stdout=fio_handle)
    started = time.time()
    with (args.output / "telemetry.jsonl").open("w") as trace:
        while time.time() - started < args.heat_seconds + args.cool_seconds:
            now = time.time()
            phase = "heat" if now - started < args.heat_seconds else "cool"
            sample = {"timestamp_s": now, "elapsed_s": now - started, "phase": phase,
                      "gpu": gpu_sample(), "ssd": smart(cd8p["path"])}
            trace.write(json.dumps(sample) + "\n")
            trace.flush()
            time.sleep(max(0.0, args.sample_seconds - (time.time() - now)))
    heater_status = heater.wait(timeout=30)
    fio_status = fio.wait(timeout=30)
    fio_handle.close()
    if heater_status or fio_status:
        raise RuntimeError(f"workload failure: heater={heater_status}, fio={fio_status}")
    final_smart = smart(cd8p["path"])
    if final_smart["critical_warning"] or final_smart["media_errors"] != initial_smart["media_errors"]:
        raise RuntimeError("CD8P SMART safety gate changed during read-only validation")
    (args.output / "safety.json").write_text(json.dumps({
        "initial": initial_smart, "final": final_smart,
        "media_error_delta": final_smart["media_errors"] - initial_smart["media_errors"],
        "fio_read_only": True,
    }, indent=2) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
