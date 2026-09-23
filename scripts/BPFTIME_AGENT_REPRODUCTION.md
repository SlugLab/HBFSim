# Rebuild the supported-weights bpftime agent

Use `scripts/reproduce_bpftime_agent.py` on giga. Its source starts at the pinned
bpftime commit `ec26daecc8e787fb80fd95dd596a576404a5e36e` and applies only
`patches/bpftime/0002-supported-weights-from-ec26.patch` (SHA-256
`63f4820fa41bdf6538ec45d805f3c68e3dea9a3e605ffbd65f3b88788c51121f`).
Do not first apply the older `0001` patch. The script clones all recursively
pinned submodule commits from a local, populated bpftime source and rejects
missing, dirty, or mismatched submodule objects. It does not modify that source.

```sh
python3 scripts/reproduce_bpftime_agent.py prepare \
  --local-pin /path/to/pinned/bpftime \
  --source /path/to/new/agent-source \
  --provision-lock-helpers \
  --allow-frida-download
python3 scripts/reproduce_bpftime_agent.py build \
  --source /path/to/new/agent-source \
  --build /path/to/new/agent-build \
  --receipts /path/to/new/agent-receipts
```

The Frida 16.1.2 Linux x86-64 devkits may instead be supplied with
`--frida-core` and `--frida-gum` for an offline build. The script checks their
SHA-256 (`45a7e47c…` core, `c20af106…` gum); when downloads are allowed,
bpftime's `cmake/frida.cmake` uses the pinned official release URLs and
`URL_HASH` before extraction. No Frida archive is redistributed here.

The build uses the unchanged `scripts/bpftime_build_lock/LOCK.json` and
`locked_build.py`: GCC/G++13, LLVM20, CUDA13, compatibility header, exact
feature/cache flags, and at most two build jobs. It records configure, build,
and check receipts and hashes the final agent and nvPTXCompiler DSOs. This lock
is host-specific: its absolute toolchain paths and SHA-256 values must match
before a build is accepted. The byte-identical GCC wrapper and Boost umbrella
header referenced by the lock are included under
`scripts/bpftime_build_lock/helpers/`; `--provision-lock-helpers` only copies
them to their pinned paths if absent and refuses to replace different files.
On a host with different absolute toolchain paths, create and validate a new
lock profile first; changing the paths is not equivalent to this tested build.
The older `scripts/build_patched_bpftime.sh`
belongs to the pre-integration patch profile and is not this build entry.

The first independent source preparation passed with all 20 nested gitlinks in
`main-reproduction-agent-recipe-prepare-v1/source.prepare.json`. The separate
actual clean agent build passed in `main-reproduction-agent-cpu-v1` with agent
SHA-256 `519ef5617b5e0cbe28ce0b91d2a14decbe3e2dc99498bf874ec3789dd36caba8`
and compiler SHA-256
`ee904bc6482648c9dcdfb43ad44e2c0bc396e8a15c42da21d87d7bb95b0b429c`.
These bytes differ from earlier successful experiment DSOs; no binary identity
with the old cohort is asserted.
