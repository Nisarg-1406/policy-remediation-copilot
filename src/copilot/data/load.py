"""
Data loading and portfolio services.

Wraps helpers/portfolio.py. Provides:
- load_portfolio_data()     → all CSVs as DataFrames
- market_values(book)       → {security_id: mv}
- scope(hh_id, rule)        → list[account_id]
- positions(accounts)       → list[Position]
- exposures(positions, rule)→ list[Exposure]
- issue_lookup()            → {norm_name: [issuer_id]}
- account_lookup()          → {norm_desc: account_id}
- data_dictionary()         → dict for prompt construction

No LLM calls.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
DATA_ROOT = REPO_ROOT / "data"
PORTFOLIO_DIR = DATA_ROOT / "portfolio"
VALUATION_DATE = "2026-09-25"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Position:
    account_id: str
    security_id: str
    quantity: float
    mv: float
    price_date: str
    is_stale: bool


@dataclass
class Exposure:
    account_id: str
    security_id: str
    via_fund: Optional[str]
    issuer_id: Optional[str]
    asset_class: str
    sub_asset_class: str
    mv: float
    source: str  # "direct" | "lookthrough"
    missing_data: bool = False


# ---------------------------------------------------------------------------
# Raw CSV loading
# ---------------------------------------------------------------------------

def _load_csv(filename: str) -> List[Dict[str, str]]:
    path = PORTFOLIO_DIR / filename
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_cache: Dict[str, Any] = {}


def _cached(key: str, loader):
    if key not in _cache:
        _cache[key] = loader()
    return _cache[key]


def households() -> List[Dict[str, str]]:
    return _cached("households", lambda: _load_csv("households.csv"))


def accounts() -> List[Dict[str, str]]:
    return _cached("accounts", lambda: _load_csv("accounts.csv"))


def holdings() -> List[Dict[str, str]]:
    return _cached("holdings", lambda: _load_csv("holdings.csv"))


def prices() -> List[Dict[str, str]]:
    return _cached("prices", lambda: _load_csv("prices.csv"))


def securities() -> List[Dict[str, str]]:
    return _cached("securities", lambda: _load_csv("securities.csv"))


def approved_list() -> List[Dict[str, str]]:
    return _cached("approved_list", lambda: _load_csv("approved_list.csv"))


def fund_constituents() -> List[Dict[str, str]]:
    return _cached("fund_constituents", lambda: _load_csv("fund_constituents.csv"))


def fund_sector_weights() -> List[Dict[str, str]]:
    return _cached("fund_sector_weights", lambda: _load_csv("fund_sector_weights.csv"))


def client_cashflows() -> List[Dict[str, str]]:
    return _cached("client_cashflows", lambda: _load_csv("client_cashflows.csv"))


# ---------------------------------------------------------------------------
# Security master index
# ---------------------------------------------------------------------------

def security_map() -> Dict[str, Dict[str, str]]:
    """security_id → security row."""
    return _cached("security_map", lambda: {s["security_id"]: s for s in securities()})


def price_map() -> Dict[str, Dict[str, float]]:
    """security_id → {date_str: close_float}."""
    def _build():
        pm: Dict[str, Dict[str, float]] = {}
        for row in prices():
            sid = row["security_id"]
            if sid not in pm:
                pm[sid] = {}
            pm[sid][row["price_date"]] = float(row["close"])
        return pm
    return _cached("price_map", _build)


def get_price(security_id: str) -> tuple[float, str, bool]:
    """
    Return (price, price_date, is_stale).
    Uses valuation-date close if available; falls back to the latest earlier close.
    """
    pm = price_map()
    if security_id not in pm:
        return 1.0, VALUATION_DATE, False  # CASH_USD / stable NAV MMF
    dates = sorted(pm[security_id].keys(), reverse=True)
    for d in dates:
        if d <= VALUATION_DATE:
            is_stale = d < VALUATION_DATE
            return pm[security_id][d], d, is_stale
    return 1.0, VALUATION_DATE, True  # no price found


def market_value(security_id: str, quantity: float) -> tuple[float, str, bool]:
    """
    Compute market value from quantity and price, respecting price_quote convention.
    Returns (mv, price_date, is_stale).
    """
    sec = security_map().get(security_id, {})
    quote = sec.get("price_quote", "per_share")
    price, price_date, is_stale = get_price(security_id)

    if quote == "pct_of_par":
        mv = quantity * price / 100.0
    else:
        mv = quantity * price  # per_share, per_unit

    return mv, price_date, is_stale


# ---------------------------------------------------------------------------
# Portfolio scope
# ---------------------------------------------------------------------------

def household_accounts(hh_id: str) -> List[Dict[str, str]]:
    """All accounts for a household."""
    return [a for a in accounts() if a["household_id"] == hh_id]


def managed_accounts(hh_id: str) -> List[str]:
    """Firm-managed account IDs for a household."""
    return [
        a["account_id"]
        for a in household_accounts(hh_id)
        if a.get("managed_by_firm", "").lower() == "true"
    ]


def scope_for_rule(hh_id: str, rule_scope_accounts: Optional[List[str]]) -> List[str]:
    """
    Return account IDs in scope for a rule.
    Default = firm-managed accounts. IPS narrowing = intersection with rule.scope_accounts.
    """
    default = managed_accounts(hh_id)
    if rule_scope_accounts is None:
        return default
    # Intersect with the rule's narrowing
    return [a for a in rule_scope_accounts if a in set(default)]


def positions_for_accounts(account_ids: List[str]) -> List[Position]:
    """Return Position objects for the given accounts."""
    positions = []
    h_map: Dict[str, List[Dict[str, str]]] = {}
    for h in holdings():
        aid = h["account_id"]
        if aid not in h_map:
            h_map[aid] = []
        h_map[aid].append(h)

    for aid in account_ids:
        for h in h_map.get(aid, []):
            sid = h["security_id"]
            qty = float(h["quantity"])
            mv, price_date, is_stale = market_value(sid, qty)
            positions.append(Position(
                account_id=aid,
                security_id=sid,
                quantity=qty,
                mv=mv,
                price_date=price_date,
                is_stale=is_stale,
            ))
    return positions


def portfolio_mv(account_ids: List[str]) -> float:
    """Total market value of accounts."""
    return sum(p.mv for p in positions_for_accounts(account_ids))


# ---------------------------------------------------------------------------
# Exposure computation (direct + optional look-through)
# ---------------------------------------------------------------------------

def _is_fund(security_id: str) -> bool:
    sec = security_map().get(security_id, {})
    return sec.get("security_type", "") in (
        "mutual_fund", "etf", "money_market", "exchange_traded_product"
    )


def has_lookthrough_data(security_id: str) -> bool:
    """Return True if fund_constituents.csv has at least one constituent row for security_id."""
    return any(r.get("fund_id") == security_id for r in fund_constituents())


def _fund_exposures(fund_id: str, fund_mv: float, depth: int = 0) -> List[Dict]:
    """
    Expand a fund into its constituent exposures by weight.
    Handles nested funds with a depth guard (max 3 levels).
    Returns list of {security_id, mv, residual_asset_class, residual_description}.
    """
    if depth > 3:
        logger.warning("Look-through depth exceeded for fund %s", fund_id)
        return []

    rows = [r for r in fund_constituents() if r["fund_id"] == fund_id]
    if not rows:
        return []

    result = []
    for row in rows:
        weight = float(row["weight_pct"]) / 100.0
        constituent_mv = fund_mv * weight
        if row.get("constituent_security_id"):
            csid = row["constituent_security_id"]
            if _is_fund(csid):
                # Recurse
                nested = _fund_exposures(csid, constituent_mv, depth + 1)
                result.extend(nested)
            else:
                result.append({
                    "security_id": csid,
                    "mv": constituent_mv,
                    "residual_asset_class": None,
                    "residual_description": None,
                })
        else:
            # Residual bucket — unattributed
            result.append({
                "security_id": None,
                "mv": constituent_mv,
                "residual_asset_class": row.get("residual_asset_class"),
                "residual_description": row.get("residual_description"),
            })
    return result


def compute_exposures(positions: List[Position], lookthrough: str = "none") -> List[Exposure]:
    """
    Build the list of Exposure objects from positions.
    lookthrough: "none" (direct only) | "funds" (expand funds into constituents)
    """
    sec_map = security_map()
    exposures = []

    for pos in positions:
        sec = sec_map.get(pos.security_id, {})
        is_fund_pos = _is_fund(pos.security_id)

        if lookthrough == "funds" and is_fund_pos:
            # Expand fund into constituents
            constituents = _fund_exposures(pos.security_id, pos.mv)
            if not constituents:
                # Fund has no constituent rows: surface unknown exposure with missing_data=True
                exposures.append(Exposure(
                    account_id=pos.account_id,
                    security_id=f"__unknown_{pos.security_id}__",
                    via_fund=pos.security_id,
                    issuer_id=sec.get("issuer_id") or None,
                    asset_class=sec.get("asset_class") or "unknown",
                    sub_asset_class=sec.get("sub_asset_class") or "",
                    mv=pos.mv,
                    source="lookthrough",
                    missing_data=True,
                ))
            else:
                for c in constituents:
                    csec = sec_map.get(c["security_id"] or "", {})
                    exposures.append(Exposure(
                        account_id=pos.account_id,
                        security_id=c["security_id"] or "__residual__",
                        via_fund=pos.security_id,
                        issuer_id=csec.get("issuer_id") or None,
                        asset_class=csec.get("asset_class") or c.get("residual_asset_class") or "unknown",
                        sub_asset_class=csec.get("sub_asset_class") or "",
                        mv=c["mv"],
                        source="lookthrough",
                        missing_data=False,
                    ))
        else:
            # Direct holding (or no look-through requested)
            exposures.append(Exposure(
                account_id=pos.account_id,
                security_id=pos.security_id,
                via_fund=None,
                issuer_id=sec.get("issuer_id") or None,
                asset_class=sec.get("asset_class", ""),
                sub_asset_class=sec.get("sub_asset_class", ""),
                mv=pos.mv,
                source="direct",
                missing_data=False,
            ))

    return exposures


# ---------------------------------------------------------------------------
# Entity and account lookup tables (for entity resolution in validators)
# ---------------------------------------------------------------------------

def issuer_lookup() -> Dict[str, List[str]]:
    """normalized_issuer_name → [issuer_id]"""
    lookup: Dict[str, List[str]] = {}
    for sec in securities():
        iname = sec.get("issuer_name", "").lower().strip()
        iid = sec.get("issuer_id", "").strip()
        if iname and iid:
            if iname not in lookup:
                lookup[iname] = []
            if iid not in lookup[iname]:
                lookup[iname].append(iid)
    return lookup


def account_lookup(hh_id: str) -> Dict[str, str]:
    """normalized_account_descriptor → account_id for this household."""
    lookup: Dict[str, str] = {}
    for a in household_accounts(hh_id):
        # Index by account_id, account_name, and registration
        for key in [a.get("account_id", ""), a.get("account_name", ""), a.get("registration", "")]:
            if key:
                lookup[key.lower().strip()] = a["account_id"]
    return lookup


# ---------------------------------------------------------------------------
# Data dictionary for prompt construction
# ---------------------------------------------------------------------------

def data_dictionary(hh_id: str) -> str:
    """
    Build a compact data dictionary for the extraction prompt.
    Contains field names and enumerations — NO holdings, prices, or balances.
    """
    # Collect unique enums from securities.csv
    asset_classes: Set[str] = set()
    sub_classes: Set[str] = set()
    sec_types: Set[str] = set()
    sectors: Set[str] = set()

    for s in securities():
        if s.get("asset_class"):
            asset_classes.add(s["asset_class"])
        if s.get("sub_asset_class"):
            sub_classes.add(s["sub_asset_class"])
        if s.get("security_type"):
            sec_types.add(s["security_type"])
        if s.get("sector"):
            sectors.add(s["sector"])

    acct_list = []
    for a in household_accounts(hh_id):
        managed = a.get("managed_by_firm", "").lower() == "true"
        acct_list.append(
            f"  {a['account_id']} ({a.get('account_name', '')} / {a.get('account_type', '')} / "
            f"managed={managed})"
        )

    lines = [
        "# Data Dictionary",
        "",
        "## securities.csv columns",
        "security_id, ticker, name, security_type, asset_class, sub_asset_class,",
        "issuer_id, issuer_name, sector, industry, country, rating_sp, rating_moodys,",
        "coupon_pct, maturity_date, price_quote, expense_ratio_pct, business_description,",
        "fossil_revenue_pct (blank=no vendor coverage; 0=zero revenue; >0 pct of revenue)",
        "",
        f"asset_class values: {sorted(asset_classes)}",
        f"sub_asset_class values: {sorted(sub_classes)}",
        f"security_type values: {sorted(sec_types)}",
        f"sector values (GICS-style): {sorted(sectors)[:15]}…",
        "",
        "## Derived fields (usable in selectors)",
        "rating_bucket: IG | SUB_IG | unrated",
        "years_to_maturity: computed from maturity_date",
        "is_fund: True for mutual_fund, etf, money_market, exchange_traded_product",
        "is_treasury: True for security_type in (treasury, treasury_bill)",
        "is_cash_like: True for asset_class in (cash, cash_equivalent)",
        "issuer_fossil_pct: max fossil_revenue_pct across issuer's equity holdings",
        "",
        f"## Accounts in scope for {hh_id}",
    ] + acct_list + [
        "",
        "## Pricing conventions",
        "per_share: close = price per share",
        "per_unit: close = 1.00 (CASH_USD and stable-NAV MMFs)",
        "pct_of_par: mv = face_value × close / 100 (bonds and T-bills)",
        "",
        "## Trading rules",
        "Whole shares for equities, funds, MMFs. $1,000 face lots for bonds/bills.",
        "Buys only from approved_list.csv. Sells: anything held in managed accounts.",
        "No short sales, no negative cash per account.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Client cashflows
# ---------------------------------------------------------------------------

def cashflows_for_household(hh_id: str) -> Optional[Dict[str, str]]:
    for row in client_cashflows():
        if row["household_id"] == hh_id:
            return row
    return None
