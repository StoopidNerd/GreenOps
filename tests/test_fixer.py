"""Focused tests for the GreenOps Fixer Agent.

Run from the project root:
    python3 -m unittest discover -s tests -p "test_fixer.py" -v

These tests use synthetic inputs. They do not connect to cloud services or run Terraform.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from contracts import (
    AnalysisFinding,
    CostCarbonEstimate,
    OptimizationRecommendation,
    ResourceMetrics,
    TerraformResource,
    ValidationCheck,
)
from agents import fixer


def sample_metrics(
    resource_id="web-01",
    instance_type="m5.xlarge",
    environment="dev",
    avg_cpu_pct=15.0,
    max_cpu_pct=30.0,
    avg_mem_pct=20.0,
):
    return ResourceMetrics(
        resource_id=resource_id,
        instance_type=instance_type,
        region="us-east-1",
        environment=environment,
        avg_cpu_pct=avg_cpu_pct,
        max_cpu_pct=max_cpu_pct,
        avg_mem_pct=avg_mem_pct,
        monthly_hours=730,
        owner="platform-team",
    )


def sample_tf(
    resource_id="web-01",
    instance_type="m5.xlarge",
    environment="dev",
    address="aws_instance.web_01",
):
    return TerraformResource(
        resource_address=address,
        resource_id=resource_id,
        instance_type=instance_type,
        region="us-east-1",
        environment=environment,
        owner="platform-team",
    )


def sample_finding(resource_id="web-01", finding_type="underused"):
    return AnalysisFinding(
        resource_id=resource_id,
        finding_type=finding_type,
        severity="medium",
        message="Synthetic test finding",
        evidence={},
    )


TEST_PRICING = {
    "instances": {
        "m5.large": {"vcpu": 2, "memory_gib": 8, "hourly_usd": 0.096, "avg_watts": 18},
        "m5.xlarge": {"vcpu": 4, "memory_gib": 16, "hourly_usd": 0.192, "avg_watts": 34},
        "m5.2xlarge": {"vcpu": 8, "memory_gib": 32, "hourly_usd": 0.384, "avg_watts": 66},
        "c5.xlarge": {"vcpu": 4, "memory_gib": 8, "hourly_usd": 0.170, "avg_watts": 32},
        "r5.large": {"vcpu": 2, "memory_gib": 16, "hourly_usd": 0.126, "avg_watts": 22},
        "t3.micro": {"vcpu": 2, "memory_gib": 1, "hourly_usd": 0.0104, "avg_watts": 6},
        "t3.medium": {"vcpu": 2, "memory_gib": 4, "hourly_usd": 0.0416, "avg_watts": 10},
    }
}
TEST_REGIONS = {"regions": {"us-east-1": {"carbon_gco2_per_kwh": 380}}}


class TestFixerHelpers(unittest.TestCase):
    def test_next_smaller_returns_previous_size(self):
        self.assertEqual(fixer.next_smaller("m5.2xlarge"), "m5.xlarge")
        self.assertEqual(fixer.next_smaller("m5.xlarge"), "m5.large")

    def test_smallest_or_unknown_size_has_no_smaller_option(self):
        self.assertIsNone(fixer.next_smaller("m5.large"))
        self.assertIsNone(fixer.next_smaller("unknown.large"))

    def test_legacy_suggest_fix_is_advisory(self):
        finding = SimpleNamespace(
            status="underused",
            instance_type="m5.xlarge",
            resource_id="web-01",
        )
        result = fixer.suggest_fix(finding)
        self.assertEqual(result["resource_id"], "web-01")
        self.assertIn("m5.xlarge", result["suggestion"])
        self.assertFalse(result["auto_apply"])


class TestFixerRecommendations(unittest.TestCase):
    def call_recommend(self, metrics, terraform_resources, findings):
        return fixer.recommend(
            metrics=metrics,
            terraform_resources=terraform_resources,
            findings=findings,
            estimates=[CostCarbonEstimate(resource_id="web-01", monthly_cost_usd=140.16,
                                          monthly_carbon_kg=9.4)],
            pricing=TEST_PRICING,
            regions=TEST_REGIONS,
        )

    def test_underused_resource_gets_safe_downsize_recommendation(self):
        recommendations, checks, errors = self.call_recommend(
            [sample_metrics()], [sample_tf()], [sample_finding()]
        )
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0].suggested_instance_type, "m5.large")
        self.assertTrue(recommendations[0].requires_human_approval)
        self.assertFalse(any(e.resource_id == "web-01" for e in errors))

    def test_missing_terraform_resource_is_skipped(self):
        recommendations, checks, errors = self.call_recommend(
            [sample_metrics()], [], [sample_finding()]
        )
        self.assertEqual(recommendations, [])
        self.assertTrue(any(c.name == "terraform_match" and c.status == "failed" for c in checks))
        self.assertTrue(any("Terraform resource" in e.message for e in errors))

    def test_terraform_instance_type_mismatch_is_skipped(self):
        recommendations, checks, errors = self.call_recommend(
            [sample_metrics(instance_type="m5.xlarge")],
            [sample_tf(instance_type="m5.2xlarge")],
            [sample_finding()],
        )
        self.assertEqual(recommendations, [])
        self.assertTrue(any(c.name == "terraform_match" and c.status == "failed" for c in checks))

    def test_environment_mismatch_is_skipped(self):
        recommendations, checks, errors = self.call_recommend(
            [sample_metrics(environment="prod")],
            [sample_tf(environment="dev")],
            [sample_finding()],
        )
        self.assertEqual(recommendations, [])
        self.assertTrue(any(c.name == "environment_match" and c.status == "failed" for c in checks))

    def test_unsafe_downsize_is_not_recommended(self):
        recommendations, checks, errors = self.call_recommend(
            [sample_metrics(max_cpu_pct=70.0, avg_mem_pct=20.0)],
            [sample_tf()],
            [sample_finding()],
        )
        self.assertEqual(recommendations, [])
        self.assertTrue(any(c.name == "right_size_headroom" and c.status == "skipped" for c in checks))


class TestTerraformProposalSafety(unittest.TestCase):
    def setUp(self):
        self.source = '''resource "aws_instance" "web_01" {
  ami           = "ami-example"
  instance_type = "m5.xlarge"
  tags = {
    Name        = "web-01"
    Environment = "dev"
  }
}
'''
        self.recommendation = OptimizationRecommendation(
            resource_id="web-01",
            current_instance_type="m5.xlarge",
            suggested_instance_type="m5.large",
            reason="Synthetic low-utilization test",
            estimated_monthly_savings_usd=70.08,
            estimated_monthly_carbon_reduction_kg=4.5,
        )
        self.tf_resources = [sample_tf()]

    def test_proposal_changes_candidate_not_original_string(self):
        original = self.source
        proposed, diff, checks, errors = fixer.generate_optimized_terraform(
            self.source, [self.recommendation], self.tf_resources
        )
        self.assertEqual(self.source, original)
        self.assertIn('instance_type = "m5.large"', proposed)
        self.assertIn("m5.xlarge", diff)
        self.assertTrue(any(c.name == "terraform_diff_scope" for c in checks))
        self.assertEqual(errors, [])

    def test_write_proposal_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "main.tf.proposed"
            first = fixer.write_proposal("# proposal\n", str(path))
            second = fixer.write_proposal("# changed proposal\n", str(path))
            self.assertEqual(first.status, "passed")
            self.assertEqual(second.status, "failed")
            self.assertEqual(path.read_text(encoding="utf-8"), "# proposal\n")

    def test_patch_export_requires_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "greenops.patch"
            result = fixer.export_patch("--- old\n+++ new\n", str(path), approved=False)
            self.assertEqual(result.status, "failed")
            self.assertFalse(path.exists())

    def test_patch_exports_only_when_approved_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "greenops.patch"
            patch_text = "--- old\n+++ new\n"
            result = fixer.export_patch(patch_text, str(path), approved=True)
            self.assertEqual(result.status, "passed")
            self.assertEqual(path.read_text(encoding="utf-8"), patch_text)

            second = fixer.export_patch("different patch\n", str(path), approved=True)
            self.assertEqual(second.status, "failed")
            self.assertEqual(path.read_text(encoding="utf-8"), patch_text)

    def test_validation_summary_does_not_claim_real_terraform_validation(self):
        result = fixer.candidate_validation_summary([
            ValidationCheck(
                name="terraform_diff_scope",
                status="passed",
                message="In-code check passed",
            )
        ])
        self.assertEqual(result.status, "passed")
        self.assertIn("Terraform fmt/validate has NOT been run", result.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
