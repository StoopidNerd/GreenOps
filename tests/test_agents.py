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
        result = orchestrator.run_pipeline()
        self.assertIn("summary", result)
        self.assertEqual(result["summary"]["resources"], len(result["findings"]))


if __name__ == "__main__":
    unittest.main()
