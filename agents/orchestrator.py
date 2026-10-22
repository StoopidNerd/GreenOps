"""Orchestrator: runs analyzer -> carbon agent -> fixer on the sample data."""

from agents import analyzer, carbon_agent, fixer


def run_pipeline(utilisation_path=None):
    pricing = carbon_agent.load_json("pricing.json")
    regions = carbon_agent.load_json("regions.json")

    rows = []
    suggestions = []
    total_kg = 0.0
    total_cost = 0.0

    for finding in analyzer.analyze(utilisation_path):
        kg = carbon_agent.estimate_emissions_kg(
            finding.instance_type, finding.region, finding.monthly_hours, pricing, regions
        )
        hourly = pricing["instances"].get(finding.instance_type, {}).get("hourly_usd", 0.0)
        cost = round(hourly * finding.monthly_hours, 2)

        total_kg += kg
        total_cost += cost
        rows.append(
            {
                "resource_id": finding.resource_id,
                "instance_type": finding.instance_type,
                "region": finding.region,
                "status": finding.status,
                "avg_cpu_pct": finding.avg_cpu_pct,
                "est_monthly_cost_usd": cost,
                "est_monthly_kg_co2e": kg,
            }
        )

        suggestion = fixer.suggest_fix(finding)
        if suggestion:
            suggestions.append(suggestion)

    return {
        "summary": {
            "resources": len(rows),
            "flagged": len(suggestions),
            "total_monthly_cost_usd": round(total_cost, 2),
            "total_monthly_kg_co2e": round(total_kg, 2),
        },
        "findings": rows,
        "suggestions": suggestions,
    }
