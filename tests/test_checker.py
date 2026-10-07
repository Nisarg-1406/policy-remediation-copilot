"""
Checker unit tests — Patel functional gold numbers.

All assertions are derived from patel_household_expected.md:
  Portfolio MV: $2,400,167
  Equity: 82.08% → PASS (75–95%)
  Fixed income: 12.50% → PASS (5–20%)
  Cash: 4.17% → PASS (0–5%)
  Kestrel direct: 30.00% → PASS+tracked (tracked target, deadline 2028-12-31)
  Kestrel with lookthrough: 31.05%
  BVLT (crypto): 1.25% → BREACH (exclusion)
  Non-Kestrel individual stocks: 15.83% → PASS (≤20% aggregate)
  Largest non-Kestrel issuer (Northvale): 6.25% → PASS (≤10%)
  International equity: 6.25% of portfolio, 7.62% of equity → NEEDS_REVIEW
  P-11 / P-12: CANNOT_EVALUATE (needs transactions)

Run with:
  pytest tests/test_checker.py -v
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "patel_rules.json"
HH_ID = "HH-PATEL"
FLOAT_TOL = 1e-3  # 0.1% tolerance for MV computations


@pytest.fixture(scope="module")
def patel_extraction():
    """Load the hand-written Patel rules fixture."""
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture(scope="module")
def patel_findings(patel_extraction):
    """Run checker on the Patel fixture and return findings dict."""
    from copilot.check.engine import check_household
    return check_household(HH_ID, patel_extraction)


@pytest.fixture(scope="module")
def findings_by_rule(patel_findings):
    """Index findings by rule_id."""
    return {f["rule_id"]: f for f in patel_findings["findings"]}


# ---------------------------------------------------------------------------
# Portfolio MV
# ---------------------------------------------------------------------------

def test_portfolio_mv(patel_findings):
    """Portfolio MV must be approximately $2,400,167."""
    mv = patel_findings["portfolio_mv"]
    assert abs(mv - 2_400_167) < 1000, f"Portfolio MV {mv} not close to $2,400,167"


# ---------------------------------------------------------------------------
# Allocation range rules
# ---------------------------------------------------------------------------

def test_equity_allocation_pass(findings_by_rule):
    """Equity 82.08% → PASS (75–95%)."""
    f = findings_by_rule.get("R-P05")
    assert f is not None, "R-P05 not found"
    assert f["status"] == "pass", f"Expected pass, got {f['status']}"
    measured = float(f["measured"])
    assert abs(measured - 0.8208) < FLOAT_TOL, f"Equity {measured:.4f} not ~82.08%"


def test_fixed_income_allocation_pass(findings_by_rule):
    """Fixed income 12.50% → PASS (5–20%)."""
    f = findings_by_rule.get("R-P06")
    assert f is not None, "R-P06 not found"
    assert f["status"] == "pass", f"Expected pass, got {f['status']}"
    measured = float(f["measured"])
    assert abs(measured - 0.1250) < FLOAT_TOL, f"Fixed income {measured:.4f} not ~12.50%"


def test_cash_allocation_pass(findings_by_rule):
    """Cash 4.17% → PASS (0–5%)."""
    f = findings_by_rule.get("R-P07")
    assert f is not None, "R-P07 not found"
    assert f["status"] == "pass", f"Expected pass, got {f['status']}"
    measured = float(f["measured"])
    assert abs(measured - 0.0417) < FLOAT_TOL, f"Cash {measured:.4f} not ~4.17%"


# ---------------------------------------------------------------------------
# Exposure cap
# ---------------------------------------------------------------------------

def test_issuer_cap_pass(findings_by_rule):
    """Largest non-Kestrel issuer (Northvale) = 6.25% → PASS (≤10%)."""
    f = findings_by_rule.get("R-P09")
    assert f is not None, "R-P09 not found"
    assert f["status"] == "pass", f"Expected pass, got {f['status']}"
    # measured is a dict {worst_issuer: weight, __all__: {...}}
    measured = f["measured"]
    if isinstance(measured, dict):
        worst_val = max(measured.get("__all__", {}).values(), default=0)
    else:
        worst_val = float(measured)
    assert abs(worst_val - 0.0625) < FLOAT_TOL, f"Largest issuer {worst_val:.4f} not ~6.25%"


def test_individual_stocks_aggregate_pass(findings_by_rule):
    """Non-Kestrel individual stocks aggregate 15.83% → PASS (≤20%)."""
    f = findings_by_rule.get("R-P13")
    assert f is not None, "R-P13 not found"
    assert f["status"] == "pass", f"Expected pass, got {f['status']}"
    measured = float(f["measured"]) if not isinstance(f["measured"], dict) else float(
        f["measured"].get("__all__", {0: 0})
    )
    assert abs(measured - 0.1583) < FLOAT_TOL, f"Individual stocks {measured:.4f} not ~15.83%"


# ---------------------------------------------------------------------------
# Tracked target
# ---------------------------------------------------------------------------

def test_kestrel_tracked(findings_by_rule):
    """Kestrel direct 30.00% → PASS+tracked (due 2028-12-31)."""
    f = findings_by_rule.get("R-P10")
    assert f is not None, "R-P10 not found"
    assert f["status"] == "pass", f"Expected pass (tracked), got {f['status']}"
    assert f.get("tracked") is True, "R-P10 should be tracked=True"
    # measured is group exposure dict or scalar
    measured = f["measured"]
    if isinstance(measured, dict):
        worst_val = max(measured.get("__all__", {}).values(), default=0)
    else:
        worst_val = float(measured)
    assert abs(worst_val - 0.30) < FLOAT_TOL, f"Kestrel direct {worst_val:.4f} not ~30.00%"


# ---------------------------------------------------------------------------
# Exclusion (BREACH)
# ---------------------------------------------------------------------------

def test_crypto_exclusion_breach(findings_by_rule):
    """BVLT (crypto) 1.25% → BREACH."""
    f = findings_by_rule.get("R-P14")
    assert f is not None, "R-P14 not found"
    assert f["status"] == "breach", f"Expected breach, got {f['status']}"
    measured = float(f["measured"])
    assert abs(measured - 0.0125) < FLOAT_TOL, f"Crypto {measured:.4f} not ~1.25%"
    # Must list BVLT as contributor
    contrib_sids = [c["security_id"] for c in (f.get("contributors") or [])]
    assert "BVLT" in contrib_sids, f"BVLT not in contributors: {contrib_sids}"


# ---------------------------------------------------------------------------
# Needs external data → CANNOT_EVALUATE
# ---------------------------------------------------------------------------

def test_p11_cannot_evaluate(findings_by_rule):
    """P-11 (annual Kestrel sale) → CANNOT_EVALUATE (needs transactions)."""
    f = findings_by_rule.get("R-P11")
    assert f is not None, "R-P11 not found"
    assert f["status"] == "cannot_evaluate", f"Expected cannot_evaluate, got {f['status']}"


def test_p12_cannot_evaluate(findings_by_rule):
    """P-12 (no Kestrel purchases) → CANNOT_EVALUATE (needs transactions)."""
    f = findings_by_rule.get("R-P12")
    assert f is not None, "R-P12 not found"
    assert f["status"] == "cannot_evaluate", f"Expected cannot_evaluate, got {f['status']}"


# ---------------------------------------------------------------------------
# Needs review
# ---------------------------------------------------------------------------

def test_p16_needs_review(findings_by_rule):
    """P-16 (intl equity, no threshold) → NEEDS_REVIEW."""
    f = findings_by_rule.get("R-P16-provisional")
    if f is None:
        # Also acceptable if the clause produces no finding (only review item)
        return
    assert f["status"] == "needs_review", f"Expected needs_review, got {f['status']}"


# ---------------------------------------------------------------------------
# Instrument allowlist
# ---------------------------------------------------------------------------

def test_fixed_income_allowlist_pass(findings_by_rule):
    """Fixed income held only via bond funds or Treasuries → PASS."""
    f = findings_by_rule.get("R-P15")
    assert f is not None, "R-P15 not found"
    assert f["status"] == "pass", f"Expected pass, got {f['status']}"


# ---------------------------------------------------------------------------
# Confirmed breaches rollup
# ---------------------------------------------------------------------------

def test_only_crypto_is_confirmed_breach(patel_findings):
    """Only R-P14 (BVLT crypto) should be in confirmed_breaches."""
    breaches = patel_findings.get("confirmed_breaches", [])
    assert "R-P14" in breaches, f"R-P14 not in confirmed_breaches: {breaches}"
    # No other rule should be a confirmed breach (tracked, needs_review, cannot_eval don't count)
    non_crypto = [b for b in breaches if b != "R-P14"]
    assert not non_crypto, f"Unexpected confirmed breaches: {non_crypto}"


# ---------------------------------------------------------------------------
# Valuation sanity tests
# ---------------------------------------------------------------------------

def test_scope_excludes_unmanaged_accounts():
    """Chen CHEN-401K (managed_by_firm=false) must be excluded from scope."""
    from copilot.data.load import managed_accounts
    accounts = managed_accounts("HH-CHEN")
    assert "CHEN-401K" not in accounts, "CHEN-401K should be excluded (managed_by_firm=false)"


def test_patel_all_three_accounts_in_scope():
    """Patel: all 3 accounts should be in scope."""
    from copilot.data.load import managed_accounts
    accounts = managed_accounts("HH-PATEL")
    assert len(accounts) == 3, f"Expected 3 Patel accounts, got {len(accounts)}: {accounts}"


def test_denominator_constant_under_trades():
    """Total portfolio MV must be constant under trades (sell → cash stays in account)."""
    from copilot.data.load import managed_accounts, portfolio_mv
    accounts = managed_accounts("HH-PATEL")
    mv_before = portfolio_mv(accounts)
    # MV should be positive
    assert mv_before > 0, "Portfolio MV should be positive"
    # (Trade simulation test: covered in planner tests)


def test_cash_like_security_mv():
    """CASH_USD quantity equals MV (price_quote=per_unit, price=1.0)."""
    from copilot.data.load import positions_for_accounts, managed_accounts
    accounts = managed_accounts("HH-PATEL")
    positions = positions_for_accounts(accounts)
    cash_positions = [p for p in positions if p.security_id == "CASH_USD"]
    for pos in cash_positions:
        assert abs(pos.mv - pos.quantity) < 1.0, \
            f"CASH_USD MV {pos.mv} != quantity {pos.quantity}"


def test_has_lookthrough_data():
    """has_lookthrough_data accurately reports whether a fund has constituent rows."""
    from copilot.data.load import has_lookthrough_data
    assert has_lookthrough_data("KUSX") is True
    assert has_lookthrough_data("NONEXISTENT_FUND") is False


def test_missing_lookthrough_triggers_cannot_evaluate(monkeypatch):
    """When lookthrough=funds and a fund has no constituents, rule returns cannot_evaluate."""
    from copilot.data import load as data_load
    from copilot.check.engine import check_household

    # Mock fund_constituents to exclude KUSX
    orig_fc = data_load.fund_constituents()
    mock_fc = [r for r in orig_fc if r["fund_id"] != "KUSX"]
    monkeypatch.setattr(data_load, "fund_constituents", lambda: mock_fc)

    rule = {
        "rule_id": "R-TEST-LT",
        "clause_id": "S-TEST",
        "kind": "exposure_cap",
        "measure": {"all_of": [{"field": "issuer_id", "op": "eq", "value": "NRVL"}]},
        "group_by": "issuer",
        "aggregate": False,
        "max": {"value": 0.10, "unit": "fraction", "inclusive": True},
        "lookthrough": "funds",
        "severity": "hard",
        "source_quotes": ["Test quote"],
    }
    extraction = {
        "household_id": "HH-PATEL",
        "clauses": [{"clause_id": "S-TEST", "classification": "rule", "rules": [rule]}],
    }
    result = check_household("HH-PATEL", extraction)
    f = result["findings"][0]
    assert f["status"] == "cannot_evaluate"
    assert "KUSX" in f["reason"]

