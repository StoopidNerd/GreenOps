#!/usr/bin/env python3
"""
create_project.py - scaffolds the GreenOps Agent project.

Run it from inside your (empty) greenops-agent folder. All files are created
relative to the folder that contains this script, so nothing is nested in a
second greenops-agent folder. Existing files are NEVER overwritten.

Uses only the Python standard library.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# File contents (raw strings so backslashes are preserved exactly)
# ---------------------------------------------------------------------------

GITIGNORE = r'''# Virtual environments
.venv/
venv/

# Python caches
__pycache__/
*.py[cod]
.pytest_cache/

# Secrets - never commit these
.env
.env.*
.streamlit/secrets.toml

# Terraform local state / providers
.terraform/
*.tfstate
*.tfstate.*

# Editor / OS
.vscode/
.idea/
.DS_Store
Thumbs.db
'''

APP_PY = r'''"""GreenOps Agent - Streamlit entry point (placeholder UI)."""

import streamlit as st

from agents.orchestrator import run_pipeline

st.set_page_config(page_title="GreenOps Agent", page_icon="🌱", layout="wide")

st.title("🌱 GreenOps Agent")
st.caption("Placeholder UI - the full application is not implemented yet.")

st.info(
    "This page only runs the sample pipeline against synthetic data in ./data. "
    "It does not connect to any cloud account."
)

if st.button("Run sample analysis"):
    result = run_pipeline()
    st.subheader("Summary")
    st.json(result["summary"])
    st.subheader("Findings")
    st.dataframe(result["findings"], use_container_width=True)
'''

REQUIREMENTS = r'''streamlit>=1.30
pytest>=7.0
'''

README = r'''# GreenOps Agent

A work-in-progress agent that finds cloud waste and estimates the carbon
impact of infrastructure, then suggests safer, greener right-sizing options.

> **Status:** project skeleton only. All agents are placeholders that work on
> synthetic sample data. Nothing here touches a real cloud account.

## Layout

```
app.py                  Streamlit entry point (placeholder UI)
agents/
  analyzer.py           Flags idle / under-utilised resources
  carbon_agent.py       Estimates energy use and CO2e
  fixer.py              Suggests changes (never applies them)
  orchestrator.py       Wires the agents together
sample_infra/main.tf    Example Terraform (not meant to be applied)
data/
  utilisation.csv       Synthetic utilisation metrics
  pricing.json          Synthetic hourly prices and power draw
  regions.json          Synthetic grid carbon intensity per region
tests/test_agents.py    Basic smoke tests
```

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest
streamlit run app.py
```

If PowerShell blocks activation, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Notes

- All numbers in `data/` are synthetic and for development only.
- Keep secrets in `.env` or `.streamlit/secrets.toml`; both are git-ignored.
'''

MAIN_TF = r'''# Sample infrastructure for GreenOps Agent development.
# This file is illustrative only - do NOT apply it to a real account.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
  # No credentials are configured here on purpose.
}

variable "environment" {
  type    = string
  default = "dev"
}

resource "aws_instance" "web_01" {
  ami           = "ami-00000000000000000" # placeholder
  instance_type = "m5.xlarge"

  tags = {
    Name        = "web-01"
    Environment = var.environment
    Owner       = "platform-team"
  }
}

resource "aws_instance" "batch_01" {
  ami           = "ami-00000000000000000" # placeholder
  instance_type = "c5.xlarge"

  tags = {
    Name        = "batch-01"
    Environment = var.environment
    Owner       = "data-team"
  }
}

resource "aws_instance" "dev_sandbox_01" {
  ami           = "ami-00000000000000000" # placeholder
  instance_type = "m5.2xlarge"

  tags = {
    Name        = "dev-sandbox-01"
    Environment = "dev"
    Owner       = "ml-team"
  }
}
'''

UTILISATION_CSV = r'''resource_id,instance_type,region,environment,avg_cpu_pct,max_cpu_pct,avg_mem_pct,monthly_hours,owner
web-01,m5.xlarge,us-east-1,prod,42.5,78.0,61.2,730,platform-team
web-02,m5.xlarge,us-east-1,prod,39.8,74.5,58.9,730,platform-team
api-01,m5.large,eu-west-1,prod,55.1,88.3,70.4,730,backend-team
api-02,m5.large,eu-west-1,prod,12.4,35.6,28.1,730,backend-team
batch-01,c5.xlarge,us-west-2,prod,18.7,92.0,33.5,420,data-team
batch-02,c5.xlarge,us-west-2,prod,3.2,11.0,12.8,730,data-team
dev-sandbox-01,m5.2xlarge,us-east-1,dev,2.1,9.4,8.3,730,ml-team
dev-sandbox-02,m5.xlarge,ap-south-1,dev,4.6,15.2,14.0,730,ml-team
staging-01,t3.medium,eu-central-1,staging,8.9,26.7,31.5,730,qa-team
staging-02,t3.medium,eu-central-1,staging,1.4,6.1,9.7,730,qa-team
cache-01,r5.large,eu-north-1,prod,61.3,83.9,77.6,730,platform-team
cron-01,t3.micro,eu-north-1,prod,6.8,22.4,35.0,730,ops-team
analytics-01,r5.large,ap-south-1,prod,27.5,64.2,52.8,730,data-team
test-runner-01,c5.xlarge,us-west-2,dev,0.9,4.3,6.2,730,qa-team
'''

PRICING_JSON = r'''{
  "currency": "USD",
  "note": "Synthetic development data. Prices and power draw are NOT real quotes.",
  "instances": {
    "t3.micro":   {"vcpu": 2, "memory_gib": 1,  "hourly_usd": 0.0104, "avg_watts": 6},
    "t3.medium":  {"vcpu": 2, "memory_gib": 4,  "hourly_usd": 0.0416, "avg_watts": 10},
    "m5.large":   {"vcpu": 2, "memory_gib": 8,  "hourly_usd": 0.0960, "avg_watts": 18},
    "m5.xlarge":  {"vcpu": 4, "memory_gib": 16, "hourly_usd": 0.1920, "avg_watts": 34},
    "m5.2xlarge": {"vcpu": 8, "memory_gib": 32, "hourly_usd": 0.3840, "avg_watts": 66},
    "c5.xlarge":  {"vcpu": 4, "memory_gib": 8,  "hourly_usd": 0.1700, "avg_watts": 32},
    "r5.large":   {"vcpu": 2, "memory_gib": 16, "hourly_usd": 0.1260, "avg_watts": 22}
  }
}
'''

REGIONS_JSON = r'''{
  "note": "Synthetic, rounded grid carbon intensity in gCO2e per kWh. For development only.",
  "regions": {
    "us-east-1":    {"name": "N. Virginia", "carbon_gco2_per_kwh": 380},
    "us-west-2":    {"name": "Oregon",      "carbon_gco2_per_kwh": 120},
    "eu-west-1":    {"name": "Ireland",     "carbon_gco2_per_kwh": 290},
    "eu-central-1": {"name": "Frankfurt",   "carbon_gco2_per_kwh": 340},
    "eu-north-1":   {"name": "Stockholm",   "carbon_gco2_per_kwh": 30},
    "ap-south-1":   {"name": "Mumbai",      "carbon_gco2_per_kwh": 700}
  }
}
'''

AGENTS_INIT = r'''"""GreenOps Agent - agent package."""
'''

ANALYZER_PY = r'''"""Analyzer agent: flags idle and under-utilised resources (placeholder logic)."""

import csv
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

IDLE_CPU_THRESHOLD = 5.0         # avg CPU % below this => "idle"
UNDERUSED_CPU_THRESHOLD = 20.0   # avg CPU % below this => "underused"


@dataclass
class Finding:
    resource_id: str
    instance_type: str
    region: str
    environment: str
    avg_cpu_pct: float
    monthly_hours: float
    status: str  # "idle" | "underused" | "ok"
    reason: str


def load_utilisation(path=None):
    """Read the utilisation CSV and return a list of row dicts."""
    csv_path = Path(path) if path else DATA_DIR / "utilisation.csv"
    with open(csv_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def classify(avg_cpu_pct):
    """Very simple threshold classification. TODO: add memory, burst and trend checks."""
    if avg_cpu_pct < IDLE_CPU_THRESHOLD:
        return "idle", "Average CPU below %.0f%%" % IDLE_CPU_THRESHOLD
    if avg_cpu_pct < UNDERUSED_CPU_THRESHOLD:
        return "underused", "Average CPU below %.0f%%" % UNDERUSED_CPU_THRESHOLD
    return "ok", "Utilisation within expected range"


def analyze(path=None):
    """Return a Finding for every row in the utilisation data."""
    findings = []
    for row in load_utilisation(path):
        avg_cpu = float(row["avg_cpu_pct"])
        status, reason = classify(avg_cpu)
        findings.append(
            Finding(
                resource_id=row["resource_id"],
                instance_type=row["instance_type"],
                region=row["region"],
                environment=row["environment"],
                avg_cpu_pct=avg_cpu,
                monthly_hours=float(row["monthly_hours"]),
                status=status,
                reason=reason,
            )
        )
    return findings
'''

CARBON_AGENT_PY = r'''"""Carbon agent: estimates energy use and CO2e (placeholder model)."""

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
'''

FIXER_PY = r'''"""Fixer agent: suggests changes. It NEVER edits files or touches infrastructure."""

# Ordered smallest -> largest within each instance family (placeholder ladder).
SIZE_LADDER = {
    "m5": ["m5.large", "m5.xlarge", "m5.2xlarge"],
    "c5": ["c5.xlarge"],
    "r5": ["r5.large"],
    "t3": ["t3.micro", "t3.medium"],
}


def next_smaller(instance_type):
    """Return the next smaller size in the same family, or None."""
    family = instance_type.split(".")[0]
    ladder = SIZE_LADDER.get(family, [])
    if instance_type in ladder:
        index = ladder.index(instance_type)
        if index > 0:
            return ladder[index - 1]
    return None


def suggest_fix(finding):
    """Return a human-readable suggestion dict for a Finding, or None if no action."""
    if finding.status == "idle":
        action = "Review for shutdown or schedule it to run only when needed"
    elif finding.status == "underused":
        smaller = next_smaller(finding.instance_type)
        if not smaller:
            return None
        action = "Consider right-sizing from %s to %s" % (finding.instance_type, smaller)
    else:
        return None

    return {
        "resource_id": finding.resource_id,
        "status": finding.status,
        "suggestion": action,
        "auto_apply": False,  # suggestions are advisory only
    }
'''

ORCHESTRATOR_PY = r'''"""Orchestrator: runs analyzer -> carbon agent -> fixer on the sample data."""

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
'''

TEST_AGENTS_PY = r'''"""Smoke tests for the placeholder agents. Run with: python -m pytest"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import analyzer, carbon_agent, fixer, orchestrator


class TestAnalyzer(unittest.TestCase):
    def test_classify_thresholds(self):
        self.assertEqual(analyzer.classify(1.0)[0], "idle")
        self.assertEqual(analyzer.classify(10.0)[0], "underused")
        self.assertEqual(analyzer.classify(50.0)[0], "ok")

    def test_sample_data_loads(self):
        findings = analyzer.analyze()
        self.assertGreater(len(findings), 0)


class TestCarbonAgent(unittest.TestCase):
    def test_known_instance_returns_positive(self):
        kg = carbon_agent.estimate_emissions_kg("m5.large", "us-east-1", 730)
        self.assertGreater(kg, 0)

    def test_unknown_instance_returns_zero(self):
        self.assertEqual(carbon_agent.estimate_emissions_kg("nope.large", "us-east-1", 730), 0.0)


class TestFixer(unittest.TestCase):
    def test_next_smaller(self):
        self.assertEqual(fixer.next_smaller("m5.2xlarge"), "m5.xlarge")
        self.assertIsNone(fixer.next_smaller("m5.large"))


class TestOrchestrator(unittest.TestCase):
    def test_pipeline_runs(self):
        result = orchestrator.run_pipeline()
        self.assertIn("summary", result)
        self.assertEqual(result["summary"]["resources"], len(result["findings"]))


if __name__ == "__main__":
    unittest.main()
'''

# ---------------------------------------------------------------------------
# Files to create (relative to ROOT)
# ---------------------------------------------------------------------------

FILES = {
    ".gitignore": GITIGNORE,
    "app.py": APP_PY,
    "requirements.txt": REQUIREMENTS,
    "README.md": README,
    "sample_infra/main.tf": MAIN_TF,
    "data/utilisation.csv": UTILISATION_CSV,
    "data/pricing.json": PRICING_JSON,
    "data/regions.json": REGIONS_JSON,
    "agents/__init__.py": AGENTS_INIT,
    "agents/analyzer.py": ANALYZER_PY,
    "agents/carbon_agent.py": CARBON_AGENT_PY,
    "agents/fixer.py": FIXER_PY,
    "agents/orchestrator.py": ORCHESTRATOR_PY,
    "tests/test_agents.py": TEST_AGENTS_PY,
}


def create_file(relative_path, content):
    """Create one file. Returns 'created' or 'skipped'. Never overwrites."""
    target = ROOT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Mode "x" = exclusive creation: raises FileExistsError if the file exists.
        with open(target, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(content.lstrip("\n"))
    except FileExistsError:
        return "skipped"
    return "created"


def main():
    print("Project folder: %s\n" % ROOT)

    created, skipped = [], []
    for relative_path, content in FILES.items():
        result = create_file(relative_path, content)
        (created if result == "created" else skipped).append(relative_path)
        print("  [%s] %s" % (result.upper(), relative_path))

    print("\nDone: %d created, %d skipped (already existed)." % (len(created), len(skipped)))
    print("\nNext steps (PowerShell):")
    print("  python -m venv .venv")
    print("  .\\.venv\\Scripts\\Activate.ps1")
    print("  pip install -r requirements.txt")
    print("  python -m pytest")
    print("  streamlit run app.py")


if __name__ == "__main__":
    main()