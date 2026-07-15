# TM issue draft (operator to file to ATS v6 sprint, or hand to Dwight's next PM pass)
# drag:pos-sync-placeholder-recurrence-20260715

Title: Post-D52 vestigial-organ audit: find every component still assuming Alpaca is the money path

Measured deficiency: the POS-SYNC placeholder-hypothesis disease (D51.1) RECURRED after the
D52 sim cutover — 4 positions opened 2026-07-15 (GE/TMUS/ETN/ABBV) plus HIMS earlier were
created by reconcile's placeholder factory with fabricated hypotheses: execute_intent's
instant sim fills never created desk positions, and sync_fills (built for slow Alpaca fills)
had nothing non-terminal to process. Root fix shipped same day (execute_intent books
positions with real lineage at fill time; sync_fills relinks every pass). This issue is the
systemic sweep the incident implies.

Scope — audit every component for pre-cutover assumptions now vestigial or misleading:
1. sim_broker shadow book + nightly parity — compares the shadow ledger to the FROZEN Alpaca
   account; post-cutover this validates nothing. Retire, or repoint at
   desk-positions-vs-sim-ledger invariants.
2. reconcile.py — placeholder repair should refuse to fabricate lineage when a desk order
   exists for the symbol (defense in depth even after the root fix).
3. sync_fills.py sync() — Alpaca get_order polling is dead weight unless P2 partial-fill
   simulation will produce working orders.
4. Anything reading alpaca list_positions/get_account as "the account" (scoreboard, sweep
   checks, snapshot_builder broker section).

Acceptance: PR table listing every audited component -> verdict (correct / vestigial-retired
/ repointed); zero remaining code paths that can fabricate hypothesis lineage; a regression
test that fails if a filled desk order ever yields a HYP-SYNC hypothesis.
