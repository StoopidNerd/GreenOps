
"""Human-controlled workflow for exporting an approved proposal.

This module exports a patch only. It never applies a patch or runs Terraform.
"""

from agents.approval import ApprovalRecord
from agents.approved_export import export_proposal
from agents.fixer import ValidationCheck


def export_with_approval(
    proposed_diff: str,
    output_path: str,
    approval: ApprovalRecord | None = None,
) -> ValidationCheck:
    """Export only if a recorded approval matches this exact proposal."""
    return export_proposal(
        proposed_diff=proposed_diff,
        output_path=output_path,
        approval=approval,
    )
