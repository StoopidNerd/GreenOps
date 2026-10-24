
"""Safety and validation rules for GreenOps.

These checks inspect proposed changes only.
They never deploy infrastructure or modify Terraform files.
"""

import sys
from pathlib import Path
from typing import List

# Make the GreenOps project root available for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from contracts import (
    OptimizationRecommendation,
    ValidationCheck,
)


def validate_resource_id(
    resource_id: str,
    known_resource_ids: List[str],
) -> ValidationCheck:
    """Ensure a recommendation refers to a known dataset resource."""

    if resource_id not in known_resource_ids:
        return ValidationCheck(
            name="resource_id_check",
            status="failed",
            message=f"Unknown resource ID: {resource_id}",
        )

    return ValidationCheck(
        name="resource_id_check",
        status="passed",
        message=f"Resource ID verified: {resource_id}",
    )


def validate_recommendation(
    recommendation: OptimizationRecommendation,
) -> List[ValidationCheck]:
    """Check a recommendation before it can be considered for approval."""

    checks = []

    if not recommendation.resource_id.strip():
        checks.append(
            ValidationCheck(
                name="resource_id_present",
                status="failed",
                message="Recommendation has no resource ID.",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                name="resource_id_present",
                status="passed",
                message="Recommendation has a resource ID.",
            )
        )

    if not recommendation.suggested_instance_type:
        checks.append(
            ValidationCheck(
                name="target_instance_type",
                status="skipped",
                message="No suggested instance type was provided.",
            )
        )
    elif (
        recommendation.suggested_instance_type
        == recommendation.current_instance_type
    ):
        checks.append(
            ValidationCheck(
                name="target_instance_type",
                status="failed",
                message="Suggested type is identical to the current type.",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                name="target_instance_type",
                status="passed",
                message="Suggested type differs from the current type.",
            )
        )

    checks.append(
        ValidationCheck(
            name="human_approval",
            status="passed" if recommendation.requires_human_approval else "failed",
            message=(
                "Human approval is required."
                if recommendation.requires_human_approval
                else "Recommendation does not require human approval."
            ),
        )
    )

    return checks


def validate_terraform_path(terraform_path: str) -> ValidationCheck:
    """Confirm the source Terraform file exists for inspection."""

    path = Path(terraform_path)

    if not path.is_file() or path.suffix != ".tf":
        return ValidationCheck(
            name="terraform_source",
            status="failed",
            message="Terraform source file is missing or has an invalid extension.",
        )

    return ValidationCheck(
        name="terraform_source",
        status="passed",
        message="Terraform source file exists and can be inspected.",
    )
