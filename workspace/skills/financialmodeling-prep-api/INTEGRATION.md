# AutoTrade FMP Integration

Status: active-path summary, refreshed 2026-07-30.

FMP supplies fundamentals, analyst grades and price targets, insider
transactions, profiles, peers, screeners, and long/delisted EOD history.
Massive supplies the primary daily-bar and bounded bulk-snapshot paths.

Operational rules:

- Use connector code under
  `workspaces/trading-intel/scripts/connectors/`; do not call vendors from an
  agent prompt.
- A live quote failure must degrade promptly and visibly. Do not turn a
  provider timeout into a serial per-ticker retry storm.
- Stored intraday marks may preserve book continuity during an outage, but
  they are labeled fallback state and cannot authorize new risk.
- Historical features must remain point-in-time and pass the cost-net,
  multiple-testing-corrected backtest gate before sized use.
- Brokerage state is not a data-provider concern. The owned internal paper
  ledger is the only execution backend.

The former external-broker fallback instructions were retired with the D52
internal-ledger cutover and must not be restored.
