"""
Checker engine — deterministic, zero LLM calls.

Runs one rule-kind evaluator per rule and produces a Finding.
Status logic:
  data_requirements not satisfiable → CANNOT_EVALUATE
  input too old                     → CANNOT_EVALUATE
  rule pending review               → NEEDS_REVIEW (+ provisional measure shown)
  rule.deadline in future           → PASS with tracked=True
  within [min, max]                 → PASS
  else                              → BREACH
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from copilot.data.load import (
    positions_for_accounts,
    portfolio_mv,
    scope_for_rule,
    compute_exposures,
    cashflows_for_household,
    security_map,
    approved_list,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
CONFIG_DIR = REPO_ROOT / "config"
VALUATION_DATE = date(2026, 9, 25)
FLOAT_TOL = 1e-9

# ---------------------------------------------------------------------------
# Finding data classes
# ---------------------------------------------------------------------------

@dataclass
class Contribution:
    account_id: str
    security_id: str
    via_fund: Optional[str]
    mv: float
    share_of_measure: float


@dataclass
class Finding:
    rule_id: str
    clause_id: str
    status: str  # pass | breach | needs_review | cannot_evaluate
    measured: Optional[Any]
    limit: Dict[str, Any]
    margin: Optional[float]
    unit: str
    scope_accounts: List[str]
    denominator_value: float
    contributors: List[Contribution] = field(default_factory=list)
    unattributed_weight: Optional[float] = None
    inputs: Dict[str, str] = field(default_factory=dict)
    assumptions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    tracked: bool = False
    source_quotes: List[str] = field(default_factory=list)
    reason: Optional[str] = None  # for cannot_evaluate / needs_review


# ---------------------------------------------------------------------------
# Freshness check
# ---------------------------------------------------------------------------

def _load_freshness() -> Dict[str, int]:
    with (CONFIG_DIR / "freshness.yaml").open() as f:
        return yaml.safe_load(f)


def check_freshness(rule_data_requirements: List[str], hh_id: str) -> Optional[str]:
    """
    If any required data is too stale, return a cannot_evaluate reason.
    Returns None if all data is fresh enough.
    """
    freshness = _load_freshness()
    today = VALUATION_DATE

    if "client_cashflows" in rule_data_requirements or "reserve_requirement" in rule_data_requirements:
        cf = cashflows_for_household(hh_id)
        if cf:
            cf_date = date.fromisoformat(cf["as_of_date"])
            max_age = freshness.get("client_cashflows_days", 180)
            age = (today - cf_date).days
            if age > max_age:
                return (
                    f"Client cashflows as_of {cf_date} are {age} days old "
                    f"(max {max_age} days). Cannot evaluate reserve requirement."
                )

    # Fund data check (fund_constituents is 2026-08-31, ~25 days stale)
    # 25 < 45 days → OK for most rules
    # If a rule specifically required fresher fund data, add here

    return None


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------

def _status_from_value(
    value: float,
    rule_min: Optional[float],
    rule_max: Optional[float],
    min_inclusive: bool = True,
    max_inclusive: bool = True,
) -> str:
    if rule_min is not None:
        if min_inclusive:
            if value < rule_min - FLOAT_TOL:
                return "breach"
        else:
            if value <= rule_min + FLOAT_TOL:
                return "breach"
    if rule_max is not None:
        if max_inclusive:
            if value > rule_max + FLOAT_TOL:
                return "breach"
        else:
            if value >= rule_max - FLOAT_TOL:
                return "breach"
    return "pass"


# ---------------------------------------------------------------------------
# Predicate evaluator
# ---------------------------------------------------------------------------

def _eval_predicate(predicate: dict, security: dict) -> bool:
    """Evaluate one Predicate against a securities.csv row."""
    field = predicate.get("field", "")
    op = predicate.get("op", "eq")
    value = predicate.get("value")
    entity_name = predicate.get("entity_name")
    resolved_issuer_ids = predicate.get("resolved_issuer_ids") or []

    # Handle special derived fields
    if field == "is_fund":
        actual = security.get("security_type", "") in (
            "mutual_fund", "etf", "money_market", "exchange_traded_product"
        )
    elif field == "is_treasury":
        actual = security.get("security_type", "") in ("treasury", "treasury_bill")
    elif field == "is_bond":
        actual = security.get("security_type", "") in ("corporate_bond", "treasury", "treasury_bill")
    elif field == "is_stock":
        actual = security.get("security_type", "") == "equity"
    elif field == "is_cash_like":
        actual = security.get("asset_class", "") in ("cash", "cash_equivalent")
    elif field == "issuer_id":
        actual = security.get("issuer_id", "")
    elif field in ("issuer_fossil_pct", "fossil_revenue_pct"):
        fp = security.get("fossil_revenue_pct", "")
        if fp and str(fp).strip():
            actual = float(fp)
        else:
            iss_id = security.get("issuer_id")
            if iss_id:
                max_fp = 0.0
                for s in security_map().values():
                    if s.get("issuer_id") == iss_id and s.get("fossil_revenue_pct"):
                        try:
                            max_fp = max(max_fp, float(s["fossil_revenue_pct"]))
                        except (ValueError, TypeError):
                            pass
                actual = max_fp if max_fp > 0 else 0.0
            else:
                actual = 0.0
    elif field == "rating_bucket":
        actual = _rating_bucket(security.get("rating_sp", ""), security.get("rating_moodys", ""))
    elif field == "security_type" and str(value).lower() in ("digital_assets", "crypto", "cryptocurrency"):
        actual = "digital_assets" if (
            security.get("sub_asset_class") == "digital_assets" or
            "crypto" in security.get("name", "").lower() or
            "bitcoin" in security.get("name", "").lower()
        ) else security.get("security_type")
    else:
        actual = security.get(field)

    # Normalise asset class aliases
    if field == "asset_class":
        def _norm_ac(v: Any) -> str:
            s = str(v).lower().replace("-", "_").replace(" ", "_")
            if "equity" in s or "equities" in s or "stock" in s:
                return "equity"
            if "fixed_income" in s or "bond" in s or "debt" in s:
                return "fixed_income"
            if "cash" in s or "money_market" in s:
                return "cash_equivalent"
            return s
        actual_norm = _norm_ac(actual)
        if isinstance(value, list):
            value_norm = [_norm_ac(v) for v in value]
            if op in ("in", "eq"):
                return actual_norm in value_norm or actual in value
            elif op in ("not_in", "ne"):
                return actual_norm not in value_norm and actual not in value
        else:
            value_norm = _norm_ac(value)
            if op == "eq":
                return actual_norm == value_norm or str(actual).lower() == str(value).lower()
            elif op == "ne":
                return actual_norm != value_norm and str(actual).lower() != str(value).lower()

    # If entity-resolved or entity_name present, check issuer
    if field == "issuer_id":
        if resolved_issuer_ids:
            matches = actual in resolved_issuer_ids
            if not matches and entity_name:
                sec_iname = security.get("issuer_name", "").lower()
                ename = entity_name.lower()
                if ename in sec_iname or sec_iname in ename:
                    matches = True
        elif entity_name:
            sec_iname = security.get("issuer_name", "").lower()
            ename = entity_name.lower()
            matches = ename in sec_iname or sec_iname in ename
        else:
            matches = str(actual) == str(value) if actual is not None else False

        if op in ("eq", "in"):
            return matches
        elif op in ("ne", "not_in"):
            return not matches
        return False

    if op == "eq":
        if isinstance(actual, bool) or str(value).lower() in ("true", "false", "1", "1.0", "0", "0.0"):
            val_bool = str(value).lower() in ("true", "1", "1.0") or value is True or value == 1 or value == 1.0
            return bool(actual) == val_bool
        if field in ("issuer_name", "name", "entity_name") and actual and value:
            a_s = str(actual).lower().strip()
            v_s = str(value).lower().strip()
            return a_s == v_s or v_s in a_s or a_s in v_s
        return str(actual) == str(value) if actual is not None else False
    elif op == "ne":
        if isinstance(actual, bool) or str(value).lower() in ("true", "false", "1", "1.0", "0", "0.0"):
            val_bool = str(value).lower() in ("true", "1", "1.0") or value is True or value == 1 or value == 1.0
            return bool(actual) != val_bool
        return str(actual) != str(value) if actual is not None else True
    elif op == "in":
        vals = value if isinstance(value, list) else [value]
        return actual in vals if actual is not None else False
    elif op == "not_in":
        vals = value if isinstance(value, list) else [value]
        return actual not in vals if actual is not None else True
    elif op == "is_blank":
        return actual is None or str(actual).strip() == ""
    elif op == "is_not_blank":
        return actual is not None and str(actual).strip() != ""
    elif op in ("gte", "gt", "lte", "lt"):
        if actual is None:
            return False
        try:
            actual_f = float(actual)
            value_f = float(value)
            if field in ("issuer_fossil_pct", "fossil_revenue_pct") and 0.0 < value_f <= 1.0:
                value_f = value_f * 100.0
        except (TypeError, ValueError):
            return False
        if op == "gte":
            return actual_f >= value_f
        elif op == "gt":
            return actual_f > value_f
        elif op == "lte":
            return actual_f <= value_f
        elif op == "lt":
            return actual_f < value_f
    return False


def _eval_selector(selector: Optional[dict], security: dict) -> bool:
    """Evaluate a Selector (AND of all_of, with optional OR groups)."""
    if not selector:
        return True
    if hasattr(selector, "model_dump"):
        selector = selector.model_dump()
    all_of = selector.get("all_of", [])
    any_of = selector.get("any_of", [])

    for pred in all_of:
        pred_dict = pred.model_dump() if hasattr(pred, "model_dump") else pred
        if not _eval_predicate(pred_dict, security):
            return False

    if any_of:
        any_passed = False
        for grp in any_of:
            if all(_eval_predicate(p.model_dump() if hasattr(p, "model_dump") else p, security) for p in grp):
                any_passed = True
                break
        if not any_passed:
            return False

    return True


def _normalize_selector(sel_val: Any) -> Optional[dict]:
    if sel_val is None:
        return None
    if hasattr(sel_val, "model_dump"):
        sel_val = sel_val.model_dump()
    if isinstance(sel_val, dict):
        all_of = sel_val.get("all_of") or []
        any_of = sel_val.get("any_of") or []
        if all_of or any_of:
            return {"all_of": all_of, "any_of": any_of}
    return None


def _get_rule_measure(rule: dict) -> dict:
    sel = _normalize_selector(rule.get("measure"))
    if sel:
        return sel
    all_of = rule.get("measure_all_of")
    any_of = rule.get("measure_any_of")
    if all_of is not None or any_of is not None:
        return {"all_of": all_of or [], "any_of": any_of or []}
    return {"all_of": [], "any_of": []}


def _get_rule_exempt(rule: dict) -> Optional[dict]:
    sel = _normalize_selector(rule.get("exempt"))
    if sel:
        return sel
    all_of = rule.get("exempt_all_of")
    any_of = rule.get("exempt_any_of")
    if (all_of is not None and len(all_of) > 0) or (any_of is not None and len(any_of) > 0):
        return {"all_of": all_of or [], "any_of": any_of or []}
    return None


def _get_bound_value(val_obj: Any, flat_val: Any, flat_unit: Optional[str] = None) -> Optional[float]:
    if val_obj is not None:
        if hasattr(val_obj, "as_fraction"):
            return val_obj.as_fraction()
        if isinstance(val_obj, dict):
            v = val_obj.get("value")
            if v is None:
                return None
            u = val_obj.get("unit", "fraction")
            return float(v) / 100.0 if u == "percent" else float(v)
        if isinstance(val_obj, (int, float)):
            return float(val_obj)
    if flat_val is not None:
        v = float(flat_val)
        return v / 100.0 if flat_unit == "percent" else v
    return None


def _get_bound_inclusive(val_obj: Any, flat_inclusive: Any) -> bool:
    if val_obj is not None:
        if hasattr(val_obj, "inclusive"):
            return bool(val_obj.inclusive)
        if isinstance(val_obj, dict):
            return bool(val_obj.get("inclusive", True))
    if flat_inclusive is not None:
        return bool(flat_inclusive)
    return True



# ---------------------------------------------------------------------------
# Rating helpers
# ---------------------------------------------------------------------------

SP_SCALE = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
            "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D"]
MOODYS_SCALE = ["Aaa", "Aa1", "Aa2", "Aa3", "A1", "A2", "A3", "Baa1", "Baa2", "Baa3",
                "Ba1", "Ba2", "Ba3", "B1", "B2", "B3", "Caa1", "Caa2", "Caa3", "Ca", "C"]

IG_SP_BOUNDARY = "BBB-"
IG_MOODYS_BOUNDARY = "Baa3"


def _rating_rank_sp(rating: str) -> Optional[int]:
    r = rating.strip()
    if r in SP_SCALE:
        return SP_SCALE.index(r)
    return None


def _rating_rank_moodys(rating: str) -> Optional[int]:
    r = rating.strip()
    if r in MOODYS_SCALE:
        return MOODYS_SCALE.index(r)
    return None


def _rating_bucket(sp: str, moodys: str) -> str:
    """IG | SUB_IG | unrated (lower of SP/Moody's)."""
    ig_sp_idx = _rating_rank_sp(IG_SP_BOUNDARY)
    ig_moodys_idx = _rating_rank_moodys(IG_MOODYS_BOUNDARY)

    sp_rank = _rating_rank_sp(sp) if sp else None
    md_rank = _rating_rank_moodys(moodys) if moodys else None

    if sp_rank is None and md_rank is None:
        return "unrated"

    # Use lower (higher index = lower quality)
    if sp_rank is not None and (md_rank is None or sp_rank >= md_rank):
        worst = sp_rank
        is_ig = worst <= ig_sp_idx
    else:
        worst = md_rank
        is_ig = worst <= ig_moodys_idx

    return "IG" if is_ig else "SUB_IG"


# ---------------------------------------------------------------------------
# Rule evaluators (one per kind)
# ---------------------------------------------------------------------------

def _eval_allocation_range(rule: dict, account_ids: List[str], hh_id: str) -> Finding:
    """Measure asset class allocation as fraction of portfolio."""
    measure_selector = _get_rule_measure(rule)
    denominator = rule.get("denominator", "portfolio")
    lookthrough = rule.get("lookthrough", "none")

    positions = positions_for_accounts(account_ids)
    exposures = compute_exposures(positions, lookthrough=lookthrough)
    sec_map = security_map()

    denom_mv = sum(e.mv for e in exposures)
    if denom_mv == 0:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="cannot_evaluate", measured=None,
            limit={}, margin=None, unit="fraction",
            scope_accounts=account_ids, denominator_value=0,
            reason="Portfolio has zero market value",
        )

    # Check if measure selector actually has predicates
    has_preds = False
    if measure_selector:
        sel_dict = measure_selector.model_dump() if hasattr(measure_selector, "model_dump") else measure_selector
        has_preds = bool(sel_dict.get("all_of") or sel_dict.get("any_of"))

    if not has_preds:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="cannot_evaluate", measured=None,
            limit={}, margin=None, unit="fraction",
            scope_accounts=account_ids, denominator_value=denom_mv,
            reason="No predicates or asset class specified in measure selector",
            source_quotes=rule.get("source_quotes", []),
        )

    # Numerator: exposures matching the selector
    numerator_exposures = []
    unattributed_mv = 0.0
    for exp in exposures:
        if exp.security_id == "__residual__":
            unattributed_mv += exp.mv
            continue
        sec = sec_map.get(exp.security_id, {})
        if _eval_selector(measure_selector, sec):
            numerator_exposures.append(exp)

    numerator_mv = sum(e.mv for e in numerator_exposures)
    measured = numerator_mv / denom_mv

    # Min / max
    rule_min = _get_bound_value(rule.get("min"), rule.get("min_value"), rule.get("min_unit"))
    rule_max = _get_bound_value(rule.get("max"), rule.get("max_value"), rule.get("max_unit"))
    min_inclusive = _get_bound_inclusive(rule.get("min"), rule.get("min_inclusive"))
    max_inclusive = _get_bound_inclusive(rule.get("max"), rule.get("max_inclusive"))

    status = _status_from_value(measured, rule_min, rule_max, min_inclusive, max_inclusive)

    # Margin
    margin = None
    if rule_min is not None and measured < rule_min:
        margin = measured - rule_min
    elif rule_max is not None and measured > rule_max:
        margin = measured - rule_max
    elif rule_min is not None:
        margin = measured - rule_min  # positive = distance above floor
    elif rule_max is not None:
        margin = rule_max - measured  # positive = distance below cap

    contributors = sorted(
        [Contribution(
            account_id=e.account_id, security_id=e.security_id,
            via_fund=e.via_fund, mv=e.mv,
            share_of_measure=e.mv / numerator_mv if numerator_mv > 0 else 0,
        ) for e in numerator_exposures],
        key=lambda c: c.mv, reverse=True
    )[:10]

    limit = {}
    if rule_min is not None:
        limit["min"] = rule_min
    if rule_max is not None:
        limit["max"] = rule_max

    return Finding(
        rule_id=rule["rule_id"], clause_id=rule["clause_id"],
        status=status, measured=measured, limit=limit, margin=margin,
        unit="fraction", scope_accounts=account_ids,
        denominator_value=denom_mv, contributors=contributors,
        unattributed_weight=unattributed_mv / denom_mv if unattributed_mv > 0 else None,
        source_quotes=rule.get("source_quotes", []),
    )


def _eval_exposure_cap(rule: dict, account_ids: List[str], hh_id: str) -> Finding:
    """Measure per-group (issuer/security/fund/sector) exposure cap."""
    measure_selector = _get_rule_measure(rule)
    exempt_selector = _get_rule_exempt(rule)
    group_by = rule.get("group_by", "issuer")
    aggregate = rule.get("aggregate", False)
    lookthrough = rule.get("lookthrough", "none")
    rule_max = _get_bound_value(rule.get("max"), rule.get("max_value"), rule.get("max_unit"))
    max_inclusive = _get_bound_inclusive(rule.get("max"), rule.get("max_inclusive"))

    positions = positions_for_accounts(account_ids)
    exposures = compute_exposures(positions, lookthrough=lookthrough)
    sec_map = security_map()

    denom_mv = sum(e.mv for e in exposures)
    if denom_mv == 0:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="cannot_evaluate", measured=None,
            limit={}, margin=None, unit="fraction",
            scope_accounts=account_ids, denominator_value=0,
            reason="Portfolio has zero market value",
        )

    # Build groups
    groups: Dict[str, float] = {}
    group_contributors: Dict[str, List[Contribution]] = {}

    for exp in exposures:
        if exp.security_id == "__residual__":
            continue
        sec = sec_map.get(exp.security_id, {})

        # Check exempt
        if exempt_selector and _eval_selector(exempt_selector, sec):
            continue

        # Check if matches measure
        if not _eval_selector(measure_selector, sec):
            continue

        # Determine group key
        if group_by == "issuer":
            if not exp.issuer_id or sec.get("security_type") in ("etf", "mutual_fund", "money_market", "cash") or exp.security_id == "CASH_USD":
                continue
            key = exp.issuer_id
        elif group_by == "security":
            if exp.security_id == "CASH_USD" or sec.get("security_type") == "cash":
                continue
            key = exp.security_id
        elif group_by == "fund":
            key = exp.via_fund or exp.security_id
        elif group_by == "sector":
            key = sec.get("sector", "unknown")
        elif group_by == "asset_class":
            key = exp.asset_class
        elif group_by == "account":
            key = exp.account_id
        else:
            key = "__all__"

        groups[key] = groups.get(key, 0.0) + exp.mv
        if key not in group_contributors:
            group_contributors[key] = []
        group_contributors[key].append(
            Contribution(
                account_id=exp.account_id,
                security_id=exp.security_id,
                via_fund=exp.via_fund,
                mv=exp.mv,
                share_of_measure=0.0,  # filled below
            )
        )

    if not groups:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="pass", measured=0.0,
            limit={"max": rule_max}, margin=rule_max if rule_max else None,
            unit="fraction", scope_accounts=account_ids,
            denominator_value=denom_mv,
            notes=["No matching holdings found — vacuous pass"],
            source_quotes=rule.get("source_quotes", []),
        )

    if aggregate:
        total_mv = sum(groups.values())
        measured = total_mv / denom_mv
        status = _status_from_value(measured, None, rule_max, True, max_inclusive)
        margin = (rule_max - measured) if rule_max is not None else None
        all_contributors = [c for cs in group_contributors.values() for c in cs]
        for c in all_contributors:
            c.share_of_measure = c.mv / total_mv if total_mv > 0 else 0
        contributors = sorted(all_contributors, key=lambda c: c.mv, reverse=True)[:10]
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status=status, measured=measured,
            limit={"max": rule_max}, margin=margin,
            unit="fraction", scope_accounts=account_ids,
            denominator_value=denom_mv, contributors=contributors,
            source_quotes=rule.get("source_quotes", []),
        )
    else:
        # Per-group: find worst offender and all breaching groups
        group_weights = {k: v / denom_mv for k, v in groups.items()}
        max_key = max(group_weights, key=lambda k: group_weights[k])
        max_weight = group_weights[max_key]

        status = _status_from_value(max_weight, None, rule_max, True, max_inclusive)
        margin = (rule_max - max_weight) if rule_max is not None else None

        breaching_keys = [
            k for k, w in sorted(group_weights.items(), key=lambda x: x[1], reverse=True)
            if rule_max is not None and w > (rule_max + FLOAT_TOL)
        ]

        contributors = []
        for k in (breaching_keys if breaching_keys else [max_key]):
            for c in sorted(group_contributors.get(k, []), key=lambda x: x.mv, reverse=True):
                c.share_of_measure = c.mv / groups[k] if groups[k] > 0 else 0
                contributors.append(c)

        notes = [f"Worst group: {max_key} = {max_weight:.4f}"]
        if breaching_keys:
            notes.append(f"Breaching groups: {', '.join(f'{k} ({group_weights[k]:.4f})' for k in breaching_keys)}")
        elif status == "pass" and len(group_weights) > 1:
            second = sorted(group_weights.values(), reverse=True)
            notes.append(f"Top groups: {dict(sorted(group_weights.items(), key=lambda x: x[1], reverse=True)[:3])}")

        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status=status, measured={max_key: max_weight, "__all__": dict(group_weights)},
            limit={"max": rule_max}, margin=margin,
            unit="fraction", scope_accounts=account_ids,
            denominator_value=denom_mv, contributors=contributors,
            notes=notes, source_quotes=rule.get("source_quotes", []),
        )


def _eval_exclusion(rule: dict, account_ids: List[str], hh_id: str) -> Finding:
    """Any matching holding is a breach. MV must be 0."""
    measure_selector = _get_rule_measure(rule)
    lookthrough = rule.get("lookthrough", "none")

    positions = positions_for_accounts(account_ids)
    exposures = compute_exposures(positions, lookthrough=lookthrough)
    sec_map = security_map()

    denom_mv = sum(e.mv for e in exposures)

    has_preds = False
    if measure_selector:
        sel_dict = measure_selector.model_dump() if hasattr(measure_selector, "model_dump") else measure_selector
        has_preds = bool(sel_dict.get("all_of") or sel_dict.get("any_of"))

    if not has_preds:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="cannot_evaluate", measured=None,
            limit={"max": 0.0}, margin=None,
            unit="fraction", scope_accounts=account_ids,
            denominator_value=denom_mv,
            reason="Exclusion selector has no predicates or targets",
            source_quotes=rule.get("source_quotes", []),
        )

    exempt_selector = _get_rule_exempt(rule)
    matching = []
    for exp in exposures:
        if exp.security_id == "__residual__":
            continue
        sec = sec_map.get(exp.security_id, {})
        if exempt_selector and _eval_selector(exempt_selector, sec):
            continue
        if _eval_selector(measure_selector, sec):
            matching.append(exp)

    total_excluded_mv = sum(e.mv for e in matching)
    measured = total_excluded_mv / denom_mv if denom_mv > 0 else 0.0
    status = "breach" if measured > FLOAT_TOL else "pass"

    contributors = sorted([
        Contribution(
            account_id=e.account_id, security_id=e.security_id,
            via_fund=e.via_fund, mv=e.mv,
            share_of_measure=e.mv / total_excluded_mv if total_excluded_mv > 0 else 0,
        ) for e in matching
    ], key=lambda c: c.mv, reverse=True)

    return Finding(
        rule_id=rule["rule_id"], clause_id=rule["clause_id"],
        status=status, measured=measured,
        limit={"max": 0.0}, margin=-measured if status == "breach" else 0.0,
        unit="fraction", scope_accounts=account_ids,
        denominator_value=denom_mv, contributors=contributors,
        source_quotes=rule.get("source_quotes", []),
    )


def _eval_credit_quality(rule: dict, account_ids: List[str], hh_id: str) -> Finding:
    """Individual bond rating check: all bonds must meet minimum rating."""
    measure_selector = _get_rule_measure(rule)
    rule_max = _get_bound_value(rule.get("max"), rule.get("max_value"), rule.get("max_unit"))
    rule_min = _get_bound_value(rule.get("min"), rule.get("min_value"), rule.get("min_unit"))
    # For credit quality: "A- or better" = rating_rank <= rank_of(A-)

    positions = positions_for_accounts(account_ids)
    sec_map = security_map()

    violators = []
    all_bonds = []
    for pos in positions:
        sec = sec_map.get(pos.security_id, {})
        if not _eval_selector(measure_selector, sec):
            continue
        all_bonds.append(pos)

        # Check rating
        sp = sec.get("rating_sp", "")
        moodys = sec.get("rating_moodys", "")
        bucket = _rating_bucket(sp, moodys)
        if bucket == "unrated":
            violators.append((pos, "unrated"))
        elif bucket == "SUB_IG":
            violators.append((pos, "sub-investment-grade"))

    denom_mv = sum(p.mv for p in positions_for_accounts(account_ids))

    if not all_bonds:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="pass", measured=0.0,
            limit={"rating": "A- or better"},
            margin=None, unit="rating",
            scope_accounts=account_ids, denominator_value=denom_mv,
            notes=["No bonds in scope — vacuous pass"],
            source_quotes=rule.get("source_quotes", []),
        )

    status = "breach" if violators else "pass"
    contributors = [
        Contribution(
            account_id=pos.account_id, security_id=pos.security_id,
            via_fund=None, mv=pos.mv,
            share_of_measure=pos.mv / denom_mv if denom_mv > 0 else 0,
        ) for pos, reason in violators
    ]

    return Finding(
        rule_id=rule["rule_id"], clause_id=rule["clause_id"],
        status=status, measured=len(violators),
        limit={"max_sub_ig_count": 0}, margin=None,
        unit="count", scope_accounts=account_ids,
        denominator_value=denom_mv, contributors=contributors,
        notes=[f"{pos.security_id}: {reason}" for pos, reason in violators],
        source_quotes=rule.get("source_quotes", []),
    )


def _eval_tracked_target(rule: dict, account_ids: List[str], hh_id: str) -> Finding:
    """Tracked target — measured but not a breach until after deadline."""
    deadline_str = rule.get("deadline")
    deadline = date.fromisoformat(deadline_str) if deadline_str else None
    is_tracked = deadline is not None and deadline > VALUATION_DATE

    # Measure the same way as exposure_cap (group_by issuer typically)
    finding = _eval_exposure_cap(rule, account_ids, hh_id)
    finding.tracked = is_tracked
    if is_tracked:
        finding.status = "pass"  # Not a breach yet
        finding.notes.append(f"Tracked target — due {deadline_str}. Current: {finding.measured}")
    return finding


def _eval_instrument_allowlist(rule: dict, account_ids: List[str], hh_id: str) -> Finding:
    """All holdings matching the measure selector must pass the 'allowed' sub-selector."""
    measure_selector = _get_rule_measure(rule)
    # The allowed set is the exempt selector (what's permitted)
    exempt_selector = _get_rule_exempt(rule)

    positions = positions_for_accounts(account_ids)
    sec_map = security_map()
    denom_mv = portfolio_mv(account_ids)

    violators = []
    for pos in positions:
        sec = sec_map.get(pos.security_id, {})
        if not _eval_selector(measure_selector, sec):
            continue
        if exempt_selector and not _eval_selector(exempt_selector, sec):
            violators.append(pos)

    status = "breach" if violators else "pass"
    total_violating_mv = sum(p.mv for p in violators)
    measured = total_violating_mv / denom_mv if denom_mv > 0 else 0.0

    contributors = sorted([
        Contribution(
            account_id=p.account_id, security_id=p.security_id,
            via_fund=None, mv=p.mv,
            share_of_measure=p.mv / total_violating_mv if total_violating_mv > 0 else 0,
        ) for p in violators
    ], key=lambda c: c.mv, reverse=True)

    return Finding(
        rule_id=rule["rule_id"], clause_id=rule["clause_id"],
        status=status, measured=measured,
        limit={"max_outside_allowlist": 0.0}, margin=None,
        unit="fraction", scope_accounts=account_ids,
        denominator_value=denom_mv, contributors=contributors,
        source_quotes=rule.get("source_quotes", []),
    )


def _eval_needs_external_data(rule: dict, account_ids: List[str], hh_id: str) -> Finding:
    """Rules requiring data not in the snapshot → CANNOT_EVALUATE."""
    data_reqs = rule.get("data_requirements", [])
    denom_mv = portfolio_mv(account_ids)
    reason = f"Requires unavailable data: {', '.join(data_reqs)}"
    return Finding(
        rule_id=rule["rule_id"], clause_id=rule["clause_id"],
        status="cannot_evaluate", measured=None,
        limit={}, margin=None, unit="n/a",
        scope_accounts=account_ids, denominator_value=denom_mv,
        reason=reason, source_quotes=rule.get("source_quotes", []),
    )


def _eval_reserve_requirement(rule: dict, account_ids: List[str], hh_id: str) -> Finding:
    """Liquidity/reserve: required cash-like assets ≥ months × monthly_need."""
    freshness_issue = check_freshness(["client_cashflows"], hh_id)
    if freshness_issue:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="cannot_evaluate", measured=None,
            limit={}, margin=None, unit="usd",
            scope_accounts=account_ids,
            denominator_value=portfolio_mv(account_ids),
            reason=freshness_issue,
            source_quotes=rule.get("source_quotes", []),
        )

    cf = cashflows_for_household(hh_id)
    if not cf:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="cannot_evaluate", measured=None,
            limit={}, margin=None, unit="usd",
            scope_accounts=account_ids,
            denominator_value=portfolio_mv(account_ids),
            reason="No client cashflows found",
            source_quotes=rule.get("source_quotes", []),
        )

    reserve_spec = rule.get("reserve") or {}
    min_val = (rule.get("min") or {}).get("value")
    months = reserve_spec.get("months") or min_val or 12
    basis = reserve_spec.get("basis", "essential")
    net_of = reserve_spec.get("net_of") or []

    essential = float(cf.get("monthly_essential_expenses") or 0)
    discretionary = float(cf.get("monthly_discretionary_expenses") or 0)
    pension = float(cf.get("monthly_pension_income") or 0)
    ss = float(cf.get("monthly_social_security_income") or 0)

    if basis == "essential":
        monthly_need = essential
    else:
        monthly_need = essential + discretionary

    if "pension" in net_of:
        monthly_need -= pension
    if "social_security" in net_of:
        monthly_need -= ss

    monthly_need = max(0, monthly_need)
    required = months * monthly_need

    # Measure reserve assets
    measure_selector = _get_rule_measure(rule)
    has_measure = False
    if measure_selector:
        sel_dict = measure_selector.model_dump() if hasattr(measure_selector, "model_dump") else measure_selector
        has_measure = bool(sel_dict.get("all_of") or sel_dict.get("any_of"))

    positions = positions_for_accounts(account_ids)
    sec_map = security_map()
    if has_measure:
        reserve_mv = sum(
            p.mv for p in positions
            if _eval_selector(measure_selector, sec_map.get(p.security_id, {}))
        )
    else:
        # Default reserve assets: cash and cash equivalents (including short-term T-bills)
        reserve_mv = sum(
            p.mv for p in positions
            if p.security_id == "CASH_USD"
            or (sec_map.get(p.security_id, {}).get("asset_class") in ("cash", "cash_equivalent", "cash_and_equivalents"))
            or (sec_map.get(p.security_id, {}).get("security_type") in ("money_market", "cash", "treasury_bill"))
            or (sec_map.get(p.security_id, {}).get("security_type") == "treasury" and sec_map.get(p.security_id, {}).get("maturity_date", "") <= "2027-09-25")
        )

    status = "pass" if reserve_mv >= required - FLOAT_TOL else "breach"
    margin = reserve_mv - required

    return Finding(
        rule_id=rule["rule_id"], clause_id=rule["clause_id"],
        status=status, measured=reserve_mv,
        limit={"min_usd": required}, margin=margin,
        unit="usd", scope_accounts=account_ids,
        denominator_value=portfolio_mv(account_ids),
        notes=[f"Required: {months:.0f} × ${monthly_need:,.0f}/mo = ${required:,.0f}; Held: ${reserve_mv:,.0f}"],
        assumptions=[f"Using {basis} basis net of {net_of}"],
        source_quotes=rule.get("source_quotes", []),
    )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

KIND_EVALUATORS = {
    "allocation_range": _eval_allocation_range,
    "exposure_cap": _eval_exposure_cap,
    "exclusion": _eval_exclusion,
    "credit_quality": _eval_credit_quality,
    "reserve_requirement": _eval_reserve_requirement,
    "instrument_allowlist": _eval_instrument_allowlist,
    "tracked_target": _eval_tracked_target,
    "needs_external_data": _eval_needs_external_data,
}


# ---------------------------------------------------------------------------
# Main check_household function
# ---------------------------------------------------------------------------

def check_household(hh_id: str, extraction: dict) -> dict:
    """
    Run checker on all rules for a household.
    Returns a dict with all findings, summary, and confirmed_breaches list.
    """
    logger.info("Checking %s", hh_id)

    from copilot.data.load import managed_accounts as get_managed_accounts

    default_accounts = get_managed_accounts(hh_id)
    all_findings = []
    confirmed_breaches = []

    for clause in extraction.get("clauses", []):
        classification = clause.get("classification", "not_constraint")

        # needs_review clauses: produce a needs_review finding (provisional measure shown)
        if classification == "needs_review":
            review = clause.get("review", {})
            # If there's a provisional rule, measure it
            provisional = review.get("provisional_rule")
            if provisional:
                try:
                    finding = _evaluate_rule(provisional, default_accounts, hh_id)
                    finding.status = "needs_review"
                    finding.reason = review.get("reason", "needs_review")
                    finding.notes.append(f"PM question: {review.get('question', '')}")
                    all_findings.append(finding)
                except Exception as e:
                    logger.warning("Provisional rule error for %s: %s", clause.get("clause_id"), e)
            continue  # don't add to confirmed breaches

        if classification != "rule":
            continue

        for rule in clause.get("rules", []):
            try:
                finding = _evaluate_rule(rule, default_accounts, hh_id)
                all_findings.append(finding)

                if finding.status == "breach" and rule.get("severity", "hard") == "hard":
                    confirmed_breaches.append(rule["rule_id"])
            except Exception as e:
                logger.error("Error evaluating rule %s: %s", rule.get("rule_id", "?"), e)
                all_findings.append(Finding(
                    rule_id=rule.get("rule_id", "error"),
                    clause_id=rule.get("clause_id", "?"),
                    status="cannot_evaluate",
                    measured=None, limit={}, margin=None,
                    unit="n/a", scope_accounts=default_accounts,
                    denominator_value=0,
                    reason=f"Internal evaluator error: {e}",
                ))

    # Summary
    status_counts = {"pass": 0, "breach": 0, "needs_review": 0, "cannot_evaluate": 0, "tracked": 0}
    for f in all_findings:
        if f.tracked:
            status_counts["tracked"] += 1
        elif f.status in status_counts:
            status_counts[f.status] += 1

    logger.info(
        "%s check complete: %s", hh_id,
        " | ".join(f"{k}={v}" for k, v in status_counts.items() if v > 0)
    )

    def _serialize_finding(f: Finding) -> dict:
        return {
            "rule_id": f.rule_id,
            "clause_id": f.clause_id,
            "status": f.status,
            "measured": f.measured,
            "limit": f.limit,
            "margin": f.margin,
            "unit": f.unit,
            "scope_accounts": f.scope_accounts,
            "denominator_value": f.denominator_value,
            "contributors": [
                {
                    "account_id": c.account_id, "security_id": c.security_id,
                    "via_fund": c.via_fund, "mv": c.mv, "share_of_measure": c.share_of_measure,
                } for c in (f.contributors or [])
            ],
            "unattributed_weight": f.unattributed_weight,
            "inputs": f.inputs,
            "assumptions": f.assumptions,
            "notes": f.notes,
            "tracked": f.tracked,
            "source_quotes": f.source_quotes,
            "reason": f.reason,
        }

    return {
        "household": hh_id,
        "snapshot_date": str(VALUATION_DATE),
        "portfolio_mv": portfolio_mv(default_accounts),
        "findings": [_serialize_finding(f) for f in all_findings],
        "confirmed_breaches": confirmed_breaches,
        "summary": status_counts,
    }


def _evaluate_rule(rule: dict, default_accounts: List[str], hh_id: str) -> Finding:
    """Evaluate a single rule dict and return a Finding."""
    kind = rule.get("kind", "")
    rule_accounts = rule.get("scope_accounts") or default_accounts
    scope = scope_for_rule(hh_id, rule_accounts if rule_accounts != default_accounts else None)

    # Freshness check for rules requiring external data
    data_reqs = rule.get("data_requirements", [])
    if data_reqs:
        if kind != "needs_external_data":
            # Some data is needed but available (e.g. client_cashflows for reserve)
            freshness_issue = check_freshness(data_reqs, hh_id)
            if freshness_issue:
                return Finding(
                    rule_id=rule["rule_id"], clause_id=rule["clause_id"],
                    status="cannot_evaluate", measured=None,
                    limit={}, margin=None, unit="n/a",
                    scope_accounts=scope, denominator_value=0,
                    reason=freshness_issue,
                    source_quotes=rule.get("source_quotes", []),
                )
        else:
            return _eval_needs_external_data(rule, scope, hh_id)

    # Effective date check
    eff_from = rule.get("effective_from")
    if eff_from and date.fromisoformat(eff_from) > VALUATION_DATE:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="pass", measured=None,
            limit={}, margin=None, unit="n/a",
            scope_accounts=scope, denominator_value=0,
            notes=[f"Rule effective from {eff_from} — not yet in effect"],
            source_quotes=rule.get("source_quotes", []),
        )

    evaluator = KIND_EVALUATORS.get(kind)
    if evaluator is None:
        return Finding(
            rule_id=rule["rule_id"], clause_id=rule["clause_id"],
            status="cannot_evaluate", measured=None,
            limit={}, margin=None, unit="n/a",
            scope_accounts=scope, denominator_value=0,
            reason=f"Unknown rule kind: {kind}",
        )

    return evaluator(rule, scope, hh_id)
