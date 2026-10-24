"""Tests for the Analyzer agent. Run with: python -m pytest tests/test_analyzer.py"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents import analyzer, fixer, orchestrator
from agents.analyzer import AnalyzerInputError, Finding, analyze, classify, load_utilisation
from contracts import AnalysisFinding

HEADER = (
    "resource_id,instance_type,region,environment,"
    "avg_cpu_pct,max_cpu_pct,avg_mem_pct,monthly_hours,owner"
)

EXPECTED_IDS = {
    "web-01", "web-02", "api-01", "api-02", "batch-01", "batch-02",
    "dev-sandbox-01", "dev-sandbox-02", "staging-01", "staging-02",
    "cache-01", "cron-01", "analytics-01", "test-runner-01",
}


def make_row(resource_id="r-1", avg_cpu="30", max_cpu="40", avg_mem="40", hours="730"):
    return "%s,m5.large,us-east-1,dev,%s,%s,%s,%s,team-a" % (
        resource_id, avg_cpu, max_cpu, avg_mem, hours)


def write_csv(tmp_path, *rows, header=HEADER, name="data.csv"):
    path = tmp_path / name
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def status_of(tmp_path, **fields):
    return analyze(write_csv(tmp_path, make_row(**fields)))[0].status


# --- Default dataset -----------------------------------------------------------

def test_default_dataset_is_backup_csv():
    assert analyzer.DEFAULT_DATASET.name == "utilisation_backup.csv"
    assert analyzer.DEFAULT_DATASET.is_file()


def test_default_dataset_produces_14_findings():
    findings = analyze()
    assert len(findings) == 14
    assert {f.resource_id for f in findings} == EXPECTED_IDS


def test_default_dataset_expected_classifications():
    by_id = {f.resource_id: f.status for f in analyze()}
    assert by_id["web-01"] == "ok"
    assert by_id["cache-01"] == "ok"
    assert by_id["analytics-01"] == "ok"
    assert by_id["api-02"] == "underused"
    assert by_id["cron-01"] == "underused"
    assert by_id["staging-01"] == "underused"
    assert by_id["batch-01"] == "review"  # avg CPU 18.7 but peak 92
    for idle_id in ("batch-02", "dev-sandbox-01", "dev-sandbox-02", "staging-02", "test-runner-01"):
        assert by_id[idle_id] == "idle"


def test_every_default_finding_has_explanation_and_evidence():
    for f in analyze():
        assert f.status in {"idle", "underused", "review", "ok"}
        assert f.classification == f.status
        assert f.explanation == f.reason and f.reason
        for key in ("avg_cpu_pct", "max_cpu_pct", "avg_mem_pct", "monthly_hours", "rule", "thresholds"):
            assert key in f.evidence
        assert f.evidence["avg_cpu_pct"] == f.avg_cpu_pct


def test_load_utilisation_returns_cleaned_rows():
    rows = load_utilisation()
    assert len(rows) == 14
    assert isinstance(rows[0]["avg_cpu_pct"], float)
    assert rows[0]["resource_id"] == "web-01"


# --- Missing / malformed files ---------------------------------------------------

def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        analyze(tmp_path / "nope.csv")


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(AnalyzerInputError, match="empty"):
        analyze(path)


def test_header_only_file_is_rejected(tmp_path):
    with pytest.raises(AnalyzerInputError, match="no data rows"):
        analyze(write_csv(tmp_path))


def test_missing_required_column_is_named(tmp_path):
    header = HEADER.replace(",avg_mem_pct", "")
    path = write_csv(tmp_path, "r-1,m5.large,us-east-1,dev,30,40,730,team-a", header=header)
    with pytest.raises(AnalyzerInputError, match="avg_mem_pct"):
        analyze(path)


def test_extra_columns_are_ignored(tmp_path):
    path = write_csv(tmp_path, make_row() + ",extra", header=HEADER + ",notes")
    assert len(analyze(path)) == 1


def test_row_with_extra_values_is_rejected(tmp_path):
    with pytest.raises(AnalyzerInputError, match="more values"):
        analyze(write_csv(tmp_path, make_row() + ",surprise"))


def test_row_with_missing_values_is_rejected(tmp_path):
    with pytest.raises(AnalyzerInputError, match="monthly_hours is blank"):
        analyze(write_csv(tmp_path, "r-1,m5.large,us-east-1,dev,30,40,40"))


def test_utf8_bom_header_is_accepted(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text(HEADER + "\n" + make_row() + "\n", encoding="utf-8-sig")
    assert analyze(path)[0].resource_id == "r-1"


@pytest.mark.parametrize("bad_id", ["", "   "])
def test_blank_resource_id_is_rejected(tmp_path, bad_id):
    with pytest.raises(AnalyzerInputError, match="resource_id is blank"):
        analyze(write_csv(tmp_path, make_row(resource_id=bad_id)))


def test_duplicate_resource_id_is_rejected(tmp_path):
    with pytest.raises(AnalyzerInputError, match="duplicate"):
        analyze(write_csv(tmp_path, make_row("dup"), make_row("dup")))


# --- Invalid numeric values -----------------------------------------------------

@pytest.mark.parametrize("field", ["avg_cpu", "max_cpu", "avg_mem", "hours"])
@pytest.mark.parametrize("bad", ["abc", "", "nan", "inf", "-inf"])
def test_non_numeric_or_non_finite_values_are_rejected(tmp_path, field, bad):
    with pytest.raises(AnalyzerInputError):
        analyze(write_csv(tmp_path, make_row(**{field: bad})))


@pytest.mark.parametrize("field", ["avg_cpu", "max_cpu", "avg_mem"])
@pytest.mark.parametrize("bad", ["-0.1", "100.1", "250"])
def test_percentages_outside_0_100_are_rejected(tmp_path, field, bad):
    # max_cpu must stay >= avg_cpu so only the range rule is exercised for avg/max.
    fields = {"avg_cpu": "10", "max_cpu": "20", field: bad}
    with pytest.raises(AnalyzerInputError, match="between 0 and 100"):
        analyze(write_csv(tmp_path, make_row(**fields)))


@pytest.mark.parametrize("bad", ["-1", "745", "100000"])
def test_monthly_hours_out_of_range_is_rejected(tmp_path, bad):
    with pytest.raises(AnalyzerInputError, match="monthly_hours must be between"):
        analyze(write_csv(tmp_path, make_row(hours=bad)))


@pytest.mark.parametrize("hours", ["0", "744"])
def test_monthly_hours_limits_are_accepted(tmp_path, hours):
    assert analyze(write_csv(tmp_path, make_row(hours=hours)))[0].monthly_hours == float(hours)


def test_percentage_limits_0_and_100_are_accepted(tmp_path):
    findings = analyze(write_csv(tmp_path, make_row(avg_cpu="0", max_cpu="100", avg_mem="100")))
    assert len(findings) == 1


def test_max_cpu_below_avg_cpu_is_rejected(tmp_path):
    with pytest.raises(AnalyzerInputError, match="max_cpu_pct is lower"):
        analyze(write_csv(tmp_path, make_row(avg_cpu="30", max_cpu="20")))


def test_all_row_problems_are_reported_with_line_numbers(tmp_path):
    path = write_csv(tmp_path, make_row("a", avg_cpu="abc"), make_row("b"), make_row("c", hours="-5"))
    with pytest.raises(AnalyzerInputError) as excinfo:
        analyze(path)
    message = str(excinfo.value)
    assert "line 2 (a)" in message and "line 4 (c)" in message
    assert "line 3" not in message


# --- Threshold boundaries ---------------------------------------------------------

@pytest.mark.parametrize(
    "avg_cpu, expected",
    [
        ("0", "idle"),
        ("4.99", "idle"),
        ("5", "underused"),       # idle threshold is exclusive
        ("19.99", "underused"),
        ("20", "ok"),             # underused threshold is exclusive
        ("20.01", "ok"),
    ],
)
def test_avg_cpu_threshold_boundaries(tmp_path, avg_cpu, expected):
    assert status_of(tmp_path, avg_cpu=avg_cpu, max_cpu="50", avg_mem="30") == expected


@pytest.mark.parametrize(
    "max_cpu, expected",
    [("59.99", "underused"), ("60", "review"), ("60.01", "review")],
)
def test_peak_cpu_threshold_boundary(tmp_path, max_cpu, expected):
    assert status_of(tmp_path, avg_cpu="10", max_cpu=max_cpu, avg_mem="30") == expected


@pytest.mark.parametrize(
    "avg_mem, expected",
    [("59.99", "underused"), ("60", "review"), ("60.01", "review")],
)
def test_memory_threshold_boundary(tmp_path, avg_mem, expected):
    assert status_of(tmp_path, avg_cpu="10", max_cpu="30", avg_mem=avg_mem) == expected


# --- High peak CPU / high memory --------------------------------------------------

def test_low_average_but_high_peak_cpu_is_review_not_idle(tmp_path):
    finding = analyze(write_csv(tmp_path, make_row(avg_cpu="2", max_cpu="95", avg_mem="10")))[0]
    assert finding.status == "review"
    assert "peak CPU" in finding.reason
    assert finding.evidence["rule"] == "high_peak_cpu"


def test_low_cpu_with_high_memory_is_review_and_does_not_claim_downsizing(tmp_path):
    finding = analyze(write_csv(tmp_path, make_row(avg_cpu="3", max_cpu="10", avg_mem="85")))[0]
    assert finding.status == "review"
    assert finding.status not in ("idle", "underused")
    assert "memory" in finding.reason
    assert "CPU alone" in finding.reason
    assert finding.evidence["rule"] == "high_memory"


def test_high_peak_and_high_memory_reports_both(tmp_path):
    finding = analyze(write_csv(tmp_path, make_row(avg_cpu="3", max_cpu="90", avg_mem="85")))[0]
    assert finding.status == "review"
    assert finding.evidence["rule"] == "high_peak_cpu+high_memory"
    assert "peak CPU" in finding.reason and "memory" in finding.reason


def test_high_memory_with_healthy_cpu_is_ok_but_not_a_downsizing_candidate(tmp_path):
    finding = analyze(write_csv(tmp_path, make_row(avg_cpu="50", max_cpu="70", avg_mem="90")))[0]
    assert finding.status == "ok"
    assert "not a downsizing candidate" in finding.reason


def test_idle_and_underused_explanations_are_hedged(tmp_path):
    idle = analyze(write_csv(tmp_path, make_row(avg_cpu="1", max_cpu="5", avg_mem="5")))[0]
    under = analyze(write_csv(tmp_path, make_row(avg_cpu="10", max_cpu="30", avg_mem="30")))[0]
    for finding in (idle, under):
        assert "heuristic" in finding.reason
        assert "safe" not in finding.reason.lower() or "not proof" in finding.reason


# --- classify() compatibility -----------------------------------------------------

def test_classify_keeps_cpu_only_signature():
    assert classify(1.0)[0] == "idle"
    assert classify(10.0)[0] == "underused"
    assert classify(50.0)[0] == "ok"


def test_classify_returns_status_and_reason():
    status, reason = classify(2.0, 9.0, 8.0)
    assert status == "idle" and isinstance(reason, str)


# --- Compatibility with other modules ---------------------------------------------

def test_finding_can_still_be_built_with_original_fields_only():
    finding = Finding(
        resource_id="x", instance_type="m5.large", region="us-east-1", environment="dev",
        avg_cpu_pct=1.0, monthly_hours=730.0, status="idle", reason="r",
    )
    assert finding.evidence == {} and finding.max_cpu_pct is None


def test_fixer_still_accepts_findings_and_ignores_review_and_ok():
    by_id = {f.resource_id: f for f in analyze()}
    assert fixer.suggest_fix(by_id["dev-sandbox-01"])["auto_apply"] is False
    assert fixer.suggest_fix(by_id["batch-01"]) is None   # review
    assert fixer.suggest_fix(by_id["web-01"]) is None     # ok

def test_orchestrator_pipeline_runs_on_default_dataset():
    from contracts import (
        ResourceMetrics,
        TerraformResource,
        ValidationCheck,
        CostCarbonEstimate,
        OptimizationRecommendation,
        GreenOpsReport,
    )

    metrics = [
        ResourceMetrics(
            resource_id=f"resource-{i}",
            instance_type="m5.large",
            region="us-east-1",
            environment="dev",
            avg_cpu_pct=10.0,
            max_cpu_pct=20.0,
            avg_mem_pct=30.0,
            monthly_hours=730.0,
            owner="test-team",
        )
        for i in range(14)
    ]

    terraform_resources = []

    class MockAnalyzer:
        def analyze(self, metrics, terraform_resources):
            return (
                [],
                [ValidationCheck(
                    name="mock_analyzer",
                    status="passed",
                    message="Mock analyzer ran",
                )],
                [],
            )

    class MockCostCarbon:
        def estimate(self, metrics, findings):
            estimates = [
                CostCarbonEstimate(resource_id=m.resource_id)
                for m in metrics
            ]
            return (
                estimates,
                [ValidationCheck(
                    name="mock_cost_carbon",
                    status="passed",
                    message="Mock cost/carbon agent ran",
                )],
                [],
            )

    class MockFixer:
        def recommend(self, metrics, terraform_resources, findings, estimates):
            return (
                [],
                [ValidationCheck(
                    name="mock_fixer",
                    status="passed",
                    message="Mock fixer ran",
                )],
                [],
            )

    result = orchestrator.run_pipeline(
        metrics,
        terraform_resources,
        MockAnalyzer(),
        MockCostCarbon(),
        MockFixer(),
    )

    assert isinstance(result, GreenOpsReport)
    assert len(result.estimates) == 14
    assert len(result.errors) == 0


def test_to_analysis_finding_maps_to_shared_contract():
    finding = analyze()[0]
    converted = analyzer.to_analysis_finding(finding)
    assert isinstance(converted, AnalysisFinding)
    assert converted.resource_id == finding.resource_id
    assert converted.finding_type == finding.status
    assert converted.message == finding.reason
    assert converted.evidence["avg_cpu_pct"] == finding.avg_cpu_pct
    assert converted.severity in {"info", "low", "medium", "high"}