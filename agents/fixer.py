
"""GreenOps Fixer Agent: conservative recommendations and review-only Terraform proposals.

This module never deploys infrastructure, never runs ``terraform apply`` or
``terraform destroy``, and never edits the original Terraform file. A human
must explicitly approve final patch export.
"""

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
 
# Make the GreenOps project root importable (same pattern as the other agents)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
 
from contracts import (  # noqa: E402
    AgentError,
    AnalysisFinding,
    CostCarbonEstimate,
    OptimizationRecommendation,
    ResourceMetrics,
    TerraformResource,
    ValidationCheck,
)
from agents.safety import validate_recommendation, validate_resource_id  # noqa: E402
 
AGENT_NAME = "fixer"
DATA_DIR = PROJECT_ROOT / "data"
 
# Ordered smallest -> largest within each instance family (placeholder ladder).
SIZE_LADDER = {
    "m5": ["m5.large", "m5.xlarge", "m5.2xlarge"],
    "c5": ["c5.xlarge"],
    "r5": ["r5.large"],
    "t3": ["t3.micro", "t3.medium"],
}
 
# --- Tunable guardrails -----------------------------------------------------
# After a downsize, the *projected* load on the smaller instance must stay
# under these limits, otherwise the fixer will not suggest that size.
MAX_PROJECTED_PEAK_CPU_PCT = 80.0
MAX_PROJECTED_AVG_MEM_PCT = 75.0
MAX_STEPS_DOWN = 2  # never jump more than this many sizes in one proposal
 
# Finding types (from the analyzer) that mean "no real work running".
IDLE_TYPES = {"idle", "idle_resource", "unused"}
# Finding types that mean "resource is bigger than it needs to be".
UNDERUSED_TYPES = {
    "underused",
    "underutilized",
    "underutilised",
    "over_provisioned",
    "overprovisioned",
    "right_size",
    "rightsize",
}
 
PROD_ENVIRONMENTS = {"prod", "production"}
 
 
# ---------------------------------------------------------------------------
# Original helpers (kept for backwards compatibility)
# ---------------------------------------------------------------------------
def next_smaller(instance_type):
    """Return the next smaller size in the same family, or None."""
    family = instance_type.split(".")[0]
    ladder = SIZE_LADDER.get(family, [])
    if instance_type in ladder:
        index = ladder.index(instance_type)
        if index > 0:
            return ladder[index - 1]
    return None
 
 
def suggest_fix(finding):
    """Return a human-readable suggestion dict for a Finding, or None if no action."""
    if finding.status == "idle":
        action = "Review for shutdown or schedule it to run only when needed"
    elif finding.status == "underused":
        smaller = next_smaller(finding.instance_type)
        if not smaller:
            return None
        action = "Consider right-sizing from %s to %s" % (finding.instance_type, smaller)
    else:
        return None
 
    return {
        "resource_id": finding.resource_id,
        "status": finding.status,
        "suggestion": action,
        "auto_apply": False,  # suggestions are advisory only
    }
 
 
# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _load_json(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as handle:
        return json.load(handle)
 
 
# ---------------------------------------------------------------------------
# Right-sizing logic
# ---------------------------------------------------------------------------
def _smaller_candidates(instance_type: str) -> List[str]:
    """Sizes below ``instance_type`` in its family, nearest first, capped."""
    family = instance_type.split(".")[0]
    ladder = SIZE_LADDER.get(family, [])
    if instance_type not in ladder:
        return []
    index = ladder.index(instance_type)
    below = ladder[:index][::-1]  # nearest smaller first
    return below[:MAX_STEPS_DOWN]
 
 
def _projected_load(
    metrics: ResourceMetrics, current_spec: dict, target_spec: dict
) -> Tuple[float, float]:
    """Project (peak CPU %, avg memory %) if the workload moved to the target.
 
    Assumes the same absolute work is done on fewer vCPUs / less memory, so
    utilisation scales by the ratio of old to new capacity.
    """
    cpu_ratio = current_spec["vcpu"] / target_spec["vcpu"]
    mem_ratio = current_spec["memory_gib"] / target_spec["memory_gib"]
    return metrics.max_cpu_pct * cpu_ratio, metrics.avg_mem_pct * mem_ratio
 
 
def _pick_smaller_size(
    metrics: ResourceMetrics, instances: dict
) -> Tuple[Optional[str], str]:
    """Return (smallest safe instance type or None, explanation)."""
    current_spec = instances.get(metrics.instance_type)
    if current_spec is None:
        return None, "current instance type %s is not in pricing data" % metrics.instance_type
 
    candidates = _smaller_candidates(metrics.instance_type)
    if not candidates:
        return None, "%s is already the smallest size in its family" % metrics.instance_type
 
    # Walk from the smallest candidate upward; take the first one that fits.
    last_reason = "no smaller size passed the headroom checks"
    for target in reversed(candidates):
        target_spec = instances.get(target)
        if target_spec is None:
            continue
        peak_cpu, avg_mem = _projected_load(metrics, current_spec, target_spec)
        if peak_cpu > MAX_PROJECTED_PEAK_CPU_PCT:
            last_reason = (
                "projected peak CPU on %s would be %.0f%% (limit %.0f%%)"
                % (target, peak_cpu, MAX_PROJECTED_PEAK_CPU_PCT)
            )
            continue
        if avg_mem > MAX_PROJECTED_AVG_MEM_PCT:
            last_reason = (
                "projected memory on %s would be %.0f%% (limit %.0f%%)"
                % (target, avg_mem, MAX_PROJECTED_AVG_MEM_PCT)
            )
            continue
        return target, (
            "projected peak CPU %.0f%% and memory %.0f%% on %s"
            % (peak_cpu, avg_mem, target)
        )
    return None, last_reason
 
 
# ---------------------------------------------------------------------------
# Savings estimates
# ---------------------------------------------------------------------------
def _monthly_cost(
    metrics: ResourceMetrics, instance_type: str, instances: dict
) -> Optional[float]:
    spec = instances.get(instance_type)
    if spec is None:
        return None
    return spec["hourly_usd"] * metrics.monthly_hours
 
 
def _monthly_carbon_kg(
    metrics: ResourceMetrics, instance_type: str, instances: dict, regions: dict
) -> Optional[float]:
    spec = instances.get(instance_type)
    intensity = regions.get(metrics.region, {}).get("carbon_gco2_per_kwh")
    if spec is None or intensity is None:
        return None
    kwh = spec["avg_watts"] * metrics.monthly_hours / 1000.0
    return kwh * intensity / 1000.0
 
 
def _savings(
    metrics: ResourceMetrics,
    target_type: Optional[str],
    estimate: Optional[CostCarbonEstimate],
    instances: dict,
    regions: dict,
) -> Tuple[Optional[float], Optional[float]]:
    """Return (monthly USD saved, monthly kg CO2e saved).
 
    ``target_type=None`` means the resource is stopped entirely, so the saving
    is the whole current cost/carbon. Current figures from the Cost & Carbon
    agent's estimate are preferred; otherwise they are computed from the
    pricing and region data with the same formulas.
    """
    cur_cost = estimate.monthly_cost_usd if estimate else None
    if cur_cost is None:
        cur_cost = _monthly_cost(metrics, metrics.instance_type, instances)
 
    cur_kg = estimate.monthly_carbon_kg if estimate else None
    if cur_kg is None:
        cur_kg = _monthly_carbon_kg(metrics, metrics.instance_type, instances, regions)
 
    if target_type is None:
        new_cost, new_kg = 0.0, 0.0
    else:
        new_cost = _monthly_cost(metrics, target_type, instances)
        new_kg = _monthly_carbon_kg(metrics, target_type, instances, regions)
 
    cost_saved = None
    if cur_cost is not None and new_cost is not None:
        cost_saved = round(cur_cost - new_cost, 2)
    kg_saved = None
    if cur_kg is not None and new_kg is not None:
        kg_saved = round(cur_kg - new_kg, 3)
    return cost_saved, kg_saved
 
 
# ---------------------------------------------------------------------------
# Main entry point (FixerInterface)
# ---------------------------------------------------------------------------
def recommend(
    metrics: List[ResourceMetrics],
    terraform_resources: List[TerraformResource],
    findings: List[AnalysisFinding],
    estimates: List[CostCarbonEstimate],
    pricing: Optional[dict] = None,
    regions: Optional[dict] = None,
) -> Tuple[List[OptimizationRecommendation], List[ValidationCheck], List[AgentError]]:
    """Turn analyzer findings into proposed (never applied) changes.
 
    ``pricing`` / ``regions`` are loaded from ``data/`` when not supplied;
    pass them in for tests or to avoid re-reading the files.
    """
    recommendations: List[OptimizationRecommendation] = []
    checks: List[ValidationCheck] = []
    errors: List[AgentError] = []
 
    try:
        instances = (pricing or _load_json("pricing.json"))["instances"]
        region_data = (regions or _load_json("regions.json"))["regions"]
    except (OSError, KeyError, ValueError) as exc:
        errors.append(
            AgentError(agent=AGENT_NAME, message="Could not load pricing/region data: %s" % exc)
        )
        return recommendations, checks, errors
 
    metrics_by_id: Dict[str, ResourceMetrics] = {m.resource_id: m for m in metrics}
    tf_by_id: Dict[str, TerraformResource] = {t.resource_id: t for t in terraform_resources}
    estimate_by_id: Dict[str, CostCarbonEstimate] = {e.resource_id: e for e in estimates}
    known_ids = list(metrics_by_id)
 
    handled = set()  # one recommendation per resource, even with several findings
 
    for finding in findings:
        rid = finding.resource_id
        ftype = finding.finding_type.strip().lower()
 
        if ftype not in IDLE_TYPES and ftype not in UNDERUSED_TYPES:
            continue  # nothing the fixer acts on (e.g. "ok", "info")
        if rid in handled:
            continue
 
        id_check = validate_resource_id(rid, known_ids)
        checks.append(id_check)
        if id_check.status == "failed":
            errors.append(
                AgentError(
                    agent=AGENT_NAME,
                    resource_id=rid,
                    message="Finding refers to an unknown resource; skipped.",
                )
            )
            continue
 
        m = metrics_by_id[rid]
        tf = tf_by_id.get(rid)
 
        # Cross-check against Terraform so we never propose a change for
        # something that doesn't match what is actually declared.
        if tf is None:
            checks.append(ValidationCheck(
                name="terraform_match",
                status="failed",
                message="[%s] No matching Terraform resource found; recommendation skipped." % rid,
            ))
            errors.append(AgentError(
                agent=AGENT_NAME,
                resource_id=rid,
                message="No matching Terraform resource; cannot safely recommend a configuration change.",
            ))
            continue
        elif tf.instance_type != m.instance_type:
            checks.append(ValidationCheck(
                name="terraform_match",
                status="failed",
                message="[%s] Terraform says %s but metrics say %s; skipped until reconciled."
                % (rid, tf.instance_type, m.instance_type),
            ))
            continue
        else:
            checks.append(ValidationCheck(
                name="terraform_match",
                status="passed",
                message="[%s] Matches Terraform resource %s." % (rid, tf.resource_address),
            ))
 
        # Do not silently ignore a disagreement between metrics and Terraform tags.
        metrics_env = (m.environment or "").strip().lower()
        terraform_env = (tf.environment or "").strip().lower()
        if terraform_env and not any(token in terraform_env for token in ("${", "var.", "local.")):
            if metrics_env and terraform_env != metrics_env:
                checks.append(ValidationCheck(
                    name="environment_match",
                    status="failed",
                    message="[%s] Metrics environment is '%s' but Terraform environment is '%s'; skipped until reconciled."
                    % (rid, m.environment, tf.environment),
                ))
                errors.append(AgentError(
                    agent=AGENT_NAME,
                    resource_id=rid,
                    message="Metrics/Terraform environment mismatch; recommendation skipped.",
                ))
                continue
            checks.append(ValidationCheck(
                name="environment_match",
                status="passed",
                message="[%s] Metrics and Terraform environments agree (%s)." % (rid, tf.environment),
            ))
        else:
            checks.append(ValidationCheck(
                name="environment_match",
                status="skipped",
                message="[%s] Terraform environment is missing or unresolved; metrics environment is '%s'. Verify environment before acting."
                % (rid, m.environment or "unknown"),
            ))
            if metrics_env in PROD_ENVIRONMENTS:
                errors.append(AgentError(
                    agent=AGENT_NAME,
                    resource_id=rid,
                    message="Production environment could not be confirmed in Terraform; recommendation skipped.",
                ))
                continue

        where = " (Terraform: %s)" % tf.resource_address
        is_prod = metrics_env in PROD_ENVIRONMENTS
        estimate = estimate_by_id.get(rid)
 
        if ftype in IDLE_TYPES:
            if is_prod:
                # Never suggest stopping a production resource outright.
                rec = OptimizationRecommendation(
                    resource_id=rid,
                    current_instance_type=m.instance_type,
                    suggested_instance_type=None,
                    reason=(
                        "Average CPU %.1f%% suggests this production resource is idle%s. "
                        "Confirm with owner '%s' before any change; do not stop it automatically."
                        % (m.avg_cpu_pct, where, m.owner)
                    ),
                )
            else:
                cost, kg = _savings(m, None, estimate, instances, region_data)
                rec = OptimizationRecommendation(
                    resource_id=rid,
                    current_instance_type=m.instance_type,
                    suggested_instance_type=None,
                    reason=(
                        "Average CPU %.1f%% in %s: review for shutdown or schedule it to run "
                        "only when needed%s. Owner: %s. Savings shown are the upper bound if "
                        "fully stopped." % (m.avg_cpu_pct, m.environment, where, m.owner)
                    ),
                    estimated_monthly_savings_usd=cost,
                    estimated_monthly_carbon_reduction_kg=kg,
                )
        else:  # underused -> right-size
            target, why = _pick_smaller_size(m, instances)
            if target is None:
                checks.append(ValidationCheck(
                    name="right_size_headroom",
                    status="skipped",
                    message="[%s] No safe downsize: %s." % (rid, why),
                ))
                continue
            checks.append(ValidationCheck(
                name="right_size_headroom",
                status="passed",
                message="[%s] Downsize to %s looks safe: %s." % (rid, target, why),
            ))
            cost, kg = _savings(m, target, estimate, instances, region_data)
            note = (
                " Production resource: schedule a maintenance window and have a rollback plan."
                if is_prod else ""
            )
            rec = OptimizationRecommendation(
                resource_id=rid,
                current_instance_type=m.instance_type,
                suggested_instance_type=target,
                reason=(
                    "Average CPU %.1f%%, peak %.1f%%, memory %.1f%%: right-size %s -> %s%s. "
                    "Projected: %s.%s Owner: %s."
                    % (m.avg_cpu_pct, m.max_cpu_pct, m.avg_mem_pct, m.instance_type,
                       target, where, why, note, m.owner)
                ),
                estimated_monthly_savings_usd=cost,
                estimated_monthly_carbon_reduction_kg=kg,
            )
 
        # Run the shared safety rules on the finished proposal.
        if rec.suggested_instance_type is not None:
            rec_checks = [
                ValidationCheck(name=c.name, status=c.status, message="[%s] %s" % (rid, c.message))
                for c in validate_recommendation(rec)
            ]
        else:
            # Idle recommendations are review-only, not instance-type changes.
            rec_checks = [validate_resource_id(rec.resource_id, known_ids)]
            if not rec.requires_human_approval:
                rec_checks.append(ValidationCheck(
                    name="human_approval", status="failed",
                    message="[%s] Review-only recommendations must require human approval." % rid,
                ))
            else:
                rec_checks.append(ValidationCheck(
                    name="human_approval", status="passed",
                    message="[%s] Human approval is required; no automatic action will occur." % rid,
                ))
            rec_checks = [
                ValidationCheck(name=c.name, status=c.status, message="[%s] %s" % (rid, c.message))
                for c in rec_checks
            ]
        checks.extend(rec_checks)
        if any(c.status == "failed" for c in rec_checks):
            errors.append(
                AgentError(
                    agent=AGENT_NAME,
                    resource_id=rid,
                    message="Recommendation failed safety validation; dropped.",
                )
            )
            continue
 
        recommendations.append(rec)
        handled.add(rid)
 
    # Biggest savings first; recommendations with no number go last.
    recommendations.sort(key=lambda r: r.estimated_monthly_savings_usd or 0.0, reverse=True)
    return recommendations, checks, errors
 
 
# ===========================================================================
# Optimised Terraform generation (proposal only - never applied)
# ===========================================================================
_RESOURCE_RE = re.compile(
    r'^[ \t]*resource\s+"(?P<type>[^"]+)"\s+"(?P<name>[^"]+)"\s*\{', re.MULTILINE
)
_INSTANCE_TYPE_RE = re.compile(r'(?P<pre>\binstance_type\s*=\s*")(?P<val>[^"]*)(?P<post>")')
_NAME_TAG_RE = re.compile(r'\bName\s*=\s*"(?P<val>[^"]*)"')
PROPOSAL_SUFFIX = ".tf.proposed"  # Terraform ignores this, so it can't clash with main.tf
 
 
def _matching_brace(text: str, open_idx: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``open_idx`` (-1 if unbalanced).
 
    Skips braces inside quoted strings, line comments, and ``/* ... */`` block comments.
    This is a lightweight scanner, not a full HCL parser.
    """
    depth, i, n = 0, open_idx, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "#" or text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end_comment = text.find("*/", i + 2)
            if end_comment == -1:
                return -1
            i = end_comment + 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1
 
 
def _find_resource_blocks(text: str) -> Dict[str, Tuple[int, int, int]]:
    """Map ``type.name`` -> (resource line start, block start, block end)."""
    blocks = {}
    for match in _RESOURCE_RE.finditer(text):
        open_idx = match.end() - 1
        close_idx = _matching_brace(text, open_idx)
        if close_idx != -1:
            blocks["%s.%s" % (match.group("type"), match.group("name"))] = (
                match.start(), open_idx, close_idx + 1
            )
    return blocks
 
 
def _locate_block(
    text: str,
    blocks: Dict[str, Tuple[int, int, int]],
    rec: OptimizationRecommendation,
    tf_by_id: Dict[str, TerraformResource],
) -> Optional[str]:
    """Find the block address for a recommendation: by Terraform address, else Name tag."""
    tf = tf_by_id.get(rec.resource_id)
    if tf and tf.resource_address in blocks:
        return tf.resource_address
    for address, (_, start, end) in blocks.items():
        name = _NAME_TAG_RE.search(text[start:end])
        if name and name.group("val") == rec.resource_id:
            return address
    return None
 
 
def _newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"
 
 
def _check_diff_scope(old: str, new: str) -> ValidationCheck:
    """Prove the proposal only changed instance_type values and added comments."""
    old_lines, new_lines = old.splitlines(), new.splitlines()
    problems = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            if not all(not l.strip() or l.strip().startswith("#") for l in new_lines[j1:j2]):
                problems.append("non-comment line inserted")
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            for o, n in zip(old_lines[i1:i2], new_lines[j1:j2]):
                mo, mn = _INSTANCE_TYPE_RE.search(o), _INSTANCE_TYPE_RE.search(n)
                if not (mo and mn):
                    problems.append("unexpected line change")
                    continue
                mask = lambda line, m: line[: m.start("val")] + "X" + line[m.end("val"):]
                strip = lambda line: line.split("#", 1)[0].rstrip()
                if strip(mask(o, mo)) != strip(mask(n, mn)):
                    problems.append("change beyond instance_type value")
        else:
            problems.append("lines deleted or reshaped")
    if problems:
        return ValidationCheck(
            name="terraform_diff_scope", status="failed",
            message="Proposal changes more than allowed: %s." % ", ".join(sorted(set(problems))),
        )
    return ValidationCheck(
        name="terraform_diff_scope", status="passed",
        message="Proposal only changes instance_type values and adds comments.",
    )
 
 
def generate_optimized_terraform(
    terraform_source: str,
    recommendations: List[OptimizationRecommendation],
    terraform_resources: Optional[List[TerraformResource]] = None,
) -> Tuple[str, str, List[ValidationCheck], List[AgentError]]:
    """Build a *proposed* Terraform file that applies the fixer's recommendations.
 
    Returns ``(proposed_text, unified_diff, checks, errors)``. The input text is
    never modified and nothing is written to disk.
 
    What changes:
      * right-size recommendations -> the ``instance_type`` value is replaced and
        a ``# GreenOps: was X`` comment is added on that line;
      * idle / review-only recommendations -> a ``# GreenOps review`` comment is
        placed above the resource; no setting is changed (stopping or scheduling
        a resource is a human decision);
      * a header comment states this is a proposal and totals the estimated savings.
    """
    checks: List[ValidationCheck] = []
    errors: List[AgentError] = []
    nl = _newline_of(terraform_source)
    tf_by_id = {t.resource_id: t for t in (terraform_resources or [])}
 
    # Collect edits first, then apply bottom-to-top so earlier offsets stay valid.
    blocks = _find_resource_blocks(terraform_source)
    edits = []  # (offset, kind, payload)
    applied = []
 
    for rec in recommendations:
        rid = rec.resource_id
        address = _locate_block(terraform_source, blocks, rec, tf_by_id)
        if address is None:
            checks.append(ValidationCheck(
                name="terraform_edit", status="failed",
                message="[%s] No matching resource block found in Terraform; not edited." % rid,
            ))
            errors.append(AgentError(agent=AGENT_NAME, resource_id=rid,
                                     message="Could not locate Terraform block."))
            continue
 
        line_start, open_idx, end = blocks[address]
        block_text = terraform_source[open_idx:end]
        indent = re.match(r"[ \t]*", terraform_source[line_start:]).group(0)
 
        if rec.suggested_instance_type:
            match = _INSTANCE_TYPE_RE.search(block_text)
            if not match or match.group("val") != rec.current_instance_type:
                found = match.group("val") if match else "no instance_type"
                checks.append(ValidationCheck(
                    name="terraform_edit", status="failed",
                    message="[%s] %s has %s, expected %s; not edited."
                    % (rid, address, found, rec.current_instance_type),
                ))
                errors.append(AgentError(agent=AGENT_NAME, resource_id=rid,
                                         message="Terraform instance_type does not match recommendation."))
                continue
            val_start = open_idx + match.start("val")
            val_end = open_idx + match.end("val")
            line_end = terraform_source.find("\n", val_end)
            line_end = len(terraform_source) if line_end == -1 else line_end
            rest = terraform_source[val_end + 1:line_end].strip()  # after the closing quote
            note = "  # GreenOps: was %s" % rec.current_instance_type if not rest else ""
            edits.append((val_start, "replace", (val_end, rec.suggested_instance_type)))
            if note:
                insert_at = val_end + 1  # just after the closing quote
                edits.append((insert_at, "insert", note))
            applied.append(rec)
            checks.append(ValidationCheck(
                name="terraform_edit", status="passed",
                message="[%s] %s: instance_type %s -> %s."
                % (rid, address, rec.current_instance_type, rec.suggested_instance_type),
            ))
        else:
            parts = ["GreenOps review: %s looks idle/low-use" % rid]
            if rec.estimated_monthly_savings_usd is not None:
                parts.append("potential saving up to $%.2f/month" % rec.estimated_monthly_savings_usd)
            parts.append("no change made - confirm with the owner first")
            edits.append((line_start, "insert", indent + "# " + "; ".join(parts) + nl))
            applied.append(rec)
            checks.append(ValidationCheck(
                name="terraform_edit", status="passed",
                message="[%s] %s: review comment added, configuration unchanged." % (rid, address),
            ))
 
    text = terraform_source
    for offset, kind, payload in sorted(edits, key=lambda e: (e[0], e[1] != "insert"), reverse=True):
        if kind == "replace":
            val_end, new_val = payload
            text = text[:offset] + new_val + text[val_end:]
        else:
            text = text[:offset] + payload + text[offset:]
 
    # Header goes on last, so it never disturbs the offsets above.
    total_usd = sum(r.estimated_monthly_savings_usd or 0.0 for r in applied if r.suggested_instance_type)
    total_kg = sum(r.estimated_monthly_carbon_reduction_kg or 0.0 for r in applied if r.suggested_instance_type)
    header = nl.join([
        "# ----------------------------------------------------------------------",
        "# GreenOps PROPOSED configuration - generated for human review, NOT applied.",
        "# Right-sizing changes: %d | est. saving $%.2f/month | est. %.2f kg CO2e/month"
        % (sum(1 for r in applied if r.suggested_instance_type), total_usd, total_kg),
        "# Run `terraform plan` and get owner approval before using any of this.",
        "# ----------------------------------------------------------------------",
        "",
    ])
    text = header + nl + text
 
    checks.append(_check_diff_scope(terraform_source, text))
    diff = "".join(difflib.unified_diff(
        terraform_source.splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile="main.tf (current)", tofile="main.tf (GreenOps proposal)",
    ))
    return text, diff, checks, errors
 
 
def propose_terraform_file(
    terraform_path: str,
    recommendations: List[OptimizationRecommendation],
    terraform_resources: Optional[List[TerraformResource]] = None,
) -> Tuple[str, str, List[ValidationCheck], List[AgentError]]:
    """Read a ``.tf`` file (read-only) and return the same tuple as ``generate_optimized_terraform``."""
    from agents.safety import validate_terraform_path
 
    path_check = validate_terraform_path(terraform_path)
    if path_check.status == "failed":
        return "", "", [path_check], [AgentError(agent=AGENT_NAME, message=path_check.message)]
    with open(terraform_path, newline="", encoding="utf-8") as handle:  # newline="" keeps CRLF
        source = handle.read()
    text, diff, checks, errors = generate_optimized_terraform(source, recommendations, terraform_resources)
    return text, diff, [path_check] + checks, errors
 
 
def write_proposal(proposed_text: str, output_path: str) -> ValidationCheck:
    """Save a review-only candidate to a NEW ``*.tf.proposed`` file.

    This is not a final patch and is not applied by Terraform. The suffix is
    intentional: Terraform loads ``*.tf`` files, so a second ``.tf`` file in
    the same directory could duplicate resource declarations. Existing files
    are never overwritten.
    """
    path = Path(output_path)
    if not path.name.endswith(PROPOSAL_SUFFIX):
        return ValidationCheck(
            name="write_proposal", status="failed",
            message="Output file must end with %s." % PROPOSAL_SUFFIX,
        )
    if not proposed_text.strip():
        return ValidationCheck(
            name="write_proposal", status="failed",
            message="Proposal is empty; nothing was written.",
        )
    if path.exists():
        return ValidationCheck(
            name="write_proposal", status="failed",
            message="%s already exists; refusing to overwrite." % path,
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create means an existing file cannot be overwritten.
        with open(path, "x", newline="", encoding="utf-8") as handle:
            handle.write(proposed_text)
    except OSError as exc:
        return ValidationCheck(
            name="write_proposal", status="failed",
            message="Could not write proposal: %s" % exc,
        )
    return ValidationCheck(
        name="write_proposal", status="passed",
        message="Wrote review-only proposal to %s. No infrastructure was changed." % path,
    )


def export_patch(
    unified_diff: str,
    output_path: str,
    approved: bool = False,
) -> ValidationCheck:
    """Export a final diff only after explicit human approval.

    The calling UI/workflow must collect deliberate human approval and then
    pass ``approved=True``. This function does not deploy code or run Terraform.
    Existing patch files are never overwritten.
    """
    path = Path(output_path)
    if approved is not True:
        return ValidationCheck(
            name="patch_export_approval", status="failed",
            message="Patch not exported: explicit human approval is required (approved=True).",
        )
    if path.suffix.lower() not in {".patch", ".diff"}:
        return ValidationCheck(
            name="patch_export_path", status="failed",
            message="Patch output must use a .patch or .diff extension.",
        )
    if not unified_diff.strip():
        return ValidationCheck(
            name="patch_export_content", status="failed",
            message="Patch is empty; nothing was exported.",
        )
    if path.exists():
        return ValidationCheck(
            name="patch_export_path", status="failed",
            message="%s already exists; refusing to overwrite." % path,
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", newline="", encoding="utf-8") as handle:
            handle.write(unified_diff)
    except OSError as exc:
        return ValidationCheck(
            name="patch_export_write", status="failed",
            message="Could not export patch: %s" % exc,
        )
    return ValidationCheck(
        name="patch_export", status="passed",
        message="Approved patch exported to %s. It has NOT been applied." % path,
    )


def candidate_validation_summary(checks: List[ValidationCheck]) -> ValidationCheck:
    """Summarize in-code checks without claiming actual Terraform validation.

    These checks inspect matching resources and diff scope only. They do not
    replace ``terraform fmt`` or ``terraform validate``; those should be run
    manually in an isolated copy before anyone uses a patch. Never run apply
    or destroy as part of validation.
    """
    failed = [c.name for c in checks if c.status == "failed"]
    skipped = [c.name for c in checks if c.status == "skipped"]
    if failed:
        return ValidationCheck(
            name="candidate_validation_summary", status="failed",
            message="Candidate has failed checks: %s. Do not export/use it until resolved."
            % ", ".join(sorted(set(failed))),
        )
    if skipped:
        return ValidationCheck(
            name="candidate_validation_summary", status="skipped",
            message="Some checks were skipped: %s. This is not Terraform syntax validation."
            % ", ".join(sorted(set(skipped))),
        )
    return ValidationCheck(
        name="candidate_validation_summary", status="passed",
        message="In-code safety/scope checks passed. Actual Terraform fmt/validate has NOT been run.",
    )
