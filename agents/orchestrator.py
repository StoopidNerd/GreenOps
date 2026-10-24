"""GreenOps Orchestrator: safely coordinates the three analysis agents.

Pipeline order:
    Analyzer -> Cost & Carbon -> Fixer -> GreenOpsReport

This module only coordinates in-memory data. It does not connect to cloud
accounts, edit the original Terraform configuration, or execute Terraform.
The individual agents are injected so they can be tested independently and
integrated as their implementations are completed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Protocol, Tuple

# Make the project root importable when this module is run from VS Code.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from contracts import (  # noqa: E402
    AgentError,
    AnalysisFinding,
    CostCarbonEstimate,
    GreenOpsReport,
    OptimizationRecommendation,
    ResourceMetrics,
    TerraformResource,
    ValidationCheck,
)


class AnalyzerAgent(Protocol):
    def analyze(
        self,
        metrics: List[ResourceMetrics],
        terraform_resources: List[TerraformResource],
    ) -> Tuple[List[AnalysisFinding], List[ValidationCheck], List[AgentError]]: ...


class CostCarbonAgent(Protocol):
    def estimate(
        self,
        metrics: List[ResourceMetrics],
        findings: List[AnalysisFinding],
    ) -> Tuple[List[CostCarbonEstimate], List[ValidationCheck], List[AgentError]]: ...


class FixerAgent(Protocol):
    def recommend(
        self,
        metrics: List[ResourceMetrics],
        terraform_resources: List[TerraformResource],
        findings: List[AnalysisFinding],
        estimates: List[CostCarbonEstimate],
    ) -> Tuple[List[OptimizationRecommendation], List[ValidationCheck], List[AgentError]]: ...


def _agent_failure(
    report: GreenOpsReport,
    agent_name: str,
    exc: Exception,
    check_name: str,
) -> None:
    """Record an agent exception without hiding it or claiming success."""
    message = f"{agent_name} failed: {type(exc).__name__}: {exc}"
    report.errors.append(AgentError(agent=agent_name, message=message))
    report.validation_checks.append(
        ValidationCheck(name=check_name, status="failed", message=message)
    )


def _validate_agent_result(result, agent_name: str):
    """Ensure an agent returns the shared three-part contract."""
    if not isinstance(result, tuple) or len(result) != 3:
        raise TypeError(
            f"{agent_name} must return (results, validation_checks, errors)."
        )

    results, checks, errors = result
    if not isinstance(results, list) or not isinstance(checks, list) or not isinstance(errors, list):
        raise TypeError(
            f"{agent_name} must return lists in all three tuple positions."
        )
    if not all(isinstance(item, ValidationCheck) for item in checks):
        raise TypeError(f"{agent_name} returned an invalid validation-check item.")
    if not all(isinstance(item, AgentError) for item in errors):
        raise TypeError(f"{agent_name} returned an invalid error item.")

    return results, checks, errors


def _add_skipped(report: GreenOpsReport, name: str, reason: str) -> None:
    report.validation_checks.append(
        ValidationCheck(name=name, status="skipped", message=reason)
    )


def run_pipeline(
    metrics: List[ResourceMetrics],
    terraform_resources: List[TerraformResource],
    analyzer_agent: AnalyzerAgent,
    cost_carbon_agent: CostCarbonAgent,
    fixer_agent: FixerAgent,
) -> GreenOpsReport:
    """Run the GreenOps pipeline and return one consolidated report.

    Any agent exception is captured in the report. If an upstream stage fails,
    dependent stages are skipped instead of being called with misleading data.
    No infrastructure action is performed.
    """
    report = GreenOpsReport()

    # Reject duplicate IDs before downstream agents could confuse resources.
    metric_ids = [metric.resource_id for metric in metrics]
    terraform_ids = [resource.resource_id for resource in terraform_resources]
    duplicate_metric_ids = sorted(
        {resource_id for resource_id in metric_ids if metric_ids.count(resource_id) > 1}
    )
    duplicate_terraform_ids = sorted(
        {resource_id for resource_id in terraform_ids if terraform_ids.count(resource_id) > 1}
    )

    if duplicate_metric_ids:
        message = "Duplicate resource IDs in metrics: " + ", ".join(duplicate_metric_ids)
        report.errors.append(AgentError(agent="orchestrator", message=message))
        report.validation_checks.append(
            ValidationCheck(
                name="input_metrics_unique_ids", status="failed", message=message
            )
        )
        _add_skipped(
            report,
            "pipeline",
            "Pipeline stopped because metrics contain duplicate resource IDs.",
        )
        return report

    report.validation_checks.append(
        ValidationCheck(
            name="input_metrics_unique_ids",
            status="passed",
            message=f"Metrics contain {len(metric_ids)} unique resource IDs.",
        )
    )

    if duplicate_terraform_ids:
        message = "Duplicate resource IDs in Terraform resources: " + ", ".join(duplicate_terraform_ids)
        report.errors.append(AgentError(agent="orchestrator", message=message))
        report.validation_checks.append(
            ValidationCheck(
                name="input_terraform_unique_ids", status="failed", message=message
            )
        )
        _add_skipped(
            report,
            "pipeline",
            "Pipeline stopped because Terraform resources contain duplicate resource IDs.",
        )
        return report

    report.validation_checks.append(
        ValidationCheck(
            name="input_terraform_unique_ids",
            status="passed",
            message=f"Terraform input contains {len(terraform_ids)} unique resource IDs.",
        )
    )

    # Stage 1: analyze utilization and Terraform metadata.
    try:
        findings, checks, errors = _validate_agent_result(
            analyzer_agent.analyze(metrics, terraform_resources), "analyzer"
        )
        if not all(isinstance(item, AnalysisFinding) for item in findings):
            raise TypeError("Analyzer returned an item that is not an AnalysisFinding.")

        report.findings.extend(findings)
        report.validation_checks.extend(checks)
        report.errors.extend(errors)
        analyzer_failed = bool(errors) or any(check.status == "failed" for check in checks)
        report.validation_checks.append(
            ValidationCheck(
                name="analyzer_stage",
                status="failed" if analyzer_failed else "passed",
                message=f"Analyzer returned {len(findings)} finding(s).",
            )
        )

        if analyzer_failed:
            _add_skipped(
                report,
                "cost_carbon_stage",
                "Skipped because Analyzer reported errors or failed checks.",
            )
            _add_skipped(
                report,
                "fixer_stage",
                "Skipped because Analyzer reported errors or failed checks.",
            )
            report.notes.append(
                "Review Analyzer errors and validation checks before rerunning the pipeline."
            )
            return report
    except Exception as exc:
        _agent_failure(report, "analyzer", exc, "analyzer_stage")
        _add_skipped(
            report, "cost_carbon_stage", "Skipped because Analyzer did not complete successfully."
        )
        _add_skipped(
            report, "fixer_stage", "Skipped because Analyzer did not complete successfully."
        )
        return report

    # Stage 2: estimate cost and carbon for analyzed resources.
    try:
        estimates, checks, errors = _validate_agent_result(
            cost_carbon_agent.estimate(metrics, report.findings), "cost_carbon"
        )
        if not all(isinstance(item, CostCarbonEstimate) for item in estimates):
            raise TypeError("Cost & Carbon agent returned an invalid estimate item.")

        report.estimates.extend(estimates)
        report.validation_checks.extend(checks)
        report.errors.extend(errors)
        cost_carbon_failed = bool(errors) or any(
            check.status == "failed" for check in checks
        )
        report.validation_checks.append(
            ValidationCheck(
                name="cost_carbon_stage",
                status="failed" if cost_carbon_failed else "passed",
                message=f"Cost & Carbon agent returned {len(estimates)} estimate(s).",
            )
        )

        if cost_carbon_failed:
            _add_skipped(
                report,
                "fixer_stage",
                "Skipped because Cost & Carbon agent reported errors or failed checks.",
            )
            report.notes.append(
                "Review Cost & Carbon errors and validation checks before generating recommendations."
            )
            return report
    except Exception as exc:
        _agent_failure(report, "cost_carbon", exc, "cost_carbon_stage")
        _add_skipped(
            report,
            "fixer_stage",
            "Skipped because Cost & Carbon estimates were not produced successfully.",
        )
        return report

    # Stage 3: generate advisory recommendations. No changes are applied.
    try:
        recommendations, checks, errors = _validate_agent_result(
            fixer_agent.recommend(
                metrics,
                terraform_resources,
                report.findings,
                report.estimates,
            ),
            "fixer",
        )
        if not all(
            isinstance(item, OptimizationRecommendation) for item in recommendations
        ):
            raise TypeError("Fixer returned an invalid recommendation item.")

        report.recommendations.extend(recommendations)
        report.validation_checks.extend(checks)
        report.errors.extend(errors)

        # A missing Terraform match is an expected safety skip. Keep its error
        # and failed terraform_match check in the report, but don't confuse it
        # with a Fixer execution failure. Other errors/check failures still block.
        safe_skip_errors = [
            error
            for error in errors
            if error.agent == "fixer"
            and error.message.startswith("No matching Terraform resource")
        ]
        blocking_errors = [error for error in errors if error not in safe_skip_errors]
        blocking_failures = [
            check
            for check in checks
            if check.status == "failed" and check.name != "terraform_match"
        ]
        missing_match_count = sum(
            check.name == "terraform_match" and check.status == "failed"
            for check in checks
        )

        fixer_failed = bool(blocking_errors) or bool(blocking_failures)
        report.validation_checks.append(
            ValidationCheck(
                name="fixer_stage",
                status="failed" if fixer_failed else "passed",
                message=(
                    f"Fixer returned {len(recommendations)} recommendation(s); "
                    f"{missing_match_count} resource(s) were skipped because no "
                    "Terraform match was found. No changes were applied."
                ),
            )
        )
    except Exception as exc:
        _agent_failure(report, "fixer", exc, "fixer_stage")

    report.notes.extend(
        [
            "All pricing and carbon figures are estimates based on the supplied data and assumptions.",
            "Recommendations are advisory and require human review and approval.",
            "The orchestrator does not edit original Terraform files or deploy infrastructure.",
        ]
    )
    return report


def report_to_dict(report: GreenOpsReport) -> dict:
    """Convert the report dataclasses to plain dictionaries for a UI or JSON."""
    from dataclasses import asdict

    return asdict(report)
