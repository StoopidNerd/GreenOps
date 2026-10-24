"""Analyzer agent: classifies resources as idle, underused, review or ok.

The classification uses simple PROTOTYPE HEURISTICS on average CPU, peak CPU
and average memory. The thresholds below are starting points for a demo, not
guarantees that a resource can be stopped or resized safely. Every finding is
advisory and should be confirmed with the resource owner.

This module reads a local CSV only. It calls no LLM, no cloud API and never
touches Terraform files.
"""

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from contracts import AnalysisFinding

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# The ONLY default dataset. (data/utilisation.csv has a different schema.)
DEFAULT_DATASET = DATA_DIR / "utilisation_backup.csv"

# --- Prototype heuristics (not guarantees of safe resizing) -----------------
IDLE_CPU_THRESHOLD = 5.0         # avg CPU % below this (and nothing else alarming) => "idle"
UNDERUSED_CPU_THRESHOLD = 20.0   # avg CPU % below this => "underused"; at/above => "ok"
HIGH_PEAK_CPU_THRESHOLD = 60.0   # max CPU % at/above this => "review" (bursty workload)
HIGH_MEMORY_THRESHOLD = 60.0     # avg memory % at/above this => "review" (CPU alone is not enough)
MAX_MONTHLY_HOURS = 744.0        # 31 days * 24 hours

REQUIRED_COLUMNS = (
    "resource_id",
    "instance_type",
    "region",
    "environment",
    "avg_cpu_pct",
    "max_cpu_pct",
    "avg_mem_pct",
    "monthly_hours",
    "owner",
)
PERCENT_COLUMNS = ("avg_cpu_pct", "max_cpu_pct", "avg_mem_pct")


class AnalyzerInputError(ValueError):
    """Raised when the utilisation CSV is malformed or contains invalid values."""


@dataclass
class Finding:
    resource_id: str
    instance_type: str
    region: str
    environment: str
    avg_cpu_pct: float
    monthly_hours: float
    status: str  # "idle" | "underused" | "review" | "ok"
    reason: str
    # Added later; all optional so existing code that builds a Finding still works.
    max_cpu_pct: Optional[float] = None
    avg_mem_pct: Optional[float] = None
    owner: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def classification(self) -> str:
        """Alias for `status` (same value)."""
        return self.status

    @property
    def explanation(self) -> str:
        """Alias for `reason` (same value)."""
        return self.reason


# --- Loading and validation --------------------------------------------------

def _parse_percent(raw, column):
    """Return a float in [0, 100] or raise ValueError with a short message."""
    value = _parse_number(raw, column)
    if not 0.0 <= value <= 100.0:
        raise ValueError("%s must be between 0 and 100 (got %s)" % (column, raw.strip()))
    return value


def _parse_number(raw, column):
    if raw is None or not raw.strip():
        raise ValueError("%s is blank" % column)
    try:
        value = float(raw.strip())
    except ValueError:
        raise ValueError("%s is not a number (got %r)" % (column, raw.strip())) from None
    if not math.isfinite(value):
        raise ValueError("%s must be a finite number (got %s)" % (column, raw.strip()))
    return value


def _validate_row(row):
    """Return a cleaned row dict (numbers converted) or raise ValueError."""
    if None in row:
        raise ValueError("row has more values than the header has columns")

    resource_id = (row.get("resource_id") or "").strip()
    if not resource_id:
        raise ValueError("resource_id is blank")

    clean = {"resource_id": resource_id}
    for column in ("instance_type", "region", "environment", "owner"):
        clean[column] = (row.get(column) or "").strip()

    for column in PERCENT_COLUMNS:
        clean[column] = _parse_percent(row.get(column), column)

    hours = _parse_number(row.get("monthly_hours"), "monthly_hours")
    if not 0.0 <= hours <= MAX_MONTHLY_HOURS:
        raise ValueError(
            "monthly_hours must be between 0 and %d (got %s)"
            % (MAX_MONTHLY_HOURS, row["monthly_hours"].strip())
        )
    clean["monthly_hours"] = hours

    if clean["max_cpu_pct"] < clean["avg_cpu_pct"]:
        raise ValueError("max_cpu_pct is lower than avg_cpu_pct")
    return clean


def load_utilisation(path=None):
    """Read and validate the utilisation CSV; return a list of cleaned row dicts.

    Defaults to data/utilisation_backup.csv. Raises FileNotFoundError if the
    file is missing and AnalyzerInputError (a ValueError) if the contents are
    invalid. All row problems are reported together, with CSV line numbers.
    """
    csv_path = Path(path) if path else DEFAULT_DATASET
    if not csv_path.is_file():
        raise FileNotFoundError("Utilisation file not found: %s" % csv_path)

    rows = []
    problems = []
    seen_ids = set()

    # utf-8-sig quietly drops the BOM that Excel/Windows editors sometimes add.
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise AnalyzerInputError("%s is empty (no header row)" % csv_path.name)
        reader.fieldnames = [name.strip() for name in reader.fieldnames]

        missing = [col for col in REQUIRED_COLUMNS if col not in reader.fieldnames]
        if missing:
            raise AnalyzerInputError(
                "%s is missing required column(s): %s" % (csv_path.name, ", ".join(missing))
            )

        for row in reader:
            line = reader.line_num
            try:
                clean = _validate_row(row)
                if clean["resource_id"] in seen_ids:
                    raise ValueError("duplicate resource_id %r" % clean["resource_id"])
            except ValueError as error:
                label = (row.get("resource_id") or "").strip() or "?"
                problems.append("line %d (%s): %s" % (line, label, error))
                continue
            seen_ids.add(clean["resource_id"])
            rows.append(clean)

    if problems:
        raise AnalyzerInputError(
            "Invalid data in %s:\n  - %s" % (csv_path.name, "\n  - ".join(problems))
        )
    if not rows:
        raise AnalyzerInputError("%s has a header but no data rows" % csv_path.name)
    return rows


# --- Classification ----------------------------------------------------------

def _classify_detail(avg_cpu_pct, max_cpu_pct=None, avg_mem_pct=None):
    """Return (status, reason, rule). `rule` names the heuristic that fired."""
    high_mem = avg_mem_pct is not None and avg_mem_pct >= HIGH_MEMORY_THRESHOLD

    if avg_cpu_pct >= UNDERUSED_CPU_THRESHOLD:
        reason = "Average CPU %.1f%% is at or above %.0f%%; no low-utilisation signal." % (
            avg_cpu_pct, UNDERUSED_CPU_THRESHOLD)
        if high_mem:
            reason += " Memory is high (%.1f%%), so this is not a downsizing candidate." % avg_mem_pct
        return "ok", reason, "cpu_in_expected_range"

    # Average CPU is low from here on. Check for signs that CPU alone is misleading.
    concerns = []
    rules = []
    if max_cpu_pct is not None and max_cpu_pct >= HIGH_PEAK_CPU_THRESHOLD:
        concerns.append("peak CPU is high (%.1f%% >= %.0f%%), so the workload is bursty"
                        % (max_cpu_pct, HIGH_PEAK_CPU_THRESHOLD))
        rules.append("high_peak_cpu")
    if high_mem:
        concerns.append("average memory is high (%.1f%% >= %.0f%%), so low CPU alone does not "
                        "show it is oversized" % (avg_mem_pct, HIGH_MEMORY_THRESHOLD))
        rules.append("high_memory")
    if concerns:
        reason = "Average CPU is low (%.1f%%) but %s. Review manually before any resizing." % (
            avg_cpu_pct, " and ".join(concerns))
        return "review", reason, "+".join(rules)

    if avg_cpu_pct < IDLE_CPU_THRESHOLD:
        reason = "Average CPU %.1f%% is below %.0f%%" % (avg_cpu_pct, IDLE_CPU_THRESHOLD)
        if max_cpu_pct is not None and avg_mem_pct is not None:
            reason += (", and peak CPU (%.1f%%) and memory (%.1f%%) are below the review thresholds"
                       % (max_cpu_pct, avg_mem_pct))
        reason += ". Possibly idle; confirm with the owner (heuristic, not proof)."
        return "idle", reason, "low_avg_cpu_idle"

    reason = "Average CPU %.1f%% is below %.0f%%" % (avg_cpu_pct, UNDERUSED_CPU_THRESHOLD)
    if max_cpu_pct is not None and avg_mem_pct is not None:
        reason += (", and peak CPU (%.1f%%) and memory (%.1f%%) are below the review thresholds"
                   % (max_cpu_pct, avg_mem_pct))
    reason += ". Possibly underused; confirm before resizing (heuristic, not proof)."
    return "underused", reason, "low_avg_cpu_underused"


def classify(avg_cpu_pct, max_cpu_pct=None, avg_mem_pct=None):
    """Return (status, reason). With only avg CPU it behaves like the old CPU-only check."""
    status, reason, _rule = _classify_detail(avg_cpu_pct, max_cpu_pct, avg_mem_pct)
    return status, reason


def analyze(path=None):
    """Return one Finding per row of the utilisation data (default: the backup CSV)."""
    findings = []
    for row in load_utilisation(path):
        status, reason, rule = _classify_detail(
            row["avg_cpu_pct"], row["max_cpu_pct"], row["avg_mem_pct"]
        )
        findings.append(
            Finding(
                resource_id=row["resource_id"],
                instance_type=row["instance_type"],
                region=row["region"],
                environment=row["environment"],
                avg_cpu_pct=row["avg_cpu_pct"],
                monthly_hours=row["monthly_hours"],
                status=status,
                reason=reason,
                max_cpu_pct=row["max_cpu_pct"],
                avg_mem_pct=row["avg_mem_pct"],
                owner=row["owner"],
                evidence={
                    "avg_cpu_pct": row["avg_cpu_pct"],
                    "max_cpu_pct": row["max_cpu_pct"],
                    "avg_mem_pct": row["avg_mem_pct"],
                    "monthly_hours": row["monthly_hours"],
                    "rule": rule,
                    "thresholds": {
                        "idle_cpu": IDLE_CPU_THRESHOLD,
                        "underused_cpu": UNDERUSED_CPU_THRESHOLD,
                        "high_peak_cpu": HIGH_PEAK_CPU_THRESHOLD,
                        "high_memory": HIGH_MEMORY_THRESHOLD,
                    },
                },
            )
        )
    return findings


_SEVERITY = {"idle": "medium", "underused": "low", "review": "info", "ok": "info"}


def to_analysis_finding(finding):
    """Convert a Finding to the shared contracts.AnalysisFinding (for the report)."""
    return AnalysisFinding(
        resource_id=finding.resource_id,
        finding_type=finding.status,
        severity=_SEVERITY[finding.status],
        message=finding.reason,
        evidence=dict(finding.evidence),
    )