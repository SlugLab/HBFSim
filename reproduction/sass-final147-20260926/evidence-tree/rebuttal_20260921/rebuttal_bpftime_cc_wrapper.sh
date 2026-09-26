#!/usr/bin/env bash
# Build-only compatibility wrapper for pinned legacy libbpf under GCC 13.
# The diagnostic remains visible, but this one const-qualification warning is
# not promoted to an error by bpftool's blanket -Werror.
exec /usr/bin/gcc-13 "$@" -Wno-error=discarded-qualifiers
