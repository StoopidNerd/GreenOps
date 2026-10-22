"""Carbon agent: estimates energy use and CO2e (placeholder model)."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(name):
    with open(DATA_DIR / name, encoding="utf-8") as handle:
        return json.load(handle)


def estimate_emissions_kg(instance_type, region, hours, pricing=None, regions=None):
    """Estimate kg CO2e = (watts * hours / 1000) kWh * (gCO2/kWh) / 1000.

    Placeholder model: ignores PUE, embodied carbon and utilisation-based power.
    Unknown instance types or regions return 0.0 instead of raising.
    """
    pricing = pricing or load_json("pricing.json")
    regions = regions or load_json("regions.json")

    watts = pricing["instances"].get(instance_type, {}).get("avg_watts")
    intensity = regions["regions"].get(region, {}).get("carbon_gco2_per_kwh")
    if watts is None or intensity is None:
        return 0.0

    kwh = watts * hours / 1000.0
    return round(kwh * intensity / 1000.0, 3)
