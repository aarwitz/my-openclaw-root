"""Shared credential loaders. All read from ~/.openclaw/credentials/."""
from __future__ import annotations

import json
from pathlib import Path
from functools import lru_cache

CRED_DIR = Path.home() / ".openclaw/credentials"


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    p = CRED_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"missing credential file: {p}")
    return json.loads(p.read_text())


def finnhub_key() -> str:
    return _load("finnhub-api.json")["api key"]


def massive_key() -> str:
    return _load("massive-api.json")["api key"]


def fmp_key() -> str:
    return _load("financial-modeling-prep-api.json")["api key"]


def alpaca() -> tuple[str, str, str]:
    c = _load("alpaca-api.json")
    return c["endpoint"], c["api key"], c["secret"]


def schwab_token_path() -> Path:
    return CRED_DIR / "schwab-dev-token.json"
