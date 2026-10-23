
"""Input/output contracts for GreenOps agents.

These interfaces describe the data each agent accepts and returns.
Agent logic will be implemented separately.
"""

from typing import Protocol, List
import sys
from pathlib import Path

# Make the GreenOps project root available for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from contracts import (
    ResourceMetrics,
    TerraformResource,
    AnalysisFinding,
    CostCarbonEstimate,
    OptimizationRecommendation,
    ValidationCheck,
    AgentError,
)


class AnalyzerInterface(Protocol):
    """Analyze utilization data and Terraform configuration."""

    def analyze(
        self,
        metrics: List[ResourceMetrics],
        terraform_resources: List[TerraformResource],
    ) -> tuple[List[AnalysisFinding], List[ValidationCheck], List[AgentError]]:
        ...


class CostCarbonInterface(Protocol):
    """Estimate cost and carbon impact for analyzed resources."""

    def estimate(
        self,
        metrics: List[ResourceMetrics],
        findings: List[AnalysisFinding],
    ) -> tuple[List[CostCarbonEstimate], List[ValidationCheck], List[AgentError]]:
        ...


class FixerInterface(Protocol):
    """Create proposed optimization recommendations."""

    def recommend(
        self,
        metrics: List[ResourceMetrics],
        terraform_resources: List[TerraformResource],
        findings: List[AnalysisFinding],
        estimates: List[CostCarbonEstimate],
    ) -> tuple[
        List[OptimizationRecommendation],
        List[ValidationCheck],
        List[AgentError],
    ]:
        ...
