#!/usr/bin/env python3
"""Validate GitHub Actions workflow policy without external dependencies."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
CHECKOUT_RE = re.compile(r"^\s*-\s+uses:\s+actions/checkout@")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def workflow_paths() -> list[Path]:
    if not WORKFLOWS_DIR.exists():
        return []
    paths = list(WORKFLOWS_DIR.glob("*.yml"))
    paths.extend(WORKFLOWS_DIR.glob("*.yaml"))
    return sorted(paths)


def has_top_level_key(lines: list[str], key: str) -> bool:
    prefix = f"{key}:"
    return any(line.startswith(prefix) for line in lines)


def validate_checkout_credentials(path: Path, lines: list[str]) -> list[str]:
    errors: list[str] = []
    for index, line in enumerate(lines):
        if not CHECKOUT_RE.match(line):
            continue

        window = lines[index + 1 : index + 9]
        if not any("persist-credentials: false" in item for item in window):
            errors.append(
                f"{rel(path)}:{index + 1} actions/checkout must set persist-credentials: false"
            )
    return errors


def validate_workflow(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    errors: list[str] = []

    if "pull_request_target:" in text:
        errors.append(f"{rel(path)} uses pull_request_target; avoid privileged untrusted PR runs")
    if re.search(r"(curl|wget)\b[^\n|]*\|\s*(bash|sh)\b", text):
        errors.append(f"{rel(path)} pipes downloaded content into a shell")
    if not has_top_level_key(lines, "permissions"):
        errors.append(f"{rel(path)} is missing an explicit top-level permissions block")

    errors.extend(validate_checkout_credentials(path, lines))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    paths = workflow_paths()
    if not paths:
        print("error: no GitHub Actions workflows found", file=sys.stderr)
        return 1

    errors: list[str] = []
    for path in paths:
        errors.extend(validate_workflow(path))

    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1

    print(f"Workflow policy validation passed for {len(paths)} workflow(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
