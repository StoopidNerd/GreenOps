# GreenOps Agent

An AI-assisted cloud optimization solution designed to identify underutilized infrastructure, estimate cloud costs and carbon emissions, and recommend safer, more sustainable resource configurations.

## Overview

Cloud infrastructure can become expensive and energy-intensive when resources are underutilized or oversized. Identifying optimization opportunities manually can be time-consuming and may lead to unnecessary cloud spending and carbon emissions.

GreenOps Agent analyzes infrastructure utilization data, estimates potential cost and carbon impacts, and generates optimization recommendations. It incorporates a human approval workflow to help prevent unauthorized infrastructure changes.

## Key Features

- **Resource Analysis:** Identifies potentially underutilized or oversized infrastructure resources.
- **Cost Estimation:** Estimates cloud spending using pricing datasets and identifies potential cost-saving opportunities.
- **Carbon Estimation:** Estimates carbon emissions using resource power assumptions and regional carbon-intensity data.
- **Optimization Recommendations:** Suggests resource configuration changes based on analysis findings.
- **Human Approval Workflow:** Requires explicit approval before exporting a proposed patch.
- **Patch Export Safety:** Exports proposed changes as patch files without automatically applying them.
- **Interactive Dashboard:** Uses Streamlit to present analysis results and optimization insights.

## Agentic Workflow

The system follows a modular workflow:

1. **Analyzer Agent:** Processes utilization data and identifies optimization opportunities.
2. **Cost Estimator Agent:** Calculates estimated infrastructure costs.
3. **Carbon Estimator Agent:** Estimates emissions based on power consumption and regional carbon intensity.
4. **Fixer Agent:** Prepares proposed optimization changes.
5. **Approval and Export Layer:** Checks approval against the proposal fingerprint and allows approved patches to be exported for human review.

The workflow is designed for decision support. It does not automatically execute Terraform changes or deploy infrastructure modifications.

## Technology Stack

- Python
- Streamlit
- Pandas
- Plotly
- Pytest
- JSON and CSV datasets
- Git and GitHub

## Project Structure

```text
GreenOps-shared/
├── agents/
│   ├── analyzer.py
│   ├── cost_estimator.py
│   ├── carbon_estimator.py
│   ├── fixer.py
│   ├── approval.py
│   ├── approved_export.py
│   └── approval_workflow.py
├── tests/
├── app.py
├── requirements.txt
├── README.md
└── .venv/
```

The exact files and dataset directories may vary depending on the current project version.

## Installation and Setup

### Prerequisites

- Python 3.10 or a compatible version supported by the project dependencies
- Git
- Visual Studio Code (recommended)

### 1. Clone the repository

```bash
git clone https://github.com/StoopidNerd/GreenOps.git
cd GreenOps
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

### 3. Activate the environment

**Windows PowerShell:**

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If Plotly is missing, install it with:

```bash
python -m pip install plotly
```

### 5. Run the dashboard

```bash
python -m streamlit run app.py
```

Open the local URL displayed in the terminal, usually `http://localhost:8501`.

## Running Tests

Run the project's automated tests with:

```bash
python -m pytest -q
```

Review the test output to verify which components pass or fail.

## Safety and Governance

GreenOps Agent is designed with safety in mind:

- Original infrastructure configuration should remain unchanged during analysis.
- Proposed changes should be kept separate from original configuration files.
- Patch export requires explicit human approval.
- Approval is checked against the proposal's SHA-256 fingerprint.
- Exported patches are not automatically applied.
- Cost and carbon figures are estimates and depend on dataset quality and assumptions.

## Measuring Impact

The project can evaluate its potential value using the following metrics:

- Number of resources identified for optimization.
- Estimated monthly and annual cost savings.
- Estimated carbon emissions reduction.
- Time required to analyze resources and generate recommendations.
- Number of proposals approved, rejected, and exported.

Actual savings and emissions reductions should be verified after any approved change is implemented.

## Current Limitations

- Results depend on the availability and quality of utilization, pricing, and regional carbon-intensity data.
- Estimates based on sample or synthetic datasets do not represent verified live-cloud measurements.
- Recommendations require human review before implementation.
- Live cloud integrations and automatic infrastructure deployment are not assumed to be available.

## Future Enhancements

- Integration with cloud provider APIs and billing data.
- More accurate carbon estimation using provider-specific energy metrics.
- Improved resource recommendations based on workload patterns.
- Audit logging and persistent approval records.
- Before-and-after reporting to measure realized savings.

## Project Goal

The goal of GreenOps Agent is to help organizations reduce unnecessary cloud spending and environmental impact through data-driven recommendations, transparent estimates, and human-controlled optimization.

## License

Add the applicable license information here before distributing the project publicly.
