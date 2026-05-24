"""
LLM Paper Trader — Flask API Server

Endpoints:
  GET  /                          → Serve SPA frontend
  POST /api/optimize              → Run portfolio optimizer
  POST /api/backtest              → Backtest optimizer weights
  GET  /api/positions             → Paper broker positions
  GET  /api/signals               → Recent LLM trade signals
  GET  /api/hitl/pending          → Pending human-review orders
  POST /api/hitl/review/<id>      → Approve or reject a pending order
  POST /api/hitl/clear            → Clear reviewed orders
  GET  /api/regime/<ticker>       → Detect market regime for a ticker
  POST /api/run_pipeline          → One-click: news → LLM → signal → HITL queue
  GET  /api/portfolio_summary     → P&L snapshot
"""

from __future__ import annotations

import sys
import json
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict
import yfinance as yf

# Make src importable
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from src.optimizer.markowitz import (
    build_efficient_frontier,
    load_price_data,
    backtest_frontier,
)
from src.regime.detector import detect_regime
from src.hitl import review_queue as queue
from src.news.news_fetcher import NewsFetcher
from src.models.llm_client import LLMClient
from src.analysis.sentiment_analyzer import SentimentAnalyzer
from src.trading.base_trader import Order
from src.trading.paper_broker import PaperBroker
from src.trading.risk_manager import RiskManager
from src.trading.signal_generator import SignalExecutor

# ──────────────────────────────────────────────────────────────────────────────
# App setup
# ──────────────────────────────────────────────────────────────────────────────

STATIC_DIR = ROOT_DIR / "static"
SIGNAL_LOG = Path("data/signal_log.json")
SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
CORS(app)

# In-process broker (survives process lifetime; for persistence, swap for SQLite)
_broker = PaperBroker(initial_cash=100_000.0)
_risk = RiskManager(max_position_pct=0.10)
_executor = SignalExecutor(_risk)
_analyzer = SentimentAnalyzer()
_llm = LLMClient()
_fetcher = NewsFetcher()

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ok(data: Any) -> Any:
    return jsonify({"status": "ok", "data": data})


def _err(msg: str, code: int = 400) -> Any:
    return jsonify({"status": "error", "message": msg}), code


def _append_signal_log(entry: dict) -> None:
    logs: list = []
    if SIGNAL_LOG.exists():
        try:
            logs = json.loads(SIGNAL_LOG.read_text())
        except Exception:
            logs = []
    logs.append(entry)
    SIGNAL_LOG.write_text(json.dumps(logs[-200:], indent=2))  # keep latest 200


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_live_quote(symbol: str) -> dict:
    """Fetch a live-ish quote snapshot from yfinance."""
    hist = yf.download(symbol, period="5d", interval="1d", auto_adjust=True, progress=False)["Close"].dropna()
    if hist.empty:
        return {
            "symbol": symbol.upper(),
            "price": None,
            "previous_close": None,
            "change": None,
            "change_pct": None,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    price = _safe_float(hist.iloc[-1], 0.0)
    prev = _safe_float(hist.iloc[-2], price) if len(hist) > 1 else price
    change = price - prev
    change_pct = (change / prev * 100.0) if prev else 0.0
    return {
        "symbol": symbol.upper(),
        "price": round(price, 4),
        "previous_close": round(prev, 4),
        "change": round(change, 4),
        "change_pct": round(change_pct, 4),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _recent_signals_for(symbol: str, limit: int = 5) -> list[dict]:
    if not SIGNAL_LOG.exists():
        return []
    try:
        logs = json.loads(SIGNAL_LOG.read_text())
    except Exception:
        return []
    out = [s for s in reversed(logs) if s.get("symbol", "").upper() == symbol.upper()]
    return out[:limit]


# ──────────────────────────────────────────────────────────────────────────────
# Static SPA
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


# ──────────────────────────────────────────────────────────────────────────────
# Portfolio Optimizer
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/optimize", methods=["POST"])
def optimize():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", [])
    start = body.get("start", "2023-01-01")
    end = body.get("end", datetime.now().strftime("%Y-%m-%d"))
    n_mc = int(body.get("n_mc_trials", 0))
    rf = float(body.get("rf_daily", 0.0))

    if not tickers or len(tickers) < 2:
        return _err("Need at least 2 tickers.")
    try:
        data = load_price_data(tickers, start, end)
        ef = build_efficient_frontier(data, rf_daily=rf, n_mc_trials=n_mc, mc_years=1.0)
        return _ok({
            "tickers": ef.tickers,
            "start": ef.start,
            "end": ef.end,
            "global_min": _pt_to_dict(ef.global_min),
            "sharpe_max": _pt_to_dict(ef.sharpe_max),
            "frontier": [_pt_to_dict(p) for p in ef.frontier_points],
            "montecarlo_paths": ef.montecarlo_paths[:50],  # cap for JSON size
        })
    except Exception as e:
        return _err(f"Optimizer failed: {e}")


def _pt_to_dict(pt) -> dict:
    return {
        "weights": pt.weights,
        "daily_return_pct": round(pt.expected_daily_return * 100, 4),
        "daily_std_pct": round(pt.daily_std * 100, 4),
        "sharpe": round(pt.sharpe_ratio, 4),
        "ann_return_pct": round(pt.annualized_return * 100, 2),
        "ann_std_pct": round(pt.annualized_std * 100, 2),
    }


@app.route("/api/backtest", methods=["POST"])
def backtest():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", [])
    model_start = body.get("model_start", "2022-01-01")
    model_end = body.get("model_end", "2023-01-01")
    test_start = body.get("test_start", "2023-01-01")
    test_end = body.get("test_end", datetime.now().strftime("%Y-%m-%d"))
    n = int(body.get("n_portfolios", 10))

    if not tickers or len(tickers) < 2:
        return _err("Need at least 2 tickers.")
    try:
        model_data = load_price_data(tickers, model_start, model_end)
        result = backtest_frontier(model_data, test_start, test_end, n)
        return _ok({
            "model_start": result.model_start,
            "model_end": result.model_end,
            "test_start": result.test_start,
            "test_end": result.test_end,
            "portfolios": result.portfolios,
        })
    except Exception as e:
        return _err(f"Backtest failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Regime detection
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/regime/<ticker>")
def regime(ticker: str):
    try:
        import yfinance as yf
        hist = yf.download(ticker, period="1y", auto_adjust=True, progress=False)["Close"]
        prices = hist.dropna().tolist()
        result = detect_regime(prices)
        return _ok({
            "ticker": ticker.upper(),
            "regime": result.regime,
            "realized_vol_ann_pct": round(result.realized_vol_annualized * 100, 2),
            "trend_signal_pct": round(result.trend_signal * 100, 4),
            "description": result.description,
        })
    except Exception as e:
        return _err(f"Regime detection failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# HITL queue
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/hitl/pending")
def hitl_pending():
    return _ok([vars(o) for o in queue.pending_orders()])


@app.route("/api/hitl/all")
def hitl_all():
    return _ok([vars(o) for o in queue.all_orders()])


@app.route("/api/hitl/review/<order_id>", methods=["POST"])
def hitl_review(order_id: str):
    body = request.get_json(silent=True) or {}
    approve = bool(body.get("approve", False))
    note = str(body.get("note", ""))
    updated = queue.review(order_id, approve, note)
    if updated is None:
        return _err("Order not found or already reviewed.", 404)
    # If approved, execute in paper broker
    if approve:
        approved_order = Order(
            symbol=updated.symbol,
            side=updated.side,
            qty=updated.qty,
            price=updated.price,
        )
        _broker.execute_order(approved_order)
    return _ok(vars(updated))


@app.route("/api/hitl/clear", methods=["POST"])
def hitl_clear():
    n = queue.clear_reviewed()
    return _ok({"cleared": n})


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline: news → LLM → signal → HITL queue
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/run_pipeline", methods=["POST"])
def run_pipeline():
    body = request.get_json(silent=True) or {}
    symbols = body.get("symbols", ["AAPL", "MSFT", "GOOGL"])
    hitl_threshold = float(body.get("hitl_threshold", 500.0))  # USD notional

    results = []
    for symbol in symbols:
        try:
            articles = _fetcher.fetch(symbol)
            for article in articles[:2]:
                insight = _llm.analyze([article])
                signal = _analyzer.build_signal(symbol, insight)

                # Rough price estimate (fallback to 100 if yfinance fails)
                price = _get_last_price(symbol)
                order = _executor.to_order(signal, _broker.portfolio_value({symbol: price}), _broker.state.cash, price)

                if order is None:
                    continue

                notional = order.qty * price
                entry = {
                    "symbol": symbol,
                    "article_title": article.title,
                    "direction": insight.direction,
                    "confidence": insight.confidence,
                    "rationale": insight.rationale,
                    "action": signal.action,
                    "qty": order.qty,
                    "price": price,
                    "notional": notional,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                _append_signal_log(entry)

                if signal.action != "hold":
                    if notional >= hitl_threshold:
                        # Route to HITL queue for human approval
                        regime_data = _get_regime_cached(symbol)
                        pending = queue.enqueue(
                            symbol=symbol,
                            side=order.side,
                            qty=order.qty,
                            price=price,
                            rationale=insight.rationale,
                            confidence=insight.confidence,
                            regime=regime_data,
                        )
                        entry["queued_for_review"] = True
                        entry["order_id"] = pending.order_id
                    else:
                        # Auto-execute small orders
                        _broker.execute_order(order)
                        entry["auto_executed"] = True

                results.append(entry)
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)})

    return _ok(results)


def _get_last_price(symbol: str) -> float:
    try:
        hist = yf.download(symbol, period="2d", auto_adjust=True, progress=False)["Close"]
        if not hist.empty:
            return float(hist.dropna().iloc[-1])
    except Exception:
        pass
    return 100.0


def _get_regime_cached(symbol: str) -> str:
    try:
        hist = yf.download(symbol, period="1y", auto_adjust=True, progress=False)["Close"]
        prices = hist.dropna().tolist()
        return detect_regime(prices).regime
    except Exception:
        return "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Broker state
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/live/quote/<ticker>")
def live_quote(ticker: str):
    try:
        return _ok(_get_live_quote(ticker))
    except Exception as e:
        return _err(f"Live quote failed: {e}")


@app.route("/api/live/candles/<ticker>")
def live_candles(ticker: str):
    period = request.args.get("period", "1mo")
    interval = request.args.get("interval", "1d")
    try:
        hist = yf.download(ticker, period=period, interval=interval, auto_adjust=True, progress=False)
        if hist.empty:
            return _ok({"ticker": ticker.upper(), "period": period, "interval": interval, "candles": []})

        candles = []
        for idx, row in hist.iterrows():
            candles.append({
                "time": idx.isoformat(),
                "open": _safe_float(row.get("Open"), 0.0),
                "high": _safe_float(row.get("High"), 0.0),
                "low": _safe_float(row.get("Low"), 0.0),
                "close": _safe_float(row.get("Close"), 0.0),
                "volume": int(_safe_float(row.get("Volume"), 0.0)),
            })

        return _ok({
            "ticker": ticker.upper(),
            "period": period,
            "interval": interval,
            "candles": candles,
        })
    except Exception as e:
        return _err(f"Live candles failed: {e}")


@app.route("/api/ticker/<ticker>")
def ticker_detail(ticker: str):
    sym = ticker.upper()
    pos = _broker.state.positions.get(sym)
    quote = _get_live_quote(sym)
    regime = _get_regime_cached(sym)
    recent_signals = _recent_signals_for(sym, limit=8)

    position = None
    if pos:
        mkt = _safe_float(quote.get("price"), pos.avg_price)
        unreal = (mkt - pos.avg_price) * pos.qty
        unreal_pct = ((mkt - pos.avg_price) / pos.avg_price * 100.0) if pos.avg_price else 0.0
        position = {
            "qty": pos.qty,
            "avg_price": round(pos.avg_price, 4),
            "market_price": round(mkt, 4),
            "market_value": round(mkt * pos.qty, 2),
            "unrealized_pnl": round(unreal, 2),
            "unrealized_pnl_pct": round(unreal_pct, 2),
        }

    return _ok({
        "ticker": sym,
        "quote": quote,
        "regime": regime,
        "position": position,
        "recent_signals": recent_signals,
    })


@app.route("/api/portfolio/tickers")
def portfolio_tickers():
    symbols = set(_broker.state.positions.keys())
    symbols.update(o.symbol for o in _broker.state.history[-100:])
    symbols.update(o.symbol for o in queue.pending_orders())
    if not symbols:
        symbols.update(["AAPL", "MSFT", "GOOGL"])

    tickers = []
    for sym in sorted(symbols):
        quote = _get_live_quote(sym)
        pos = _broker.state.positions.get(sym)
        qty = pos.qty if pos else 0
        avg = pos.avg_price if pos else 0.0
        mkt = _safe_float(quote.get("price"), avg)
        tickers.append({
            "symbol": sym,
            "in_portfolio": bool(pos),
            "qty": qty,
            "avg_price": round(avg, 4) if pos else None,
            "market_price": quote.get("price"),
            "change_pct": quote.get("change_pct"),
            "market_value": round(mkt * qty, 2) if pos else 0.0,
        })

    return _ok(tickers)

@app.route("/api/positions")
def positions():
    mark_prices = {}
    positions_out = {}
    for sym, pos in _broker.state.positions.items():
        q = _get_live_quote(sym)
        market_price = _safe_float(q.get("price"), pos.avg_price)
        mark_prices[sym] = market_price
        unrealized = (market_price - pos.avg_price) * pos.qty
        unrealized_pct = ((market_price - pos.avg_price) / pos.avg_price * 100.0) if pos.avg_price else 0.0
        positions_out[sym] = {
            "qty": pos.qty,
            "avg_price": round(pos.avg_price, 2),
            "market_price": round(market_price, 2),
            "value": round(market_price * pos.qty, 2),
            "unrealized_pnl": round(unrealized, 2),
            "unrealized_pnl_pct": round(unrealized_pct, 2),
        }

    return _ok({
        "cash": round(_broker.state.cash, 2),
        "portfolio_value": round(_broker.portfolio_value(mark_prices), 2),
        "positions": positions_out,
        "history": [
            {"symbol": o.symbol, "side": o.side, "qty": o.qty, "price": o.price}
            for o in _broker.state.history[-50:]
        ],
    })


@app.route("/api/signals")
def signals():
    if SIGNAL_LOG.exists():
        try:
            logs = json.loads(SIGNAL_LOG.read_text())
            return _ok(list(reversed(logs[-50:])))
        except Exception:
            pass
    return _ok([])


@app.route("/api/portfolio_summary")
def portfolio_summary():
    mark_prices = {sym: _safe_float(_get_live_quote(sym).get("price"), pos.avg_price) for sym, pos in _broker.state.positions.items()}
    n_trades = len(_broker.state.history)
    return _ok({
        "total_pnl": 0.0,  # would need cost-basis tracking for real P&L
        "n_trades": n_trades,
        "win_rate": 0,
        "cash": round(_broker.state.cash, 2),
        "portfolio_value": round(_broker.portfolio_value(mark_prices), 2),
    })


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8011, debug=False)
