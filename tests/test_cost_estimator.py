"""Tests for agents/cost_estimator.py. Run: python -m pytest tests/test_cost_estimator.py"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents import analyzer, cost_estimator
from agents.cost_estimator import estimate_all, estimate_resource_cost, load_pricing

PRICING = {
    "currency": "USD",
    "note": "synthetic",
    "instances": {"m5.large": {"hourly_usd": 0.1}},
}


def finding(**overrides):
    base = {"resource_id": "r-1", "instance_type": "m5.large",
            "monthly_hours": 100, "status": "idle"}
    base.update(overrides)
    return base


def test_idle_cost_and_savings_scenario():
    result = estimate_resource_cost(finding(), PRICING)
    assert result["estimated_monthly_cost_usd"] == 10.0
    assert result["potential_monthly_savings_usd"] == 10.0
    assert "human review" in result["savings_assumption"]


@pytest.mark.parametrize("status", ["underused", "review", "ok"])
def test_non_idle_has_cost_but_no_savings(status):
    result = estimate_resource_cost(finding(status=status), PRICING)
    assert result["estimated_monthly_cost_usd"] == 10.0
    assert result["potential_monthly_savings_usd"] == 0.0


def test_classification_key_is_accepted_as_status():
    item = finding()
    item["classification"] = item.pop("status")
    assert estimate_resource_cost(item, PRICING)["status"] == "idle"


@pytest.mark.parametrize(
    "overrides",
    [
        {"resource_id": ""}, {"resource_id": "  "}, {"resource_id": None},
        {"status": None}, {"status": "iDle"},
        {"monthly_hours": "100"}, {"monthly_hours": True},
        {"monthly_hours": -1}, {"monthly_hours": 745}, {"monthly_hours": float("nan")},
        {"instance_type": "nope.large"}, {"instance_type": ["m5.large"]}, {"instance_type": None},
    ],
)
def test_invalid_findings_raise(overrides):
    with pytest.raises(ValueError):
        estimate_resource_cost(finding(**overrides), PRICING)


@pytest.mark.parametrize("rate", [-0.1, "0.1", None, True])
def test_bad_hourly_rate_raises(rate):
    pricing = {"instances": {"m5.large": {"hourly_usd": rate}}}
    with pytest.raises(ValueError, match="hourly_usd"):
        estimate_resource_cost(finding(), pricing)


def test_non_dict_pricing_entry_raises():
    with pytest.raises(ValueError, match="unknown instance type"):
        estimate_resource_cost(finding(), {"instances": {"m5.large": 5}})


@pytest.mark.parametrize("content", ["[]", "{}", '{"instances": []}'])
def test_load_pricing_rejects_bad_shape(tmp_path, content):
    path = tmp_path / "p.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="instances"):
        load_pricing(path)


def test_estimate_all_totals_and_note(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(PRICING), encoding="utf-8")
    result = estimate_all([finding(), finding(resource_id="r-2", status="ok")], path)
    assert result["total_estimated_monthly_cost_usd"] == 20.0
    assert result["total_potential_monthly_savings_usd"] == 10.0
    assert "upper bound" in result["savings_note"]


def test_works_with_analyzer_finding_dataclasses():
    findings = analyzer.analyze()
    result = estimate_all(findings)
    assert len(result["resources"]) == 14
    by_id = {r["resource_id"]: r for r in result["resources"]}
    assert by_id["dev-sandbox-01"]["potential_monthly_savings_usd"] > 0
    assert by_id["batch-01"]["potential_monthly_savings_usd"] == 0.0  # review, not idle
    assert by_id["web-01"]["potential_monthly_savings_usd"] == 0.0


def test_max_hours_matches_analyzer():
    assert cost_estimator.MAX_MONTHLY_HOURS == analyzer.MAX_MONTHLY_HOURS