"""
GreenOps Console - cloud cost, energy and carbon optimizer.

Run:  streamlit run app.py
"""
import hashlib
import io
import json
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="GreenOps Console", page_icon="🌿", layout="wide")

# ----------------------------------------------------------------------------
# Model constants
# ----------------------------------------------------------------------------
SIZES = ["small", "medium", "large", "xlarge", "2xlarge"]
VCPU = [2, 4, 8, 16, 32]
PRICE_PER_VCPU_HR = 0.048          # USD, x86, baseline region
ARM_PRICE_FACTOR = 0.80            # ARM is ~20% cheaper
ARM_POWER_FACTOR = 0.75            # ARM draws ~25% less power
PUE = 1.25                         # datacenter overhead
FULL_MONTH_HOURS = 730.0

# region: (grid carbon intensity kgCO2/kWh, price multiplier)
REGIONS = {
    "us-east-1 (Virginia)": (0.38, 1.00),
    "us-west-2 (Oregon)": (0.12, 1.00),
    "eu-north-1 (Stockholm)": (0.02, 1.05),
    "eu-west-1 (Ireland)": (0.30, 1.08),
    "ap-south-1 (Mumbai)": (0.71, 0.97),
    "ap-southeast-1 (Singapore)": (0.49, 1.10),
}

FIXES = {
    "terminate": dict(label="Terminate idle", icon="🗑️", risk="Low", effort="Low", color="#ef4444"),
    "rightsize": dict(label="Rightsize", icon="📐", risk="Medium", effort="Medium", color="#f59e0b"),
    "schedule": dict(label="Off-hours schedule", icon="⏰", risk="Low", effort="Low", color="#3b82f6"),
    "arm": dict(label="Move to ARM", icon="🧬", risk="Medium", effort="Medium", color="#8b5cf6"),
    "region": dict(label="Greener region", icon="🌍", risk="High", effort="High", color="#10b981"),
}
PIPELINE_ORDER = ["rightsize", "schedule", "arm", "region"]


# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------
st.markdown(
    """
<style>
.block-container {padding-top: 1.4rem; max-width: 1350px;}
.hero {
  background: linear-gradient(120deg, #064e3b 0%, #047857 45%, #0ea5a4 100%);
  border-radius: 20px; padding: 28px 34px; color: #fff; margin-bottom: 18px;
  box-shadow: 0 10px 30px rgba(4,120,87,.25);
}
.hero h1 {margin: 0; font-size: 2.2rem; letter-spacing: -.5px; color:#fff;}
.hero p {margin: 6px 0 0 0; opacity: .92; font-size: 1.02rem; color:#fff;}
.pill {display:inline-block; padding:3px 12px; border-radius:999px; background:rgba(255,255,255,.18);
       font-size:.78rem; margin-right:8px; margin-top:12px; color:#fff;}
.kpi {border-radius: 18px; padding: 18px 20px; color: #fff; height: 100%;
      box-shadow: 0 6px 18px rgba(0,0,0,.14);}
.kpi .t {font-size:.82rem; text-transform:uppercase; letter-spacing:1px; opacity:.85;}
.kpi .v {font-size:2rem; font-weight:800; line-height:1.15; margin-top:4px;}
.kpi .s {font-size:.86rem; opacity:.92; margin-top:4px;}
.k-green  {background: linear-gradient(135deg,#059669,#34d399);}
.k-blue   {background: linear-gradient(135deg,#1d4ed8,#38bdf8);}
.k-amber  {background: linear-gradient(135deg,#d97706,#fbbf24);}
.k-slate  {background: linear-gradient(135deg,#334155,#64748b);}
.k-violet {background: linear-gradient(135deg,#6d28d9,#a78bfa);}
.rec {border-radius:16px; padding:16px 18px; border:1px solid rgba(128,128,128,.28);
      border-left-width:6px; background: rgba(128,128,128,.07); height:100%;}
.rec .h {font-weight:700; font-size:1.02rem;}
.rec .d {opacity:.85; font-size:.88rem; margin:4px 0 10px 0;}
.rec .m {font-size:.88rem;}
.tag {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.72rem; font-weight:600;
      margin-right:6px; color:#fff;}
.tag-Low {background:#16a34a;} .tag-Medium {background:#d97706;} .tag-High {background:#dc2626;}
.banner {border-radius:14px; padding:14px 18px; margin:6px 0 14px 0; font-weight:600;}
.banner-ok {background:rgba(16,185,129,.15); border:1px solid #10b981;}
.banner-info {background:rgba(59,130,246,.13); border:1px solid #3b82f6;}
.cta {border-radius:18px; padding:34px; text-align:center; border:2px dashed rgba(16,185,129,.55);
      background:rgba(16,185,129,.07); margin: 8px 0 18px 0;}
.cta h3 {margin:0 0 6px 0;}
div[data-testid="stTabs"] button {font-weight:600; font-size:1rem;}
</style>
""",
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Core calculations
# ----------------------------------------------------------------------------
def calc(vcpu, arch, region, hours, load):
    """Return (monthly cost USD, kWh, kgCO2) for a resource state."""
    util = min(load / vcpu, 0.98)
    watts = vcpu * (2.5 + 6.5 * util) * (ARM_POWER_FACTOR if arch == "arm" else 1.0)
    kwh = watts * hours / 1000.0 * PUE
    intensity, price_mult = REGIONS[region]
    cost = vcpu * PRICE_PER_VCPU_HR * (ARM_PRICE_FACTOR if arch == "arm" else 1.0) * price_mult * hours
    return cost, kwh, kwh * intensity


def eligible(r, cfg):
    """Which fixes apply to this resource, with their parameters."""
    if r["cpu_avg"] < cfg["idle_thr"] and r["cpu_peak"] < cfg["idle_thr"] * 3:
        return {"terminate": {}}
    out = {}
    idx = SIZES.index(r["instance_size"])
    peak_load = r["cpu_peak"] / 100 * VCPU[idx]
    for j in range(idx):
        if peak_load / VCPU[j] <= cfg["headroom"] / 100:
            out["rightsize"] = {"to": j}
            break
    if r["env"] in ("dev", "test", "staging") and r["hours"] >= 700:
        out["schedule"] = {"hours": cfg["sched_hours"] * 52 / 12}
    if r["arch"] == "x86":
        out["arm"] = {}
    ri = REGIONS[r["region"]][0]
    ti = REGIONS[cfg["green_region"]][0]
    if r["region"] != cfg["green_region"] and ri >= 0.25 and ri >= 2 * ti:
        out["region"] = {"to": cfg["green_region"]}
    return out


def run_pipeline(r, params, chosen):
    """Apply chosen fixes in order. Returns (base, final, per-fix deltas)."""
    vc = VCPU[SIZES.index(r["instance_size"])]
    s = dict(vcpu=vc, arch=r["arch"], region=r["region"], hours=r["hours"], load=r["cpu_avg"] / 100 * vc)
    base = calc(**s)
    if "terminate" in chosen:
        return base, (0.0, 0.0, 0.0), {"terminate": base}
    cur, deltas = base, {}
    for fix in PIPELINE_ORDER:
        if fix in chosen:
            p = params[fix]
            if fix == "rightsize":
                s["vcpu"] = VCPU[p["to"]]
            elif fix == "schedule":
                s["hours"] = p["hours"]
            elif fix == "arm":
                s["arch"] = "arm"
            elif fix == "region":
                s["region"] = p["to"]
            new = calc(**s)
            deltas[fix] = tuple(a - b for a, b in zip(cur, new))
            cur = new
    return base, cur, deltas


def detail_text(fix, r, p, cfg):
    if fix == "terminate":
        return f"Decommission (avg CPU {r['cpu_avg']:.0f}%, peak {r['cpu_peak']:.0f}%)"
    if fix == "rightsize":
        return f"{r['instance_size']} → {SIZES[p['to']]} (peak {r['cpu_peak']:.0f}% CPU)"
    if fix == "schedule":
        return f"Run {cfg['sched_hours']} h/week, stop nights & weekends"
    if fix == "arm":
        return "Migrate x86 → ARM (Graviton-class)"
    return f"{r['region']} → {p['to']}"


def build_recs(df, cfg):
    rows = []
    for r in df.to_dict("records"):
        params = eligible(r, cfg)
        if not params:
            continue
        _, _, deltas = run_pipeline(r, params, set(params))
        for fix, (dc, dk, dco) in deltas.items():
            rows.append(
                dict(
                    id=f"{r['name']}|{fix}",
                    resource=r["name"],
                    env=r["env"],
                    fix=fix,
                    action=f"{FIXES[fix]['icon']} {FIXES[fix]['label']}",
                    detail=detail_text(fix, r, params[fix], cfg),
                    cost=dc,
                    kwh=dk,
                    co2=dco,
                    risk=FIXES[fix]["risk"],
                    effort=FIXES[fix]["effort"],
                )
            )
    recs = pd.DataFrame(rows)
    if recs.empty:
        return recs
    recs = recs.sort_values("cost", ascending=False).reset_index(drop=True)
    n = len(recs)
    recs["priority"] = ["High" if i < n * 0.2 else "Medium" if i < n * 0.5 else "Low" for i in range(n)]
    return recs


def evaluate(df, cfg, sel):
    """Simulate the fleet with the selected fixes applied."""
    res, by_fix = [], {f: np.zeros(3) for f in FIXES}
    for r in df.to_dict("records"):
        params = eligible(r, cfg)
        chosen = {f for f in params if f"{r['name']}|{f}" in sel}
        base, final, deltas = run_pipeline(r, params, chosen)
        for f, d in deltas.items():
            by_fix[f] += np.array(d)
        res.append(
            dict(
                name=r["name"], env=r["env"], region=r["region"], instance_size=r["instance_size"],
                arch=r["arch"], cpu_avg=r["cpu_avg"], cpu_peak=r["cpu_peak"],
                cost_now=base[0], cost_opt=final[0], kwh_now=base[1], kwh_opt=final[1],
                co2_now=base[2], co2_opt=final[2],
                fixes=", ".join(FIXES[f]["label"] for f in PIPELINE_ORDER + ["terminate"] if f in chosen) or "—",
                status="Idle" if "terminate" in params else ("Optimizable" if params else "Healthy"),
            )
        )
    out = pd.DataFrame(res)
    totals = dict(
        cost_now=out.cost_now.sum(), cost_opt=out.cost_opt.sum(),
        kwh_now=out.kwh_now.sum(), kwh_opt=out.kwh_opt.sum(),
        co2_now=out.co2_now.sum(), co2_opt=out.co2_opt.sum(),
    )
    return out, totals, by_fix


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def sample_fleet(seed, n=48):
    rng = np.random.default_rng(seed)
    teams = ["checkout", "search", "ml-train", "etl", "api", "web", "billing", "auth", "reports", "queue"]
    rows = []
    for i in range(n):
        env = rng.choice(["prod", "staging", "dev", "test"], p=[0.35, 0.15, 0.30, 0.20])
        kind = rng.choice(["idle", "over", "ok"], p=[0.15, 0.35, 0.50])
        if kind == "idle":
            avg, peak = rng.uniform(0.8, 4.0), rng.uniform(3, 12)
        elif kind == "over":
            avg, peak = rng.uniform(6, 18), rng.uniform(15, 34)
        else:
            avg, peak = rng.uniform(30, 60), rng.uniform(58, 92)
        rows.append(
            dict(
                name=f"{rng.choice(teams)}-{env}-{i:02d}",
                env=str(env),
                region=str(rng.choice(list(REGIONS), p=[0.30, 0.12, 0.05, 0.13, 0.25, 0.15])),
                instance_size=str(rng.choice(SIZES, p=[0.10, 0.25, 0.30, 0.22, 0.13])),
                arch=str(rng.choice(["x86", "arm"], p=[0.85, 0.15])),
                cpu_avg=round(float(avg), 1),
                cpu_peak=round(float(max(peak, avg + 1)), 1),
                hours=FULL_MONTH_HOURS,
            )
        )
    return pd.DataFrame(rows)


def parse_upload(raw: bytes):
    df = pd.read_csv(io.BytesIO(raw))
    need = {"name", "env", "region", "instance_size", "cpu_avg", "cpu_peak"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")
    if "arch" not in df:
        df["arch"] = "x86"
    if "hours" not in df:
        df["hours"] = FULL_MONTH_HOURS
    df["region"] = df["region"].where(df["region"].isin(REGIONS), "us-east-1 (Virginia)")
    df = df[df["instance_size"].isin(SIZES)].copy()
    df["env"] = df["env"].astype(str).str.lower()
    df["arch"] = df["arch"].astype(str).str.lower().where(lambda s: s.isin(["x86", "arm"]), "x86")
    if df.empty:
        raise ValueError(f"No valid rows. instance_size must be one of {SIZES}.")
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------
ss = st.session_state
ss.setdefault("seed", 7)
ss.setdefault("analysis", None)
ss.setdefault("applied", None)
ss.setdefault("sel_base", set())
ss.setdefault("editor_ver", 0)
ss.setdefault("run_requested", False)


def request_run():
    ss.run_requested = True


def new_fleet():
    ss.seed += 1


def current_selection():
    a = ss.analysis
    if a is None:
        return set()
    ids = a["plan_ids"]
    sel = set(ss.sel_base)
    edits = ss.get(f"editor_{a['run']}_{ss.editor_ver}", {}).get("edited_rows", {})
    for pos, change in edits.items():
        if "Apply" in change and int(pos) < len(ids):
            (sel.add if change["Apply"] else sel.discard)(ids[int(pos)])
    return sel


def select_all():
    ss.sel_base = set(ss.analysis["plan_ids"])
    ss.editor_ver += 1


def clear_all():
    ss.sel_base = set()
    ss.editor_ver += 1


def apply_plan():
    ss.applied = set(current_selection())
    ss.toast_msg = "✅ Fix plan applied - savings are now realized."


def revert_plan():
    ss.sel_base = set(ss.applied or [])
    ss.applied = None
    ss.editor_ver += 1


# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🌿 GreenOps")
    st.caption("Configure the fleet and the optimization rules, then run the analysis.")

    st.markdown("### 1 · Fleet data")
    source = st.radio("Source", ["Sample fleet", "Upload CSV"], horizontal=True, label_visibility="collapsed")
    uploaded = None
    if source == "Sample fleet":
        c1, c2 = st.columns([3, 2])
        c1.caption(f"Telemetry seed: **{ss.seed}**")
        c2.button("🔄 New", on_click=new_fleet, help="Generate a different sample fleet", width="stretch")
    else:
        uploaded = st.file_uploader("CSV", type="csv", label_visibility="collapsed")
        tpl = sample_fleet(1, 6).to_csv(index=False)
        st.download_button("⬇ CSV template", tpl, "fleet_template.csv", "text/csv", width="stretch")

    st.markdown("### 2 · Optimization rules")
    idle_thr = st.slider("Idle if avg CPU below (%)", 1, 15, 5)
    headroom = st.slider("Rightsize: max peak load after resize (%)", 50, 90, 70)
    sched_hours = st.slider("Dev/test run hours per week", 30, 100, 60)
    green_region = st.selectbox("Preferred green region", list(REGIONS), index=2)

    st.markdown("### 3 · Display")
    currency = st.radio("Currency", ["USD ($)", "INR (₹)"], horizontal=True)
    fx = 1.0
    sym = "$"
    if currency.startswith("INR"):
        sym = "₹"
        fx = st.number_input("INR per USD", 50.0, 150.0, 85.0, 0.5)

    st.divider()
    st.button("🚀 Run analysis", type="primary", width="stretch", on_click=request_run, key="run_side")

cfg = dict(idle_thr=float(idle_thr), headroom=float(headroom), sched_hours=float(sched_hours), green_region=green_region)


def money(usd, dec=0):
    return f"{sym}{usd * fx:,.{dec}f}"


def kwh_fmt(x):
    return f"{x:,.0f} kWh" if x < 100000 else f"{x / 1000:,.1f} MWh"


def co2_fmt(x):
    return f"{x:,.0f} kg" if x < 10000 else f"{x / 1000:,.1f} t"


def kpi(title, value, sub, cls):
    st.markdown(
        f'<div class="kpi {cls}"><div class="t">{title}</div><div class="v">{value}</div><div class="s">{sub}</div></div>',
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# Load fleet
# ----------------------------------------------------------------------------
fleet, fleet_sig = None, ""
if source == "Sample fleet":
    fleet = sample_fleet(ss.seed)
    fleet_sig = f"sample-{ss.seed}"
elif uploaded is not None:
    raw = uploaded.getvalue()
    try:
        fleet = parse_upload(raw)
        fleet_sig = "csv-" + hashlib.md5(raw).hexdigest()[:10]
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"CSV problem: {e}")

settings_sig = hashlib.md5((fleet_sig + json.dumps(cfg, sort_keys=True)).encode()).hexdigest()

# ----------------------------------------------------------------------------
# Hero
# ----------------------------------------------------------------------------
st.markdown(
    """
<div class="hero">
  <h1>🌿 GreenOps Console</h1>
  <p>Cut cloud spend, burn less energy, emit less carbon - find waste, get fixes, see the impact.</p>
  <span class="pill">💰 Cost</span><span class="pill">⚡ Energy</span>
  <span class="pill">🌍 Carbon</span><span class="pill">🛠️ Automated fixer</span>
</div>
""",
    unsafe_allow_html=True,
)

if fleet is None:
    st.info("Upload a fleet CSV in the sidebar (or switch back to the sample fleet) to begin.")
    st.stop()

# ----------------------------------------------------------------------------
# Run analysis
# ----------------------------------------------------------------------------
if ss.run_requested:
    ss.run_requested = False
    with st.status("Running GreenOps analysis…", expanded=True) as status:
        st.write(f"🔎 Scanning {len(fleet)} resources…")
        time.sleep(0.5)
        st.write("📈 Evaluating CPU utilization and idle patterns…")
        time.sleep(0.5)
        st.write("🧮 Modelling cost, energy and carbon for each fix…")
        recs = build_recs(fleet, cfg)
        time.sleep(0.5)
        st.write("🛠️ Building fixer recommendations…")
        time.sleep(0.4)
        status.update(label=f"Analysis complete - {len(recs)} recommendations found", state="complete", expanded=False)
    prev = ss.analysis["run"] if ss.analysis else 0
    ss.analysis = dict(
        run=prev + 1, ts=datetime.now().strftime("%H:%M:%S"), sig=settings_sig,
        fleet=fleet.copy(), cfg=dict(cfg), recs=recs, plan_ids=list(recs["id"]) if len(recs) else [],
    )
    ss.sel_base = set(ss.analysis["plan_ids"])
    ss.applied = None
    ss.editor_ver += 1
    ss.toast_msg = f"Analysis #{prev + 1} complete: {len(recs)} fixes identified"

if ss.get("toast_msg"):
    st.toast(ss.pop("toast_msg"))

# Always read analysis results from Streamlit session state.
A = ss.get("analysis")

# If analysis has not run yet, show the baseline dashboard and stop before
# accessing analysis-only fields.
if not isinstance(A, dict):
    base_df, base_tot, _ = evaluate(fleet, cfg, set())
    st.markdown(
        '<div class="cta"><h3>Ready when you are</h3>'
        "Your fleet is loaded. Run the analysis to discover idle, oversized and carbon-heavy resources.</div>",
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([2, 2, 2])
    mid.button("🚀 Run analysis", type="primary", width="stretch", on_click=request_run, key="run_main")
    k = st.columns(4)
    with k[0]:
        kpi("Resources", f"{len(fleet)}", "in scope", "k-slate")
    with k[1]:
        kpi("Current monthly cost", money(base_tot["cost_now"]), "baseline spend", "k-blue")
    with k[2]:
        kpi("Energy / month", kwh_fmt(base_tot["kwh_now"]), "incl. datacenter overhead", "k-amber")
    with k[3]:
        kpi("Carbon / month", co2_fmt(base_tot["co2_now"]), "CO₂e", "k-violet")
    c1, c2 = st.columns(2)
    by_env = base_df.groupby("env", as_index=False).cost_now.sum()
    by_env["cost"] = by_env.cost_now * fx
    c1.plotly_chart(
        px.bar(by_env, x="env", y="cost", color="env", title=f"Monthly cost by environment ({sym})",
               color_discrete_sequence=px.colors.qualitative.Safe).update_layout(showlegend=False),
        width="stretch",
    )
    c2.plotly_chart(
        px.histogram(fleet, x="cpu_avg", nbins=20, title="Average CPU utilization (%)",
                     color_discrete_sequence=["#10b981"]),
        width="stretch",
    )
    st.stop()

# Defensive guard immediately before any dictionary access.
# Launch with `streamlit run app.py` so st.stop() is handled by Streamlit.
if not isinstance(A, dict):
    st.error("Analysis results are not available yet. Click Run analysis to generate them.")
    st.stop()

required_analysis_keys = {"fleet", "cfg", "recs", "plan_ids", "run", "ts", "sig"}
missing_analysis_keys = required_analysis_keys.difference(A)
if missing_analysis_keys:
    st.error(
        "Analysis results are incomplete. Missing fields: "
        + ", ".join(sorted(missing_analysis_keys))
        + ". Please click Run analysis again."
    )
    st.stop()

sel = set(ss.applied) if ss.applied is not None else current_selection()
realized = ss.applied is not None
afleet, acfg, recs = A["fleet"], A["cfg"], A["recs"]
res, tot, by_fix = evaluate(afleet, acfg, sel)

sav_cost = tot["cost_now"] - tot["cost_opt"]
sav_kwh = tot["kwh_now"] - tot["kwh_opt"]
sav_co2 = tot["co2_now"] - tot["co2_opt"]
pct = lambda a, b: (a / b * 100) if b else 0.0  # noqa: E731

if A["sig"] != settings_sig:
    st.markdown(
        '<div class="banner banner-info">⚠️ Settings or fleet changed since analysis #%d. '
        "Click <b>Run analysis</b> to refresh the results.</div>" % A["run"],
        unsafe_allow_html=True,
    )
if realized:
    st.markdown(
        f'<div class="banner banner-ok">✅ Fix plan applied - {len(sel)} fixes realized, saving '
        f'<b>{money(sav_cost)}</b>/month and <b>{kwh_fmt(sav_kwh)}</b> of energy.</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f'<div class="banner banner-info">Analysis #{A["run"]} · {A["ts"]} · showing <b>projected</b> impact of '
        f'{len(sel)} selected fixes. Apply the plan in the Fixer tab to make it real.</div>',
        unsafe_allow_html=True,
    )

tab_over, tab_fix, tab_res, tab_method = st.tabs(["📊 Impact overview", "🛠️ Fixer recommendations", "🔍 Resources", "ℹ️ Method"])

# ---------------------------- Overview --------------------------------------
with tab_over:
    label = "Realized" if realized else "Projected"
    st.markdown(f"#### {label}: what we save vs. what we still spend")
    c = st.columns(3)
    with c[0]:
        kpi("💰 Savings made", money(sav_cost), f"{money(sav_cost * 12)} per year · {pct(sav_cost, tot['cost_now']):.0f}% of spend", "k-green")
    with c[1]:
        kpi("📉 Cost minimised to", money(tot["cost_opt"]), f"down from {money(tot['cost_now'])} / month", "k-blue")
    with c[2]:
        kpi("⚡ Energy saved", kwh_fmt(sav_kwh), f"{pct(sav_kwh, tot['kwh_now']):.0f}% less · {co2_fmt(sav_co2)} CO₂e avoided", "k-amber")

    st.write("")
    b = st.columns(3)
    specs = [
        (f"Monthly cost ({sym})", tot["cost_now"] * fx, tot["cost_opt"] * fx, "#3b82f6"),
        ("Energy (kWh / month)", tot["kwh_now"], tot["kwh_opt"], "#f59e0b"),
        ("Carbon (kg CO₂e / month)", tot["co2_now"], tot["co2_opt"], "#8b5cf6"),
    ]
    for col, (title, a0, a1, color) in zip(b, specs):
        fig = go.Figure(go.Bar(x=["Current", "Optimized"], y=[a0, a1], marker_color=["#94a3b8", color],
                               text=[f"{a0:,.0f}", f"{a1:,.0f}"], textposition="outside"))
        fig.update_layout(title=title, height=330, margin=dict(t=50, b=10, l=10, r=10), yaxis_range=[0, max(a0, 1) * 1.2])
        col.plotly_chart(fig, width="stretch")

    w1, w2 = st.columns([3, 2])
    steps = [(FIXES[f]["label"], by_fix[f][0] * fx) for f in FIXES if by_fix[f][0] > 0.001]
    wf = go.Figure(go.Waterfall(
        orientation="v",
        measure=["absolute"] + ["relative"] * len(steps) + ["total"],
        x=["Current spend"] + [s[0] for s in steps] + ["Optimized spend"],
        y=[tot["cost_now"] * fx] + [-s[1] for s in steps] + [tot["cost_opt"] * fx],
        connector={"line": {"color": "#94a3b8"}},
        decreasing={"marker": {"color": "#10b981"}},
        totals={"marker": {"color": "#3b82f6"}},
    ))
    wf.update_layout(title=f"Cost waterfall ({sym} / month)", height=400, margin=dict(t=50, b=10))
    w1.plotly_chart(wf, width="stretch")

    if steps:
        pie_df = pd.DataFrame(
            [dict(fix=FIXES[f]["label"], energy=by_fix[f][1], cost=by_fix[f][0] * fx) for f in FIXES if by_fix[f][0] > 0.001]
        )
        pie = px.pie(pie_df, names="fix", values="cost", hole=0.55, title="Savings by fix type",
                     color="fix", color_discrete_map={FIXES[f]["label"]: FIXES[f]["color"] for f in FIXES})
        pie.update_layout(height=400, margin=dict(t=50, b=10))
        w2.plotly_chart(pie, width="stretch")
    else:
        w2.info("No fixes selected - nothing to show yet. Pick fixes in the Fixer tab.")

# ---------------------------- Fixer -----------------------------------------
with tab_fix:
    if recs.empty:
        st.success("Nothing to fix - this fleet is already well optimized. 🎉")
    else:
        st.markdown("#### Top fixes by monthly savings")
        top = recs.head(6)
        for i in range(0, len(top), 3):
            cols = st.columns(3)
            for col, (_, r) in zip(cols, top.iloc[i:i + 3].iterrows()):
                col.markdown(
                    f"""<div class="rec" style="border-left-color:{FIXES[r.fix]['color']}">
                    <div class="h">{r.action} · {r.resource}</div>
                    <div class="d">{r.detail}</div>
                    <div class="m">💰 <b>{money(r.cost)}</b>/mo &nbsp; ⚡ <b>{r.kwh:,.0f}</b> kWh &nbsp; 🌍 <b>{r.co2:,.0f}</b> kg</div>
                    <div style="margin-top:10px"><span class="tag tag-{r.risk}">Risk: {r.risk}</span>
                    <span class="tag tag-{r.effort}">Effort: {r.effort}</span>
                    <span class="tag tag-{r.priority}">Priority: {r.priority}</span></div></div>""",
                    unsafe_allow_html=True,
                )
            st.write("")

        st.markdown("#### Fix plan")
        st.caption("Tick the fixes you want. The Overview updates live. Estimates stack in order "
                   "(rightsize → schedule → ARM → region); totals always recompute for your selection.")
        bc = st.columns([1, 1, 2, 2])
        bc[0].button("Select all", on_click=select_all, width="stretch")
        bc[1].button("Clear", on_click=clear_all, width="stretch")
        if realized:
            bc[2].button("↩ Reopen plan", on_click=revert_plan, width="stretch")
        else:
            bc[2].button(f"✅ Apply {len(sel)} selected fixes", type="primary", on_click=apply_plan,
                         width="stretch", disabled=len(sel) == 0)

        table = pd.DataFrame(
            {
                "Apply": [i in ss.sel_base for i in recs["id"]],
                "Priority": recs.priority,
                "Resource": recs.resource,
                "Env": recs.env,
                "Fix": recs.action,
                "What changes": recs.detail,
                f"Saves {sym}/mo": (recs.cost * fx).round(0),
                "Energy kWh": recs.kwh.round(0),
                "CO₂ kg": recs.co2.round(0),
                "Risk": recs.risk,
            }
        )
        st.data_editor(
            table, hide_index=True, width="stretch", height=420,
            key=f"editor_{A['run']}_{ss.editor_ver}",
            disabled=[c for c in table.columns if c != "Apply"],
            column_config={"Apply": st.column_config.CheckboxColumn("Apply", default=False)},
        )
        export = recs[["resource", "env", "action", "detail", "cost", "kwh", "co2", "risk", "effort", "priority"]]
        st.download_button("⬇ Export recommendations (CSV)", export.to_csv(index=False), "greenops_recommendations.csv", "text/csv")

# ---------------------------- Resources -------------------------------------
with tab_res:
    f1, f2 = st.columns([3, 1])
    envs = f1.multiselect("Environment", sorted(res.env.unique()), default=sorted(res.env.unique()))
    only = f2.toggle("Only changed", value=False)
    view = res[res.env.isin(envs)].copy()
    if only:
        view = view[view.fixes != "—"]
    view["saving"] = (view.cost_now - view.cost_opt) * fx
    show = view[["name", "env", "status", "region", "instance_size", "arch", "cpu_avg", "cost_now", "cost_opt",
                 "saving", "kwh_now", "kwh_opt", "co2_now", "co2_opt", "fixes"]].copy()
    for col in ("cost_now", "cost_opt"):
        show[col] = show[col] * fx
    st.dataframe(
        show.rename(columns={"cost_now": f"cost now ({sym})", "cost_opt": f"cost after ({sym})", "saving": f"saved ({sym})"}),
        hide_index=True, width="stretch", height=430,
        column_config={"cpu_avg": st.column_config.ProgressColumn("avg CPU %", min_value=0, max_value=100, format="%.0f")},
    )
    sc = px.scatter(view, x="cpu_avg", y=view.cost_now * fx, color="status", size="cost_now", hover_name="name",
                    title="Utilization vs. cost - idle & oversized resources stand out",
                    labels={"cpu_avg": "avg CPU %", "y": f"monthly cost ({sym})"},
                    color_discrete_map={"Idle": "#ef4444", "Optimizable": "#f59e0b", "Healthy": "#10b981"})
    st.plotly_chart(sc, width="stretch")

# ---------------------------- Method ----------------------------------------
with tab_method:
    st.markdown(
        f"""
**How the numbers are modelled** (edit the constants at the top of `app.py` to match your cloud):

- **Cost** = vCPUs × ${PRICE_PER_VCPU_HR}/vCPU-hr × running hours × regional price factor (ARM is {int((1 - ARM_PRICE_FACTOR) * 100)}% cheaper).
- **Energy** = vCPUs × (2.5 W idle + 6.5 W × utilization) × hours × PUE {PUE} (ARM draws {int((1 - ARM_POWER_FACTOR) * 100)}% less).
- **Carbon** = energy × regional grid intensity (kg CO₂e per kWh).

**Fixes**

| Fix | Rule |
|---|---|
| 🗑️ Terminate idle | avg CPU < {acfg['idle_thr']:.0f}% and peak < {acfg['idle_thr'] * 3:.0f}% |
| 📐 Rightsize | smallest size keeping peak load ≤ {acfg['headroom']:.0f}% |
| ⏰ Off-hours schedule | dev/test/staging running 24/7 → {acfg['sched_hours']:.0f} h/week |
| 🧬 Move to ARM | x86 workloads |
| 🌍 Greener region | grid ≥ 0.25 kg/kWh and ≥ 2× the target ({acfg['green_region']}) |

Upload your own fleet as CSV with columns: `name, env, region, instance_size, cpu_avg, cpu_peak` (optional: `arch, hours`).
"""
    )