# GreenOps Agent

GreenOps Agent is a work-in-progress sustainability tool designed to identify cloud infrastructure waste, estimate potential cost savings and carbon emissions, and recommend safer, more efficient resource configurations.

The project explores how AI-driven agents can help organizations improve cloud resource utilization, reduce unnecessary infrastructure expenditure, and make more environmentally responsible computing decisions.

> **Project Status:** Early development. The application uses synthetic sample data, and the agents are currently placeholders. No real cloud accounts are accessed, and no infrastructure changes are applied automatically.

## Problem Statement

Cloud infrastructure can waste money and energy when resources are over-provisioned, underutilized, or left running unnecessarily. Identifying these inefficiencies manually can be time-consuming, and reducing costs without compromising system reliability requires careful analysis.

GreenOps Agent aims to address this problem by analyzing infrastructure utilization, estimating its environmental impact, and suggesting potential optimization strategies.

## Proposed Solution

The project uses a modular, agent-based architecture to analyze cloud resource usage and generate optimization recommendations.

The proposed workflow includes:

1. Analyze infrastructure utilization data.
2. Identify idle or underutilized resources.
3. Estimate energy consumption and carbon dioxide equivalent emissions (CO2e).
4. Evaluate potential cost savings and right-sizing opportunities.
5. Generate proposed configuration changes.
6. Validate recommendations and require human approval before any future implementation.

Currently, these capabilities are being developed and tested using synthetic data.

## Key Features

- **Resource Utilization Analysis:** Identify potentially idle or underutilized infrastructure.
- **Carbon Impact Estimation:** Estimate energy consumption and associated CO2e emissions.
- **Cost Optimization:** Analyze synthetic pricing data to estimate potential savings.
- **Right-Sizing Recommendations:** Suggest more appropriate resource configurations.
- **Configuration Validation:** Provide a foundation for validating proposed infrastructure changes.
- **Human-in-the-Loop Workflow:** Keep infrastructure modifications under human control.
- **Interactive Dashboard:** Provide a Streamlit interface for exploring results.

## Project Architecture

The project follows a modular architecture in which individual agents handle specific responsibilities.

- **Analyzer Agent:** Identifies idle and underutilized resources.
- **Carbon Agent:** Estimates energy usage and carbon emissions.
- **Fixer Agent:** Suggests potential infrastructure changes without applying them.
- **Orchestrator Agent:** Coordinates the agents and combines their outputs.
- **Streamlit Application:** Provides the user interface for interacting with the system.

## Project Structure

```text
GreenOps/
├── app.py
├── agents/
│   ├── analyzer.py
│   ├── carbon_agent.py
│   ├── fixer.py
│   └── orchestrator.py
├── sample_infra/
│   └── main.tf
├── data/
│   ├── utilisation.csv
│   ├── pricing.json
│   └── regions.json
├── tests/
│   └── test_agents.py
├── requirements.txt
└── README.md
```

## Technology Stack

- **Python:** Core application logic and agent implementation.
- **Streamlit:** Interactive application interface.
- **Pandas:** Data processing and analysis, where implemented.
- **Terraform:** Example infrastructure configuration.
- **Pytest:** Automated testing.
- **CSV and JSON:** Storage of synthetic utilization, pricing, and regional carbon-intensity data.

The technology stack may evolve as development progresses.

## Getting Started

### Prerequisites

Install Python 3 and pip. Python 3.10 or later is a reasonable starting point, provided it is compatible with the dependencies in `requirements.txt`.

### 1. Clone the repository

```bash
git clone https://github.com/StoopidNerd/GreenOps.git
cd GreenOps
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
```

### 3. Activate the environment

On macOS or Linux:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Run the tests

```bash
python -m pytest
```

### 6. Launch the application

```bash
streamlit run app.py
```

The application will provide a local URL in the terminal. Open that URL in your browser to access the dashboard.

## Dataset

The current project uses synthetic data for development and testing.

| File | Purpose |
|---|---|
| `data/utilisation.csv` | Synthetic infrastructure utilization metrics |
| `data/pricing.json` | Synthetic hourly pricing and power consumption values |
| `data/regions.json` | Synthetic regional electricity-grid carbon intensity |

These files are intended to demonstrate the analysis workflow. They should not be treated as real cloud billing, measured energy consumption, or verified emissions data.

## Safety and Security

GreenOps Agent is designed around a human-approved optimization workflow.

- No real cloud account is accessed by the current prototype.
- Suggested fixes are not automatically applied.
- Sample infrastructure configurations are illustrative and should not be applied directly.
- Credentials and secrets should never be committed to version control.
- The `.env` and `.streamlit/secrets.toml` files should remain excluded through `.gitignore`.

Any future integration with cloud providers should include appropriate authentication, permissions, validation, and explicit approval before modifying infrastructure.

## Future Enhancements

- Integration with AWS, Microsoft Azure, and Google Cloud.
- Analysis of real cloud monitoring and billing data.
- Improved detection of idle and over-provisioned resources.
- More accurate energy and carbon-emission estimates.
- Cost forecasting and optimization prioritization.
- Automated validation of proposed Terraform changes.
- Human-approved execution of infrastructure modifications.
- Expanded test coverage and an interactive optimization dashboard.

## Project Goals

GreenOps Agent aims to demonstrate how agent-based automation can support sustainable cloud operations by combining infrastructure analysis, cost optimization, environmental impact estimation, and human oversight.

## License

No open-source license has been specified yet. A suitable license can be added when the project is ready for distribution.

---

**GreenOps Agent: Building more efficient, cost-conscious, and sustainable cloud infrastructure.**
