"""Smoke tests for the placeholder agents. Run with: python -m pytest"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import analyzer, carbon_agent, fixer, orchestrator


class TestAnalyzer(unittest.TestCase):
    def test_classify_thresholds(self):
        self.assertEqual(analyzer.classify(1.0)[0], "idle")
        self.assertEqual(analyzer.classify(10.0)[0], "underused")
        self.assertEqual(analyzer.classify(50.0)[0], "ok")

    def test_sample_data_loads(self):
        findings = analyzer.analyze()
        self.assertGreater(len(findings), 0)


class TestCarbonAgent(unittest.TestCase):
    def test_known_instance_returns_positive(self):
        kg = carbon_agent.estimate_emissions_kg("m5.large", "us-east-1", 730)
        self.assertGreater(kg, 0)

    def test_unknown_instance_returns_zero(self):
        self.assertEqual(carbon_agent.estimate_emissions_kg("nope.large", "us-east-1", 730), 0.0)


class TestFixer(unittest.TestCase):
    def test_next_smaller(self):
        self.assertEqual(fixer.next_smaller("m5.2xlarge"), "m5.xlarge")
        self.assertIsNone(fixer.next_smaller("m5.large"))



class TestOrchestrator(unittest.TestCase):
    def test_pipeline_runs(self):
        from contracts import (
            AgentError,
            AnalysisFinding,
            CostCarbonEstimate,
            OptimizationRecommendation,
            ResourceMetrics,
            TerraformResource,
            ValidationCheck,
            GreenOpsReport,
        )

        metrics = [
            ResourceMetrics(
                resource_id="web-01",
                instance_type="m5.xlarge",
                region="us-east-1",
                environment="dev",
                avg_cpu_pct=10.0,
                max_cpu_pct=30.0,
                avg_mem_pct=20.0,
                monthly_hours=730,
                owner="platform-team",
            )
        ]

        terraform_resources = [
            TerraformResource(
                resource_address="aws_instance.web_01",
                resource_id="web-01",
                instance_type="m5.xlarge",
                region="us-east-1",
                environment="dev",
                owner="platform-team",
            )
        ]

        class MockAnalyzer:
            def analyze(self, metrics, terraform_resources):
                findings = [
                    AnalysisFinding(
                        resource_id="web-01",
                        finding_type="underused",
                        severity="medium",
                        message="Low average utilization",
                        evidence={"avg_cpu_pct": 10.0},
                    )
                ]
                return findings, [
                    ValidationCheck(
                        name="mock_analyzer",
                        status="passed",
                        message="Mock Analyzer completed",
                    )
                ], []

        class MockCostCarbon:
            def estimate(self, metrics, findings):
                estimates = [
                    CostCarbonEstimate(
                        resource_id="web-01",
                        monthly_cost_usd=140.16,
                        monthly_energy_kwh=24.82,
                        monthly_carbon_kg=9.43,
                        assumptions=["Synthetic test estimates"],
                    )
                ]
                return estimates, [
                    ValidationCheck(
                        name="mock_cost_carbon",
                        status="passed",
                        message="Mock Cost & Carbon completed",
                    )
                ], []

        class MockFixer:
            def recommend(
                self, metrics, terraform_resources, findings, estimates
            ):
                recommendations = [
                    OptimizationRecommendation(
                        resource_id="web-01",
                        current_instance_type="m5.xlarge",
                        suggested_instance_type="m5.large",
                        reason="Synthetic test recommendation",
                        estimated_monthly_savings_usd=70.08,
                        estimated_monthly_carbon_reduction_kg=4.5,
                        requires_human_approval=True,
                    )
                ]
                return recommendations, [
                    ValidationCheck(
                        name="mock_fixer",
                        status="passed",
                        message="Mock Fixer completed",
                    )
                ], []

        result = orchestrator.run_pipeline(
            metrics,
            terraform_resources,
            MockAnalyzer(),
            MockCostCarbon(),
            MockFixer(),
        )

        self.assertIsInstance(result, GreenOpsReport)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(len(result.estimates), 1)
        self.assertEqual(len(result.recommendations), 1)
        self.assertEqual(
            result.recommendations[0].suggested_instance_type,
            "m5.large",
        )
        self.assertEqual(result.errors, [])


if __name__ == "__main__":
    unittest.main()
