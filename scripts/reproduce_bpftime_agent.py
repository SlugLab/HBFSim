#!/usr/bin/env python3
"""Prepare and build the pinned ec26 bpftime agent in an isolated directory."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import re
from pathlib import Path

COMMIT = "ec26daecc8e787fb80fd95dd596a576404a5e36e"
PATCH_SHA = "63f4820fa41bdf6538ec45d805f3c68e3dea9a3e605ffbd65f3b88788c51121f"
COMPATIBILITY_PATCH_SHA = "9d7c957f62e3626140e09f8bb8cacb7acd8052c582dfcf937436fa6a310244fd"
FRIDA = {
    "core": "45a7e47c4181662611ce89c2891e5327676aaaf9e53ee205ef98f8d58a312a00",
    "gum": "c20af106e089bbbdb9ed4d5dfc63ce9ae8f6643bbb76a6b0afb196726d9a241a",
}


def verify_lock_helpers(root, provision):
    lock = json.loads((root / "scripts/bpftime_build_lock/LOCK.json").read_text())
    tools = lock["profiles"]["agent"]["tools"]
    for name, relative in {
        "cc_wrapper": "rebuttal_bpftime_cc_wrapper.sh",
        "compat_boost_process": "include/boost/process/v1.hpp",
    }.items():
        packaged = root / "scripts/bpftime_build_lock/helpers" / relative
        expected = tools[name]["sha256"]
        if digest(packaged) != expected:
            raise RuntimeError(f"packaged lock helper SHA mismatch: {packaged}")
        target = Path(tools[name]["path"])
        if not target.exists():
            if not provision:
                raise RuntimeError(f"locked helper missing: {target}; pass --provision-lock-helpers")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(packaged, target)
        if target.resolve() != Path(tools[name]["realpath"]):
            raise RuntimeError(f"locked helper realpath drift: {target}")
        if digest(target) != expected:
            raise RuntimeError(f"locked helper content drift: {target}")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run(argv, *, cwd=None, timeout=3600):
    proc = subprocess.Popen(argv, cwd=cwd, start_new_session=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        raise RuntimeError(f"timed out: {argv}\n{out[-2000:]}\n{err[-2000:]}")
    if proc.returncode:
        raise RuntimeError(f"failed {proc.returncode}: {argv}\n{out[-2000:]}\n{err[-2000:]}")
    return out.strip()


def gitlink(parent, name):
    row = run(["git", "-C", str(parent), "ls-tree", "HEAD", "--", name])
    parts = row.split()
    if len(parts) < 4 or parts[0] != "160000" or parts[1] != "commit":
        raise RuntimeError(f"missing gitlink: {parent}/{name}: {row}")
    return parts[2]


def submodules(source, dest):
    completed = []
    def visit(src, dst):
        listing = run(["git", "-C", str(dst), "ls-tree", "-r", "HEAD"])
        paths = []
        for line in listing.splitlines():
            left, _, name = line.partition("\t")
            if left.startswith("160000 commit "):
                paths.append((name, left.split()[2]))
        for name, expected in paths:
            source_child = src / name
            dest_child = dst / name
            if not source_child.is_dir() or not (source_child / ".git").exists():
                raise RuntimeError(f"pinned local submodule unavailable: {source_child}")
            if run(["git", "-C", str(source_child), "rev-parse", "HEAD"]) != expected:
                raise RuntimeError(f"source submodule gitlink mismatch: {name}")
            if run(["git", "-C", str(source_child), "status", "--porcelain"]):
                raise RuntimeError(f"source submodule dirty: {name}")
            if dest_child.exists() and any(dest_child.iterdir()):
                raise RuntimeError(f"destination submodule already populated: {dest_child}")
            dest_child.parent.mkdir(parents=True, exist_ok=True)
            run(["git", "clone", "--local", "--no-checkout", str(source_child), str(dest_child)])
            run(["git", "-C", str(dest_child), "checkout", "--detach", expected])
            completed.append({"path": str(dest_child.relative_to(dest)), "gitlink": expected})
            visit(source_child, dest_child)
    visit(source, dest)
    return completed


def source_inputs(source, patches, module_rows):
    """Bind tracked inputs and overlay additions.

    Generated build outputs are excluded; pinned submodules remain independent
    Git identities. Every initial source/header input is checked before building.
    """
    names = set()
    roots = [source] + [source / row["path"] for row in module_rows]
    for tree in roots:
        for name in run(["git", "-C", str(tree), "ls-files", "-z"]).split("\0"):
            path = tree / name
            if name and path.is_file():
                names.add(path.relative_to(source).as_posix())
    for patch in patches:
        names.update(re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), re.M))
    rows = []
    for name in sorted(names):
        path = source / name
        if not path.is_file():
            raise RuntimeError(f"prepared source input missing: {name}")
        rows.append({"path": name, "sha256": digest(path), "bytes": path.stat().st_size})
    return rows


def verify_prepared_source(source, patch, compatibility):
    receipt_path = source.parent / f"{source.name}.prepare.json"
    if not receipt_path.is_file():
        raise RuntimeError("compatibility preparation receipt missing; prepare a new source directory")
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("schema") != "hbfsim.bpftime_source_prepare.v2"
            or receipt.get("commit") != COMMIT
            or receipt.get("source") != str(source)
            or receipt.get("patch_sha256") != digest(patch)
            or receipt.get("compatibility_patch_sha256") != digest(compatibility)):
        raise RuntimeError("old or mismatched source profile; prepare a new source directory (historical sources are preserved)")
    if run(["git", "-C", str(source), "rev-parse", "HEAD"]) != COMMIT:
        raise RuntimeError("prepared source Git identity drift")
    for row in receipt["submodules"]:
        tree = source / row["path"]
        if run(["git", "-C", str(tree), "rev-parse", "HEAD"]) != row["gitlink"]:
            raise RuntimeError(f"prepared source submodule identity drift: {tree}")
    # CMake may download these after prepare. Hash their known identities when
    # present without treating that expected download as source-manifest drift.
    for kind, expected in FRIDA.items():
        archive = source / f"third_party/frida/frida-{kind}-devkit-16.1.2-linux-x86_64.tar.xz"
        if archive.exists() and digest(archive) != expected:
            raise RuntimeError(f"prepared Frida archive drift: {archive}")
    actual = source_inputs(source, (patch, compatibility), receipt["submodules"])
    if actual != receipt.get("source_inputs"):
        raise RuntimeError("prepared source input drift; prepare a new source directory")
    return {"path": str(receipt_path), "sha256": digest(receipt_path),
            "compatibility_patch_sha256": digest(compatibility), "inputs": len(actual)}


def prepare(args, patch, compatibility):
    pinned = args.local_pin.resolve()
    dest = args.source.resolve()
    if dest.exists():
        raise RuntimeError(f"source destination already exists: {dest}")
    receipt_path = dest.parent / f"{dest.name}.prepare.json"
    if receipt_path.exists():
        raise RuntimeError(f"preparation receipt already exists: {receipt_path}")
    for kind, expected in FRIDA.items():
        archive = getattr(args, f"frida_{kind}")
        if archive is None and not args.allow_frida_download:
            raise RuntimeError(f"Frida {kind} archive required or pass --allow-frida-download")
        if archive is not None and digest(archive.resolve()) != expected:
            raise RuntimeError(f"Frida {kind} SHA mismatch: {archive}")
    if run(["git", "-C", str(pinned), "rev-parse", "HEAD"]) != COMMIT:
        raise RuntimeError("local bpftime source is not pinned ec26 commit")
    run(["git", "clone", "--local", "--no-checkout", str(pinned), str(dest)])
    run(["git", "-C", str(dest), "checkout", "--detach", COMMIT])
    for item in (patch, compatibility):
        run(["git", "-c", "core.autocrlf=false", "-C", str(dest), "apply", "--check", str(item)])
        run(["git", "-c", "core.autocrlf=false", "-C", str(dest), "apply", str(item)])
    rows = submodules(pinned, dest)
    frida_dir = dest / "third_party/frida"
    frida_dir.mkdir(parents=True, exist_ok=True)
    for kind, expected in FRIDA.items():
        path = getattr(args, f"frida_{kind}")
        if path is None:
            continue
        path = path.resolve()
        shutil.copyfile(path, frida_dir / f"frida-{kind}-devkit-16.1.2-linux-x86_64.tar.xz")
    receipt = {"schema": "hbfsim.bpftime_source_prepare.v2", "commit": COMMIT,
               "patch_sha256": digest(patch), "source": str(dest), "submodules": rows,
               "compatibility_patch_sha256": digest(compatibility),
               "patch_order": [patch.name, compatibility.name],
               "source_inputs": source_inputs(dest, (patch, compatibility), rows),
               "frida": {k: (str(getattr(args, f"frida_{k}")) if getattr(args, f"frida_{k}") else "CMake URL_HASH download") for k in FRIDA}}
    with receipt_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(receipt, indent=2) + "\n")
    return receipt


def build(args, lock_script, patch, compatibility):
    source = args.source.resolve()
    build_dir = args.build.resolve()
    receipts = args.receipts.resolve()
    preparation = verify_prepared_source(source, patch, compatibility)
    receipts.mkdir(parents=True, exist_ok=True)
    rows = []
    for mode in ("configure", "build", "check"):
        verify_prepared_source(source, patch, compatibility)
        receipt = receipts / f"agent-{mode}.json"
        if receipt.exists():
            raise RuntimeError(f"refusing existing receipt: {receipt}")
        command = [sys.executable, str(lock_script), mode, "--profile", "agent",
                   "--source", str(source), "--build", str(build_dir),
                   "--receipt", str(receipt)]
        if mode == "build":
            command += ["--target", "bpftime-agent", "--jobs", "2"]
        run(command, timeout=3600)
        result = json.loads(receipt.read_text())
        if result.get("status") != "PASS":
            raise RuntimeError(f"locked {mode} did not pass: {receipt}")
        rows.append({"mode": mode, "receipt": str(receipt), "sha256": digest(receipt)})
    artifacts = {}
    for name, rel in {"agent": "runtime/agent/libbpftime-agent.so",
                      "compiler": "attach/nv_attach_impl/ptx_compiler/libnv_attach_impl_ptx_compiler.so"}.items():
        path = build_dir / rel
        if not path.is_file():
            raise RuntimeError(f"missing artifact: {path}")
        artifacts[name] = {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}
    verify_prepared_source(source, patch, compatibility)
    return {"status": "PASS", "locked_steps": rows, "artifacts": artifacts,
            "source_preparation": preparation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "build"))
    parser.add_argument("--local-pin", type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--frida-core", type=Path)
    parser.add_argument("--frida-gum", type=Path)
    parser.add_argument("--allow-frida-download", action="store_true")
    parser.add_argument("--provision-lock-helpers", action="store_true",
                        help="copy packaged GCC/Boost helpers only if their locked paths are absent")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    patch = root / "patches/bpftime/0002-supported-weights-from-ec26.patch"
    compatibility = root / "patches/bpftime/0003-supported-weights-ptx-compatibility.patch"
    lock_script = root / "scripts/bpftime_build_lock/locked_build.py"
    verify_lock_helpers(root, args.provision_lock_helpers)
    if digest(patch) != PATCH_SHA:
        raise RuntimeError("full ec26 patch SHA mismatch")
    if digest(compatibility) != COMPATIBILITY_PATCH_SHA:
        raise RuntimeError("supported-weights compatibility patch SHA mismatch")
    if args.mode == "prepare":
        if args.local_pin is None:
            parser.error("--local-pin is required for prepare")
        result = prepare(args, patch, compatibility)
    else:
        if args.build is None or args.receipts is None:
            parser.error("--build and --receipts are required for build")
        result = build(args, lock_script, patch, compatibility)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
