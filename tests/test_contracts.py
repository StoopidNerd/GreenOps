
import sys
from pathlib import Path

# Make the GreenOps project root available for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    from contracts import (
    ResourceMetrics,
    OptimizationRecommendation,
    GreenOpsReport,
)

from agents.safety import (
    validate_resource_id,
    validate_recommendation,
    validate_terraform_path,
)


def test_contracts():
    # Sample resource from our GreenOps dataset
    resource = ResourceMetrics(
        resource_id="dev-sandbox-01",
        instance_type="m5.2xlarge",
        region="us-east-1",
        environment="dev",
        avg_cpu_pct=2.1,
        max_cpu_pct=9.4,
        avg_mem_pct=8.3,
        monthly_hours=730,
        owner="ml-team",
    )

    # A sample proposal, NOT an approved change
    recommendation = OptimizationRecommendation(
        resource_id=resource.resource_id,
        current_instance_type=resource.instance_type,
        suggested_instance_type="m5.large",
        reason="Low CPU and memory usage; requires further review.",
    )

    checks = [
        validate_resource_id(
            recommendation.resource_id,
            [resource.resource_id],
        ),
        *validate_recommendation(recommendation),
        validate_terraform_path("sample_infra/main.tf"),
    ]

    report = GreenOpsReport(
        recommendations=[recommendation],
        validation_checks=checks,
    )

    assert all(check.status == "passed" for check in checks)
    assert len(report.recommendations) == 1
    assert report.recommendations[0].requires_human_approval

    print("All Phase 3 contract checks passed!")


if __name__ == "__main__":
    test_contracts()
