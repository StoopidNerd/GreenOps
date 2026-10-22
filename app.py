"""GreenOps Agent - Streamlit entry point (placeholder UI)."""

import streamlit as st

from agents.orchestrator import run_pipeline

st.set_page_config(page_title="GreenOps Agent", page_icon="🌱", layout="wide")

st.title("🌱 GreenOps Agent")
st.caption("Placeholder UI - the full application is not implemented yet.")

st.info(
    "This page only runs the sample pipeline against synthetic data in ./data. "
    "It does not connect to any cloud account."
)

if st.button("Run sample analysis"):
    result = run_pipeline()
    st.subheader("Summary")
    st.json(result["summary"])
    st.subheader("Findings")
    st.dataframe(result["findings"], use_container_width=True)
