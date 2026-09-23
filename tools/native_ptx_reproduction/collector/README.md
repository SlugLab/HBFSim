# Native PTX collector v2

This isolated CPU tool extracts original embedded PTX members with
`cuobjdump -xptx all`. It does not use `--dump-ptx` display output, disassembly,
or entry-name guesses as identity. The manifest retains container and member
SHA-256 identities, raw member bytes, PTX version/target, all entries, duplicate
content relationships, and one-to-many entry/member mappings.

Collector states are `READY`, `NO_PTX`, `PARSE_FAILURE`, and `TOOL_FAILURE`.
`READY` establishes collection provenance only; it does not claim runtime
binding, model coverage, transformed execution, or scientific admission.

Every output path is a single immutable attempt and must not already exist.
The container's path, size, SHA-256, device, inode, and nanosecond mtime are
checked before listing, after listing, and after extraction. A change produces
`CONTAINER_CHANGED` and no member identity claim.
