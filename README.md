# OpenADR 3 Demand Flexibility for Hot Water Heaters

Demonstrates how to use OpenADR 3.0 to communicate demand flexibility signals for heat pump water heaters (HPWHs). The system fetches electricity pricing data, publishes it through an OpenADR 3 VTN, and uses an LP-based scheduler to generate globally optimal heat pump operation schedules.

## Folder Structure

```
annex96-a3-hotwater/
├── README.md                        # This file
├── requirements.txt                 # Python dependencies
├── instructions.ipynb               # Setup guide using the Python VTN reference implementation
├── instructions-openleadr.ipynb     # Setup guide using the Rust-based openleadr-rs VTN
├── quickstart.ipynb                 # Interactive demo (Python VTN)
├── quickstart-openleadr.ipynb       # Interactive demo (openleadr-rs VTN)
├── controls/                        # HPWH load-shift schedulers and CTA-2045 generation
├── sample_data/                     # Pre-built OpenADR 3 JSON payloads
├── presentation/                    # Marp presentation slides and SVG diagrams
└── OpenADR 3.0 Specification_3.0.1/ # OpenADR 3.0.1 spec (YAML, PDFs)
```

## Installation

```bash
pip install -r requirements.txt
```

## Getting Started

There are two VTN options. Choose one based on your preference:

### Option A: Python VTN Reference Implementation

Uses the OpenADR Alliance's Python-based VTN reference implementation.

> **Access:** The `openadr3-vtn-reference-implementation` repository is not publicly available. Contact **Anand Prakash (anandkrp@andrew.cmu.edu)** for access.

1. Follow `instructions.ipynb` for setup
2. Start the VTN in a separate terminal
3. Open `quickstart.ipynb` and run all cells

### Option B: openleadr-rs (Rust VTN)

Uses the open-source Rust-based OpenADR 3.0 implementation. Requires Docker (for PostgreSQL) and Rust.

1. Clone the repository: `git clone https://github.com/OpenLEADR/openleadr-rs.git`
2. Follow `instructions-openleadr.ipynb` for setup
3. Start the VTN in a separate terminal
4. Open `quickstart-openleadr.ipynb` and run all cells

## Quick Overview

| File | Description |
|---|---|
| `instructions.ipynb` | Prerequisites, setup, and component docs for the Python VTN reference implementation |
| `instructions-openleadr.ipynb` | Same for the Rust-based openleadr-rs VTN (requires Docker for PostgreSQL) |
| `quickstart.ipynb` | End-to-end Python notebook: fetch prices, publish to VTN, read as VEN, run LP scheduler |
| `quickstart-openleadr.ipynb` | Same workflow using the openleadr-rs VTN |
| `controls/` | LP and heuristic HPWH schedulers, CTA-2045 schedule generation |
| `sample_data/` | Example OpenADR 3 program and event JSON files |
| `OpenADR 3.0 Specification_3.0.1/` | Normative YAML spec and reference PDFs (download from [OpenADR Alliance](https://www.openadr.org/)) |

## References

- OpenADR Alliance, "OpenADR 3.0.1 Specification," [openadr.org](https://www.openadr.org/)
- OpenLEADR, "openleadr-rs," [github.com/OpenLEADR/openleadr-rs](https://github.com/OpenLEADR/openleadr-rs)
- scipy, "scipy.optimize.linprog / HiGHS," [scipy.org](https://scipy.org)
