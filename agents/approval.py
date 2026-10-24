
"""Human approval decisions for GreenOps proposal exports.

This module records decisions and gates patch export.
It never executes Terraform.

Security note:
- Reviewer identity is self-declared text, not authentication.
- Approval is tied to a proposal fingerprint.
- Approval records are not persisted by this module.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal

Decision = Literal["approved", "rejected"]

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _require_text(value: object, name: str) -> str:
    """Return stripped, non-empty text or raise ValueError."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required and must be non-empty text.")
    return value.strip()


def _validate_reviewer(value: object) -> str:
    """Validate reviewer text and prevent multiline identities."""
    reviewer = _require_text(value, "Reviewer identity")

    if "\n" in reviewer or "\r" in reviewer:
        raise ValueError("Reviewer identity must be a single line.")

    return reviewer


def _validate_timestamp(value: object) -> str:
    """Require a valid ISO timestamp with timezone information."""
    timestamp = _require_text(value, "decided_at_utc")

    try:
        parsed = datetime.fromisoformat(timestamp)
    except (ValueError, TypeError) as exc:
        raise ValueError("decided_at_utc must be a valid ISO timestamp.") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decided_at_utc must include timezone information.")

    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError("decided_at_utc must use UTC.")

    return timestamp


@dataclass(frozen=True)
class ApprovalRecord:
    """Immutable record of a decision about a specific proposal."""

    proposal_hash: str
    reviewer: str
    decision: Decision
    reason: str
    decided_at_utc: str

    def __post_init__(self) -> None:
        # Validate records even if constructed directly.
        if (
            not isinstance(self.proposal_hash, str)
            or not _SHA256_HEX.fullmatch(self.proposal_hash)
        ):
            raise ValueError(
                "proposal_hash must be a 64-character lowercase SHA-256 hex string."
            )

        _validate_reviewer(self.reviewer)

        if self.decision not in ("approved", "rejected"):
            raise ValueError("Decision must be approved or rejected.")

        _require_text(self.reason, "A reason for the decision")
        _validate_timestamp(self.decided_at_utc)


def fingerprint_proposal(proposed_diff: str) -> str:
    """Return a stable SHA-256 fingerprint of a proposal.

    CRLF line endings are normalized to LF so Windows line-ending
    conversion alone does not invalidate an approval.
    Other text changes produce a different fingerprint.
    """
    if not isinstance(proposed_diff, str) or not proposed_diff.strip():
        raise ValueError("Cannot fingerprint an empty or non-text proposal.")

    normalized = proposed_diff.replace("\r\n", "\n")
    return sha256(normalized.encode("utf-8")).hexdigest()


def record_decision(
    proposed_diff: str,
    reviewer: str,
    decision: Decision,
    reason: str,
    now: datetime | None = None,
) -> ApprovalRecord:
    """Create an approval or rejection record.

    ``now`` is optional and allows deterministic tests.
    The supplied reviewer is not authenticated by this function.
    """
    reviewer = _validate_reviewer(reviewer)

    if decision not in ("approved", "rejected"):
        raise ValueError("Decision must be approved or rejected.")

    # Collapse whitespace to keep the reason on one line in logs.
    reason = " ".join(
        _require_text(reason, "A reason for the decision").split()
    )

    if now is not None:
        if not isinstance(now, datetime):
            raise ValueError("now must be a datetime.")

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must include timezone information.")

        timestamp = now.astimezone(timezone.utc).isoformat()
    else:
        timestamp = datetime.now(timezone.utc).isoformat()

    return ApprovalRecord(
        proposal_hash=fingerprint_proposal(proposed_diff),
        reviewer=reviewer,
        decision=decision,
        reason=reason,
        decided_at_utc=timestamp,
    )


def approval_matches(
    proposed_diff: str,
    approval: ApprovalRecord | None,
) -> bool:
    """Return True only for an approval matching this exact proposal.

    Missing, rejected, malformed, or mismatched approvals return False.
    """
    if not isinstance(approval, ApprovalRecord):
        return False

    if approval.decision != "approved":
        return False

    try:
        current_hash = fingerprint_proposal(proposed_diff)
    except (TypeError, ValueError):
        return False

    return approval.proposal_hash == current_hash


def export_if_approved(
    proposed_diff: str,
    output_path: str,
    approval: ApprovalRecord | None,
):
    """Gate patch export using the matching approval record.

    Delegates the actual file-writing and existing safety checks to
    agents.fixer.export_patch(). Does not run Terraform.
    """
    from agents import fixer

    approved = approval_matches(proposed_diff, approval)

    return fixer.export_patch(
        proposed_diff,
        output_path,
        approved=approved,
    )
