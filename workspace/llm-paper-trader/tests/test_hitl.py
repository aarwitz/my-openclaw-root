"""Tests for the HITL review queue."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import src.hitl.review_queue as rq


@pytest.fixture(autouse=True)
def temp_queue(tmp_path, monkeypatch):
    """Redirect queue storage to a temp file for test isolation."""
    q = tmp_path / "hitl_queue.json"
    monkeypatch.setattr(rq, "QUEUE_PATH", q)
    yield q


def test_enqueue_creates_entry():
    order = rq.enqueue("AAPL", "buy", 10, 150.0, "Positive signal", 0.75, "bull")
    assert order.status == "pending"
    assert order.symbol == "AAPL"
    assert order.notional == 1500.0


def test_enqueue_persists():
    rq.enqueue("MSFT", "sell", 5, 300.0, "Bearish", 0.65, "bear")
    pending = rq.pending_orders()
    assert len(pending) == 1
    assert pending[0].symbol == "MSFT"


def test_approve_order():
    order = rq.enqueue("NVDA", "buy", 3, 500.0, "Strong momentum", 0.80, "bull")
    updated = rq.review(order.order_id, approve=True, note="Looks good")
    assert updated is not None
    assert updated.status == "approved"
    assert updated.reviewer_note == "Looks good"


def test_reject_order():
    order = rq.enqueue("TSLA", "sell", 2, 200.0, "Negative signal", 0.60, "bear")
    updated = rq.review(order.order_id, approve=False, note="Too risky")
    assert updated.status == "rejected"


def test_pending_filters_reviewed():
    o1 = rq.enqueue("AAPL", "buy", 1, 100.0, "x", 0.6, "bull")
    o2 = rq.enqueue("MSFT", "buy", 1, 200.0, "y", 0.7, "bull")
    rq.review(o1.order_id, approve=True)
    pending = rq.pending_orders()
    assert len(pending) == 1
    assert pending[0].order_id == o2.order_id


def test_clear_reviewed():
    o = rq.enqueue("GOOG", "sell", 1, 50.0, "z", 0.5, "sideways")
    rq.review(o.order_id, approve=True)
    cleared = rq.clear_reviewed()
    assert cleared == 1
    assert len(rq.all_orders()) == 0


def test_review_nonexistent_returns_none():
    result = rq.review("fake-id-0000", approve=True)
    assert result is None
