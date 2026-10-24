
import streamlit as st
import pandas as pd

from agents.runtime import run_sample_pipeline
from agents.orchestrator import report_to_dict

st.set_page_config(page_title="GreenOps Agent", page_icon="🌱", layout="wide")
st.title("🌱 GreenOps Agent")
st.caption("Stage 4 — real-agent integration test using synthetic data")

st.warning(
    "This prototype uses local sample data only. Recommendations are advisory. "
    "It does not deploy infrastructure or edit the original Terraform file."
)

if st.button("Run pipeline", type="primary"):
    try:
        report = run_sample_pipeline()
        result = report_to_dict(report)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Resources analyzed", len(result["findings"]))
        c2.metric("Cost/carbon estimates", len(result["estimates"]))
        c3.metric("Recommendations", len(result["recommendations"]))
        c4.metric("Runtime errors", len(result["errors"]))

        for title, key in [
            ("Findings", "findings"),
            ("Cost and carbon estimates", "estimates"),
            ("Recommendations — approval required", "recommendations"),
            ("Validation checks", "validation_checks"),
            ("Runtime errors", "errors"),
        ]:
            st.subheader(title)
            st.dataframe(pd.DataFrame(result[key]), use_container_width=True)

        if result["notes"]:
            st.subheader("Notes")
            for note in result["notes"]:
                st.write(f"- {note}")

    except Exception as exc:
        st.error(f"{type(exc).__name__}: {exc}")