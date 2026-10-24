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
from agents.runtime import run_sample_pipeline, parse_terraform_resources
from agents.fixer import generate_optimized_terraform
from agents.approval import record_decision, approval_matches
from agents.approval_workflow import export_with_approval
from contracts import OptimizationRecommendation

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


def run_pipeline() -> None:
    try:
        with st.spinner("Analyzing synthetic resource data…"):
            report = run_sample_pipeline()
            st.session_state["greenops_report"] = report_to_dict(report)
            st.session_state["greenops_last_run"] = datetime.now().astimezone().strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
            # A fresh analysis must never inherit an approval from an older proposal.
            for key in ("greenops_approval", "greenops_approval_diff", "greenops_approval_status", "greenops_approved_patch"):
                st.session_state.pop(key, None)
            st.session_state.pop("greenops_run_error", None)
    except Exception as exc:
        st.session_state["greenops_run_error"] = f"{type(exc).__name__}: {exc}"


def build_proposal(report: dict) -> tuple[str, str, list[dict], list[dict]]:
    """Build an in-memory Terraform proposal; never edits the source file."""
    recs = as_records(report.get("recommendations"))
    if not recs:
        return "", "", [], []
    typed_recs = [
        OptimizationRecommendation(
            resource_id=str(item.get("resource_id", "")),
            current_instance_type=str(item.get("current_instance_type", "")),
            suggested_instance_type=item.get("suggested_instance_type"),
            reason=str(item.get("reason", "")),
            estimated_monthly_savings_usd=number(item.get("estimated_monthly_savings_usd")),
            estimated_monthly_carbon_reduction_kg=number(item.get("estimated_monthly_carbon_reduction_kg")),
            requires_human_approval=True,
        )
        for item in recs
    ]
    tf_path = PROJECT_ROOT / "sample_infra" / "main.tf"
    if not tf_path.is_file():
        return "", "", [], [{"agent": "approval", "message": f"Terraform sample not found: {tf_path}"}]
    source = tf_path.read_text(encoding="utf-8")
    resources = parse_terraform_resources(tf_path)
    proposed_text, diff, checks_out, errors_out = generate_optimized_terraform(
        source, typed_recs, resources
    )
    check_rows = [
        {"name": c.name, "status": c.status, "message": c.message}
        for c in checks_out
    ]
    error_rows = [
        {"agent": e.agent, "resource_id": e.resource_id, "message": e.message}
        for e in errors_out
    ]
    return proposed_text, diff, check_rows, error_rows


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

overview_tab, findings_tab, impact_tab, roi_tab, recommendations_tab, validation_tab = st.tabs(
    [
        "Overview",
        "Resource findings",
        "Cost & carbon",
        "Savings & ROI",
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


with recommendations_tab:
    st.markdown("### Proposed optimizations")
    st.caption(
        "Review each suggestion before exporting a patch. Approval is tied to the exact proposal; "
        "no cloud changes are applied by this dashboard."
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

        st.divider()
        st.markdown("### Human approval")
        st.write(
            "GreenOps has prepared a proposed Terraform diff for review. Read the changes and "
            "validation results before recording a decision. Approving only enables export of a "
            "`.patch` file; it does not deploy or apply the change."
        )
        proposed_text, proposal_diff, proposal_checks, proposal_errors = build_proposal(report)
        alignment_checks = [c for c in checks if c.get("name") == "terraform_data_alignment"]
        alignment_ok = bool(alignment_checks) and all(c.get("status") == "passed" for c in alignment_checks)
        proposal_checks_ok = bool(proposal_checks) and all(c.get("status") == "passed" for c in proposal_checks)
        proposal_ok = bool(proposal_diff.strip()) and alignment_ok and proposal_checks_ok and not proposal_errors

        if proposal_diff:
            with st.expander("Review proposed Terraform diff", expanded=True):
                st.code(proposal_diff, language="diff")
                st.download_button(
                    "Download proposed diff for review (not approved)",
                    data=proposal_diff.encode("utf-8"),
                    file_name="greenops_proposed_review.diff",
                    mime="text/plain",
                    key="download_unapproved_diff",
                )
        else:
            st.warning("A Terraform proposal could not be generated. Check the Fixer output and sample infrastructure file.")

        if proposal_checks:
            st.markdown("#### Proposal validation checks")
            st.dataframe(pd.DataFrame(proposal_checks), use_container_width=True, hide_index=True)
        if proposal_errors:
            st.error("The proposal has errors and cannot be approved.")
            st.dataframe(pd.DataFrame(proposal_errors), use_container_width=True, hide_index=True)
        if not alignment_ok:
            st.warning("Approval is blocked because CSV and Terraform data alignment did not pass. Resolve the mismatch before approving.")
        if proposal_checks and not proposal_checks_ok:
            st.warning("Approval is blocked because one or more proposal checks did not pass.")

        if proposal_ok:
            with st.form("greenops_human_approval_form", clear_on_submit=False):
                reviewer = st.text_input("Reviewer name", placeholder="Enter the person reviewing this change")
                decision_label = st.radio(
                    "Decision", ["Approve proposal", "Reject proposal"], horizontal=True,
                )
                reason = st.text_area(
                    "Reason / review notes",
                    placeholder="Explain why this change is safe or why it should be rejected.",
                )
                submitted = st.form_submit_button("Record human decision", type="primary")
            if submitted:
                if not reviewer.strip() or not reason.strip():
                    st.error("Enter your name and a reason before recording the decision.")
                else:
                    decision_value = "approved" if decision_label == "Approve proposal" else "rejected"
                    approval_record = record_decision(
                        proposed_diff=proposal_diff,
                        reviewer=reviewer,
                        decision=decision_value,
                        reason=reason,
                    )
                    st.session_state["greenops_approval"] = approval_record
                    st.session_state["greenops_approval_diff"] = proposal_diff
                    st.session_state["greenops_approval_status"] = decision_value
                    st.session_state.pop("greenops_approved_patch", None)
                    st.rerun()

            saved_approval = st.session_state.get("greenops_approval")
            approval_is_current = (
                st.session_state.get("greenops_approval_diff") == proposal_diff
                and approval_matches(proposal_diff, saved_approval)
            )
            saved_status = st.session_state.get("greenops_approval_status")
            if approval_is_current and saved_status == "approved":
                st.success(f"Approved by {saved_approval.reviewer} at {saved_approval.decided_at_utc}.")
                st.caption(f"Review note: {saved_approval.reason}")
                if st.button("Export approved patch", type="primary", key="export_approved_patch"):
                    output_path = PROJECT_ROOT / "artifacts" / f"greenops_approved_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.patch"
                    result = export_with_approval(proposal_diff, str(output_path), saved_approval)
                    if result.status == "passed":
                        st.session_state["greenops_approved_patch"] = proposal_diff
                        st.session_state["greenops_export_message"] = result.message
                    else:
                        st.session_state["greenops_export_message"] = result.message
                    st.rerun()
                if st.session_state.get("greenops_approved_patch"):
                    st.success(st.session_state.get("greenops_export_message", "Approved patch exported."))
                    st.download_button(
                        "Download approved patch",
                        data=st.session_state["greenops_approved_patch"].encode("utf-8"),
                        file_name="greenops_approved.patch",
                        mime="text/plain",
                        key="download_approved_patch",
                    )
                    st.caption("This is a patch file only. It has not been applied to any infrastructure.")
            elif approval_is_current and saved_status == "rejected":
                st.error(f"Proposal rejected by {saved_approval.reviewer}.")
                st.caption(f"Review note: {saved_approval.reason}. No patch was exported.")
            elif saved_approval is not None:
                st.info("The previous decision does not match the current proposal. Review and record a new decision.")
        else:
            st.info("Human approval is disabled until the proposal and data-alignment checks pass.")
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
