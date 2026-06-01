#!/usr/bin/env python3
"""Produce a reference candidate result for the LaLonde recovery benchmark.

This is a deliberately simple, transparent, dependency-free pipeline: it computes
the naive ATT, a pre-adjustment SMD balance table, and a regression-adjusted ATT
(OLS conditioning on pre-period earnings). It writes a candidate results.json
that the benchmark checker then grades. It exists so the benchmark is runnable
end to end out of the box; a real agent run would drop its own results.json in a
sibling candidate directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import lalonde  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "demo-notebooks" / "_lalonde_data.csv"
OUT = Path(__file__).resolve().parent / "candidates" / "reference-ols" / "results.json"


def main() -> int:
    rows = lalonde.load(DATA)
    treated, control = lalonde.split(rows, "treat")
    result = {
        "task": "lalonde-recovery",
        "method": "OLS regression adjustment (full controls incl. re74, re75)",
        "n_treated": len(treated),
        "n_control": len(control),
        "naive_att": round(lalonde.naive_att(rows, "treat", "re78"), 1),
        "adjusted_att": round(lalonde.adjusted_att(rows, "treat", "re78"), 1),
        "balance": {k: round(v, 3) for k, v in lalonde.smd_table(rows, "treat").items()},
        "notes": "Reference pipeline: pure-stdlib OLS. Naive comparison is "
                 "negative; adjustment conditioning on re74/re75 flips it positive "
                 "and near the experimental benchmark.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}")
    print(f"  naive ATT     = {result['naive_att']:>10,.1f}")
    print(f"  adjusted ATT  = {result['adjusted_att']:>10,.1f}")
    print(f"  imbalanced covariates (|SMD|>0.25): "
          f"{sum(1 for v in result['balance'].values() if abs(v) > 0.25)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
