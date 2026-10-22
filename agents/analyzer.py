"""Analyzer agent: flags idle and under-utilised resources (placeholder logic)."""

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
