#!/usr/bin/env python3
"""code-lint — source code must be English (CLAUDE.md §6a).

Why this exists
───────────────
The Hinglish rule was only ever about `docs/` and chat. It leaked into code
comments anyway, and by 2026-08-27 there were 4,435 Hinglish line hits across
51 source files. Nothing was checking, so nothing stopped it.

`docs-lint` is the mirror of this file: it asserts docs/ is Latin script,
this one asserts code is English. Both run inside `make verify`.

The token list is deliberately conservative — high-frequency Hinglish function
words that are not English words and not common identifiers. False positives
are worse than misses here: a lint nobody trusts gets disabled.
"""
from __future__ import annotations

import pathlib
import re
import sys

# Directories that are allowed to be Hinglish, per CLAUDE.md §6.
SKIP_DIRS = {"docs", ".git", ".venv", "venv", "node_modules", "__pycache__",
             ".pytest_cache", ".ruff_cache", ".mypy_cache", "data", "runs"}

# Files that are operating instructions or study material, not source.
SKIP_FILES = {"CLAUDE.md", "AGENTS.md", "AUDIT.md", "MEMORY.md"}

SUFFIXES = {".py", ".js", ".css", ".html", ".sql", ".sh", ".yml", ".yaml", ".toml"}
EXTRA_FILES = {"Makefile"}

TOKENS = [
    "hai", "hain", "nahi", "nahin", "kya", "kyun", "kyunki", "karta", "karte",
    "karna", "karo", "karke", "kiya", "yeh", "woh", "matlab", "chahiye",
    "zyada", "thoda", "pehle", "baad", "jaata", "jaati", "hota", "hoti",
    "hoga", "hogi", "milega", "sirf", "lekin", "agar", "toh", "phir", "abhi",
    "wahi", "yahan", "wahan", "iska", "uska", "unka", "humein", "mein",
    "saath", "bina", "jaan", "boojh", "poora", "poori", "sab", "kuch",
    "koi", "aur", "par", "raha", "rahi", "rahe", "gaya", "gayi", "diya",
    "liya", "banata", "banaya", "dekho", "socho", "wapas", "aata", "aati",
]
PATTERN = re.compile(r"\b(" + "|".join(TOKENS) + r")\b", re.IGNORECASE)


def targets(root: pathlib.Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix in SUFFIXES or path.name in EXTRA_FILES:
            yield path


def main() -> int:
    root = pathlib.Path(".")
    hits: dict[str, int] = {}
    samples: list[str] = []

    for path in targets(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        count = 0
        for lineno, line in enumerate(lines, 1):
            match = PATTERN.search(line)
            if match:
                count += 1
                if len(samples) < 12:
                    samples.append(f"  {path}:{lineno}: {line.strip()[:90]}")
        if count:
            hits[str(path)] = count

    if not hits:
        print("✅ clean — no Hinglish outside docs/")
        return 0

    total = sum(hits.values())
    print(f"🔴 {total} Hinglish line(s) across {len(hits)} file(s). Code is English (CLAUDE.md §6a).\n")
    for name, count in sorted(hits.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {count:5d}  {name}")
    if len(hits) > 20:
        print(f"  ... and {len(hits) - 20} more files")
    print("\nExamples:")
    print("\n".join(samples))
    return 1


if __name__ == "__main__":
    sys.exit(main())
