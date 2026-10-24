
from agents.runtime import (
    load_resource_metrics,
    parse_terraform_resources,
    run_sample_pipeline,
)
from contracts import GreenOpsReport


def test_sample_data_loads():
    metrics = load_resource_metrics()
    resources = parse_terraform_resources()

    assert len(metrics) == 14
    assert len({m.resource_id for m in metrics}) == 14
    assert len(resources) == 3


def test_real_agents_work_together():
    report = run_sample_pipeline()

    assert isinstance(report, GreenOpsReport)
    assert len(report.findings) == 14
    assert len(report.estimates) == 14

    failed_agent_errors = [
        error for error in report.errors
        if error.agent in {"analyzer", "cost_carbon", "orchestrator"}
    ]
    assert not failed_agent_errors, failed_agent_errors

    stage_checks = {
        check.name: check.status
        for check in report.validation_checks
    }
    assert stage_checks["analyzer_stage"] == "passed"
    assert stage_checks["cost_carbon_stage"] == "passed"
    assert stage_checks["fixer_stage"] == "passed"


def test_recommendations_require_human_approval():
    report = run_sample_pipeline()

    assert all(
        recommendation.requires_human_approval
        for recommendation in report.recommendations
    )

    # Unmatched resources must be flagged rather than silently modified.
    assert any(
        check.name == "terraform_data_alignment"
        for check in report.validation_checks
    )
