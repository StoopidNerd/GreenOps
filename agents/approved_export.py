
"""Explicit approval-controlled patch export.

This module does not run Terraform or modify Terraform source files.
It is intentionally separate from the dashboard.
"""

from agents.approval import ApprovalRecord, export_if_approved


def export_proposal(
    proposed_diff: str,
    output_path: str,
    approval: ApprovalRecord | None = None,
):
    """Export a proposal only when its approval matches the exact diff."""
    return export_if_approved(
        proposed_diff=proposed_diff,
        output_path=output_path,
        approval=approval,
    )
