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
from pathlib import Path

COMMIT = "ec26daecc8e787fb80fd95dd596a576404a5e36e"
PATCH_SHA = "63f4820fa41bdf6538ec45d805f3c68e3dea9a3e605ffbd65f3b88788c51121f"
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


def prepare(args, patch):
    pinned = args.local_pin.resolve()
    dest = args.source.resolve()
    if dest.exists():
        raise RuntimeError(f"source destination already exists: {dest}")
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
    run(["git", "-C", str(dest), "apply", "--check", str(patch)])
    run(["git", "-C", str(dest), "apply", str(patch)])
    rows = submodules(pinned, dest)
    frida_dir = dest / "third_party/frida"
    frida_dir.mkdir(parents=True, exist_ok=True)
    for kind, expected in FRIDA.items():
        path = getattr(args, f"frida_{kind}")
        if path is None:
            continue
        path = path.resolve()
        shutil.copyfile(path, frida_dir / f"frida-{kind}-devkit-16.1.2-linux-x86_64.tar.xz")
    receipt = {"schema": "hbfsim.bpftime_source_prepare.v1", "commit": COMMIT,
               "patch_sha256": digest(patch), "source": str(dest), "submodules": rows,
               "frida": {k: (str(getattr(args, f"frida_{k}")) if getattr(args, f"frida_{k}") else "CMake URL_HASH download") for k in FRIDA}}
    (dest.parent / f"{dest.name}.prepare.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def build(args, lock_script):
    source = args.source.resolve()
    build_dir = args.build.resolve()
    receipts = args.receipts.resolve()
    receipts.mkdir(parents=True, exist_ok=True)
    rows = []
    for mode in ("configure", "build", "check"):
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
    return {"status": "PASS", "locked_steps": rows, "artifacts": artifacts}


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
    lock_script = root / "scripts/bpftime_build_lock/locked_build.py"
    verify_lock_helpers(root, args.provision_lock_helpers)
    if digest(patch) != PATCH_SHA:
        raise RuntimeError("full ec26 patch SHA mismatch")
    if args.mode == "prepare":
        if args.local_pin is None:
            parser.error("--local-pin is required for prepare")
        result = prepare(args, patch)
    else:
        if args.build is None or args.receipts is None:
            parser.error("--build and --receipts are required for build")
        result = build(args, lock_script)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
