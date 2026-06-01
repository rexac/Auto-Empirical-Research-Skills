"""Tests for scenario loading/validation and the grader (eval-harness/run_evals.py)."""

from __future__ import annotations

import unittest

from _helpers import load_module

run_evals = load_module("eval-harness/run_evals.py", "aers_run_evals")


class TestRealScenarios(unittest.TestCase):
    def test_all_shipped_scenarios_are_valid(self):
        scenarios = run_evals.load_scenarios()
        self.assertGreaterEqual(len(scenarios), 1, "no scenarios found")
        problems = [p for s in scenarios for p in run_evals.validate_scenario(s)]
        self.assertEqual(problems, [], f"shipped scenarios invalid:\n" + "\n".join(problems))

    def test_every_scenario_skill_exists(self):
        for s in run_evals.load_scenarios():
            self.assertTrue((run_evals.ROOT / s["skill"]).exists(),
                            f"{s['id']} -> missing skill {s['skill']}")


class TestValidationCatchesBadScenarios(unittest.TestCase):
    def _base(self):
        return {
            "id": "x", "_path": "eval-harness/scenarios/x.toml", "skill": "skills",
            "title": "t", "category": "c", "severity": "high", "prompt": "p",
            "rubric": [{"id": "r", "check": "regex_any", "pattern": "a", "description": "d"}],
        }

    def test_missing_required_field(self):
        s = self._base(); del s["prompt"]
        self.assertTrue(any("prompt" in p for p in run_evals.validate_scenario(s)))

    def test_bad_severity(self):
        s = self._base(); s["severity"] = "urgent"
        self.assertTrue(any("severity" in p for p in run_evals.validate_scenario(s)))

    def test_stem_must_match_id(self):
        s = self._base(); s["_path"] = "eval-harness/scenarios/other.toml"
        self.assertTrue(any("file stem" in p for p in run_evals.validate_scenario(s)))

    def test_invalid_check_type(self):
        s = self._base(); s["rubric"][0]["check"] = "nope"
        self.assertTrue(any("invalid check" in p for p in run_evals.validate_scenario(s)))

    def test_regex_without_pattern(self):
        s = self._base(); s["rubric"][0] = {"id": "r", "check": "regex_any", "description": "d"}
        self.assertTrue(any("needs 'pattern'" in p for p in run_evals.validate_scenario(s)))

    def test_duplicate_rubric_ids(self):
        s = self._base()
        s["rubric"].append({"id": "r", "check": "manual", "description": "d"})
        self.assertTrue(any("duplicate rubric id" in p for p in run_evals.validate_scenario(s)))


class TestGrader(unittest.TestCase):
    def test_example_candidates_discriminate(self):
        scenarios = run_evals.load_scenarios()
        by_id = {s["id"]: s for s in scenarios}
        cand = run_evals.ROOT / "eval-harness" / "candidates" / "_example"

        # The deliberately weak weak-IV answer must be gated.
        weak = run_evals.grade_candidate(by_id["statspai-weak-iv"], cand)
        self.assertEqual(weak["status"], "fail-required")
        self.assertIn("flags-weak-instrument", weak["required_failed"])

        # The good staggered-DiD answer must pass its required items.
        good = run_evals.grade_candidate(by_id["statspai-staggered-did"], cand)
        self.assertEqual(good["status"], "pass")


class TestJudge(unittest.TestCase):
    def test_parse_fenced_json(self):
        text = 'Sure!\n```json\n{"items": [{"id": "a", "verdict": "pass", "why": "ok"}]}\n```'
        out = run_evals.parse_judge_response(text)
        self.assertEqual(out["a"]["verdict"], "pass")

    def test_parse_bare_json_with_prose(self):
        text = 'Here is my judgement: {"items":[{"id":"x","verdict":"fail"}]} done.'
        out = run_evals.parse_judge_response(text)
        self.assertEqual(out["x"]["verdict"], "fail")

    def test_parse_no_json_raises(self):
        with self.assertRaises(ValueError):
            run_evals.parse_judge_response("no json here")

    def test_mock_judge_resolves_manual_item(self):
        scenarios = run_evals.load_scenarios()
        by_id = {s["id"]: s for s in scenarios}
        cand = run_evals.ROOT / "eval-harness" / "candidates" / "_example"

        # de-aigc has a manual item "meaning-and-fluency"; a mock judge passes it.
        def mock_judge(prompt):
            return {"meaning-and-fluency": {"verdict": "pass", "why": "faithful"}}

        graded = run_evals.grade_candidate(by_id["de-aigc-structural"], cand, judge=mock_judge)
        manual_item = next(i for i in graded["items"] if i["id"] == "meaning-and-fluency")
        self.assertEqual(manual_item["status"], "pass")
        self.assertNotIn("meaning-and-fluency", graded["manual_items"])

    def test_judge_failure_does_not_crash(self):
        scenarios = run_evals.load_scenarios()
        by_id = {s["id"]: s for s in scenarios}
        cand = run_evals.ROOT / "eval-harness" / "candidates" / "_example"

        def boom(prompt):
            raise RuntimeError("network down")

        graded = run_evals.grade_candidate(by_id["de-aigc-structural"], cand, judge=boom)
        self.assertIn("judge_note", graded)
        # Manual item stays manual when the judge is unavailable.
        self.assertIn("meaning-and-fluency", graded["manual_items"])


if __name__ == "__main__":
    unittest.main()
