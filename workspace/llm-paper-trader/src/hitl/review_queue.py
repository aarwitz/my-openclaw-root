"""
Human-in-the-Loop (HITL) Trade Review Queue

Provides semi-automated trading with mandatory human approval for any order
above a configurable notional threshold.

State is stored in a JSON file so it persists across server restarts.
The frontend polls /api/hitl/pending and approves or rejects via POST.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


QUEUE_PATH = Path("data/hitl_queue.json")


@dataclass
class PendingOrder:
    order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    notional: float
    rationale: str
    signal_confidence: float
    regime: str
    submitted_at: str
    status: str = "pending"  # pending | approved | rejected
    reviewed_at: Optional[str] = None
    reviewer_note: Optional[str] = None


def _load() -> List[dict]:
    if not QUEUE_PATH.exists():
        return []
    try:
        return json.loads(QUEUE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save(orders: List[dict]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(orders, indent=2))


def enqueue(
    symbol: str,
    side: str,
    qty: int,
    price: float,
    rationale: str,
    confidence: float,
    regime: str,
) -> PendingOrder:
    """Add a trade to the pending approval queue and return it."""
    order = PendingOrder(
        order_id=str(uuid.uuid4()),
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        notional=qty * price,
        rationale=rationale,
        signal_confidence=confidence,
        regime=regime,
        submitted_at=datetime.now(timezone.utc).isoformat(),
    )
    orders = _load()
    orders.append(asdict(order))
    _save(orders)
    return order


def pending_orders() -> List[PendingOrder]:
    return [PendingOrder(**o) for o in _load() if o["status"] == "pending"]


def all_orders() -> List[PendingOrder]:
    return [PendingOrder(**o) for o in _load()]


def review(order_id: str, approve: bool, note: str = "") -> Optional[PendingOrder]:
    """Approve or reject a pending order. Returns the updated order or None if not found."""
    orders = _load()
    now = datetime.now(timezone.utc).isoformat()
    for o in orders:
        if o["order_id"] == order_id and o["status"] == "pending":
            o["status"] = "approved" if approve else "rejected"
            o["reviewed_at"] = now
            o["reviewer_note"] = note
            _save(orders)
            return PendingOrder(**o)
    return None


def clear_reviewed() -> int:
    """Remove all approved/rejected orders. Returns count cleared."""
    orders = _load()
    remaining = [o for o in orders if o["status"] == "pending"]
    cleared = len(orders) - len(remaining)
    _save(remaining)
    return cleared
