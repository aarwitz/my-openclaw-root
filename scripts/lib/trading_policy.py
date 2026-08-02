"""Pure runtime trading-policy invariants shared across the money path."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


# Research and backtests may study short signals. Runtime short opening remains
# disabled until the simulator models borrow availability, fees, collateral,
# rebates, recalls, and margin. Existing shorts may always be covered/reduced.
SHORT_OPEN_ENABLED = False
SHORT_OPEN_BLOCK_REASON = (
    "short_open_disabled: internal simulator has no borrow availability, "
    "borrow-fee, collateral, rebate, recall, or margin model"
)


def blocks_new_short(action: object, direction: object) -> bool:
    return (
        not SHORT_OPEN_ENABLED
        and str(direction or "long").lower() == "short"
        and str(action or "open").lower() not in {"exit", "trim"}
    )


def would_increase_short(existing_qty: float, side: str, qty: float) -> bool:
    """Whether a fill would create or enlarge negative signed inventory."""
    signed = float(qty) if str(side).lower() == "buy" else -float(qty)
    before = min(float(existing_qty), 0.0)
    after = min(float(existing_qty) + signed, 0.0)
    return abs(after) > abs(before) + 1e-9


def short_collateral(positions: Iterable[Mapping[str, object]]) -> float:
    """Conservative restricted proceeds at current mark, falling back to basis."""
    total = 0.0
    for position in positions:
        qty = float(position.get("qty") or 0.0)
        if qty >= 0:
            continue
        price = float(
            position.get("current_price")
            or position.get("cost_basis")
            or 0.0
        )
        total += abs(qty) * max(0.0, price)
    return total


def deployable_cash(ledger_cash: float, restricted_short_collateral: float) -> float:
    return max(0.0, float(ledger_cash) - max(0.0, float(restricted_short_collateral)))
