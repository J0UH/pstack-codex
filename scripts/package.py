#!/usr/bin/env python3
"""Assemble or check the distributable plugin without local runs or dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = (".codex-plugin", "skills", "agents", "automations", "companion-skills", "adapters", "hooks", "scripts", "docs", "examples", "upstream", "tests", "evidence")
FILES = ("README.md", "LICENSE", "NOTICE.md", "adaptations.json", ".gitignore")
SKIP = {"__pycache__", "node_modules", ".pytest_cache", ".DS_Store", ".poteto-mode-tools-install-key"}


def inputs() -> dict[str, bytes]:
    result = {}
    for name in DIRECTORIES:
        for path in (ROOT / name).rglob("*"):
            rel = path.relative_to(ROOT)
            if path.is_file() and not any(part in SKIP for part in rel.parts) and path.suffix not in {".pyc", ".log"}:
                if path.is_symlink():
                    raise ValueError(f"Package input must not be a symlink: {rel}")
                result[str(rel)] = path.read_bytes()
    for name in FILES:
        result[name] = (ROOT / name).read_bytes()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), "--check"], check=True, stdout=subprocess.DEVNULL)
    expected = inputs()
    target = ROOT / "plugins/pstack-codex"
    if args.check:
        actual = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()} if target.exists() else {}
        changed = sorted(set(expected) ^ set(actual) | {p for p in expected.keys() & actual.keys() if expected[p] != actual[p]})
        for name in expected.keys() & actual.keys():
            mode = 0o755 if (ROOT / name).stat().st_mode & 0o111 else 0o644
            if (target / name).stat().st_mode & 0o777 != mode:
                changed.append(name + " (mode)")
        if changed:
            print(json.dumps({"status": "stale", "files": changed}))
            return 1
    else:
        staging = target.with_name(".pstack-codex-build")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for name, data in expected.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755 if (ROOT / name).stat().st_mode & 0o111 else 0o644)
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    digest = hashlib.sha256(b"".join(name.encode() + b"\0" + expected[name] for name in sorted(expected))).hexdigest()
    print(json.dumps({"status": "verified" if args.check else "built", "files": len(expected), "content_sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
