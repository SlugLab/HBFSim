"""One bounded, CPU-only isolated host build; never loads a GPU context."""
from pathlib import Path
import hashlib
import json
import os
import resource
import shutil
import subprocess
import time

TASK = Path(__file__).resolve().parent
BASE = TASK.parent / "router-li7-host-adapter-v1"
WORK = TASK / "build-v5"
SOURCE = TASK / "source"

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def limit():
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))

def run(label, args, cwd):
    (WORK / f"{label}.argv.json").write_text(json.dumps(args, indent=2) + "\n")
    with (WORK / f"{label}.stdout").open("wb") as out, (WORK / f"{label}.stderr").open("wb") as err:
        proc = subprocess.run(args, cwd=cwd, stdout=out, stderr=err,
                              timeout=600, preexec_fn=limit,
                              env={**os.environ, "CUDA_VISIBLE_DEVICES": ""})
    (WORK / f"{label}.rc").write_text(str(proc.returncode) + "\n")
    if proc.returncode:
        raise RuntimeError(f"{label} failed: {proc.returncode}")

def changed_argv(path, replacements):
    values = json.loads(path.read_text())
    for old, new in replacements.items():
        values = [entry.replace(old, new) for entry in values]
    return values

def main():
    if WORK.exists():
        raise RuntimeError("build-v5 already exists; refusing overwrite")
    WORK.mkdir()
    started = time.time()
    receipt = {"schema": "hbfsim.li6_li7_combined_cpu_build.v1",
               "status": "STARTED", "gpu_used": False}
    try:
        old_agent = BASE / "build-agent-v2"
        archive = WORK / "libbpftime_nv_attach_impl.a"
        shutil.copyfile(old_agent / archive.name, archive)
        old_obj = old_agent / "nv_attach_impl.cpp.o"
        setup_obj = WORK / "nv_attach_impl_frida_setup.cpp.o"
        args = changed_argv(old_agent / "agent_setup_compile.argv.json", {
            str(old_agent): str(WORK),
            str(BASE / "source/nv_attach_impl_frida_setup.cpp"):
                str(SOURCE / "nv_attach_impl_frida_setup.cpp")})
        run("agent_setup_compile", args, BASE.parent.parent.parent / "native-supported-partial-exact-agent-build-v1/build")
        run("agent_setup_archive", ["/usr/bin/x86_64-linux-gnu-ar", "r", str(archive), str(setup_obj)], WORK)
        run("agent_ranlib", ["/usr/bin/x86_64-linux-gnu-ranlib", str(archive)], WORK)
        args = changed_argv(old_agent / "agent_link.argv.json", {
            str(old_agent): str(WORK)})
        run("agent_link", args, BASE.parent.parent.parent / "native-supported-partial-exact-agent-build-v1/build")
        old_provider = BASE / "build-provider-v3"
        args = changed_argv(old_provider / "provider_compile.argv.json", {
            str(BASE / "source/provider_router_exact.cpp"): str(SOURCE / "provider_router_exact.cpp"),
            str(BASE / "source"): str(SOURCE),
            str(old_provider / "libprovider_router.so"): str(WORK / "libprovider_router.so")})
        run("provider_compile", args, TASK)
        for name in ("libbpftime-agent.so", "libprovider_router.so"):
            run(name + ".exports", ["/usr/bin/x86_64-linux-gnu-nm", "-D", "--defined-only", str(WORK/name)], TASK)
            run(name + ".resolution", ["/usr/bin/ldd", "-r", str(WORK/name)], TASK)
        receipt["status"] = "CPU_BUILD_PASS_NO_GPU"
        receipt["source_to_product"] = {
            "setup": {"source": str(SOURCE/"nv_attach_impl_frida_setup.cpp"),
                      "command": "agent_setup_compile.argv.json",
                      "object": str(setup_obj), "archive": str(archive),
                      "final_dso": str(WORK/"libbpftime-agent.so")},
            "unchanged_impl_member": {"object": str(old_obj), "base_archive": str(old_agent/archive.name),
                                      "actual_archive": str(archive)},
            "provider": {"source": str(SOURCE/"provider_router_exact.cpp"),
                         "companion_source": str(SOURCE/"library_identity_core.cpp"),
                         "command": "provider_compile.argv.json", "archive": None,
                         "final_dso": str(WORK/"libprovider_router.so")},
            "unchanged_gate": str(BASE/"build-gate-v2/libhbfsim_launch_gate.so")}
        receipt["products"] = {p.name: {"sha256": digest(p), "bytes": p.stat().st_size}
                               for p in (archive, WORK/"libbpftime-agent.so", WORK/"libprovider_router.so")}
    except Exception as exc:
        receipt["status"] = "CPU_BUILD_FAILED"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        receipt["elapsed_seconds"] = time.time() - started
        (TASK/"CPU_BUILD_RECEIPT.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

if __name__ == "__main__":
    main()
