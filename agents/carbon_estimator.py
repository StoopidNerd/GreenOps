"""Estimate energy use and carbon emissions using synthetic power data."""

import json
import math
from pathlib import Path
from typing import Any

from agents.analyzer import MAX_MONTHLY_HOURS
from agents.cost_estimator import DEFAULT_PRICING_PATH, PROJECT_ROOT, load_pricing

DEFAULT_REGIONS_PATH = PROJECT_ROOT / "data" / "regions.json"

# Fallback used ONLY when a region is missing from regions.json.
# Illustrative assumption, not a verified regional grid factor.
DEFAULT_CARBON_INTENSITY_KG_PER_KWH = 0.4

ASSUMPTIONS = [
    "Constant average power draw (avg_watts) regardless of CPU load.",
    "Excludes data-centre overhead (PUE) and embodied carbon.",
    "Grid intensity values are synthetic development data, not verified factors.",
]


def load_regions(regions_path: str | Path = DEFAULT_REGIONS_PATH) -> dict[str, Any]:
    """Load synthetic regional grid intensities (gCO2e per kWh)."""
    with Path(regions_path).open("r", encoding="utf-8") as file:
        regions = json.load(file)

    if not isinstance(regions, dict) or not isinstance(regions.get("regions"), dict):
        raise ValueError("Regions file must contain a 'regions' object.")

    return regions


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _intensity_for(region: Any, regions: dict[str, Any] | None, fallback: float):
    """Return (kg_per_kwh, source_label) for a region."""
    entry = None
    if regions is not None and isinstance(region, str):
        entry = regions.get("regions", {}).get(region)
    if entry is None:
        return fallback, "default assumption (region not in regions.json)"

    grams = entry.get("carbon_gco2_per_kwh") if isinstance(entry, dict) else None
    if not _is_finite_number(grams) or grams < 0:
        raise ValueError(f"{region}: carbon_gco2_per_kwh must be a finite, non-negative number.")
    return grams / 1000, "regions.json (synthetic)"


def estimate_resource_carbon(
    finding: Any,
    pricing: dict[str, Any],
    carbon_intensity: float = DEFAULT_CARBON_INTENSITY_KG_PER_KWH,
    regions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate monthly energy use and emissions for one resource.

    If `regions` is given and the finding's region is listed, that regional
    intensity is used. Otherwise `carbon_intensity` is used and flagged.
    """
    return _estimate(finding, pricing, carbon_intensity, regions)[0]


def _estimate(finding, pricing, carbon_intensity, regions):
    """Return (result_dict, unrounded_energy_kwh, unrounded_emissions_kg)."""
    def get_value(key: str, default: Any = None) -> Any:
        if isinstance(finding, dict):
            return finding.get(key, default)
        return getattr(finding, key, default)

    resource_id = get_value("resource_id")
    instance_type = get_value("instance_type")
    region = get_value("region")
    monthly_hours = get_value("monthly_hours")

    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("Finding must have a non-empty resource_id.")

    if not isinstance(instance_type, str) or not instance_type.strip():
        raise ValueError(f"{resource_id}: instance_type must be a non-empty string.")

    if not _is_finite_number(monthly_hours) or not 0 <= monthly_hours <= MAX_MONTHLY_HOURS:
        raise ValueError(
            f"{resource_id}: monthly_hours must be between 0 and {MAX_MONTHLY_HOURS:g}."
        )

    if not _is_finite_number(carbon_intensity) or carbon_intensity < 0:
        raise ValueError("carbon_intensity must be a finite, non-negative number.")

    instance = pricing.get("instances", {}).get(instance_type)
    if not isinstance(instance, dict):
        raise ValueError(f"{resource_id}: unknown instance type {instance_type!r}.")

    watts = instance.get("avg_watts")
    if not _is_finite_number(watts) or watts < 0:
        raise ValueError(f"{instance_type}: avg_watts must be a finite, non-negative number.")

    intensity, source = _intensity_for(region, regions, carbon_intensity)

    energy_kwh = watts * monthly_hours / 1000
    emissions_kg = energy_kwh * intensity

    result = {
        "resource_id": resource_id,
        "instance_type": instance_type,
        "region": region,
        "monthly_hours": monthly_hours,
        "estimated_energy_kwh": round(energy_kwh, 4),
        "carbon_intensity_kg_per_kwh": intensity,
        "intensity_source": source,
        "estimated_emissions_kg_co2e": round(emissions_kg, 4),
        "power_data_note": pricing.get("note", ""),
        "carbon_intensity_note": (
            "Illustrative estimate from synthetic data; replace with verified "
            "factors before making real-world claims."
        ),
        "assumptions": list(ASSUMPTIONS),
    }
    return result, energy_kwh, emissions_kg


def estimate_all(
    findings: list[Any],
    pricing_path: str | Path = DEFAULT_PRICING_PATH,
    carbon_intensity: float = DEFAULT_CARBON_INTENSITY_KG_PER_KWH,
    regions_path: str | Path | None = DEFAULT_REGIONS_PATH,
) -> dict[str, Any]:
    """Estimate monthly energy and emissions for multiple resources.

    Pass regions_path=None to skip regional lookup and use the flat fallback.
    """
    pricing = load_pricing(pricing_path)
    regions = load_regions(regions_path) if regions_path is not None else None

    estimates = [_estimate(item, pricing, carbon_intensity, regions) for item in findings]
    results = [est[0] for est in estimates]
    total_energy = sum(est[1] for est in estimates)
    total_emissions = sum(est[2] for est in estimates)

    return {
        "fallback_carbon_intensity_kg_per_kwh": carbon_intensity,
        "power_data_note": pricing.get("note", ""),
        "carbon_intensity_note": (
            "Synthetic regional intensities where available; otherwise an "
            "illustrative fallback. Not verified grid factors."
        ),
        "assumptions": list(ASSUMPTIONS),
        "resources": results,
        "total_estimated_energy_kwh": round(total_energy, 4),
        "total_estimated_emissions_kg_co2e": round(total_emissions, 4),
    }