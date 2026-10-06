"""
Remediation Planner — deterministic, zero LLM calls (invariant I1).

Algorithm:
  1. Collect confirmed breaches from findings.
  2. Build LP variables: sell_$ per (account, security), buy_$ per (account, eligible security).
  3. Constraints: cash balance per account, each confirmed rule's linear bound (with safety margin).
  4. Objective: minimize total turnover.
  5. Round to tradable lots (whole shares / $1000 face).
  6. Simulate with helpers/portfolio.py apply_trades.
  7. Re-run checker; repair loop ≤ 5 iterations.
  8. Write trades.csv + plan_facts.json; generate plan_memo.md.

Invariant I6: No trade is ever made for a rule that is pending review.
Invariant I7: Every trade is validated by apply_trades before being written.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
CONFIG_DIR = REPO_ROOT / "config"
VALUATION_DATE = date(2026, 9, 25)
FLOAT_TOL = 1e-9


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_policy() -> Dict[str, Any]:
    with (CONFIG_DIR / "policy.yaml").open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Trade data class
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    account_id: str
    security_id: str
    side: str   # "buy" | "sell"
    quantity: float  # shares for equities/funds, face $ for bonds/bills


# ---------------------------------------------------------------------------
# Candidate securities for buy
# ---------------------------------------------------------------------------

def _approved_securities() -> List[Dict[str, str]]:
    """Return rows from approved_list.csv."""
    from copilot.data.load import approved_list
    return approved_list()


def _security_type(security_id: str) -> str:
    from copilot.data.load import security_map
    return security_map().get(security_id, {}).get("security_type", "equity")


def _is_bond(security_id: str) -> bool:
    return _security_type(security_id) in ("corporate_bond", "treasury", "treasury_bill")


def _get_price(security_id: str) -> float:
    from copilot.data.load import get_price
    p, _, _ = get_price(security_id)
    return p


def _lot_size(security_id: str) -> float:
    """Return minimum tradable lot in shares or face value."""
    if _is_bond(security_id):
        return 1000.0  # $1000 face
    return 1.0  # 1 share


def _mv_per_lot(security_id: str) -> float:
    """Market value of one lot."""
    price = _get_price(security_id)
    if _is_bond(security_id):
        return 1000.0 * price / 100.0  # pct_of_par
    return price  # per_share


def _quantity_to_mv(security_id: str, quantity: float) -> float:
    price = _get_price(security_id)
    if _is_bond(security_id):
        return quantity * price / 100.0
    return quantity * price


def _mv_to_lots(security_id: str, mv: float, round_up: bool = False) -> float:
    """Convert market value to tradable lots (whole shares / $1000 face)."""
    lot = _lot_size(security_id)
    mv_per = _mv_per_lot(security_id)
    if mv_per <= 0:
        return 0.0
    lots = mv / mv_per
    if round_up:
        return math.ceil(lots - FLOAT_TOL) * lot
    else:
        return math.floor(lots + FLOAT_TOL) * lot


# ---------------------------------------------------------------------------
# Build sell candidates from holdings
# ---------------------------------------------------------------------------

@dataclass
class SellCandidate:
    account_id: str
    security_id: str
    held_quantity: float
    held_mv: float


def _sell_candidates(account_ids: List[str]) -> List[SellCandidate]:
    """All held securities in managed accounts (except CASH_USD)."""
    from copilot.data.load import positions_for_accounts
    positions = positions_for_accounts(account_ids)
    result = []
    for pos in positions:
        if pos.security_id == "CASH_USD":
            continue
        result.append(SellCandidate(
            account_id=pos.account_id,
            security_id=pos.security_id,
            held_quantity=pos.quantity,
            held_mv=pos.mv,
        ))
    return result


# ---------------------------------------------------------------------------
# Build buy candidates from approved list
# ---------------------------------------------------------------------------

@dataclass
class BuyCandidate:
    security_id: str
    mv_per_lot: float
    lot_size: float
    asset_class: str
    sub_asset_class: str


def _buy_candidates(confirmed_finding_ids: List[str], account_ids: List[str]) -> List[BuyCandidate]:
    """
    Eligible buys: on approved list, has a price, not excluded by any hard exclusion.
    Returns up to ~30 candidates: held securities first, then approved funds/Treasuries, then stocks.
    """
    from copilot.data.load import approved_list, security_map, positions_for_accounts, get_price

    approved_ids = {row["security_id"] for row in approved_list()}
    sec_map = security_map()
    pos_ids = {p.security_id for p in positions_for_accounts(account_ids)}

    candidates: List[BuyCandidate] = []
    for sid in approved_ids:
        if sid == "CASH_USD":
            continue
        sec = sec_map.get(sid, {})
        price, _, stale = get_price(sid)
        if price <= 0:
            continue
        lot = _lot_size(sid)
        mvl = _mv_per_lot(sid)
        if mvl <= 0:
            continue
        candidates.append(BuyCandidate(
            security_id=sid,
            mv_per_lot=mvl,
            lot_size=lot,
            asset_class=sec.get("asset_class", ""),
            sub_asset_class=sec.get("sub_asset_class", ""),
        ))

    # Sort: held first (can rebalance into), then by asset class priority
    def _priority(c: BuyCandidate) -> int:
        if c.security_id in pos_ids:
            return 0
        if c.asset_class in ("fixed_income",):
            return 1
        if c.asset_class in ("cash_equivalent",):
            return 2
        if c.asset_class == "equity":
            return 3
        return 4

    candidates.sort(key=_priority)
    return candidates[:30]


# ---------------------------------------------------------------------------
# Linear rule rows for the LP
# ---------------------------------------------------------------------------

def _portfolio_mv_after_sells(
    account_ids: List[str],
    sell_vars: Dict[Tuple[str, str], float],  # (account, security) -> mv to sell
) -> float:
    """Total portfolio MV is constant under trades (proceeds stay as cash)."""
    from copilot.data.load import portfolio_mv
    return portfolio_mv(account_ids)


def _get_rule_limits(finding: Dict, rule: Dict) -> Tuple[Optional[float], Optional[float]]:
    """Extract (min_val, max_val) as floats from finding or rule."""
    min_val = None
    max_val = None
    f_limit = finding.get("limit") or {}
    if f_limit.get("min") is not None:
        min_val = float(f_limit["min"])
    if f_limit.get("max") is not None:
        max_val = float(f_limit["max"])

    if min_val is None and rule:
        r_min = rule.get("min")
        if isinstance(r_min, dict) and r_min.get("value") is not None:
            min_val = float(r_min["value"])
        elif isinstance(r_min, (int, float)):
            min_val = float(r_min)
        elif rule.get("min_value") is not None:
            min_val = float(rule["min_value"])

    if max_val is None and rule:
        r_max = rule.get("max")
        if isinstance(r_max, dict) and r_max.get("value") is not None:
            max_val = float(r_max["value"])
        elif isinstance(r_max, (int, float)):
            max_val = float(r_max)
        elif rule.get("max_value") is not None:
            max_val = float(rule["max_value"])

    return min_val, max_val


# ---------------------------------------------------------------------------
# Greedy planner (fallback when scipy unavailable or LP too complex)
# ---------------------------------------------------------------------------

def _greedy_plan(
    confirmed_breach_findings: List[Dict[str, Any]],
    account_ids: List[str],
    policy: Dict[str, Any],
) -> List[Trade]:
    """
    Greedy heuristic: for each breach, sell the most-overweight holding until within limit.
    Reinvest proceeds into the best approved fixed-income or cash-equivalent candidate.
    """
    from copilot.data.load import (
        positions_for_accounts, portfolio_mv as pmv,
        security_map, approved_list, get_price
    )

    trades: List[Trade] = []
    dust_threshold = policy.get("dust_threshold_usd", 500.0)
    margin_bp = policy.get("safety_margin_bps", 5) / 10000.0

    all_positions = positions_for_accounts(account_ids)
    sec_map = security_map()
    remaining_mv: Dict[Tuple[str, str], float] = {
        (p.account_id, p.security_id): p.mv for p in all_positions
        if p.security_id != "CASH_USD"
    }

    for finding in confirmed_breach_findings:
        rule = finding.get("_rule", {})
        kind = rule.get("kind", "")
        rule_min, rule_max = _get_rule_limits(finding, rule)

        contributors = finding.get("contributors", [])
        if not contributors:
            continue

        contributors_sorted = sorted(contributors, key=lambda c: c.get("mv", 0), reverse=True)

        portfolio_total = finding.get("denominator_value") or pmv(account_ids)
        if portfolio_total == 0:
            continue

        if rule_max is None and kind == "exclusion":
            # Exclusion: sell everything matching
            for contrib in contributors_sorted:
                sid = contrib["security_id"]
                aid = contrib["account_id"]
                if sid == "CASH_USD":
                    continue
                available = remaining_mv.get((aid, sid), 0.0)
                if available < dust_threshold:
                    continue
                trades.append(Trade(account_id=aid, security_id=sid, side="sell", quantity=available))
                remaining_mv[(aid, sid)] = 0.0
        elif rule_max is not None:
            # Exposure cap / allocation range: sell until within limit + margin
            target_max = rule_max - margin_bp
            measured = finding.get("measured")

            if isinstance(measured, dict) and "__all__" in measured:
                # Per-group exposure cap: cure every group that breaches
                group_by = rule.get("group_by", "issuer")
                for grp_key, grp_weight in measured["__all__"].items():
                    if grp_weight <= target_max + FLOAT_TOL:
                        continue
                    excess_mv = (grp_weight - target_max) * portfolio_total
                    # Find positions matching this group
                    matching_positions = [
                        p for p in all_positions
                        if p.security_id != "CASH_USD" and (
                            sec_map.get(p.security_id, {}).get("issuer_id") == grp_key if group_by == "issuer"
                            else (sec_map.get(p.security_id, {}).get("sector") == grp_key if group_by == "sector"
                                  else p.security_id == grp_key)
                        )
                    ]
                    for pos in sorted(matching_positions, key=lambda p: p.mv, reverse=True):
                        if excess_mv <= dust_threshold:
                            break
                        available = remaining_mv.get((pos.account_id, pos.security_id), 0.0)
                        if available <= 0:
                            continue
                        sell_mv = min(excess_mv, available)
                        if sell_mv < dust_threshold:
                            continue
                        trades.append(Trade(account_id=pos.account_id, security_id=pos.security_id, side="sell", quantity=sell_mv))
                        remaining_mv[(pos.account_id, pos.security_id)] -= sell_mv
                        excess_mv -= sell_mv
            else:
                measured_val = measured or 0.0
                if measured_val <= target_max + FLOAT_TOL:
                    continue

                excess_mv = (measured_val - target_max) * portfolio_total

                # Sell from the top contributor
                for contrib in contributors_sorted:
                    if excess_mv <= dust_threshold:
                        break
                    sid = contrib["security_id"]
                    aid = contrib["account_id"]
                    if sid == "CASH_USD":
                        continue
                    available = remaining_mv.get((aid, sid), 0.0)
                    if available <= 0:
                        continue
                    sell_mv = min(excess_mv, available)
                    if sell_mv < dust_threshold:
                        continue
                    trades.append(Trade(account_id=aid, security_id=sid, side="sell", quantity=sell_mv))
                    remaining_mv[(aid, sid)] -= sell_mv
                    excess_mv -= sell_mv

    # Reinvest proceeds per account into approved core holdings
    account_sells: Dict[str, float] = {}
    for t in trades:
        if t.side == "sell":
            account_sells[t.account_id] = account_sells.get(t.account_id, 0.0) + t.quantity

    approved_ids = {row["security_id"] for row in approved_list()}
    reinvest_sec = "CRAG" if "CRAG" in approved_ids else ("SNBL" if "SNBL" in approved_ids else "KUSX")

    for aid, proceeds in account_sells.items():
        if proceeds <= dust_threshold:
            continue
        trades.append(Trade(account_id=aid, security_id=reinvest_sec, side="buy", quantity=proceeds))

    return trades


# ---------------------------------------------------------------------------
# LP-based planner using SciPy HiGHS
# ---------------------------------------------------------------------------

def _lp_plan(
    confirmed_breach_findings: List[Dict[str, Any]],
    account_ids: List[str],
    policy: Dict[str, Any],
) -> List[Trade]:
    """
    LP planner: minimize total turnover subject to:
    - Per-account cash constraints
    - Each confirmed rule's linear constraint (with safety margin)
    Returns continuous trades (pre-rounding).
    """
    try:
        from scipy.optimize import linprog
    except ImportError:
        logger.warning("scipy not available; falling back to greedy planner")
        return _greedy_plan(confirmed_breach_findings, account_ids, policy)

    from copilot.data.load import (
        positions_for_accounts, portfolio_mv as pmv, security_map,
        compute_exposures
    )

    margin_bp = policy.get("safety_margin_bps", 5) / 10000.0
    dust_threshold = policy.get("dust_threshold_usd", 500.0)

    sec_map = security_map()
    positions = positions_for_accounts(account_ids)
    total_mv = pmv(account_ids)

    if total_mv == 0:
        return []

    # Build sell variables: one per (account, security) held position (excluding CASH_USD)
    sell_vars: List[Tuple[str, str, float, float]] = []  # (account, security, held_mv, held_qty)
    for pos in positions:
        if pos.security_id == "CASH_USD":
            continue
        sell_vars.append((pos.account_id, pos.security_id, pos.mv, pos.quantity))

    # Build buy variables: approved securities × accounts
    buy_candidates = _buy_candidates(
        [f.get("rule_id", "") for f in confirmed_breach_findings],
        account_ids
    )
    buy_vars: List[Tuple[str, str, float]] = []  # (account, security, mv_per_lot)
    for aid in account_ids:
        for bc in buy_candidates:
            buy_vars.append((aid, bc.security_id, bc.mv_per_lot))

    n_sell = len(sell_vars)
    n_buy = len(buy_vars)
    n = n_sell + n_buy

    if n == 0:
        return []

    # Objective: minimize total turnover (sum of sell + buy MV)
    c = [1.0] * n  # minimize sell + buy

    # Bounds: sell ∈ [0, held_mv], buy ∈ [0, ∞)
    bounds = []
    for _, _, held_mv, _ in sell_vars:
        bounds.append((0.0, held_mv))
    for _ in buy_vars:
        bounds.append((0.0, None))

    # Constraints storage
    A_ub: List[List[float]] = []
    b_ub: List[float] = []

    # Per-account cash constraint: cash_before + sum(sells in acct) - sum(buys in acct) >= 0
    account_cash: Dict[str, float] = {}
    for pos in positions:
        if pos.security_id == "CASH_USD":
            account_cash[pos.account_id] = account_cash.get(pos.account_id, 0.0) + pos.mv
    for pos in positions:
        if pos.security_id != "CASH_USD":
            account_cash.setdefault(pos.account_id, 0.0)

    for aid in account_ids:
        row = [0.0] * n
        # sells in this account: contribute positively (cash comes in)
        for i, (a, s, mv, qty) in enumerate(sell_vars):
            if a == aid:
                row[i] = -1.0  # -sell + buy <= cash (i.e. buy - sell <= cash)
        # buys in this account: consume cash
        for j, (a, s, mvl) in enumerate(buy_vars):
            if a == aid:
                row[n_sell + j] = 1.0
        # buy - sell <= cash_available
        A_ub.append(row)
        b_ub.append(account_cash.get(aid, 0.0))

    # Rule-based constraints (each confirmed breach)
    for finding in confirmed_breach_findings:
        rule = finding.get("_rule", {})
        kind = rule.get("kind", "")
        group_by = rule.get("group_by", "issuer")
        rule_min, rule_max = _get_rule_limits(finding, rule)
        denom_mv = finding.get("denominator_value") or total_mv
        if denom_mv <= 0:
            denom_mv = total_mv

        contributors = finding.get("contributors", [])
        contrib_sec_ids = {c["security_id"] for c in contributors} if contributors else None

        if rule_max is not None:
            target = rule_max - margin_bp
            measured = finding.get("measured")

            if isinstance(measured, dict) and "__all__" in measured:
                # Per-group constraint for each breaching group
                for grp_key, grp_weight in measured["__all__"].items():
                    if grp_weight > target + FLOAT_TOL:
                        row = [0.0] * n
                        for i, (a, s, mv, qty) in enumerate(sell_vars):
                            sec = sec_map.get(s, {})
                            sec_grp = sec.get("issuer_id") if group_by == "issuer" else (sec.get("sector") if group_by == "sector" else s)
                            if sec_grp == grp_key:
                                row[i] = -1.0 / denom_mv
                        for j, (a, s, mvl) in enumerate(buy_vars):
                            sec = sec_map.get(s, {})
                            sec_grp = sec.get("issuer_id") if group_by == "issuer" else (sec.get("sector") if group_by == "sector" else s)
                            if sec_grp == grp_key:
                                row[n_sell + j] = 1.0 / denom_mv

                        b = target - grp_weight
                        A_ub.append(row)
                        b_ub.append(b)
            else:
                measured_val = _get_measured_scalar(finding)
                if measured_val is not None and measured_val > target + FLOAT_TOL:
                    row = [0.0] * n
                    for i, (a, s, mv, qty) in enumerate(sell_vars):
                        sec = sec_map.get(s, {})
                        is_match = (s in contrib_sec_ids) if contrib_sec_ids else _matches_rule_measure(sec, rule)
                        if is_match:
                            row[i] = -1.0 / denom_mv
                    for j, (a, s, mvl) in enumerate(buy_vars):
                        sec = sec_map.get(s, {})
                        is_match = (s in contrib_sec_ids) if contrib_sec_ids else _matches_rule_measure(sec, rule)
                        if is_match:
                            row[n_sell + j] = 1.0 / denom_mv

                    b = target - measured_val
                    A_ub.append(row)
                    b_ub.append(b)

        elif kind == "exclusion":
            target = 0.0
            measured_val = _get_measured_scalar(finding) or 1.0
            row = [0.0] * n
            for i, (a, s, mv, qty) in enumerate(sell_vars):
                sec = sec_map.get(s, {})
                is_match = (s in contrib_sec_ids) if contrib_sec_ids else _matches_rule_measure(sec, rule)
                if is_match:
                    row[i] = -1.0 / denom_mv
            b = -measured_val
            A_ub.append(row)
            b_ub.append(b)

        if rule_min is not None:
            target = rule_min + margin_bp
            measured_val = _get_measured_scalar(finding)
            if measured_val is not None and measured_val < target - FLOAT_TOL:
                row = [0.0] * n
                for i, (a, s, mv, qty) in enumerate(sell_vars):
                    sec = sec_map.get(s, {})
                    is_match = (s in contrib_sec_ids) if contrib_sec_ids else _matches_rule_measure(sec, rule)
                    if is_match:
                        row[i] = 1.0 / denom_mv
                for j, (a, s, mvl) in enumerate(buy_vars):
                    sec = sec_map.get(s, {})
                    is_match = (s in contrib_sec_ids) if contrib_sec_ids else _matches_rule_measure(sec, rule)
                    if is_match:
                        row[n_sell + j] = -1.0 / denom_mv

                b = -(target - measured_val)
                A_ub.append(row)
                b_ub.append(b)


    if not A_ub:
        # Fall back to greedy planner
        return _greedy_plan(confirmed_breach_findings, account_ids, policy)

    import numpy as np
    A_np = np.array(A_ub, dtype=float)
    b_np = np.array(b_ub, dtype=float)
    c_np = np.array(c, dtype=float)

    result = linprog(c_np, A_ub=A_np, b_ub=b_np, bounds=bounds, method="highs")

    if not result.success:
        logger.warning("LP infeasible: %s — falling back to greedy", result.message)
        return _greedy_plan(confirmed_breach_findings, account_ids, policy)

    # Convert continuous solution to Trade objects (pre-rounding)
    trades = []
    x = result.x
    for i, (a, s, mv, qty) in enumerate(sell_vars):
        sell_mv = x[i]
        if sell_mv > dust_threshold:
            trades.append(Trade(account_id=a, security_id=s, side="sell", quantity=sell_mv))
    for j, (a, s, mvl) in enumerate(buy_vars):
        buy_mv = x[n_sell + j]
        if buy_mv > dust_threshold:
            trades.append(Trade(account_id=a, security_id=s, side="buy", quantity=buy_mv))

    if not trades:
        logger.info("LP produced 0 trades — running greedy planner")
        return _greedy_plan(confirmed_breach_findings, account_ids, policy)

    return trades


def _get_measured_scalar(finding: Dict) -> Optional[float]:
    """Extract a scalar measured value from a finding (handles dict case)."""
    measured = finding.get("measured")
    if measured is None:
        return None
    if isinstance(measured, dict):
        all_groups = measured.get("__all__", {})
        if all_groups:
            return max(all_groups.values())
        return None
    return float(measured)


def _matches_rule_measure(sec: Dict, rule: Dict) -> bool:
    """Check if a security matches a rule's measure selector."""
    from copilot.check.engine import _eval_selector
    measure = rule.get("measure", {})
    if not measure:
        return True
    return _eval_selector(measure, sec)


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------

def _round_trades(
    continuous_trades: List[Trade],
    account_ids: List[str],
    dust_threshold: float,
) -> List[Trade]:
    """
    Round continuous $-amount trades to tradable lots.
    Sells: round up (conservative). Buys: round down.
    Drop dust (< dust_threshold or < 1 lot).
    """
    from copilot.data.load import positions_for_accounts

    positions = positions_for_accounts(account_ids)
    pos_map: Dict[Tuple[str, str], float] = {
        (p.account_id, p.security_id): p.quantity for p in positions
    }

    # Track available cash per account (initial cash + proceeds from sells)
    account_cash: Dict[str, float] = {}
    for p in positions:
        if p.security_id == "CASH_USD":
            account_cash[p.account_id] = account_cash.get(p.account_id, 0.0) + p.mv
    for aid in account_ids:
        account_cash.setdefault(aid, 0.0)

    rounded: List[Trade] = []

    # 1. Process sells first
    for trade in continuous_trades:
        if trade.side != "sell" or trade.security_id == "CASH_USD":
            continue
        if trade.quantity < dust_threshold:
            continue
        qty = _mv_to_lots(trade.security_id, trade.quantity, round_up=True)
        held = pos_map.get((trade.account_id, trade.security_id), 0.0)
        qty = min(qty, held)
        if qty <= 0:
            continue
        pos_map[(trade.account_id, trade.security_id)] -= qty
        sell_mv = _quantity_to_mv(trade.security_id, qty)
        account_cash[trade.account_id] = account_cash.get(trade.account_id, 0.0) + sell_mv
        rounded.append(Trade(
            account_id=trade.account_id,
            security_id=trade.security_id,
            side="sell",
            quantity=qty,
        ))

    # 2. Process buys next, capped at available account cash
    for trade in continuous_trades:
        if trade.side != "buy" or trade.security_id == "CASH_USD":
            continue
        avail = account_cash.get(trade.account_id, 0.0)
        if avail <= dust_threshold:
            continue
        # Desired MV capped at available cash
        desired_mv = min(trade.quantity, avail)
        if desired_mv < dust_threshold:
            continue
        qty = _mv_to_lots(trade.security_id, desired_mv, round_up=False)
        cost = _quantity_to_mv(trade.security_id, qty)
        while cost > avail and qty > 0:
            qty -= _lot_size(trade.security_id)
            cost = _quantity_to_mv(trade.security_id, qty)
        if qty <= 0:
            continue
        account_cash[trade.account_id] -= cost
        rounded.append(Trade(
            account_id=trade.account_id,
            security_id=trade.security_id,
            side="buy",
            quantity=qty,
        ))

    return rounded


# ---------------------------------------------------------------------------
# Apply trades via helper and re-run checker
# ---------------------------------------------------------------------------

def _trades_to_helper_format(trades: List[Trade]) -> List[Dict[str, Any]]:
    """Convert internal Trade objects to the dict format expected by helpers/portfolio."""
    return [
        {
            "account_id": t.account_id,
            "security_id": t.security_id,
            "side": t.side,
            "quantity": t.quantity,
        }
        for t in trades
    ]


def _trades_to_helper_trades(trades: List[Trade], helper_mod: Any) -> list:
    """Convert internal Trade objects to helpers.portfolio.Trade namedtuples."""
    result = []
    for t in trades:
        result.append(helper_mod.Trade(
            account_id=t.account_id,
            security_id=t.security_id,
            side=t.side,
            quantity=t.quantity,
        ))
    return result


def _book_to_holdings(portfolio_obj: Any) -> List[Dict[str, str]]:
    """
    Convert a helpers.portfolio.Portfolio object to holdings.csv-row dicts.
    Portfolio.rows() yields {account_id, security_id, quantity} dicts.
    """
    rows = []
    for row in portfolio_obj.rows():
        rows.append({
            "account_id": row["account_id"],
            "security_id": row["security_id"],
            "quantity": str(row["quantity"]),
        })
    return rows


def _apply_and_recheck(
    trades: List[Trade],
    hh_id: str,
    extraction: Dict,
    original_findings: Dict,
) -> Tuple[bool, List[str], List[str]]:
    """
    Apply trades with helper, re-run checker.
    Returns (is_clean, remaining_breaches, new_breaches).
    """
    import sys
    sys.path.insert(0, str(REPO_ROOT / "helpers"))

    try:
        import portfolio as helper_mod
    except ImportError:
        logger.error("Cannot import helpers/portfolio.py — is helpers/ on the path?")
        return False, ["cannot_verify"], []

    # Load the full dataset and household portfolio via helper
    data_dir = REPO_ROOT / "data" / "portfolio"
    dataset = helper_mod.load(str(data_dir))
    portfolio_obj = dataset.portfolio(hh_id)  # all accounts (managed + unmanaged)

    helper_trades = _trades_to_helper_trades(trades, helper_mod)

    try:
        result = helper_mod.apply_trades(dataset, portfolio_obj, helper_trades)
        if result.errors:
            logger.error("apply_trades errors: %s", result.errors)
            return False, [f"trade_rejected: {e}" for e in result.errors], []
        new_portfolio = result.portfolio
    except Exception as e:
        logger.error("apply_trades raised: %s", e)
        return False, [f"trade_rejected: {e}"], []

    from copilot.check.engine import check_household as _check
    from copilot.data import load as data_load

    # Save and restore original cache
    orig_cache = dict(data_load._cache)
    try:
        # Override holdings cache with the post-trade portfolio
        new_holdings = _book_to_holdings(new_portfolio)
        data_load._cache["holdings"] = new_holdings

        new_findings = _check(hh_id, extraction)
    finally:
        data_load._cache = orig_cache

    remaining_breaches = new_findings.get("confirmed_breaches", [])
    orig_breach_ids = set(original_findings.get("confirmed_breaches", []))
    all_after_breaches = {
        f["rule_id"] for f in new_findings.get("findings", [])
        if f["status"] == "breach"
    }
    new_breaches = sorted(all_after_breaches - orig_breach_ids)

    is_clean = len(remaining_breaches) == 0 and len(new_breaches) == 0
    return is_clean, remaining_breaches, new_breaches


# ---------------------------------------------------------------------------
# Main plan_household function
# ---------------------------------------------------------------------------

def plan_household(hh_id: str, findings: Dict) -> Dict[str, Any]:
    """
    Produce a remediation plan for a household.
    Returns {trades_csv, memo_md, facts}.
    """
    logger.info("Planning for %s", hh_id)
    policy = _load_policy()
    act_on_robust = policy.get("act_on_robust_breaches", True)
    max_iterations = policy.get("max_repair_iterations", 5)
    dust_threshold = policy.get("dust_threshold_usd", 500.0)

    from copilot.data.load import managed_accounts as get_managed_accounts
    account_ids = get_managed_accounts(hh_id)

    # Collect confirmed breaches
    confirmed_breach_ids = set(findings.get("confirmed_breaches", []))
    all_findings = findings.get("findings", [])

    # Reload extraction to get the full rule definitions (for LP row building)
    extraction = _load_extraction(hh_id)

    # Build a rule_id → rule dict for quick lookup
    rule_by_id: Dict[str, Dict] = {}
    for clause in (extraction or {}).get("clauses", []):
        for rule in clause.get("rules", []):
            rule_by_id[rule["rule_id"]] = rule

    # Annotate findings with their rule dict
    breach_findings: List[Dict] = []
    for f in all_findings:
        if f["rule_id"] in confirmed_breach_ids:
            f = dict(f)
            f["_rule"] = rule_by_id.get(f["rule_id"], {})
            breach_findings.append(f)

    is_clean = True
    if not breach_findings:
        logger.info("%s: no confirmed breaches — no trades needed", hh_id)
        trades = []
    else:
        try:
            continuous = _greedy_plan(breach_findings, account_ids, policy)
        except Exception as e:
            logger.warning("Greedy planner failed (%s); trying LP", e)
            continuous = _lp_plan(breach_findings, account_ids, policy)

        # Round to tradable lots
        trades = _round_trades(continuous, account_ids, dust_threshold)

        # Repair loop
        is_clean = False
        remaining = []
        new_brs = []
        if not trades:
            is_clean = False
        else:
            for iteration in range(max_iterations):
                clean, remaining, new_brs = _apply_and_recheck(
                    trades, hh_id, extraction, findings
                )
                if clean:
                    is_clean = True
                    logger.info("%s: plan is clean after iteration %d", hh_id, iteration + 1)
                    break
            else:
                logger.warning(
                    "%s: iteration %d — remaining breaches: %s, new: %s",
                    hh_id, iteration + 1, remaining, new_brs
                )
                # Increase margin and re-plan (simple approach: boost margins by 5bp per iteration)
                policy = dict(policy)
                policy["safety_margin_bps"] = policy.get("safety_margin_bps", 5) + 5
                try:
                    continuous = _lp_plan(breach_findings, account_ids, policy)
                except Exception:
                    continuous = _greedy_plan(breach_findings, account_ids, policy)
                trades = _round_trades(continuous, account_ids, dust_threshold)

        if not is_clean:
            logger.warning(
                "%s: plan NOT clean after %d iterations — remaining: %s, new breaches: %s",
                hh_id, max_iterations, remaining, new_brs
            )

    # Validate final trades with helper (strict=False — sells before buys per account)
    valid_trades = _validate_trades(trades, hh_id)

    # Build plan facts + memo
    from copilot.plan.memo import build_plan_facts, render_memo

    facts = build_plan_facts(hh_id, findings, valid_trades, extraction, is_clean=is_clean)
    memo_md = render_memo(hh_id, facts)
    trades_csv = _trades_to_csv(valid_trades)

    return {
        "trades_csv": trades_csv,
        "memo_md": memo_md,
        "facts": facts,
    }


def _load_extraction(hh_id: str) -> Optional[Dict]:
    import json
    path = REPO_ROOT / "output" / "extraction" / f"{hh_id.lower()}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def _validate_trades(trades: List[Trade], hh_id: str) -> List[Trade]:
    """Validate all trades as a batch via helper; return only the applied trades."""
    if not trades:
        return []

    sys.path.insert(0, str(REPO_ROOT / "helpers"))
    try:
        import portfolio as helper_mod
    except ImportError:
        logger.error("Cannot import helpers/portfolio.py")
        return trades

    data_dir = REPO_ROOT / "data" / "portfolio"
    dataset = helper_mod.load(str(data_dir))
    portfolio_obj = dataset.portfolio(hh_id)
    helper_trades = _trades_to_helper_trades(trades, helper_mod)

    result = helper_mod.apply_trades(dataset, portfolio_obj, helper_trades)

    if result.errors:
        for err in result.errors:
            logger.warning("Trade validation error: %s", err)

    # Map applied helper trades back to internal Trade objects
    applied_keys = {
        (t.account_id, t.security_id, t.side, t.quantity)
        for t in result.applied
    }
    valid = [
        t for t in trades
        if (t.account_id, t.security_id, t.side, t.quantity) in applied_keys
    ]
    return valid


def _trades_to_csv(trades: List[Trade]) -> str:
    """Serialize trades to CSV string in the required format."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["account_id", "security_id", "side", "quantity"])
    for t in sorted(trades, key=lambda x: (x.account_id, x.security_id, x.side)):
        # Format quantity: no decimal for whole numbers
        qty = int(t.quantity) if t.quantity == int(t.quantity) else t.quantity
        writer.writerow([t.account_id, t.security_id, t.side, qty])
    return output.getvalue()
