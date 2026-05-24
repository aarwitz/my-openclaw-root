"""Curated universe constants — ETFs, sector mappings, mover thresholds.

~100 major US ETFs covering broad market, sectors, themes, factor, intl, fixed income.
Used by market_scanner.py for liquid-mover ranking and sector-leadership detection.
"""
from __future__ import annotations


# ----- Broad market / index ETFs -----
BROAD_MARKET_ETFS = (
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "VTV", "VUG", "MDY", "RSP",
)

# ----- Sector SPDRs (one per GICS sector) -----
SECTOR_ETFS = {
    "Technology":            "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":      "XLP",
    "Energy":                "XLE",
    "Financials":            "XLF",
    "Health Care":           "XLV",
    "Industrials":           "XLI",
    "Materials":             "XLB",
    "Real Estate":           "XLRE",
    "Utilities":             "XLU",
}

# ----- Industry / sub-sector ETFs -----
INDUSTRY_ETFS = (
    "SMH", "SOXX",                                    # Semiconductors
    "XBI", "IBB", "IHI",                              # Biotech / Med Devices
    "KRE", "KBE", "IAI",                              # Banks / Brokers
    "XOP", "OIH", "AMLP",                             # Oil & Gas
    "ITA", "PPA", "XAR",                              # Defense / Aerospace
    "XHB", "ITB",                                     # Homebuilders
    "JETS",                                           # Airlines
    "MOO",                                            # Agribusiness
    "GDX", "GDXJ", "SIL",                             # Gold / Silver miners
    "URA", "NLR",                                     # Uranium / Nuclear
    "LIT",                                            # Lithium / Battery
    "TAN", "ICLN", "QCLN",                            # Solar / Clean Energy
    "REMX",                                           # Rare earth / strategic metals
    "COPX",                                           # Copper miners
    "WOOD",                                           # Timber
    "PBJ",                                            # Food & Beverage
)

# ----- Thematic / growth ETFs -----
THEMATIC_ETFS = (
    "ARKK", "ARKG", "ARKF", "ARKQ", "ARKW",          # ARK
    "BOTZ", "ROBO",                                   # Robotics / AI
    "AIQ", "QTUM",                                    # AI / Quantum
    "BLOK", "BITO",                                   # Crypto / Blockchain
    "HACK", "CIBR",                                   # Cybersecurity
    "CLOU", "WCLD", "SKYY",                           # Cloud
    "ROKT", "UFO",                                    # Space
    "MJ",                                             # Cannabis
    "ESPO", "HERO",                                   # Gaming / Esports
    "FINX",                                           # Fintech
)

# ----- Factor / smart beta -----
FACTOR_ETFS = (
    "MTUM", "QUAL", "USMV", "VLUE", "SIZE",          # iShares factor
    "MOAT",                                           # Wide moat
    "SCHD",                                           # Dividend
    "HYG", "LQD", "TLT", "AGG", "BND",               # Fixed income
    "VYM",                                            # High dividend yield
)

# ----- International -----
INTERNATIONAL_ETFS = (
    "EFA", "EEM", "VEA", "VWO", "FXI", "EWJ", "EWZ", "INDA", "MCHI", "EWG",
)

# ----- Volatility / Inverse -----
VOL_ETFS = (
    "VIXY", "UVXY", "SVXY", "SQQQ", "TQQQ", "SOXL", "SOXS",
)


def etf_universe() -> list[str]:
    """Return the full curated ETF universe (~100 names) for scanning."""
    seen: set[str] = set()
    out: list[str] = []
    for group in (
        BROAD_MARKET_ETFS,
        tuple(SECTOR_ETFS.values()),
        INDUSTRY_ETFS,
        THEMATIC_ETFS,
        FACTOR_ETFS,
        INTERNATIONAL_ETFS,
        VOL_ETFS,
    ):
        for t in group:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def sector_etf_for(sector: str) -> str | None:
    """Map GICS sector name to its SPDR ticker."""
    return SECTOR_ETFS.get(sector)


def is_etf(ticker: str) -> bool:
    """Heuristic — true if ticker is in our curated ETF universe."""
    return ticker.upper() in set(etf_universe())


# ----- Junk / exclude patterns -----
EXCLUDE_SUFFIX = ("W", "WS", "WT", "R", "RT", "U")  # warrants/rights/units
EXCLUDE_PREFIX_DOTS = (".",)


def is_junk_ticker(ticker: str) -> bool:
    """Filter out warrants, rights, OTC-style symbols."""
    if not ticker or not isinstance(ticker, str):
        return True
    t = ticker.upper().strip()
    if "." in t:
        return True
    if "/" in t:  # class shares like BRK/B (not the regular BRKB)
        return True
    if len(t) > 5:  # most US common stocks are <=5 chars
        return True
    return False


# ----- Default mover-scan thresholds (Druck-approved) -----
DEFAULT_MIN_PRICE = 5.0
DEFAULT_MIN_DOLLAR_VOL_M = 25.0   # $25M
DEFAULT_MAX_SPREAD_PCT = 0.02     # 2% hard cap
DEFAULT_PREF_SPREAD_PCT = 0.01    # 1% preferred
