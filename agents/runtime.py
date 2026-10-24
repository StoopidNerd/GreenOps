"""Connect the real GreenOps agents using local synthetic data only."""

from pathlib import Path
import sys
import re

# Ensure the GreenOps project root is available for imports.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents import analyzer, carbon_estimator, cost_estimator, fixer, orchestrator
from contracts import (
    AgentError,
    AnalysisFinding,
    CostCarbonEstimate,
    ResourceMetrics,
    TerraformResource,
    ValidationCheck,
)

DEFAULT_TF = ROOT / "sample_infra" / "main.tf"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TF = ROOT / "sample_infra" / "main.tf"


def load_resource_metrics(csv_path=None):
    """Load and validate the sample resource metrics."""
    rows = analyzer.load_utilisation(csv_path)
    return [
        ResourceMetrics(
            resource_id=r["resource_id"],
            instance_type=r["instance_type"],
            region=r["region"],
            environment=r["environment"],
            avg_cpu_pct=r["avg_cpu_pct"],
            max_cpu_pct=r["max_cpu_pct"],
            avg_mem_pct=r["avg_mem_pct"],
            monthly_hours=r["monthly_hours"],
            owner=r["owner"],
        )
        for r in rows
    ]


def parse_terraform_resources(terraform_path=None):
    """Parse the simple aws_instance blocks used by our sample Terraform."""
    path = Path(terraform_path) if terraform_path else DEFAULT_TF
    if not path.is_file():
        raise FileNotFoundError(f"Terraform file not found: {path}")

    source = path.read_text(encoding="utf-8")

    # Resolve simple string defaults such as var.environment = "dev".
    variables = {}
    for match in re.finditer(
        r'variable\s+"([^"]+)"\s*\{([^{}]*)\}', source, re.S
    ):
        default = re.search(r'default\s*=\s*"([^"]*)"', match.group(2))
        if default:
            variables[match.group(1)] = default.group(1)

    resources = []
    pattern = re.compile(
        r'resource\s+"aws_instance"\s+"([^"]+)"\s*\{(.*?)\n\}',
        re.S,
    )

    for match in pattern.finditer(source):
        name, body = match.groups()
        instance = re.search(r'instance_type\s*=\s*"([^"]+)"', body)
        tags_match = re.search(r'tags\s*=\s*\{(.*?)\}', body, re.S)
        if not instance or not tags_match:
            continue

        tags = {}
        for item in re.finditer(
            r'(\w+)\s*=\s*(?:"([^"]*)"|([\w.-]+))',
            tags_match.group(1),
        ):
            key, quoted, bare = item.groups()
            value = quoted if quoted is not None else bare
            var = re.fullmatch(r'var\.([\w-]+)', value)
            if var:
                value = variables.get(var.group(1), value)
            tags[key] = value

        if not tags.get("Name"):
            continue

        resources.append(
            TerraformResource(
                resource_address=f"aws_instance.{name}",
                resource_id=tags["Name"],
                instance_type=instance.group(1),
                environment=tags.get("Environment"),
                owner=tags.get("Owner"),
            )
        )

    return resources


class RuntimeAnalyzer:
    """Adapt the Analyzer's classification function to the shared contract."""

    def analyze(self, metrics, terraform_resources):
        findings = []
        checks = [
            ValidationCheck(
                name="analyzer_input",
                status="passed",
                message=f"Received {len(metrics)} validated resources.",
            )
        ]

        severity = {
            "idle": "medium",
            "underused": "low",
            "review": "medium",
            "ok": "info",
        }

        for metric in metrics:
            status, reason = analyzer.classify(
                metric.avg_cpu_pct,
                metric.max_cpu_pct,
                metric.avg_mem_pct,
            )
            findings.append(
                AnalysisFinding(
                    resource_id=metric.resource_id,
                    finding_type=status,
                    severity=severity[status],
                    message=reason,
                    evidence={
                        "avg_cpu_pct": metric.avg_cpu_pct,
                        "max_cpu_pct": metric.max_cpu_pct,
                        "avg_mem_pct": metric.avg_mem_pct,
                        "instance_type": metric.instance_type,
                        "environment": metric.environment,
                        "region": metric.region,
                        "owner": metric.owner,
                    },
                )
            )

        return findings, checks, []


class RuntimeCostCarbon:
    """Combine the existing cost and carbon estimators."""

    def estimate(self, metrics, findings):
        pricing = cost_estimator.load_pricing()
        regions = carbon_estimator.load_regions()
        finding_by_id = {f.resource_id: f for f in findings}
        estimates = []
        errors = []

        for metric in metrics:
            finding = finding_by_id.get(metric.resource_id)
            if finding is None:
                errors.append(
                    AgentError(
                        agent="cost_carbon",
                        resource_id=metric.resource_id,
                        message="No Analyzer finding exists for this resource.",
                    )
                )
                continue

            try:
                cost = cost_estimator.estimate_resource_cost(
                    {
                        "resource_id": metric.resource_id,
                        "instance_type": metric.instance_type,
                        "monthly_hours": metric.monthly_hours,
                        "status": finding.finding_type,
                    },
                    pricing,
                )
                carbon = carbon_estimator.estimate_resource_carbon(
                    metric, pricing, regions=regions
                )
                estimates.append(
                    CostCarbonEstimate(
                        resource_id=metric.resource_id,
                        monthly_cost_usd=cost["estimated_monthly_cost_usd"],
                        monthly_energy_kwh=carbon["estimated_energy_kwh"],
                        monthly_carbon_kg=carbon["estimated_emissions_kg_co2e"],
                        assumptions=[
                            pricing.get("note", "Synthetic pricing assumptions."),
                            carbon["carbon_intensity_note"],
                            *carbon["assumptions"],
                            cost["savings_assumption"],
                        ],
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(
                    AgentError(
                        agent="cost_carbon",
                        resource_id=metric.resource_id,
                        message=f"Estimation failed: {exc}",
                    )
                )

        checks = [
            ValidationCheck(
                name="cost_carbon_inputs",
                status="passed" if not errors else "failed",
                message=f"Produced {len(estimates)} estimates for {len(metrics)} resources.",
            )
        ]
        return estimates, checks, errors


def run_sample_pipeline(csv_path=None, terraform_path=None):
    """Run the real local agents; never edits or applies Terraform."""
    metrics = load_resource_metrics(csv_path)
    terraform_resources = parse_terraform_resources(terraform_path)

    report = orchestrator.run_pipeline(
        metrics,
        terraform_resources,
        RuntimeAnalyzer(),
        RuntimeCostCarbon(),
        fixer,
    )

    metric_by_id = {m.resource_id: m for m in metrics}
    tf_by_id = {r.resource_id: r for r in terraform_resources}
    mismatches = []

    for resource_id in sorted(metric_by_id.keys() & tf_by_id.keys()):
        metric = metric_by_id[resource_id]
        resource = tf_by_id[resource_id]
        if metric.instance_type != resource.instance_type:
            mismatches.append(
                f"{resource_id}: instance type differs between CSV and Terraform"
            )
        if (
            resource.environment
            and metric.environment.strip().lower()
            != resource.environment.strip().lower()
        ):
            mismatches.append(
                f"{resource_id}: CSV environment {metric.environment} "
                f"differs from Terraform environment {resource.environment}"
            )

    matched = len(metric_by_id.keys() & tf_by_id.keys())
    report.validation_checks.append(
        ValidationCheck(
            name="terraform_data_alignment",
            status="failed" if mismatches else "passed",
            message=(
                f"Matched {matched} resource IDs. "
                + ("; ".join(mismatches) if mismatches else "Matched data agrees.")
            ),
        )
    )

    unmatched = sorted(metric_by_id.keys() - tf_by_id.keys())
    if unmatched:
        report.notes.append(
            "No matching Terraform resource for: "
            + ", ".join(unmatched)
            + ". The Fixer should skip these resources."
        )

    return report



def load_resource_metrics(csv_path=None):
    """Load and validate the sample resource metrics."""
    rows = analyzer.load_utilisation(csv_path)
    return [
        ResourceMetrics(
            resource_id=r["resource_id"],
            instance_type=r["instance_type"],
            region=r["region"],
            environment=r["environment"],
            avg_cpu_pct=r["avg_cpu_pct"],
            max_cpu_pct=r["max_cpu_pct"],
            avg_mem_pct=r["avg_mem_pct"],
            monthly_hours=r["monthly_hours"],
            owner=r["owner"],
        )
        for r in rows
    ]


def parse_terraform_resources(terraform_path=None):
    """Parse the simple aws_instance blocks used by our sample Terraform."""
    path = Path(terraform_path) if terraform_path else DEFAULT_TF
    if not path.is_file():
        raise FileNotFoundError(f"Terraform file not found: {path}")

    source = path.read_text(encoding="utf-8")

    # Resolve simple string defaults such as var.environment = "dev".
    variables = {}
    for match in re.finditer(
        r'variable\s+"([^"]+)"\s*\{([^{}]*)\}', source, re.S
    ):
        default = re.search(r'default\s*=\s*"([^"]*)"', match.group(2))
        if default:
            variables[match.group(1)] = default.group(1)

    resources = []
    pattern = re.compile(
        r'resource\s+"aws_instance"\s+"([^"]+)"\s*\{(.*?)\n\}',
        re.S,
    )

    for match in pattern.finditer(source):
        name, body = match.groups()
        instance = re.search(r'instance_type\s*=\s*"([^"]+)"', body)
        tags_match = re.search(r'tags\s*=\s*\{(.*?)\}', body, re.S)
        if not instance or not tags_match:
            continue

        tags = {}
        for item in re.finditer(
            r'(\w+)\s*=\s*(?:"([^"]*)"|([\w.-]+))',
            tags_match.group(1),
        ):
            key, quoted, bare = item.groups()
            value = quoted if quoted is not None else bare
            var = re.fullmatch(r'var\.([\w-]+)', value)
            if var:
                value = variables.get(var.group(1), value)
            tags[key] = value

        if not tags.get("Name"):
            continue

        resources.append(
            TerraformResource(
                resource_address=f"aws_instance.{name}",
                resource_id=tags["Name"],
                instance_type=instance.group(1),
                environment=tags.get("Environment"),
                owner=tags.get("Owner"),
            )
        )

    return resources


class RuntimeAnalyzer:
    """Adapt the Analyzer's classification function to the shared contract."""

    def analyze(self, metrics, terraform_resources):
        findings = []
        checks = [
            ValidationCheck(
                name="analyzer_input",
                status="passed",
                message=f"Received {len(metrics)} validated resources.",
            )
        ]

        severity = {
            "idle": "medium",
            "underused": "low",
            "review": "medium",
            "ok": "info",
        }

        for metric in metrics:
            status, reason = analyzer.classify(
                metric.avg_cpu_pct,
                metric.max_cpu_pct,
                metric.avg_mem_pct,
            )
            findings.append(
                AnalysisFinding(
                    resource_id=metric.resource_id,
                    finding_type=status,
                    severity=severity[status],
                    message=reason,
                    evidence={
                        "avg_cpu_pct": metric.avg_cpu_pct,
                        "max_cpu_pct": metric.max_cpu_pct,
                        "avg_mem_pct": metric.avg_mem_pct,
                        "instance_type": metric.instance_type,
                        "environment": metric.environment,
                        "region": metric.region,
                        "owner": metric.owner,
                    },
                )
            )

        return findings, checks, []


class RuntimeCostCarbon:
    """Combine the existing cost and carbon estimators."""

    def estimate(self, metrics, findings):
        pricing = cost_estimator.load_pricing()
        regions = carbon_estimator.load_regions()
        finding_by_id = {f.resource_id: f for f in findings}
        estimates = []
        errors = []

        for metric in metrics:
            finding = finding_by_id.get(metric.resource_id)
            if finding is None:
                errors.append(
                    AgentError(
                        agent="cost_carbon",
                        resource_id=metric.resource_id,
                        message="No Analyzer finding exists for this resource.",
                    )
                )
                continue

            try:
                cost = cost_estimator.estimate_resource_cost(
                    {
                        "resource_id": metric.resource_id,
                        "instance_type": metric.instance_type,
                        "monthly_hours": metric.monthly_hours,
                        "status": finding.finding_type,
                    },
                    pricing,
                )
                carbon = carbon_estimator.estimate_resource_carbon(
                    metric, pricing, regions=regions
                )
                estimates.append(
                    CostCarbonEstimate(
                        resource_id=metric.resource_id,
                        monthly_cost_usd=cost["estimated_monthly_cost_usd"],
                        monthly_energy_kwh=carbon["estimated_energy_kwh"],
                        monthly_carbon_kg=carbon["estimated_emissions_kg_co2e"],
                        assumptions=[
                            pricing.get("note", "Synthetic pricing assumptions."),
                            carbon["carbon_intensity_note"],
                            *carbon["assumptions"],
                            cost["savings_assumption"],
                        ],
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(
                    AgentError(
                        agent="cost_carbon",
                        resource_id=metric.resource_id,
                        message=f"Estimation failed: {exc}",
                    )
                )

        checks = [
            ValidationCheck(
                name="cost_carbon_inputs",
                status="passed" if not errors else "failed",
                message=f"Produced {len(estimates)} estimates for {len(metrics)} resources.",
            )
        ]
        return estimates, checks, errors


def run_sample_pipeline(csv_path=None, terraform_path=None):
    """Run the real local agents; never edits or applies Terraform."""
    metrics = load_resource_metrics(csv_path)
    terraform_resources = parse_terraform_resources(terraform_path)

    report = orchestrator.run_pipeline(
        metrics,
        terraform_resources,
        RuntimeAnalyzer(),
        RuntimeCostCarbon(),
        fixer,
    )

    metric_by_id = {m.resource_id: m for m in metrics}
    tf_by_id = {r.resource_id: r for r in terraform_resources}
    mismatches = []

    for resource_id in sorted(metric_by_id.keys() & tf_by_id.keys()):
        metric = metric_by_id[resource_id]
        resource = tf_by_id[resource_id]
        if metric.instance_type != resource.instance_type:
            mismatches.append(
                f"{resource_id}: instance type differs between CSV and Terraform"
            )
        if (
            resource.environment
            and metric.environment.strip().lower()
            != resource.environment.strip().lower()
        ):
            mismatches.append(
                f"{resource_id}: CSV environment {metric.environment} "
                f"differs from Terraform environment {resource.environment}"
            )

    matched = len(metric_by_id.keys() & tf_by_id.keys())
    report.validation_checks.append(
        ValidationCheck(
            name="terraform_data_alignment",
            status="failed" if mismatches else "passed",
            message=(
                f"Matched {matched} resource IDs. "
                + ("; ".join(mismatches) if mismatches else "Matched data agrees.")
            ),
        )
    )

    unmatched = sorted(metric_by_id.keys() - tf_by_id.keys())
    if unmatched:
        report.notes.append(
            "No matching Terraform resource for: "
            + ", ".join(unmatched)
            + ". The Fixer should skip these resources."
        )

    return report
