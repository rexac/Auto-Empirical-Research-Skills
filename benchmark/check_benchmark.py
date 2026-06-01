#!/usr/bin/env python3
"""Grade a candidate result against an AERS benchmark task.

A "candidate" is a results.json produced by any pipeline (the reference pipeline,
the repo's demo outputs, or a real agent run). The checker recomputes the
data-derived golds (imbalance, naive sign, and the true naive/SMD values) from
the dataset itself, so a candidate cannot pass by reporting fabricated balance or
effect numbers. Literature golds (the experimental benchmark) come from the task
spec.

Usage:
    python3 benchmark/reference_pipeline.py            # produce reference candidate
    python3 benchmark/check_benchmark.py               # grade reference candidate
    python3 benchmark/check_benchmark.py --candidate benchmark/candidates/<run>
    python3 benchmark/check_benchmark.py --strict      # nonzero exit on required fail
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import lalonde  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TASK = Path(__file__).resolve().parent / "tasks" / "lalonde-recovery.toml"
DEFAULT_CANDIDATE = Path(__file__).resolve().parent / "candidates" / "reference-ols"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def grade(task: dict, candidate: dict, truth: dict) -> list[dict]:
    out = []
    for g in task["gold"]:
        gid, check = g["id"], g["check"]
        passed, detail = False, ""

        if check == "imbalance_count":
            thr = g["smd_threshold"]
            bal = candidate.get("balance", {})
            n_big = sum(1 for v in bal.values() if abs(v) > thr)
            passed = n_big >= g["min_covariates"]
            detail = f"{n_big} covariates with |SMD|>{thr} (need >= {g['min_covariates']})"

        elif check == "naive_sign":
            v = candidate.get("naive_att")
            want = g["expected_sign"]
            passed = v is not None and ((v < 0) if want == "negative" else (v > 0))
            detail = f"naive_att = {v} (want {want})"

        elif check == "adjusted_recovery":
            naive = candidate.get("naive_att")
            adj = candidate.get("adjusted_att")
            if naive is not None and adj is not None:
                swing = adj - naive
                passed = adj > 0 and swing >= g["min_swing"]
                detail = f"adjusted {adj} vs naive {naive} (swing {swing:+.0f}, need +{g['min_swing']:.0f} & positive)"
            else:
                detail = "missing naive_att or adjusted_att"

        elif check == "near_benchmark":
            adj = candidate.get("adjusted_att")
            bench, tol = task["experimental_att"], task["experimental_tol"]
            if adj is not None:
                passed = abs(adj - bench) <= tol
                detail = f"adjusted {adj} vs benchmark {bench} (tol {tol}; |diff|={abs(adj-bench):.0f})"
            else:
                detail = "missing adjusted_att"

        elif check == "cross_check":
            # Compare reported numbers to truth recomputed from the data.
            problems = []
            rn = candidate.get("naive_att")
            if rn is None or abs(rn - truth["naive_att"]) > g["naive_tol"]:
                problems.append(f"naive_att {rn} vs true {truth['naive_att']:.1f}")
            rbal = candidate.get("balance", {})
            for k, tv in truth["smd"].items():
                rv = rbal.get(k)
                if rv is None or abs(rv - tv) > g["smd_tol"]:
                    problems.append(f"SMD[{k}] {rv} vs true {tv:.3f}")
            passed = not problems
            detail = "reported numbers match data" if passed else "; ".join(problems[:3])

        else:
            detail = f"unknown check {check}"

        out.append({"id": gid, "required": g.get("required", False),
                    "weight": g.get("weight", 1), "passed": passed, "detail": detail})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grade an AERS benchmark candidate")
    ap.add_argument("--candidate", default=str(DEFAULT_CANDIDATE),
                    help="directory containing results.json")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    with TASK.open("rb") as fh:
        task = tomllib.load(fh)

    cand_path = Path(args.candidate)
    results_json = cand_path / "results.json" if cand_path.is_dir() else cand_path
    if not results_json.exists():
        print(f"No candidate results.json at {results_json}", file=sys.stderr)
        print("Run: python3 benchmark/reference_pipeline.py", file=sys.stderr)
        return 1
    candidate = json.loads(results_json.read_text(encoding="utf-8"))

    # Recompute data-derived truth.
    data = ROOT / task["data"]
    rows = lalonde.load(data)
    truth = {
        "naive_att": lalonde.naive_att(rows, task["treatment"], task["outcome"]),
        "smd": lalonde.smd_table(rows, task["treatment"]),
    }

    graded = grade(task, candidate, truth)
    earned = sum(g["weight"] for g in graded if g["passed"])
    possible = sum(g["weight"] for g in graded)
    req_fail = [g["id"] for g in graded if g["required"] and not g["passed"]]

    print(f"Benchmark: {task['id']}  (candidate: {results_json.parent.name})")
    print(f"Data: {task['data']}  |  N = {len(rows)}")
    print("-" * 72)
    for g in graded:
        mark = "PASS" if g["passed"] else "FAIL"
        req = "*" if g["required"] else " "
        print(f"  [{mark}]{req} {g['id']:28s} {g['detail']}")
    print("-" * 72)
    print(f"Score: {earned}/{possible}  |  required failures: {req_fail or 'none'}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "lalonde-recovery.json").write_text(
        json.dumps({"task": task["id"], "candidate": results_json.parent.name,
                    "earned": earned, "possible": possible,
                    "required_failures": req_fail, "items": graded,
                    "truth": {"naive_att": round(truth["naive_att"], 1),
                              "smd": {k: round(v, 3) for k, v in truth["smd"].items()}}},
                   indent=2) + "\n", encoding="utf-8")

    return 1 if (req_fail and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
