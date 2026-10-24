
"""Tests for agents.approval.

Run:
    python -m pytest tests/test_approval.py -q
"""

import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.approval import (
    ApprovalRecord,
    approval_matches,
    export_if_approved,
    fingerprint_proposal,
    record_decision,
)

DIFF = (
    '--- a/main.tf\n'
    '+++ b/main.tf\n'
    '-instance_type = "m5.xlarge"\n'
    '+instance_type = "m5.large"\n'
)

FIXED_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def approve(diff=DIFF, decision="approved"):
    return record_decision(
        diff,
        "alex",
        decision,
        "Looks right",
        now=FIXED_TIME,
    )


def test_matching_approval_is_accepted():
    assert approval_matches(DIFF, approve()) is True


def test_one_character_change_invalidates_approval():
    assert approval_matches(DIFF + " ", approve()) is False
    assert approval_matches(DIFF.replace("large", "larGe"), approve()) is False


def test_rejected_record_never_matches():
    assert approval_matches(DIFF, approve(decision="rejected")) is False


def test_missing_or_wrong_type_approval_returns_false():
    assert approval_matches(DIFF, None) is False
    assert approval_matches(DIFF, "approved") is False
    assert approval_matches(DIFF, object()) is False


@pytest.mark.parametrize("bad_diff", ["", "   \n", None, 5])
def test_invalid_proposal_does_not_match(bad_diff):
    assert approval_matches(bad_diff, approve()) is False


def test_windows_line_endings_do_not_break_approval():
    crlf = DIFF.replace("\n", "\r\n")

    assert fingerprint_proposal(crlf) == fingerprint_proposal(DIFF)
    assert approval_matches(crlf, approve()) is True


def test_fingerprint_is_stable_and_sha256_length():
    first = fingerprint_proposal(DIFF)
    second = fingerprint_proposal(DIFF)

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


@pytest.mark.parametrize("bad", ["", "  \n", None, 5])
def test_invalid_proposal_fingerprint_raises(bad):
    with pytest.raises(ValueError):
        fingerprint_proposal(bad)


@pytest.mark.parametrize(
    "reviewer",
    ["", "   ", None, 7, "alex\nother", "alex\rother"],
)
def test_invalid_reviewer_is_rejected(reviewer):
    with pytest.raises(ValueError):
        record_decision(DIFF, reviewer, "approved", "ok")


@pytest.mark.parametrize("reason", ["", "   ", None, 7])
def test_invalid_reason_is_rejected(reason):
    with pytest.raises(ValueError):
        record_decision(DIFF, "alex", "approved", reason)


@pytest.mark.parametrize("decision", ["maybe", "APPROVED", "", None])
def test_invalid_decision_is_rejected(decision):
    with pytest.raises(ValueError):
        record_decision(DIFF, "alex", decision, "ok")


def test_fields_are_cleaned_and_timestamped():
    record = record_decision(
        DIFF,
        "  alex  ",
        "approved",
        "line one\nline two",
        now=FIXED_TIME,
    )

    assert record.reviewer == "alex"
    assert record.reason == "line one line two"
    assert record.decided_at_utc == FIXED_TIME.isoformat()
    assert record.proposal_hash == fingerprint_proposal(DIFF)


def test_default_timestamp_is_utc():
    record = record_decision(DIFF, "alex", "approved", "ok")

    parsed = datetime.fromisoformat(record.decided_at_utc)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_naive_test_timestamp_is_rejected():
    naive_time = datetime(2026, 1, 1, 12, 0)

    with pytest.raises(ValueError):
        record_decision(DIFF, "alex", "approved", "ok", now=naive_time)


def test_non_utc_offset_is_normalized_to_utc():
    from datetime import timedelta

    local_time = datetime(
        2026, 1, 1, 17, 30,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )

    record = record_decision(
        DIFF, "alex", "approved", "ok", now=local_time
    )

    assert record.decided_at_utc == FIXED_TIME.isoformat()


def test_record_is_immutable():
    record = approve()

    with pytest.raises(FrozenInstanceError):
        record.decision = "rejected"


def test_direct_record_rejects_invalid_fields():
    valid = {
        "proposal_hash": "a" * 64,
        "reviewer": "alex",
        "decision": "approved",
        "reason": "ok",
        "decided_at_utc": "2026-01-01T00:00:00+00:00",
    }

    invalid_cases = [
        {"proposal_hash": "abc"},
        {"proposal_hash": "G" * 64},
        {"proposal_hash": "A" * 64},
        {"reviewer": " "},
        {"reviewer": "alex\nother"},
        {"decision": "approve"},
        {"reason": ""},
        {"decided_at_utc": ""},
        {"decided_at_utc": "not-a-date"},
        {"decided_at_utc": "2026-01-01T00:00:00"},
        {"decided_at_utc": "2026-01-01T05:30:00+05:30"},
    ]

    for changes in invalid_cases:
        fields = {**valid, **changes}

        with pytest.raises(ValueError):
            ApprovalRecord(**fields)


def test_export_writes_nothing_without_matching_approval(tmp_path):
    target = tmp_path / "out.patch"

    approvals = (
        None,
        approve(decision="rejected"),
        approve(diff=DIFF + "changed"),
    )

    for approval in approvals:
        check = export_if_approved(DIFF, str(target), approval)

        assert check.status == "failed"
        assert not target.exists()


def test_export_writes_file_with_matching_approval(tmp_path):
    target = tmp_path / "out.patch"

    check = export_if_approved(DIFF, str(target), approve())

    assert check.status == "passed"
    assert target.read_text(encoding="utf-8") == DIFF


def test_export_never_overwrites_existing_file(tmp_path):
    target = tmp_path / "out.patch"
    target.write_text("keep me", encoding="utf-8")

    check = export_if_approved(DIFF, str(target), approve())

    assert check.status == "failed"
    assert target.read_text(encoding="utf-8") == "keep me"
