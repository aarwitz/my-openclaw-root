# LLM Paper Trader

A modular, testable trading research system inspired by Ethan Holly's `llm_trader` project structure and development flow, but restricted to paper trading only.

## What This Repo Does

- Ingests market-news text (API-backed or offline fixtures)
- Generates sentiment-driven trade signals
- Applies risk controls and position sizing
- Simulates orders with an in-memory paper broker
- Produces performance metrics for evaluation

## Safety Scope

This project does not support live order routing.

- `TRADING_MODE` must be `paper`
- `PAPER_TRADING_ONLY=true` guard is required
- Any non-paper mode raises an error

## Architecture

```
llm-paper-trader/
├── src/
│   ├── config/
│   ├── news/
│   ├── models/
│   ├── analysis/
│   ├── optimizer/
│   ├── regime/
│   ├── hitl/
│   ├── trading/
│   ├── evaluation/
│   └── utils/
├── static/
├── server.py
├── scripts/
├── tests/
├── data/
└── logs/
```

## Prerequisites

- Python 3.10+
- `pip`
- Linux/macOS shell (commands below use bash)

## Setup

```bash
git clone https://github.com/aaronclawrsl-bot/llm-paper-trader.git
cd llm-paper-trader

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

## Quick Start (CLI Pipeline)

```bash
source .venv/bin/activate
python scripts/test_pipeline.py
python scripts/paper_trading_demo.py
```

## Web App Start (Frontend + API)

```bash
source .venv/bin/activate
python server.py
```

Default URL:

- `http://127.0.0.1:8011`
- On this machine/hostname setup: `http://rsl:8011`

## Run In Background

```bash
source .venv/bin/activate
mkdir -p logs
nohup python server.py > logs/server.log 2>&1 &
```

Stop it:

```bash
kill $(lsof -ti:8011)
```

## Testing

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
```

## Core Features

- Markowitz optimizer (efficient frontier, global-min-variance, Sharpe-max)
- Walk-forward backtest
- Regime detection (`bull`, `bear`, `high_vol`, `sideways`)
- Paper broker execution only
- Human-in-the-loop (HITL) order review queue
- Live quote and candles via yfinance
- Dashboard with clickable ticker drill-down

## Key API Endpoints

- `GET /` web UI
- `GET /api/portfolio_summary`
- `GET /api/positions`
- `GET /api/signals`
- `POST /api/run_pipeline`
- `POST /api/optimize`
- `POST /api/backtest`
- `GET /api/regime/<ticker>`
- `GET /api/live/quote/<ticker>`
- `GET /api/live/candles/<ticker>?period=1mo&interval=1d`
- `GET /api/portfolio/tickers`
- `GET /api/ticker/<ticker>`
- `GET /api/hitl/pending`
- `GET /api/hitl/all`
- `POST /api/hitl/review/<order_id>`
- `POST /api/hitl/clear`

## Example Usage

Run pipeline for three symbols and route larger notionals to manual review:

```bash
curl -sS -X POST http://127.0.0.1:8011/api/run_pipeline \
	-H 'Content-Type: application/json' \
	--data '{"symbols":["AAPL","MSFT","GOOGL"],"hitl_threshold":500}' | jq
```

Check pending approvals:

```bash
curl -sS http://127.0.0.1:8011/api/hitl/pending | jq
```

## Important Operational Notes

- Data source is yfinance and can be delayed/intermittent.
- The paper broker is in-memory for process lifetime; restarting `server.py` resets broker state.
- HITL queue and signal logs are persisted under `data/`.
- This repo intentionally does not place live trades.

## Disclaimer

Educational and research use only. No live trading functionality is implemented in this repository.
