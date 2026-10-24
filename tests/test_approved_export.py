
from agents.approval import record_decision
from agents.approved_export import export_proposal


def test_export_proposal_blocks_missing_approval(tmp_path):
    diff = "--- a/main.tf\n+++ b/main.tf\n"
    output = tmp_path / "candidate.patch"

    result = export_proposal(diff, str(output))
    assert not output.exists()
    assert result.status == "failed"
    assert "approval is required" in result.message.lower()


def test_export_proposal_blocks_rejected_approval(tmp_path):
    diff = "--- a/main.tf\n+++ b/main.tf\n"
    output = tmp_path / "candidate.patch"
    approval = record_decision(
        proposed_diff=diff,
        reviewer="test reviewer",
        decision="rejected",
        reason="Needs review",
    )

    result = export_proposal(diff, str(output), approval)
    assert not output.exists()
    assert result.status == "failed"
    assert "approval is required" in result.message.lower()
