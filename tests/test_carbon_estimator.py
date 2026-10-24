"""Tests for agents/carbon_estimator.py. Run: python -m pytest tests/test_carbon_estimator.py"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents import analyzer, carbon_estimator
from agents.carbon_estimator import (
    DEFAULT_CARBON_INTENSITY_KG_PER_KWH,
    estimate_all,
    estimate_resource_carbon,
    load_regions,
)

PRICING = {"note": "synthetic", "instances": {"m5.large": {"avg_watts": 18}}}
REGIONS = {"regions": {
    "eu-north-1": {"carbon_gco2_per_kwh": 30},
    "ap-south-1": {"carbon_gco2_per_kwh": 700},
}}


def finding(**overrides):
    base = {"resource_id": "r-1", "instance_type": "m5.large",
            "region": "eu-north-1", "monthly_hours": 730}
    base.update(overrides)
    return base


def test_flat_fallback_known_value():
    result = estimate_resource_carbon(finding(), PRICING)  # no regions given
    assert result["estimated_energy_kwh"] == 13.14
    assert result["estimated_emissions_kg_co2e"] == 5.256  # 13.14 * 0.4
    assert "default assumption" in result["intensity_source"]


def test_regional_intensity_is_used_and_labeled():
    result = estimate_resource_carbon(finding(), PRICING, regions=REGIONS)
    assert result["carbon_intensity_kg_per_kwh"] == 0.03
    assert result["estimated_emissions_kg_co2e"] == round(13.14 * 0.03, 4)
    assert "regions.json" in result["intensity_source"]


def test_region_changes_result_more_than_20x():
    low = estimate_resource_carbon(finding(region="eu-north-1"), PRICING, regions=REGIONS)
    high = estimate_resource_carbon(finding(region="ap-south-1"), PRICING, regions=REGIONS)
    assert high["estimated_emissions_kg_co2e"] > 20 * low["estimated_emissions_kg_co2e"]


@pytest.mark.parametrize("region", ["mars-1", None, 5])
def test_unknown_region_uses_flagged_fallback(region):
    result = estimate_resource_carbon(finding(region=region), PRICING, regions=REGIONS)
    assert result["carbon_intensity_kg_per_kwh"] == DEFAULT_CARBON_INTENSITY_KG_PER_KWH
    assert "default assumption" in result["intensity_source"]


@pytest.mark.parametrize("grams", [-1, "300", None, float("nan"), True])
def test_bad_region_intensity_raises(grams):
    regions = {"regions": {"eu-north-1": {"carbon_gco2_per_kwh": grams}}}
    with pytest.raises(ValueError, match="carbon_gco2_per_kwh"):
        estimate_resource_carbon(finding(), PRICING, regions=regions)


@pytest.mark.parametrize(
    "overrides",
    [
        {"resource_id": ""}, {"resource_id": None}, {"instance_type": ""},
        {"instance_type": "nope.large"}, {"instance_type": None},
        {"monthly_hours": "100"}, {"monthly_hours": True}, {"monthly_hours": -1},
        {"monthly_hours": 745}, {"monthly_hours": float("nan")},
        {"monthly_hours": float("inf")}, {"monthly_hours": 10**400},
    ],
)
def test_invalid_findings_raise_value_error(overrides):
    with pytest.raises(ValueError):
        estimate_resource_carbon(finding(**overrides), PRICING, regions=REGIONS)


@pytest.mark.parametrize("intensity", [-0.1, float("nan"), float("inf"), "0.4", True, 10**400])
def test_bad_fallback_intensity_raises(intensity):
    with pytest.raises(ValueError, match="carbon_intensity"):
        estimate_resource_carbon(finding(), PRICING, carbon_intensity=intensity)


@pytest.mark.parametrize("watts", [-1, "18", None, float("nan"), True])
def test_bad_watts_raises(watts):
    pricing = {"instances": {"m5.large": {"avg_watts": watts}}}
    with pytest.raises(ValueError, match="avg_watts"):
        estimate_resource_carbon(finding(), pricing)


def test_malformed_pricing_dict_raises_value_error_not_key_error():
    with pytest.raises(ValueError, match="unknown instance type"):
        estimate_resource_carbon(finding(), {})


def test_direct_call_result_has_no_private_keys():
    result = estimate_resource_carbon(finding(), PRICING)
    assert not [key for key in result if key.startswith("_")]
    assert result["assumptions"]


def test_load_regions_rejects_bad_shape(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="regions"):
        load_regions(path)


def test_estimate_all_totals(tmp_path):
    pricing_path = tmp_path / "p.json"
    pricing_path.write_text(json.dumps(PRICING), encoding="utf-8")
    regions_path = tmp_path / "r.json"
    regions_path.write_text(json.dumps(REGIONS), encoding="utf-8")
    result = estimate_all(
        [finding(), finding(resource_id="r-2", region="ap-south-1")],
        pricing_path, regions_path=regions_path,
    )
    assert result["total_estimated_energy_kwh"] == 26.28
    assert result["total_estimated_emissions_kg_co2e"] == round(13.14 * 0.03 + 13.14 * 0.7, 4)
    assert result["assumptions"]


def test_estimate_all_without_regions_uses_flat_fallback():
    result = estimate_all([finding()], regions_path=None)
    assert result["resources"][0]["carbon_intensity_kg_per_kwh"] == 0.4


def test_works_with_analyzer_findings_and_real_regions():
    result = estimate_all(analyzer.analyze())
    assert len(result["resources"]) == 14
    # Every region in the backup CSV is present in regions.json, so no fallback is used.
    assert all("regions.json" in r["intensity_source"] for r in result["resources"])
    by_id = {r["resource_id"]: r for r in result["resources"]}
    assert by_id["web-01"]["carbon_intensity_kg_per_kwh"] == 0.38      # us-east-1
    assert by_id["cache-01"]["carbon_intensity_kg_per_kwh"] == 0.03    # eu-north-1


def test_constants_shared_with_other_modules():
    assert carbon_estimator.MAX_MONTHLY_HOURS == analyzer.MAX_MONTHLY_HOURS