"""GreenOps Stage 6 dashboard.

Uses local synthetic sample data only. This UI displays analysis results and
recommendations; it does not approve, export, or apply infrastructure changes.
The Stage 5 approval workflow can be connected here when its interface is merged.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from agents.orchestrator import report_to_dict
from agents.runtime import run_sample_pipeline

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"


st.set_page_config(
    page_title="GreenOps | Cloud Sustainability",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# A restrained visual style: readable cards, clear hierarchy, and no false
# implication that a recommendation has been approved or applied.
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; padding-bottom: 2.5rem; max-width: 1500px;}
      .greenops-eyebrow {font-size: .78rem; font-weight: 700; letter-spacing: .12em;
                         text-transform: uppercase; color: #198754; margin-bottom: .3rem;}
      .greenops-subtitle {color: #64748b; font-size: 1rem; margin-top: -.4rem;}
      div[data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.22);
          padding: 14px 16px; border-radius: 14px; background: rgba(128,128,128,.035);}
      .greenops-note {border-left: 4px solid #198754; padding: .7rem 1rem;
          background: rgba(25,135,84,.07); border-radius: 0 8px 8px 0;}
    </style>
    """,
    unsafe_allow_html=True,
)


def as_records(value: Any) -> list[dict]:
    """Return a list of dictionary rows, tolerating absent/invalid report fields."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def number(value: Any) -> float | None:
    """Convert a report value to a finite float, or None when unavailable."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if pd.notna(parsed) else None


def total(rows: list[dict], field: str) -> float:
    return sum(number(row.get(field)) or 0.0 for row in rows)


def money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def decimal(value: float | None, suffix: str = "") -> str:
    return "—" if value is None else f"{value:,.2f}{suffix}"


def flatten_findings(findings: list[dict]) -> list[dict]:
    flattened = []
    for item in findings:
        evidence = item.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        flattened.append(
            {
                "Resource": item.get("resource_id", "—"),
                "Finding": item.get("finding_type", "—"),
                "Severity": item.get("severity", "—"),
                "Environment": evidence.get("environment", "—"),
                "Instance type": evidence.get("instance_type", "—"),
                "Avg CPU (%)": evidence.get("avg_cpu_pct"),
                "Peak CPU (%)": evidence.get("max_cpu_pct"),
                "Avg memory (%)": evidence.get("avg_mem_pct"),
                "Owner": evidence.get("owner", "—"),
                "Explanation": item.get("message", ""),
            }
        )
    return flattened


def flatten_estimates(estimates: list[dict]) -> list[dict]:
    return [
        {
            "Resource": item.get("resource_id", "—"),
            "Monthly cost (USD)": item.get("monthly_cost_usd"),
            "Energy (kWh/month)": item.get("monthly_energy_kwh"),
            "Carbon (kg CO₂e/month)": item.get("monthly_carbon_kg"),
            "Assumptions": "; ".join(item.get("assumptions") or []),
        }
        for item in estimates
    ]


def flatten_recommendations(recommendations: list[dict]) -> list[dict]:
    rows = []
    for item in recommendations:
        requires_approval = item.get("requires_human_approval", True)
        rows.append(
            {
                "Resource": item.get("resource_id", "—"),
                "Current type": item.get("current_instance_type", "—"),
                "Suggested type": item.get("suggested_instance_type") or "Review manually",
                "Reason": item.get("reason", ""),
                "Est. monthly savings (USD)": item.get("estimated_monthly_savings_usd"),
                "Est. carbon reduction (kg CO₂e/month)": item.get(
                    "estimated_monthly_carbon_reduction_kg"
                ),
                "Approval required": "Yes" if requires_approval else "Check policy",
                "Approval status": item.get("approval_status", "Pending human review"),
            }
        )
    return rows


def status_counts(checks: list[dict]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for check in checks:
        status = check.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def build_savings_comparison(recommendations: list[dict]) -> tuple[list[dict], list[str]]:
    """Calculate transparent before/after scenarios from local synthetic assumptions.

    This is a scenario model, not a live cloud bill or guaranteed savings forecast.
    A recommendation with no target type is treated as a shutdown upper-bound only
    when the Fixer supplied savings estimates (its non-production idle scenario).
    """
    import csv

    pricing_path = DATA_DIR / "pricing.json"
    regions_path = DATA_DIR / "regions.json"
    metrics_path = DATA_DIR / "utilisation_backup.csv"
    assumptions: list[str] = []

    try:
        with pricing_path.open("r", encoding="utf-8") as handle:
            pricing = json.load(handle)
        with regions_path.open("r", encoding="utf-8") as handle:
            region_data = json.load(handle).get("regions", {})
        with metrics_path.open("r", encoding="utf-8", newline="") as handle:
            metrics = {row["resource_id"]: row for row in csv.DictReader(handle)}
    except (OSError, ValueError, KeyError) as exc:
        return [], [f"Could not load pricing, region or utilization assumptions: {exc}"]

    instances = pricing.get("instances", {})
    currency = pricing.get("currency", "USD")
    rows: list[dict] = []
    for rec in recommendations:
        resource_id = rec.get("resource_id")
        metric = metrics.get(resource_id)
        current_type = rec.get("current_instance_type")
        target_type = rec.get("suggested_instance_type")
        if not metric:
            assumptions.append(f"{resource_id}: no utilization row; comparison omitted.")
            continue

        try:
            hours = float(metric["monthly_hours"])
        except (TypeError, ValueError, KeyError):
            assumptions.append(f"{resource_id}: invalid monthly_hours; comparison omitted.")
            continue

        current_spec = instances.get(current_type)
        if not isinstance(current_spec, dict):
            assumptions.append(f"{resource_id}: current type {current_type!r} is not priced; comparison omitted.")
            continue

        region = metric.get("region", "")
        region_entry = region_data.get(region, {})
        intensity_grams = region_entry.get("carbon_gco2_per_kwh") if isinstance(region_entry, dict) else None
        if isinstance(intensity_grams, (int, float)) and not isinstance(intensity_grams, bool) and intensity_grams >= 0:
            intensity_kg = float(intensity_grams) / 1000.0
            intensity_source = f"regions.json synthetic factor ({intensity_grams:g} gCO₂e/kWh)"
        else:
            intensity_kg = 0.4
            intensity_source = "fallback assumption (0.4 kg CO₂e/kWh; region missing)"

        before_cost = float(current_spec["hourly_usd"]) * hours
        before_energy = float(current_spec["avg_watts"]) * hours / 1000.0
        before_carbon = before_energy * intensity_kg

        if target_type:
            target_spec = instances.get(target_type)
            if not isinstance(target_spec, dict):
                assumptions.append(f"{resource_id}: target type {target_type!r} is not priced; comparison omitted.")
                continue
            after_cost = float(target_spec["hourly_usd"]) * hours
            after_energy = float(target_spec["avg_watts"]) * hours / 1000.0
            after_carbon = after_energy * intensity_kg
            scenario = f"Right-size {current_type} → {target_type}"
            scenario_assumption = "Assumes the workload runs the same monthly hours on the suggested type."
        elif rec.get("estimated_monthly_savings_usd") is not None:
            # Only show this as a shutdown upper-bound if the Fixer itself supplied
            # a savings estimate. Do not infer shutdown for production review-only recs.
            after_cost = 0.0
            after_energy = 0.0
            after_carbon = 0.0
            scenario = "Full shutdown upper-bound scenario"
            scenario_assumption = "Assumes the resource is off for all recorded hours; not an automatic action or guaranteed saving."
        else:
            assumptions.append(f"{resource_id}: review-only recommendation has no modeled after-state; no savings claimed.")
            continue

        monthly_savings = before_cost - after_cost
        monthly_carbon_reduction = before_carbon - after_carbon
        savings_pct = (monthly_savings / before_cost * 100.0) if before_cost > 0 else None
        rows.append({
            "Resource": resource_id,
            "Scenario": scenario,
            "Current type": current_type,
            "Proposed type": target_type or "Shutdown upper-bound (review only)",
            "Current monthly cost (USD)": round(before_cost, 2),
            "Scenario monthly cost (USD)": round(after_cost, 2),
            "Potential monthly savings (USD)": round(monthly_savings, 2),
            "Potential annualized gross savings (USD)": round(monthly_savings * 12, 2),
            "Cost reduction (%)": round(savings_pct, 1) if savings_pct is not None else None,
            "Current energy (kWh/month)": round(before_energy, 3),
            "Scenario energy (kWh/month)": round(after_energy, 3),
            "Potential carbon reduction (kg CO₂e/month)": round(monthly_carbon_reduction, 3),
            "Carbon factor source": intensity_source,
            "Assumption": scenario_assumption,
            "Currency": currency,
        })

    return rows, assumptions



def build_memory_comparison(
    recommendations: list[dict], findings: list[dict]
) -> tuple[list[dict], list[str]]:
    """Build a transparent memory scenario from explicit recs or safe candidates.

    If the Fixer did not emit a target instance, an illustrative candidate is
    generated only for underused findings, using synthetic specs and conservative
    average-memory/peak-CPU headroom checks. This is not an applied change.
    """
    import csv

    pricing_path = DATA_DIR / "pricing.json"
    metrics_path = DATA_DIR / "utilisation_backup.csv"
    notes: list[str] = []
    try:
        with pricing_path.open("r", encoding="utf-8") as handle:
            pricing = json.load(handle)
        with metrics_path.open("r", encoding="utf-8", newline="") as handle:
            metrics = {str(row.get("resource_id", "")).strip(): row for row in csv.DictReader(handle)}
    except (OSError, ValueError, KeyError) as exc:
        return [], [f"Could not load memory assumptions: {exc}"]

    instances = pricing.get("instances", {})
    if not isinstance(instances, dict) or not instances:
        return [], ["data/pricing.json has no usable 'instances' dictionary."]

    # Normalize the incoming report so field-name differences do not silently
    # hide usable recommendations. Findings provide the observed utilization.
    rec_by_id: dict[str, dict] = {}
    for rec in recommendations:
        rid = str(rec.get("resource_id") or "").strip()
        if rid:
            rec_by_id[rid] = rec

    finding_by_id: dict[str, dict] = {}
    for finding in findings:
        rid = str(finding.get("resource_id") or "").strip()
        if rid:
            finding_by_id[rid] = finding

    resource_ids = list(dict.fromkeys(list(rec_by_id) + list(finding_by_id)))
    rows: list[dict] = []
    for rid in resource_ids:
        rec = rec_by_id.get(rid, {})
        finding = finding_by_id.get(rid, {})
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
        metric = metrics.get(rid, {})

        current_type = (rec.get("current_instance_type") or rec.get("instance_type")
                        or evidence.get("instance_type") or metric.get("instance_type"))
        target_type = (rec.get("suggested_instance_type") or rec.get("target_instance_type")
                       or rec.get("recommended_instance_type") or rec.get("suggested_type"))
        current_spec = instances.get(current_type, {}) if current_type else {}
        if not isinstance(current_spec, dict) or number(current_spec.get("memory_gib")) is None:
            notes.append(f"{rid}: current instance type {current_type!r} is missing from pricing.json.")
            continue

        mem_pct = number(evidence.get("avg_mem_pct"))
        if mem_pct is None:
            mem_pct = number(metric.get("avg_mem_pct"))
        cpu_peak = number(evidence.get("max_cpu_pct"))
        if cpu_peak is None:
            cpu_peak = number(metric.get("max_cpu_pct"))
        if mem_pct is None or not 0 <= mem_pct <= 100:
            notes.append(f"{rid}: average memory utilization is missing or invalid; excluded.")
            continue

        # Only infer a candidate when the analyzer explicitly marks the resource
        # underused. Idle resources need a separate shutdown/scheduling review.
        finding_type = str(finding.get("finding_type") or finding.get("status") or "").lower()
        if not target_type and finding_type in {
            "underused", "underutilized", "underutilised", "over_provisioned",
            "overprovisioned", "right_size", "rightsize"
        }:
            current_mem = float(current_spec["memory_gib"])
            estimated_used = current_mem * mem_pct / 100.0
            current_vcpu = number(current_spec.get("vcpu")) or 0
            candidates = []
            for candidate, spec in instances.items():
                if not isinstance(spec, dict) or candidate == current_type:
                    continue
                candidate_mem = number(spec.get("memory_gib"))
                candidate_vcpu = number(spec.get("vcpu"))
                if candidate_mem is None or candidate_vcpu is None:
                    continue
                # Keep average memory <=75% and projected peak CPU <=80%.
                projected_mem_pct = (estimated_used / candidate_mem * 100.0) if candidate_mem else 999
                projected_cpu = (cpu_peak * current_vcpu / candidate_vcpu) if cpu_peak is not None and candidate_vcpu else None
                if candidate_mem < current_mem and projected_mem_pct <= 75 and (projected_cpu is None or projected_cpu <= 80):
                    candidates.append((candidate_mem, candidate))
            if candidates:
                target_type = min(candidates)[1]
                notes.append(f"{rid}: proposed type {target_type} is an illustrative candidate inferred from the underused signal; verify peak/headroom before acting.")

        if not target_type:
            notes.append(f"{rid}: no right-sizing target was emitted and no safe underused candidate could be inferred.")
            continue

        target_spec = instances.get(target_type, {})
        if not isinstance(target_spec, dict) or number(target_spec.get("memory_gib")) is None:
            notes.append(f"{rid}: proposed type {target_type!r} is missing from pricing.json.")
            continue

        current_mem = float(current_spec["memory_gib"])
        target_mem = float(target_spec["memory_gib"])
        estimated_used = current_mem * mem_pct / 100.0
        before_unused = max(current_mem - estimated_used, 0.0)
        after_unused = max(target_mem - estimated_used, 0.0)
        target_risk = estimated_used > target_mem
        rows.append({
            "Resource": rid,
            "Current instance": current_type,
            "Proposed instance": target_type,
            "Average memory utilization (%)": round(mem_pct, 1),
            "Current allocated memory (GiB)": round(current_mem, 2),
            "Estimated workload memory used (GiB)": round(estimated_used, 2),
            "Proposed allocated memory (GiB)": round(target_mem, 2),
            "Capacity released (GiB)": round(current_mem - target_mem, 2),
            "Estimated unused before (GiB)": round(before_unused, 2),
            "Estimated unused after (GiB)": round(after_unused, 2),
            "Unused capacity reduction (%)": round((before_unused - after_unused) / before_unused * 100, 1) if before_unused > 0 else None,
            "Capacity check": "REVIEW: proposed memory below estimated current use" if target_risk else "Average-use check passed; peak headroom still needs validation",
            "Scenario type": "Existing recommendation" if (rec.get("suggested_instance_type") or rec.get("target_instance_type") or rec.get("recommended_instance_type") or rec.get("suggested_type")) else "Illustrative candidate — not an approved recommendation",
        })

    return rows, notes

def run_pipeline() -> None:
    try:
        with st.spinner("Analyzing synthetic resource data…"):
            report = run_sample_pipeline()
            st.session_state["greenops_report"] = report_to_dict(report)
            st.session_state["greenops_last_run"] = datetime.now().astimezone().strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
            st.session_state.pop("greenops_run_error", None)
    except Exception as exc:
        st.session_state["greenops_run_error"] = f"{type(exc).__name__}: {exc}"


# Sidebar: clear scope and a single, explicit way to rerun the analysis.
with st.sidebar:
    st.markdown("## 🌱 GreenOps")
    st.caption("Cloud cost & sustainability intelligence")
    st.divider()
    st.markdown("**Analysis controls**")
    if st.button("↻ Run analysis", type="primary", use_container_width=True):
        run_pipeline()
    if "greenops_last_run" in st.session_state:
        st.caption(f"Last run: {st.session_state['greenops_last_run']}")
    st.divider()
    st.markdown("**Prototype scope**")
    st.caption("Local CSV + synthetic pricing and carbon assumptions.")
    st.caption("No cloud account is connected.")
    st.caption("No Terraform changes are applied.")
    st.caption("Recommendations are not approvals.")


st.markdown('<div class="greenops-eyebrow">Cloud sustainability workspace</div>', unsafe_allow_html=True)
st.title("GreenOps dashboard")
st.markdown(
    '<div class="greenops-subtitle">Find waste signals, understand estimated impact, '
    'and review recommendations before any infrastructure change.</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="greenops-note"><strong>Safety by design:</strong> this dashboard uses '
    'synthetic sample data. All cost, energy, and carbon figures are estimates. '
    'No changes are deployed automatically.</div>',
    unsafe_allow_html=True,
)

if "greenops_report" not in st.session_state and "greenops_run_error" not in st.session_state:
    run_pipeline()

if st.session_state.get("greenops_run_error"):
    st.error("The analysis could not complete.")
    st.code(st.session_state["greenops_run_error"])
    st.info("Check the terminal traceback and verify the sample data files are present.")
    st.stop()

report = st.session_state.get("greenops_report")
if not isinstance(report, dict):
    st.info("Click **Run analysis** in the sidebar to generate the first report.")
    st.stop()

findings = as_records(report.get("findings"))
estimates = as_records(report.get("estimates"))
recommendations = as_records(report.get("recommendations"))
checks = as_records(report.get("validation_checks"))
errors = as_records(report.get("errors"))
notes = report.get("notes") if isinstance(report.get("notes"), list) else []

finding_rows = flatten_findings(findings)
estimate_rows = flatten_estimates(estimates)
recommendation_rows = flatten_recommendations(recommendations)

cost_total = total(estimates, "monthly_cost_usd")
energy_total = total(estimates, "monthly_energy_kwh")
carbon_total = total(estimates, "monthly_carbon_kg")
potential_savings = total(recommendations, "estimated_monthly_savings_usd")
potential_carbon_reduction = total(
    recommendations, "estimated_monthly_carbon_reduction_kg"
)

review_count = sum(
    str(item.get("finding_type", "")).lower() in {"idle", "underused", "review"}
    for item in findings
)
approval_count = sum(
    bool(item.get("requires_human_approval", True)) for item in recommendations
)
counts = status_counts(checks)

# Top-level summary.
st.subheader("Executive overview")
metric_cols = st.columns(5)
metric_cols[0].metric("Resources analyzed", f"{len(findings):,}")
metric_cols[1].metric("Signals to review", f"{review_count:,}")
metric_cols[2].metric("Est. monthly cost", money(cost_total))
metric_cols[3].metric("Est. monthly carbon", f"{carbon_total:,.2f} kg")
metric_cols[4].metric("Recommendations", f"{len(recommendations):,}")

st.caption(
    f"Estimates cover {len(estimates)} resource record(s). "
    f"Potential savings shown below are illustrative scenarios, not guaranteed outcomes."
)

overview_tab, findings_tab, impact_tab, roi_tab, memory_tab, recommendations_tab, validation_tab = st.tabs(
    [
        "Overview",
        "Resource findings",
        "Cost & carbon",
        "Savings & ROI",
        "Before vs After Memory",
        "Recommendations & approval",
        "Validation & errors",
    ]
)

with overview_tab:
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("### What needs attention?")
        if finding_rows:
            summary_df = pd.DataFrame(finding_rows)
            summary = (
                summary_df.groupby("Finding", dropna=False)
                .size()
                .reset_index(name="Resources")
                .sort_values("Resources", ascending=False)
            )
            st.bar_chart(summary.set_index("Finding"), height=260)
        else:
            st.info("No findings were returned by the Analyzer.")
    with right:
        st.markdown("### Validation health")
        health_cols = st.columns(3)
        health_cols[0].metric("Passed", counts["passed"])
        health_cols[1].metric("Failed", counts["failed"])
        health_cols[2].metric("Skipped", counts["skipped"])
        if counts["failed"]:
            st.warning(
                f"{counts['failed']} validation check(s) failed. Review the Validation & errors tab."
            )
        elif counts["skipped"]:
            st.info(
                f"{counts['skipped']} check(s) were skipped. Review their reasons before acting."
            )
        else:
            st.success("No failed or skipped validation checks were reported.")

    st.markdown("### Largest estimated resource costs")
    if estimate_rows:
        cost_df = pd.DataFrame(estimate_rows)
        cost_df["Monthly cost (USD)"] = pd.to_numeric(
            cost_df["Monthly cost (USD)"], errors="coerce"
        )
        cost_df = cost_df.dropna(subset=["Monthly cost (USD)"]).sort_values(
            "Monthly cost (USD)", ascending=False
        ).head(8)
        if not cost_df.empty:
            st.bar_chart(
                cost_df.set_index("Resource")[["Monthly cost (USD)"]],
                height=280,
            )
        else:
            st.info("Cost values are unavailable for charting.")
    else:
        st.info("No cost estimates are available.")

with findings_tab:
    st.markdown("### Resource utilization findings")
    if not finding_rows:
        st.info("No resource findings are available.")
    else:
        findings_df = pd.DataFrame(finding_rows)
        filter_cols = st.columns(2)
        finding_types = sorted(findings_df["Finding"].dropna().astype(str).unique())
        severities = sorted(findings_df["Severity"].dropna().astype(str).unique())
        selected_types = filter_cols[0].multiselect(
            "Filter by finding", finding_types, default=finding_types
        )
        selected_severities = filter_cols[1].multiselect(
            "Filter by severity", severities, default=severities
        )
        filtered = findings_df[
            findings_df["Finding"].astype(str).isin(selected_types)
            & findings_df["Severity"].astype(str).isin(selected_severities)
        ]
        st.dataframe(filtered, use_container_width=True, hide_index=True)
        st.download_button(
            "Download findings CSV",
            data=filtered.to_csv(index=False).encode("utf-8"),
            file_name="greenops_findings.csv",
            mime="text/csv",
        )

with impact_tab:
    st.markdown("### Estimated cost, energy and carbon")
    impact_cols = st.columns(3)
    impact_cols[0].metric("Monthly cost estimate", money(cost_total))
    impact_cols[1].metric("Monthly energy estimate", f"{energy_total:,.2f} kWh")
    impact_cols[2].metric("Monthly carbon estimate", f"{carbon_total:,.2f} kg CO₂e")

    st.caption(
        "Values use local synthetic pricing, power assumptions and regional carbon factors. "
        "They are not live cloud billing or verified emissions measurements."
    )

    if estimate_rows:
        estimates_df = pd.DataFrame(estimate_rows)
        st.dataframe(estimates_df, use_container_width=True, hide_index=True)
        if "Monthly cost (USD)" in estimates_df:
            chart_df = estimates_df.copy()
            chart_df["Monthly cost (USD)"] = pd.to_numeric(
                chart_df["Monthly cost (USD)"], errors="coerce"
            )
            chart_df = chart_df.dropna(subset=["Monthly cost (USD)"]).sort_values(
                "Monthly cost (USD)", ascending=False
            ).head(10)
            if not chart_df.empty:
                st.markdown("#### Cost by resource")
                st.bar_chart(
                    chart_df.set_index("Resource")[["Monthly cost (USD)"]],
                    height=280,
                )
    else:
        st.info("No cost/carbon estimates were returned.")

    if recommendation_rows:
        st.markdown("### Illustrative recommendation impact")
        savings_cols = st.columns(2)
        savings_cols[0].metric("Potential monthly savings", money(potential_savings))
        savings_cols[1].metric(
            "Potential monthly carbon reduction",
            f"{potential_carbon_reduction:,.2f} kg CO₂e",
        )
        st.caption(
            "These figures are sums of recommendation estimates where available. "
            "They may be incomplete and are not guaranteed savings."
        )

with roi_tab:
    st.markdown("### Before-and-after savings scenarios")
    st.caption(
        "Scenario estimates use local synthetic pricing and recorded monthly hours. "
        "They are not live cloud prices, guaranteed savings, or approval to make a change."
    )
    comparison_rows, comparison_notes = build_savings_comparison(recommendations)

    if not comparison_rows:
        st.info("No recommendations have a calculable before/after scenario yet. Review-only recommendations are intentionally not assigned fictional savings.")
    else:
        comparison_df = pd.DataFrame(comparison_rows)
        monthly_before = float(comparison_df["Current monthly cost (USD)"].sum())
        monthly_after = float(comparison_df["Scenario monthly cost (USD)"].sum())
        monthly_savings = monthly_before - monthly_after
        annualized_savings = monthly_savings * 12
        monthly_carbon_reduction = float(comparison_df["Potential carbon reduction (kg CO₂e/month)"].sum())
        cost_reduction_pct = (monthly_savings / monthly_before * 100.0) if monthly_before > 0 else None

        roi_cols = st.columns(4)
        roi_cols[0].metric("Current monthly cost", money(monthly_before))
        roi_cols[1].metric("Scenario monthly cost", money(monthly_after))
        roi_cols[2].metric("Potential monthly savings", money(monthly_savings), delta=f"{cost_reduction_pct:.1f}% cost reduction" if cost_reduction_pct is not None else None)
        roi_cols[3].metric("Annualized gross savings", money(annualized_savings))
        st.metric("Potential monthly carbon reduction", f"{monthly_carbon_reduction:,.3f} kg CO₂e")

        st.markdown("#### Compare each proposed scenario")
        chart_df = comparison_df.set_index("Resource")[["Current monthly cost (USD)", "Scenario monthly cost (USD)"]]
        st.bar_chart(chart_df, height=300)
        st.dataframe(comparison_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download before/after comparison CSV",
            data=comparison_df.to_csv(index=False).encode("utf-8"),
            file_name="greenops_before_after_comparison.csv",
            mime="text/csv",
        )

        st.markdown("#### Optional first-year ROI scenario")
        implementation_cost = st.number_input(
            "Estimated one-time implementation cost (USD)",
            min_value=0.0,
            value=0.0,
            step=100.0,
            help="Enter your own estimated labor/migration/testing cost. This is an assumption, not a value GreenOps can infer.",
        )
        if implementation_cost > 0:
            first_year_net_benefit = annualized_savings - implementation_cost
            first_year_roi = first_year_net_benefit / implementation_cost * 100.0
            roi_cols = st.columns(2)
            roi_cols[0].metric("First-year net benefit estimate", money(first_year_net_benefit))
            roi_cols[1].metric("First-year ROI estimate", f"{first_year_roi:,.1f}%")
            if monthly_savings > 0:
                st.caption(f"Simple payback estimate: {implementation_cost / monthly_savings:,.1f} month(s), assuming the monthly savings scenario is realized consistently.")
            else:
                st.caption("Payback is not meaningful because this scenario does not produce positive monthly savings.")
        else:
            st.info("Enter a one-time implementation cost above to calculate an illustrative first-year ROI. Until then, GreenOps reports gross savings potential only—not an ROI percentage.")

    with st.expander("How the savings and ROI estimates are calculated"):
        st.markdown(
            """
            - **Current monthly cost** = synthetic hourly price × `monthly_hours` from `data/utilisation_backup.csv`.
            - **Right-sized scenario cost** = target synthetic hourly price × the same recorded monthly hours.
            - **Energy estimate** = synthetic average watts × monthly hours ÷ 1,000.
            - **Carbon estimate** = estimated kWh × regional synthetic carbon intensity from `data/regions.json`. If the region is absent, a clearly labelled 0.4 kg CO₂e/kWh fallback is used.
            - **Potential monthly savings** = current estimated cost − scenario estimated cost. A negative value means the scenario costs more under these assumptions.
            - **Annualized gross savings** = potential monthly savings × 12. This assumes the same scenario and usage persist for a full year.
            - **First-year ROI** = (annualized gross savings − user-entered one-time implementation cost) ÷ implementation cost × 100. It excludes ongoing operating costs, taxes, price changes, workload changes, and migration risk.
            - **Shutdown upper bound** is shown only when the Fixer supplied a savings estimate for that idle resource. It assumes zero usage/cost for all recorded hours and is not a recommendation to stop a resource automatically.
            - **No actual deployment occurs.** All recommendations still require human approval; these are synthetic development estimates, not verified cloud quotes or emissions measurements.
            """
        )

    if comparison_notes:
        with st.expander("Items excluded from the comparison"):
            for note in comparison_notes:
                st.write(f"- {note}")



with memory_tab:
    st.markdown("### Before vs after: memory capacity and estimated waste")
    st.caption(
        "This comparison uses average memory utilization from the sample utilization data "
        "and memory capacity from synthetic instance specifications. It estimates unused "
        "allocated capacity; it is not a direct measurement of physical RAM or a production guarantee."
    )
    memory_rows, memory_notes = build_memory_comparison(recommendations, findings)
    if not memory_rows:
        st.info(
            "No valid before/after memory scenario could be calculated. GreenOps only infers an "
            "illustrative smaller instance for findings explicitly marked underused and only when "
            "the synthetic memory and CPU headroom checks pass."
        )
        if memory_notes:
            with st.expander("Why resources were excluded"):
                for note in memory_notes:
                    st.write(f"- {note}")
    else:
        memory_df = pd.DataFrame(memory_rows)
        total_before_mem = float(memory_df["Current allocated memory (GiB)"].sum())
        total_after_mem = float(memory_df["Proposed allocated memory (GiB)"].sum())
        total_before_unused = float(memory_df["Estimated unused before (GiB)"].sum())
        total_after_unused = float(memory_df["Estimated unused after (GiB)"].sum())
        total_capacity_released = total_before_mem - total_after_mem
        unused_reduction_pct = (
            (total_before_unused - total_after_unused) / total_before_unused * 100.0
            if total_before_unused > 0 else None
        )
        mem_cols = st.columns(4)
        mem_cols[0].metric("Allocated memory before", f"{total_before_mem:,.2f} GiB")
        mem_cols[1].metric("Allocated memory after", f"{total_after_mem:,.2f} GiB")
        mem_cols[2].metric("Capacity released", f"{total_capacity_released:,.2f} GiB")
        mem_cols[3].metric(
            "Estimated unused-capacity reduction",
            f"{unused_reduction_pct:,.1f}%" if unused_reduction_pct is not None else "—",
        )

        st.markdown("#### The visible difference")
        chart_cols = st.columns(2)
        with chart_cols[0]:
            st.markdown("**Allocated memory by resource (GiB)**")
            allocated_chart = memory_df.set_index("Resource")[
                ["Current allocated memory (GiB)", "Proposed allocated memory (GiB)"]
            ]
            st.bar_chart(allocated_chart, height=300)
        with chart_cols[1]:
            st.markdown("**Estimated unused capacity (GiB)**")
            unused_chart = memory_df.set_index("Resource")[
                ["Estimated unused before (GiB)", "Estimated unused after (GiB)"]
            ]
            st.bar_chart(unused_chart, height=300)

        st.markdown("#### Resource-level evidence and proposed change")
        st.dataframe(memory_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download memory before/after CSV",
            data=memory_df.to_csv(index=False).encode("utf-8"),
            file_name="greenops_memory_before_after.csv",
            mime="text/csv",
        )
        unsafe = memory_df[memory_df["Capacity check"].str.startswith("REVIEW:")]
        if not unsafe.empty:
            st.warning(
                f"{len(unsafe)} proposed change(s) have less memory than the estimated average "
                "workload use. Do not present these as safe savings; review/reject or resize the target."
            )

    with st.expander("How memory waste is estimated"):
        st.markdown(
            """
            - **Estimated workload memory used** = current instance memory capacity × recorded average memory utilization (%).
            - **Estimated unused capacity before** = current capacity − estimated workload memory used.
            - **Estimated unused capacity after** = max(proposed capacity − estimated workload memory used, 0).
            - **Capacity released** = current allocated capacity − proposed allocated capacity. A negative number means the proposed type allocates more memory.
            - This is a simplified average-utilization estimate. Peak usage, safety headroom, workload changes, cache behavior, and real cloud telemetry are not modeled. A proposed target below estimated current use is flagged for review.
            - All values rely on synthetic instance specifications and sample data; they are not measured production savings.
            """
        )
    if memory_notes:
        with st.expander("Memory comparison notes"):
            for note in memory_notes:
                st.write(f"- {note}")


with recommendations_tab:
    st.markdown("### Proposed optimizations")
    st.caption(
        "Recommendations are suggestions only. Review the rationale and data alignment "
        "before approving any future configuration change."
    )
    if recommendation_rows:
        rec_df = pd.DataFrame(recommendation_rows)
        st.dataframe(rec_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download recommendations CSV",
            data=rec_df.to_csv(index=False).encode("utf-8"),
            file_name="greenops_recommendations.csv",
            mime="text/csv",
        )
        st.info(
            f"{approval_count} recommendation(s) require human approval. "
            "Approval/rejection actions are intentionally not simulated here; "
            "connect the Stage 5 approval workflow when your teammate's implementation is ready."
        )
    else:
        st.success("No optimization recommendations were generated for this run.")
        st.caption("This may mean no eligible findings were found, or safety checks blocked changes.")

with validation_tab:
    st.markdown("### Data and safety validation")
    if checks:
        check_df = pd.DataFrame(
            [
                {
                    "Check": item.get("name", "—"),
                    "Status": item.get("status", "—"),
                    "Details": item.get("message", ""),
                }
                for item in checks
            ]
        )
        st.dataframe(check_df, use_container_width=True, hide_index=True)

        alignment = [
            item for item in checks if item.get("name") == "terraform_data_alignment"
        ]
        if alignment and any(item.get("status") == "failed" for item in alignment):
            st.warning(
                "CSV and Terraform data do not fully align. Do not approve affected "
                "recommendations until the mismatches have been reviewed."
            )
    else:
        st.info("No validation checks were returned.")

    if errors:
        st.markdown("### Agent errors")
        st.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)
    else:
        st.success("No runtime agent errors were reported.")

    if notes:
        st.markdown("### Run notes")
        for note in notes:
            st.write(f"- {note}")

# A reproducible artifact for debugging, judging, or future integration.
st.divider()
with st.expander("View raw report JSON"):
    st.json(report)

st.download_button(
    "Download full report JSON",
    data=json.dumps(report, indent=2, ensure_ascii=False, default=str),
    file_name="greenops_report.json",
    mime="application/json",
)
