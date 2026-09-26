"""Extract the sole Li7 image bytes from cuobjdump's noisy fatbin wrapper."""

import argparse
import hashlib
import json
from pathlib import Path
import re


SOURCE_SHA = "c9ba206e63d61c4e6e4c96e8603863beb20bd6f3460b7e0c3f7cee2f6e2dcf2a"
SYMBOL = ("_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi7ELb0E18"
          "cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    raw = args.source.read_bytes()
    if digest(raw) != SOURCE_SHA:
        raise RuntimeError("source Li7 SASS identity mismatch")
    headers = list(re.finditer(rb"(?m)^Fatbin elf code:\r?$", raw))
    if len(headers) != 1348 or headers[0].start() != 1:
        raise RuntimeError("unexpected fatbin wrapper shape")
    split = headers[1].start()
    image, rest = raw[:split], raw[split:]
    function_tag = b"Function : " + SYMBOL.encode("ascii")
    addresses = [int(m.group(1), 16) for m in re.finditer(
        rb"(?m)^\s*/\*([0-9a-fA-F]{4})\*/", image)]
    if image.count(function_tag) != 1 or image.count(b"Function : ") != 1:
        raise RuntimeError("first image is not the sole exact Li7 function")
    if addresses != list(range(0, 0x7C00, 16)):
        raise RuntimeError("first image does not contain exact 1984 aligned instructions")
    if b"Function : " in rest or re.search(rb"(?m)^\s*/\*[0-9a-fA-F]{4}\*/", rest):
        raise RuntimeError("trailing fatbin wrapper has a function or instruction")
    args.out.mkdir(parents=True, exist_ok=False)
    target = args.out / "li7-single-image.sass"
    target.write_bytes(image)
    receipt = {
        "schema": "hbfsim.li7_first_image_slice.v1",
        "source": str(args.source), "source_sha256": SOURCE_SHA,
        "source_bytes": len(raw), "first_image_byte_range": [0, split],
        "first_image_sha256": digest(image), "first_image_bytes": len(image),
        "trailing_byte_range": [split, len(raw)],
        "trailing_sha256": digest(rest), "trailing_header_count": len(headers) - 1,
        "trailing_function_count": 0, "trailing_instruction_count": 0,
        "exact_symbol": SYMBOL, "first_instruction": "0x0000",
        "last_instruction": "0x7bf0", "instruction_count": len(addresses),
    }
    (args.out / "SLICE_RECEIPT.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
