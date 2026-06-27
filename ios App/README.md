# AutoTrade iOS App

Consumer-facing iOS app that surfaces AutoTrade signals and lets users link their Robinhood account to follow or auto-execute trades.

## What it does

- Streams live trading signals from the OpenClaw AutoTrade engine (AI-generated, risk-gated)
- Users link their Robinhood account once via OAuth-style flow
- Signals arrive as push notifications with one-tap execution
- Portfolio view shows P&L on followed signals

## Architecture

```
iPhone App (SwiftUI)
    │
    ▼
OpenClaw Mobile API  (ios App/backend/autotrade_mobile_api.py)
    │
    ├── trading-intel.sqlite  (signal/hypothesis data)
    └── Robinhood API         (robin_stocks, same auth as MCP tools)
```

The backend is a lightweight Flask app that runs alongside the gateway. It exposes:
- `GET  /api/signals`        — approved/submitted/filled intents
- `GET  /api/signals/:id`    — signal detail + hypothesis thesis
- `GET  /api/portfolio`      — live Robinhood portfolio snapshot
- `POST /api/robinhood/link` — store user's Robinhood session token
- `POST /api/signals/:id/execute` — place order via Robinhood
- `GET  /api/performance`    — win rate, P&L, recent outcomes

## Setup (Xcode)

1. Open Xcode → File → New → Project → App
2. Set: Product Name = `AutoTrade`, Bundle ID = `com.lidisolutions.autotrade`
3. Language: Swift, Interface: SwiftUI, Minimum iOS: 16.0
4. Replace generated files with the ones in `AutoTrade/`
5. Add `AutoTrade/Resources/Assets.xcassets` for icons/colors

### Start the backend
```bash
cd "ios App/backend"
pip install -r requirements.txt
python autotrade_mobile_api.py
# Runs on :8765 by default; set OPENCLAW_MOBILE_API_PORT to override
```

Expose externally (for push notifications + remote app access):
```bash
tailscale funnel 8765
```

## Design language

- Dark background (#0D0D0D), card surface (#1A1A1A)
- Signal green (#00D96A), signal red (#FF3B3B), accent (#7B61FF)
- SF Pro Rounded for numbers; SF Pro Display for headings
- Haptic feedback on signal arrival and trade execution

## Signal lifecycle (what the app shows)

```
proposed → critic_review → risk_review → approved* → submitted* → filled*
                                                  ↘ blocked
```
`*` = visible to app users. `approved` = signal to act on. `filled` = executed.
