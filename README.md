# GreenOps Agent

A work-in-progress agent that finds cloud waste and estimates the carbon
impact of infrastructure, then suggests safer, greener right-sizing options.

> **Status:** project skeleton only. All agents are placeholders that work on
> synthetic sample data. Nothing here touches a real cloud account.

## Layout

```
app.py                  Streamlit entry point (placeholder UI)
agents/
  analyzer.py           Flags idle / under-utilised resources
  carbon_agent.py       Estimates energy use and CO2e
  fixer.py              Suggests changes (never applies them)
  orchestrator.py       Wires the agents together
sample_infra/main.tf    Example Terraform (not meant to be applied)
data/
  utilisation.csv       Synthetic utilisation metrics
  pricing.json          Synthetic hourly prices and power draw
  regions.json          Synthetic grid carbon intensity per region
tests/test_agents.py    Basic smoke tests
```

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest
streamlit run app.py
```

If PowerShell blocks activation, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Notes

- All numbers in `data/` are synthetic and for development only.
- Keep secrets in `.env` or `.streamlit/secrets.toml`; both are git-ignored.
