from __future__ import annotations

from pathlib import Path

from scripts.run_evals import ROOT, run_repair, run_routing


def test_routing_golden_eval_is_perfect():
    report = run_routing(ROOT / "tests/evals/routing_golden.jsonl")

    assert report["scenario_count"] >= 50
    assert report["failed"] == 0
    assert report["score"] == 1.0


def test_repair_scenarios_are_registered():
    report = run_repair(ROOT / "tests/evals/repair_scenarios")

    assert report["scenario_count"] >= 7
    assert report["failed"] == 0
