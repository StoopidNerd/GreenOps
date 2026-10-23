
"""
Shared contracts for the GreenOps Agent project.

These data structures define how agents exchange information.
They do not connect to cloud accounts or modify infrastructure.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal


# Allowed statuses for validation checks
CheckStatus = Literal["passed", "failed", "skipped"]


@dataclass
class ResourceMetrics:
    """Utilization information for one resource from the CSV."""

    resource_id: str
    instance_type: str
    region: str
    environment: str
    avg_cpu_pct: float
    max_cpu_pct: float
    avg_mem_pct: float
    monthly_hours: float
    owner: str


@dataclass
class TerraformResource:
    """Relevant details extracted from a Terraform resource."""

    resource_address: str
    resource_id: str
    instance_type: str
    region: Optional[str] = None
    environment: Optional[str] = None
    owner: Optional[str] = None


@dataclass
class AnalysisFinding:
    """A potential issue or optimization opportunity."""

    resource_id: str
    finding_type: str
    severity: Literal["info", "low", "medium", "high"]
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CostCarbonEstimate:
    """Estimated cost and carbon impact for a resource."""

    resource_id: str
    monthly_cost_usd: Optional[float] = None
    monthly_energy_kwh: Optional[float] = None
    monthly_carbon_kg: Optional[float] = None
    assumptions: List[str] = field(default_factory=list)


@dataclass
class OptimizationRecommendation:
    """A proposed change. This is not an instruction to deploy it."""

    resource_id: str
    current_instance_type: str
    suggested_instance_type: Optional[str]
    reason: str
    estimated_monthly_savings_usd: Optional[float] = None
    estimated_monthly_carbon_reduction_kg: Optional[float] = None
    requires_human_approval: bool = True


@dataclass
class ValidationCheck:
    """Records the result of a validation or safety check."""

    name: str
    status: CheckStatus
    message: str


@dataclass
class AgentError:
    """A structured record of an agent error."""

    agent: str
    message: str
    resource_id: Optional[str] = None


@dataclass
class GreenOpsReport:
    """Consolidated output produced by the Orchestrator."""

    findings: List[AnalysisFinding] = field(default_factory=list)
    estimates: List[CostCarbonEstimate] = field(default_factory=list)
    recommendations: List[OptimizationRecommendation] = field(
        default_factory=list
    )
    validation_checks: List[ValidationCheck] = field(default_factory=list)
    errors: List[AgentError] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
