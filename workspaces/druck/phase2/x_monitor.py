from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .http_util import now_iso

CRED_PATH = Path(os.path.expanduser('~/.openclaw/credentials/x-api.json'))
CACHE_PATH = Path(os.path.expanduser('~/.openclaw/workspaces/druck/phase2_cache/intraday/x_monitor_posts.json'))
TARGET_ACCOUNTS = ["TrumpDailyPosts", "BillAckman"]

DOLLAR_TICKER_RE = re.compile(r'\$([A-Z]{1,5})\b')
PLAIN_TICKER_RE = re.compile(r'\b[A-Z]{2,5}\b')
COMMON_NON_TICKERS = {
    'WHEN','FAKE','NEWS','SAYS','THAT','ENEMY','DOING','WELL','SUCH','FALSE','EVEN','THEY','ARE','TRUMP','POSTS','VIDEO','OBAMA','WHITE','HOUSE','LIKE','HER','HTTPS','COURT','RULED','BLOCK','BASED','MAPS','THIS','PUTS','SEAT','FAUCI','HOOK','YET','CUBA','GOING','TALK','FRONT','SHUT','DOWN','USING','JUST','WITH','FROM','YOUR','HUGE','ABOUT','ONLY','HELP','COUNTRY','FAILED','HEADING','DIRECTION','LEFTIST','ELITISTS','PRESIDENT','SOCIAL','POST','DONALD','TRUTH','EST','PM','AM','THE','AND','FOR','NOT','OFF','LET','ITS','AIDING','AGAINST','MIDDLE','EAST','COALITION','RADICAL','MULLAHS','IRAN','CNBC','WORLD','GREATEST','BUSINESSMEN','WOMEN','CHINA'
}
THEME_MAP = {
    'tariff': 'trade_policy',
    'government': 'government_support',
    'department of defense': 'defense',
    'pentagon': 'defense',
    'chips': 'semiconductors',
    'intel': 'semiconductors',
    'lithium': 'critical_minerals',
    'rare earth': 'critical_minerals',
    'nuclear': 'nuclear_power',
    'uranium': 'nuclear_power',
    'factory': 'domestic_manufacturing',
}


@dataclass
class XEvent:
    source: str
    account: str
    posted_at: str
    text: str
    event_type: str
    mentioned_tickers: list[str]
    theme_tags: list[str]
    confidence: float
    first_order_beneficiaries: list[str]
    sympathy_names: list[str]
    requires_confirmation: bool
    url: Optional[str] = None



def _creds() -> dict:
    with open(CRED_PATH) as f:
        return json.load(f)



def _headers() -> dict:
    return {
        'Authorization': f"Bearer {_creds()['bearer_token']}",
        'Content-Type': 'application/json',
    }



def _user_lookup(username: str) -> Optional[str]:
    r = requests.get(
        f'https://api.twitter.com/2/users/by/username/{username}',
        headers=_headers(),
        timeout=20,
    )
    r.raise_for_status()
    data = r.json().get('data') or {}
    return data.get('id')



def _recent_posts(user_id: str, limit: int = 10) -> list[dict]:
    params = {
        'max_results': min(max(limit, 5), 20),
        'tweet.fields': 'created_at,text',
        'exclude': 'replies,retweets',
    }
    r = requests.get(
        f'https://api.twitter.com/2/users/{user_id}/tweets',
        headers=_headers(),
        params=params,
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get('data') or []



def _normalize_text(text: str) -> str:
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace('’', "'")
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_tickers(text: str) -> list[str]:
    out = []
    upper = text.upper()
    for tok in DOLLAR_TICKER_RE.findall(upper):
        if tok not in out:
            out.append(tok)
    if out:
        return out
    for tok in PLAIN_TICKER_RE.findall(upper):
        if tok in COMMON_NON_TICKERS or len(tok) <= 2:
            continue
        if tok not in out:
            out.append(tok)
    return out[:6]



def _theme_tags(text: str) -> list[str]:
    t = text.lower()
    tags = []
    for k, v in THEME_MAP.items():
        if k in t and v not in tags:
            tags.append(v)
    return tags



def _sympathy(tags: list[str], tickers: list[str]) -> list[str]:
    out = []
    if 'critical_minerals' in tags:
        out += ['LAC', 'ALB', 'MP']
    if 'semiconductors' in tags:
        out += ['INTC', 'NVDA', 'AMD', 'TSM']
    if 'nuclear_power' in tags:
        out += ['OKLO', 'SMR', 'CCJ']
    if 'defense' in tags:
        out += ['LMT', 'NOC', 'RTX']
    dedup = []
    for x in out:
        if x not in dedup and x not in tickers:
            dedup.append(x)
    return dedup[:8]



def _classify_event(text: str, tags: list[str]) -> str:
    tl = text.lower()
    if any(x in tl for x in ['buy', 'bought', 'acquired', 'stake', 'position']):
        return 'purchase_signal'
    if any(x in tl for x in ['government', 'department', 'pentagon', 'white house', 'executive order', 'tariff', 'subsidy', 'grant']):
        return 'policy_announcement'
    if any(x in tl for x in ['earnings', 'guidance', 'forecast', 'raises guidance']):
        return 'company_catalyst'
    if tags:
        return 'theme_signal'
    return 'macro_commentary'



def normalize_post(account: str, post: dict) -> XEvent:
    raw_text = post.get('text') or ''
    text = _normalize_text(raw_text)
    tickers = _extract_tickers(text)
    tags = _theme_tags(text)
    event_type = _classify_event(text, tags)
    conf = 0.85 if (tickers and tags) else 0.65 if (tickers or tags) else 0.25
    if event_type == 'macro_commentary' and not tickers and not tags:
        conf = 0.15
    url = None
    if post.get('id'):
        url = f'https://x.com/{account}/status/{post["id"]}'
    return XEvent(
        source='x',
        account=account,
        posted_at=post.get('created_at') or now_iso(),
        text=text,
        event_type=event_type,
        mentioned_tickers=tickers,
        theme_tags=tags,
        confidence=conf,
        first_order_beneficiaries=tickers[:6] if tickers else [],
        sympathy_names=_sympathy(tags, tickers),
        requires_confirmation=True,
        url=url,
    )



def fetch_events(accounts: Optional[list[str]] = None, limit_per_account: int = 5) -> list[dict]:
    accounts = accounts or TARGET_ACCOUNTS
    events = []
    for acct in accounts:
        uid = _user_lookup(acct)
        if not uid:
            continue
        posts = _recent_posts(uid, limit=limit_per_account)
        for p in posts:
            events.append(asdict(normalize_post(acct, p)))
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({'fetched_at': now_iso(), 'events': events}, indent=2))
    return events


if __name__ == '__main__':
    print(json.dumps(fetch_events(), indent=2))
