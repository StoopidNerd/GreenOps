"""Estimate infrastructure costs and potential savings using synthetic pricing."""

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from agents.analyzer import MAX_MONTHLY_HOURS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICING_PATH = PROJECT_ROOT / "data" / "pricing.json"

VALID_STATUSES = {"idle", "underused", "review", "ok"}


def load_pricing(pricing_path: str | Path = DEFAULT_PRICING_PATH) -> dict[str, Any]:
    """Load instance pricing assumptions from a JSON file."""
    path = Path(pricing_path)

    with path.open("r", encoding="utf-8") as file:
        pricing = json.load(file)

    if not isinstance(pricing, dict) or not isinstance(pricing.get("instances"), dict):
        raise ValueError("Pricing file must contain an 'instances' object.")

    return pricing


def _as_dict(finding: Any) -> dict[str, Any]:
    """Accept either a dict or an Analyzer Finding dataclass."""
    if is_dataclass(finding) and not isinstance(finding, type):
        return asdict(finding)
    return finding


def estimate_resource_cost(
    finding: dict[str, Any],
    pricing: dict[str, Any],
) -> dict[str, Any]:
    """Estimate monthly cost and a clearly labeled savings scenario."""
    finding = _as_dict(finding)
    resource_id = finding.get("resource_id")
    instance_type = finding.get("instance_type")
    monthly_hours = finding.get("monthly_hours")
    status = finding.get("status", finding.get("classification"))

    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("Finding must have a non-empty resource_id.")

    if status not in VALID_STATUSES:
        raise ValueError(f"{resource_id}: unknown status {status!r}.")

    if not isinstance(monthly_hours, (int, float)) or isinstance(monthly_hours, bool):
        raise ValueError(f"{resource_id}: monthly_hours must be numeric.")

    if not 0 <= monthly_hours <= MAX_MONTHLY_HOURS:
        raise ValueError(
            f"{resource_id}: monthly_hours must be between 0 and {MAX_MONTHLY_HOURS:g}."
        )

    instance = (
        pricing.get("instances", {}).get(instance_type)
        if isinstance(instance_type, str)
        else None
    )
    if not isinstance(instance, dict):
        raise ValueError(f"{resource_id}: unknown instance type {instance_type!r}.")

    hourly_rate = instance.get("hourly_usd")
    if not isinstance(hourly_rate, (int, float)) or isinstance(hourly_rate, bool) or hourly_rate < 0:
        raise ValueError(f"{instance_type}: hourly_usd must be a non-negative number.")

    monthly_cost = round(hourly_rate * monthly_hours, 2)

    # Scenario only: assume the resource could be turned off for all its
    # recorded monthly hours. This is NOT a recommendation to stop it.
    eligible = status == "idle"
    potential_savings = monthly_cost if eligible else 0.0

    return {
        "resource_id": resource_id,
        "instance_type": instance_type,
        "status": status,
        "monthly_hours": monthly_hours,
        "currency": pricing.get("currency", "USD"),
        "estimated_monthly_cost_usd": monthly_cost,
        "potential_monthly_savings_usd": round(potential_savings, 2),
        "savings_assumption": (
            "Illustrative full-hours-off scenario; requires human review."
            if eligible
            else "No savings scenario estimated for this status."
        ),
        "pricing_note": pricing.get("note", ""),
    }


def estimate_all(
    findings: list[dict[str, Any]],
    pricing_path: str | Path = DEFAULT_PRICING_PATH,
) -> dict[str, Any]:
    """Estimate costs for findings and summarize potential savings."""
    pricing = load_pricing(pricing_path)
    results = [estimate_resource_cost(item, pricing) for item in findings]

    return {
        "currency": pricing.get("currency", "USD"),
        "pricing_note": pricing.get("note", ""),
        "resources": results,
        "total_estimated_monthly_cost_usd": round(
            sum(item["estimated_monthly_cost_usd"] for item in results), 2
        ),
        "total_potential_monthly_savings_usd": round(
            sum(item["potential_monthly_savings_usd"] for item in results), 2
        ),
        "savings_note": (
            "Illustrative upper bound: assumes every idle resource is switched off "
            "for all recorded hours. Uses synthetic, region-independent prices. "
            "Not expected savings."
        ),
    }