
from agents.approval import record_decision
from agents.approval_workflow import export_with_approval


def test_missing_approval_blocks_export(tmp_path):
    diff = "--- a/main.tf\n+++ b/main.tf\n"
    output = tmp_path / "candidate.patch"

    result = export_with_approval(diff, str(output))

    assert result.status == "failed"
    assert not output.exists()


def test_matching_approval_allows_export(tmp_path):
    diff = "--- a/main.tf\n+++ b/main.tf\n"
    output = tmp_path / "candidate.patch"
    approval = record_decision(
        proposed_diff=diff,
        reviewer="human reviewer",
        decision="approved",
        reason="Reviewed proposal",
    )

    result = export_with_approval(diff, str(output), approval)

    assert result.status == "passed"
    assert output.read_text(encoding="utf-8") == diff


def test_changed_proposal_invalidates_approval(tmp_path):
    original = "--- a/main.tf\n+++ b/main.tf\n"
    changed = original + "# changed\n"
    output = tmp_path / "candidate.patch"
    approval = record_decision(
        proposed_diff=original,
        reviewer="human reviewer",
        decision="approved",
        reason="Reviewed original proposal",
    )

    result = export_with_approval(changed, str(output), approval)

    assert result.status == "failed"
    assert not output.exists()
