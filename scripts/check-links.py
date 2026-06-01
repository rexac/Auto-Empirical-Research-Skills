#!/usr/bin/env python3
"""Check external links in AERS-maintained Markdown and HTML files."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "catalog" / "external-link-check.json"
LINK_RE = re.compile(r"(?:href=[\"']|\]\()(https?://[^)\"' >]+)")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
ACCESS_LIMITED_STATUSES = {403, 429}


def maintained_docs() -> list[Path]:
    paths: list[Path] = []
    paths.extend(ROOT.glob("*.md"))
    if (ROOT / "docs").exists():
        paths.extend((ROOT / "docs").rglob("*.md"))
        paths.extend((ROOT / "docs").rglob("*.html"))
    return sorted(set(paths))


def iter_links(paths: list[Path]) -> dict[str, list[str]]:
    links: dict[str, list[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in LINK_RE.finditer(text):
            url = match.group(1).rstrip(".,")
            links.setdefault(url, []).append(path.relative_to(ROOT).as_posix())
    return links


def check_url(url: str, timeout: float) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AERS-link-check/1.0"},
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"url": url, "status": response.status, "ok": response.status < 400}
    except urllib.error.HTTPError as error:
        if error.code in REDIRECT_STATUSES | ACCESS_LIMITED_STATUSES | {405}:
            return retry_get(url, timeout, tolerated_status=error.code)
        return {"url": url, "status": error.code, "ok": False, "error": str(error)}
    except Exception as error:  # noqa: BLE001 - CLI should report every failure type.
        return retry_get(url, timeout, error=str(error))


def retry_get(url: str, timeout: float, tolerated_status: int | None = None, error: str | None = None) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"User-Agent": "AERS-link-check/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"url": url, "status": response.status, "ok": response.status < 400}
    except urllib.error.HTTPError as http_error:
        if http_error.code in ACCESS_LIMITED_STATUSES:
            return {
                "url": url,
                "status": http_error.code,
                "ok": True,
                "warning": "Access-limited endpoint; treated as reachable.",
            }
        if http_error.code in REDIRECT_STATUSES:
            return {
                "url": url,
                "status": http_error.code,
                "ok": True,
                "warning": "Redirect endpoint; treated as reachable.",
            }
        return {"url": url, "status": http_error.code, "ok": False, "error": str(http_error)}
    except Exception as get_error:  # noqa: BLE001
        if tolerated_status in ACCESS_LIMITED_STATUSES:
            return {
                "url": url,
                "status": tolerated_status,
                "ok": True,
                "warning": "Access-limited endpoint; treated as reachable.",
            }
        if tolerated_status in REDIRECT_STATUSES:
            return {
                "url": url,
                "status": tolerated_status,
                "ok": True,
                "warning": "Redirect endpoint; treated as reachable.",
            }
        return {"url": url, "status": None, "ok": False, "error": error or str(get_error)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--max-links", type=int, default=0, help="debug limit; 0 checks all links")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    links = iter_links(maintained_docs())
    urls = sorted(links)
    if args.max_links:
        urls = urls[: args.max_links]

    results = []
    for index, url in enumerate(urls, start=1):
        result = check_url(url, args.timeout)
        result["files"] = links[url]
        results.append(result)
        print(f"[{index}/{len(urls)}] {url} -> {result.get('status')} {'ok' if result['ok'] else 'FAIL'}")

    payload = {
        "checked_files": [path.relative_to(ROOT).as_posix() for path in maintained_docs()],
        "checked_links": len(results),
        "failures": [result for result in results if not result["ok"]],
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if payload["failures"]:
        print(f"{len(payload['failures'])} external link(s) failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
