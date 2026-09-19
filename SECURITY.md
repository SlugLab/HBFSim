# Security policy

## What this project is

HBFSim is a research simulator. It runs as an unprivileged user process, rewrites
the PTX of a CUDA workload you launch yourself, and talks to a local daemon over
shared memory. It is not a network service, it does not listen on a socket, and
it is not intended to enforce a security boundary between the workload and the
host.

Treat an HBFSim build the way you would treat any research artifact: run it on
workloads and machines you control.

## Reporting a vulnerability

Report privately, not in a public issue.

1. Open a private advisory through GitHub: **Security → Report a vulnerability**
   on <https://github.com/SlugLab/HBFSim>.
2. If that is unavailable to you, email `huyp@shanghaitech.edu.cn` with
   `HBFSim security` in the subject.

Please include the commit you tested, the build options you configured with, the
CUDA toolkit and driver versions, and the smallest reproducer you have.

We aim to acknowledge a report within seven days. Because this is an academic
project with no release engineering team, a fix may land as a commit on the
default branch rather than as a patched release.

## Supported versions

Only the default branch is supported. Older branches (`hybrid`,
`feature/*`, `exp/*`) are kept for provenance and receive no fixes.

## Out of scope

- A CUDA workload crashing under instrumentation, or producing different output
  under instrumentation. That is a correctness bug; open a normal issue.
- Anything that requires the reporter to already have code execution as the user
  running HBFSim.
- Vulnerabilities in the pinned dependencies (`bpftime`, `MQSim`). Report those
  upstream; tell us as well so the pin can move.
