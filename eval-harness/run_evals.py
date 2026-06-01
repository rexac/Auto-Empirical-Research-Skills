#!/usr/bin/env python3
"""AERS skill evaluation harness.

Skills in this repo are prompt-context, not executable code, so we evaluate them
with *rubrics*: per-skill scenarios that pin down what an agent following the
skill must (and must not) produce. Each rubric item is either machine-checkable
(deterministic regex / word-count / numeric assertions, see ``lib/checks.py``)
or ``manual`` (routed to a human or LLM judge).

Three things this harness does, none of which need an API key:

1. ``lint`` (default) -- validate every scenario, confirm the skill-under-test
   and any fixture data exist on disk, confirm every machine-checkable rubric
   item is well-formed, and emit a coverage report. This is what CI runs.
2. ``--grade DIR`` -- score candidate agent outputs (``DIR/<scenario_id>.md``)
   against the machine-checkable rubric items and write a scorecard. Items that
   need judgement are reported as ``manual`` rather than silently passed.
3. ``--judge-prompts DIR`` -- emit a ready-to-paste judge prompt per scenario
   (scenario + rubric + candidate) so an external LLM or human can grade the
   manual items.

Usage:
    python3 eval-harness/run_evals.py                 # lint + coverage (CI gate)
    python3 eval-harness/run_evals.py --list
    python3 eval-harness/run_evals.py --grade eval-harness/candidates/_example
    python3 eval-harness/run_evals.py --judge-prompts /tmp/judge --grade eval-harness/candidates/_example
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from checks import ALL_CHECKS, AUTO_CHECKS, MANUAL_CHECK, run_check  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = ROOT / "eval-harness"
SCENARIO_DIR = EVALS_DIR / "scenarios"
RESULTS_DIR = EVALS_DIR / "results"


def rel(path: Path) -> str:
    """Repo-relative posix path when possible, else absolute."""
    p = path.resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)

REQUIRED_SCENARIO_FIELDS = ("id", "skill", "title", "category", "severity", "prompt", "rubric")
SEVERITIES = {"critical", "high", "medium", "low"}


class ScenarioError(Exception):
    pass


def load_scenarios() -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for path in sorted(SCENARIO_DIR.glob("*.toml")):
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        data["_path"] = path.relative_to(ROOT).as_posix()
        scenarios.append(data)
    return scenarios


def validate_scenario(s: dict[str, Any]) -> list[str]:
    """Return a list of problems; empty list means valid."""
    problems: list[str] = []
    sid = s.get("id", s.get("_path", "<unknown>"))

    for field_name in REQUIRED_SCENARIO_FIELDS:
        if not s.get(field_name):
            problems.append(f"{sid}: missing required field '{field_name}'")

    if s.get("severity") and s["severity"] not in SEVERITIES:
        problems.append(f"{sid}: severity '{s['severity']}' not in {sorted(SEVERITIES)}")

    # Filename should match id for predictable candidate lookup.
    if s.get("id") and s.get("_path"):
        stem = Path(s["_path"]).stem
        if stem != s["id"]:
            problems.append(f"{sid}: file stem '{stem}' != id '{s['id']}'")

    # Skill-under-test must exist.
    skill = s.get("skill")
    if skill and not (ROOT / skill).exists():
        problems.append(f"{sid}: skill path does not exist: {skill}")

    # Optional fixture data must exist if referenced.
    for data_path in s.get("context_data", []) or []:
        if not (ROOT / data_path).exists():
            problems.append(f"{sid}: context_data missing: {data_path}")

    rubric = s.get("rubric") or []
    if not rubric:
        problems.append(f"{sid}: rubric has no items")
    seen_ids: set[str] = set()
    for i, item in enumerate(rubric):
        rid = item.get("id")
        if not rid:
            problems.append(f"{sid}: rubric[{i}] missing 'id'")
        elif rid in seen_ids:
            problems.append(f"{sid}: duplicate rubric id '{rid}'")
        else:
            seen_ids.add(rid)
        check = item.get("check", MANUAL_CHECK)
        if check not in ALL_CHECKS:
            problems.append(f"{sid}/{rid}: invalid check '{check}'")
        if not item.get("description"):
            problems.append(f"{sid}/{rid}: missing 'description'")
        # Field-shape checks for auto checks so authoring errors fail fast.
        if check in {"regex_any", "regex_all", "regex_none", "regex_count_max"}:
            if not (item.get("pattern") or item.get("patterns")):
                problems.append(f"{sid}/{rid}: {check} needs 'pattern' or 'patterns'")
        if check == "regex_count_max" and "target" not in item:
            problems.append(f"{sid}/{rid}: regex_count_max needs 'target'")
        if check in {"word_count_max", "word_count_min"} and "target" not in item:
            problems.append(f"{sid}/{rid}: {check} needs 'target'")
        if check == "numeric_tolerance" and not ({"extract", "expected"} <= item.keys()):
            problems.append(f"{sid}/{rid}: numeric_tolerance needs 'extract' and 'expected'")
        if check == "numeric_sign" and "extract" not in item:
            problems.append(f"{sid}/{rid}: numeric_sign needs 'extract'")

    return problems


def rubric_stats(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    auto = manual = total = 0
    per_skill: dict[str, int] = {}
    per_severity: dict[str, int] = {}
    for s in scenarios:
        per_skill[s["skill"]] = per_skill.get(s["skill"], 0) + 1
        per_severity[s.get("severity", "?")] = per_severity.get(s.get("severity", "?"), 0) + 1
        for item in s.get("rubric", []):
            total += 1
            if item.get("check", MANUAL_CHECK) in AUTO_CHECKS:
                auto += 1
            else:
                manual += 1
    return {
        "scenarios": len(scenarios),
        "rubric_items": total,
        "auto_checkable": auto,
        "manual": manual,
        "skills_covered": len(per_skill),
        "per_skill": per_skill,
        "per_severity": per_severity,
    }


def grade_candidate(s: dict[str, Any], candidate_dir: Path, judge=None) -> dict[str, Any]:
    sid = s["id"]
    cand_path = None
    for ext in (".md", ".txt"):
        p = candidate_dir / f"{sid}{ext}"
        if p.exists():
            cand_path = p
            break
    if cand_path is None:
        return {"id": sid, "skill": s["skill"], "status": "no-candidate",
                "items": [], "auto_score": None}

    text = cand_path.read_text(encoding="utf-8", errors="replace")

    # Optional LLM judge verdicts for manual (and any other) items.
    verdicts: dict[str, dict] = {}
    judge_note = ""
    if judge is not None and any(i.get("check", MANUAL_CHECK) == MANUAL_CHECK for i in s["rubric"]):
        try:
            verdicts = judge(emit_judge_prompt(s, candidate_dir))
        except Exception as exc:  # network/parse failure must not crash the run
            judge_note = f"judge unavailable: {exc}"

    items_out = []
    earned = possible = 0.0
    required_failed = []
    manual_items = []
    for item in s["rubric"]:
        res = run_check(item, text)
        status, detail, evidence = res.status, res.detail, res.evidence
        # A judge can resolve a manual item into pass/fail.
        if status == "manual" and res.item_id in verdicts:
            v = verdicts[res.item_id]
            verdict = str(v.get("verdict", "")).lower()
            if verdict in {"pass", "fail"}:
                status = verdict
                detail = "judge: " + str(v.get("why", "")).strip()
        items_out.append({
            "id": res.item_id, "status": status, "weight": res.weight,
            "required": res.required, "detail": detail, "evidence": evidence,
        })
        if status in {"pass", "fail"}:
            possible += res.weight
            if status == "pass":
                earned += res.weight
            elif res.required:
                required_failed.append(res.item_id)
        elif status == "manual":
            manual_items.append(res.item_id)
    auto_score = (earned / possible) if possible else None
    status = "pass"
    if required_failed:
        status = "fail-required"
    elif auto_score is not None and auto_score < 1.0:
        status = "partial"
    out = {
        "id": sid, "skill": s["skill"], "candidate": rel(cand_path),
        "status": status, "auto_score": auto_score, "earned": earned, "possible": possible,
        "required_failed": required_failed, "manual_items": manual_items, "items": items_out,
    }
    if judge_note:
        out["judge_note"] = judge_note
    return out


def parse_judge_response(text: str) -> dict[str, Any]:
    """Extract the JSON verdict object from a (possibly fenced) LLM reply.

    Returns a dict keyed by rubric item id -> {verdict, why}. Tolerant of
    ```json fences and surrounding prose; raises ValueError if no JSON found.
    """
    cleaned = text.strip()
    # Strip a leading/trailing code fence if present.
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    blob = fence.group(1) if fence else None
    if blob is None:
        start, depth = cleaned.find("{"), 0
        if start == -1:
            raise ValueError("no JSON object in judge response")
        for i in range(start, len(cleaned)):
            depth += (cleaned[i] == "{") - (cleaned[i] == "}")
            if depth == 0:
                blob = cleaned[start:i + 1]
                break
    data = json.loads(blob)
    return {str(it.get("id")): it for it in (data.get("items") or []) if it.get("id")}


def make_anthropic_judge(model: str):
    """Return a callable(prompt)->verdict-map backed by the Anthropic SDK, or
    None if the SDK or ANTHROPIC_API_KEY is unavailable (graceful degrade)."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    client = anthropic.Anthropic()

    def judge(prompt: str) -> dict[str, Any]:
        msg = client.messages.create(
            model=model, max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        return parse_judge_response(text)

    return judge


def emit_judge_prompt(s: dict[str, Any], candidate_dir: Path | None) -> str:
    lines = [
        f"# Judge prompt: {s['id']}",
        "",
        f"You are grading an agent's output against the skill "
        f"`{s['skill']}`. Be a strict, fair econometrics referee.",
        "",
        "## Task given to the agent",
        "",
        s["prompt"].strip(),
        "",
        "## Rubric (score each item PASS/FAIL with one sentence of justification)",
        "",
    ]
    for item in s["rubric"]:
        flag = " (REQUIRED)" if item.get("required") else ""
        lines.append(f"- [{item['id']}]{flag} {item['description']}")
    lines.append("")
    if candidate_dir:
        for ext in (".md", ".txt"):
            p = candidate_dir / f"{s['id']}{ext}"
            if p.exists():
                lines += ["## Candidate output", "", "```", p.read_text(encoding="utf-8",
                                                                         errors="replace").strip(), "```", ""]
                break
    lines += ["## Output format", "",
              "Return a JSON object: {\"items\": [{\"id\": ..., \"verdict\": \"pass|fail\", "
              "\"why\": ...}], \"overall\": \"pass|partial|fail\"}."]
    return "\n".join(lines)


def cmd_lint(scenarios: list[dict[str, Any]], strict: bool) -> int:
    problems: list[str] = []
    for s in scenarios:
        problems.extend(validate_scenario(s))
    stats = rubric_stats(scenarios) if scenarios else {}
    print(f"Loaded {len(scenarios)} scenario(s) from {SCENARIO_DIR.relative_to(ROOT)}/")
    if scenarios:
        print(f"  rubric items: {stats['rubric_items']} "
              f"({stats['auto_checkable']} auto-checkable, {stats['manual']} manual)")
        print(f"  skills covered: {stats['skills_covered']}")
        print(f"  by severity: {stats['per_severity']}")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll scenarios valid.")
    return 0


def write_results(graded: list[dict[str, Any]], stats: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"summary": stats, "scenarios": graded}
    (RESULTS_DIR / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md = ["# Eval results", "",
          "Generated by `eval-harness/run_evals.py --grade`. Machine-checkable rubric items "
          "only; `manual` items still need a human or LLM judge.", "",
          "| Scenario | Skill | Status | Auto-score | Required-fails | Manual |",
          "|---|---|---|---:|---|---:|"]
    for g in graded:
        score = "-" if g.get("auto_score") is None else f"{g['auto_score']*100:.0f}%"
        rf = ", ".join(g.get("required_failed", [])) or "-"
        manual = len(g.get("manual_items", []))
        md.append(f"| `{g['id']}` | {g['skill'].split('/')[-1] or g['skill']} | "
                  f"{g['status']} | {score} | {rf} | {manual} |")
    (RESULTS_DIR / "RESULTS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Wrote {RESULTS_DIR.relative_to(ROOT)}/results.json and RESULTS.md")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AERS skill evaluation harness")
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    ap.add_argument("--grade", metavar="DIR", help="score candidate outputs in DIR")
    ap.add_argument("--judge-prompts", metavar="DIR", help="write judge prompts to DIR")
    ap.add_argument("--judge", action="store_true",
                    help="auto-grade manual items with an LLM judge (needs ANTHROPIC_API_KEY + anthropic SDK)")
    ap.add_argument("--judge-model", default="claude-sonnet-4-6", help="model id for --judge")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args(argv)

    if not SCENARIO_DIR.exists():
        print(f"No scenario directory at {SCENARIO_DIR}", file=sys.stderr)
        return 1

    scenarios = load_scenarios()

    # Always lint first; a malformed scenario should never be graded.
    lint_problems = [p for s in scenarios for p in validate_scenario(s)]
    if lint_problems and (args.grade or args.judge_prompts):
        print("Refusing to grade: scenarios have validation problems:", file=sys.stderr)
        for p in lint_problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if args.list:
        for s in scenarios:
            print(f"{s['id']:36s} {s.get('severity',''):8s} {s['skill']}")
        return 0

    if args.judge_prompts:
        out = Path(args.judge_prompts)
        out.mkdir(parents=True, exist_ok=True)
        cand = Path(args.grade) if args.grade else None
        for s in scenarios:
            (out / f"{s['id']}.md").write_text(emit_judge_prompt(s, cand), encoding="utf-8")
        print(f"Wrote {len(scenarios)} judge prompt(s) to {out}")

    if args.grade:
        cand = Path(args.grade)
        if not cand.exists():
            print(f"Candidate dir not found: {cand}", file=sys.stderr)
            return 1
        judge = None
        if args.judge:
            judge = make_anthropic_judge(args.judge_model)
            if judge is None:
                print("--judge requested but no judge available "
                      "(set ANTHROPIC_API_KEY and `pip install anthropic`); "
                      "grading machine-checkable items only.", file=sys.stderr)
            else:
                print(f"Judging manual items with {args.judge_model}…")
        graded = [grade_candidate(s, cand, judge=judge) for s in scenarios]
        stats = rubric_stats(scenarios)
        n_graded = sum(1 for g in graded if g["status"] != "no-candidate")
        n_failed = sum(1 for g in graded if g["status"] == "fail-required")
        write_results(graded, stats)
        print(f"\nGraded {n_graded}/{len(graded)} scenario(s); "
              f"{n_failed} with required-item failures.")
        return 1 if (n_failed and args.strict) else 0

    return cmd_lint(scenarios, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
