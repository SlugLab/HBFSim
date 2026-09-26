"""One isolated CPU-only Li7 recovery, offline assembly, and ABI extraction.

The strict diagnostic may return 1 and quarantine PTX. Offline assembly of
that quarantine is a syntax/resource check, never admission or model proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


SYMBOL = ("_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi7ELb0E18"
          "cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_")
BINARY_SHA = "3a5fb93bcc5a241afe578eeab4da4751c62646002064c5d1f4078f5d4a11990e"
LIFTER_SHA = "0ec6653a78a2e8eb1ac5a8f5aa1a34c2fbe310fd5c7bb073bbb36a289ee12ca9"
SASS_SHA = "b47041a156744059482761addf9241ea09c4ebdb759a0a0c8938bd47b158527a"
ORIGINAL_CUBIN_SHA = "7491aacefddd62ecf0c11f2ff798852d0a05a60ce465a377f52bd15d881e2963"
ORIGINAL_META_SHA = "837980a686e053203d9f3d60dca4d3dae162eb3b9f1aa961150f0b5388bbf4cf"
PTXAS_SHA = "85836303aeca1a2ac9387a6d364b276a3603907c95c3b06c9e3aadf46195b0d8"
CUOBJDUMP_SHA = "d76bde61348813d19643b18d7ae1b8287eabcdb2195cc4d6e6d860a9b1995900"
ROW = re.compile(r"Ordinal\s*:\s*0x([0-9a-fA-F]+)\s+Offset\s*:\s*0x([0-9a-fA-F]+)\s+Size\s*:\s*0x([0-9a-fA-F]+)")
CBANK = re.compile(r"EIATTR_CBANK_PARAM_SIZE.*?Value:\s*0x([0-9a-fA-F]+)", re.S)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def require_file(path: Path, expected: str | None = None) -> dict:
    if not path.is_file():
        raise RuntimeError(f"required input missing: {path}")
    actual = sha(path)
    if expected is not None and actual != expected:
        raise RuntimeError(f"input identity mismatch: {path}: {actual}")
    return {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}


def parse_kparams(text: str) -> tuple[int, list[dict]]:
    if SYMBOL not in text:
        raise RuntimeError("candidate ELF lacks exact Li7 function name")
    match = CBANK.search(text)
    if not match:
        raise RuntimeError("candidate ELF lacks CBANK_PARAM_SIZE")
    bank = int(match.group(1), 16)
    rows = [{"ordinal": int(o, 16), "offset": int(p, 16),
             "size": int(s, 16)} for o, p, s in ROW.findall(text)]
    if not rows or len({r["ordinal"] for r in rows}) != len(rows):
        raise RuntimeError("candidate KPARAM rows absent or duplicate")
    return bank, sorted(rows, key=lambda r: r["ordinal"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "lifter-source", "sass", "original-cubin",
                 "original-metadata", "ptxas", "cuobjdump", "out"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    inputs = {
        "binary": require_file(args.binary, BINARY_SHA),
        "lifter_source": require_file(args.lifter_source, LIFTER_SHA),
        "sass": require_file(args.sass, SASS_SHA),
        "original_cubin": require_file(args.original_cubin, ORIGINAL_CUBIN_SHA),
        "original_metadata": require_file(args.original_metadata, ORIGINAL_META_SHA),
        "ptxas": require_file(args.ptxas, PTXAS_SHA),
        "cuobjdump": require_file(args.cuobjdump, CUOBJDUMP_SHA),
    }
    original_text = args.original_metadata.read_text(encoding="utf-8")
    if SYMBOL not in original_text or not re.search(
            r"Ordinal\s*:\s*0x0\s+Offset\s*:\s*0x0\s+Size\s*:\s*0x98",
            original_text):
        raise RuntimeError("official original single 152-byte aggregate missing")
    out = args.out.resolve(strict=False)
    out.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "hbfsim.li7_cpu_recovery.v2", "status": "STARTED",
               "gpu_used": False, "symbol": SYMBOL, "inputs": inputs,
               "source_aggregate_bytes": 152, "jobs": []}
    started = time.monotonic()

    def job(name: str, argv: list[str], timeout: int = 120) -> int:
        remaining = 590 - (time.monotonic() - started)
        if remaining <= 0:
            raise RuntimeError("total CPU time budget expired")
        began = time.monotonic()
        proc = subprocess.run(argv, cwd=out, capture_output=True,
                              timeout=min(timeout, remaining), check=False)
        stdout = out / (name + ".stdout")
        stderr = out / (name + ".stderr")
        stdout.write_bytes(proc.stdout)
        stderr.write_bytes(proc.stderr)
        receipt["jobs"].append({"name": name, "argv": argv, "rc": proc.returncode,
                                "elapsed_seconds": time.monotonic() - began,
                                "stdout": str(stdout), "stderr": str(stderr),
                                "stdout_bytes": len(proc.stdout),
                                "stderr_bytes": len(proc.stderr)})
        return proc.returncode

    try:
        candidate = out / "li7.candidate.ptx"
        diagnostic = out / "li7.diagnostics.json"
        quarantine = out / "li7.quarantine.ptx"
        rc = job("recovery", [str(args.binary), "--input", str(args.sass),
                              "--kernel", SYMBOL, "--sm", "sm_120",
                              "--ptx-out", str(candidate),
                              "--diagnostics-out", str(diagnostic),
                              "--quarantine-out", str(quarantine),
                              "--cubin", str(args.original_cubin)])
        if not diagnostic.is_file():
            raise RuntimeError("recovery produced no structured diagnostics")
        d = json.loads(diagnostic.read_text(encoding="utf-8"))
        if d.get("kernel") != SYMBOL or d.get("observed_sm") != "sm_120" or \
                d.get("parsed_selected_instructions") != 1984 or \
                d.get("raw_instruction_rows") != 1984:
            raise RuntimeError("diagnostic selected-function identity/count changed")
        receipt["diagnostic"] = {"path": str(diagnostic), "sha256": sha(diagnostic),
                                 "status": d.get("status"), "admitted": d.get("admitted"),
                                 "reason_codes": [x.get("code") for x in d.get("reasons", [])]}
        if rc not in (0, 1):
            raise RuntimeError(f"recovery failed before normal strict result: {rc}")
        if rc == 1:
            if d.get("admitted") is not False or not quarantine.is_file():
                raise RuntimeError("strict rejection lacks preserved quarantine")
            reason_codes = [x.get("code") for x in d.get("reasons", [])]
            if len(reason_codes) != 13 or any(
                    code != "REQUIRES_SEMANTIC_PROOF" for code in reason_codes):
                raise RuntimeError("expected 13 preserved synchronization diagnostics")
            assembly_input = quarantine
        else:
            if not candidate.is_file():
                raise RuntimeError("successful recovery lacks candidate PTX")
            assembly_input = candidate
        receipt["offline_assembly_input"] = require_file(assembly_input)
        assembled = out / "li7-assembled.cubin"
        if job("ptxas", [str(args.ptxas), "-arch=sm_120", "-o", str(assembled),
                         str(assembly_input)]) != 0 or not assembled.is_file():
            raise RuntimeError("offline ptxas failed")
        receipt["candidate_cubin"] = require_file(assembled)
        if job("candidate-elf", [str(args.cuobjdump), "--dump-elf",
                                 "--function", SYMBOL, str(assembled)]) != 0:
            raise RuntimeError("candidate ELF metadata extraction failed")
        elf = (out / "candidate-elf.stdout").read_text(encoding="utf-8", errors="replace")
        bank, rows = parse_kparams(elf)
        layout_ok = (len(rows) == 19 and bank == 148 and all(
            row["ordinal"] == i and row["offset"] == 8 * i and
            row["size"] == (4 if i in (9, 14, 18) else 8)
            for i, row in enumerate(rows)))
        mapping = {"schema": "hbfsim.li7_byte_abi_mapping.v1",
                   "status": "LAYOUT_MATCH_NO_MODEL_VALIDATION" if layout_ok
                             else "CANDIDATE_LAYOUT_REQUIRES_REVIEW",
                   "source": {"symbol": SYMBOL, "original_cubin_sha256": ORIGINAL_CUBIN_SHA,
                              "single_aggregate_offset": 0, "single_aggregate_bytes": 152},
                   "candidate": {"cubin_sha256": sha(assembled),
                                 "cbank_param_size": bank, "kparam_rows": rows},
                   "mapping": [{"ordinal": r["ordinal"],
                                "source_aggregate_slice": [r["offset"], r["offset"] + r["size"]],
                                "candidate_offset": r["offset"],
                                "candidate_size": r["size"]} for r in rows] if layout_ok else [],
                   "semantic_pointer_roles": "UNKNOWN",
                   "gpu_output_equivalence": "NOT_RUN"}
        map_path = out / "li7-byte-abi-map.json"
        map_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
        receipt["abi_map"] = require_file(map_path)
        receipt["status"] = ("CPU_ASSEMBLY_AND_ABI_LAYOUT_PASS_QUARANTINED"
                             if layout_ok and rc == 1 else
                             "CPU_ASSEMBLY_AND_ABI_LAYOUT_PASS" if layout_ok else
                             "CPU_ASSEMBLY_PASS_ABI_REVIEW_REQUIRED")
        return 0 if layout_ok else 2
    except Exception as exc:
        receipt["status"] = "CPU_FAILED"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        return 3
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        (out / "CPU_RECEIPT.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
