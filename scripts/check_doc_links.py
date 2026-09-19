#!/usr/bin/env python3
"""Check that every relative Markdown link in the repository resolves to a file.

Only links that point inside the repository are checked.  http(s) links, mailto
links, and pure `#anchor` links are skipped, because resolving those needs the
network or a Markdown renderer.  A link with a trailing `#anchor` is checked up
to the `#`.

Exits non-zero and prints one line per broken link.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_PREFIX = ("http://", "https://", "mailto:", "#", "ftp://", "tel:")


def tracked_markdown(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "*.md"],
        check=True,
        capture_output=True,
    ).stdout
    return [root / p.decode() for p in out.split(b"\0") if p]


def main() -> int:
    root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    broken: list[str] = []
    checked = 0
    for md in tracked_markdown(root):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for target in LINK.findall(line):
                if target.startswith(SKIP_PREFIX):
                    continue
                path_part = target.split("#", 1)[0]
                if not path_part:
                    continue
                checked += 1
                resolved = (md.parent / path_part).resolve()
                if not resolved.exists():
                    rel = md.relative_to(root)
                    broken.append(f"{rel}:{lineno}: broken link -> {target}")
    for line in broken:
        print(line)
    print(f"checked {checked} in-repository links, {len(broken)} broken")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
