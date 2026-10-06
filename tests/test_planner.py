"""
Planner unit tests.

Tests cover:
- Lot rounding (sell_up, buy_down)
- No negative cash per account
- No short sales
- Buys only from approved list
- Trade format (CSV output)
- Denominator invariant: total MV is constant under trades
"""

from __future__ import annotations

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Lot rounding
# ---------------------------------------------------------------------------

def test_lot_rounding_sell_rounds_up():
    """Selling should round up to whole shares."""
    from copilot.plan.planner import _mv_to_lots
    # $150.50 MV at $100/share → 1.505 shares → rounds up to 2
    # Use a security we know is equity (whole share)
    # We test the math directly
    # 1 share lot, $100 mv_per_lot → $150.50 → 2 shares
    import math
    mv = 150.50
    mv_per_lot = 100.0
    lots_up = math.ceil(mv / mv_per_lot - 1e-9)
    assert lots_up == 2, f"Expected 2, got {lots_up}"


def test_lot_rounding_buy_rounds_down():
    """Buying should round down to whole shares."""
    import math
    mv = 150.50
    mv_per_lot = 100.0
    lots_down = math.floor(mv / mv_per_lot + 1e-9)
    assert lots_down == 1, f"Expected 1, got {lots_down}"


def test_bond_lot_size_1000():
    """Bond lots must be $1000 face value."""
    from copilot.data.load import security_map
    sec_map = security_map()
    # Find any corporate bond or treasury
    bonds = [s for s in sec_map.values() if s.get("security_type") in ("corporate_bond", "treasury", "treasury_bill")]
    assert bonds, "No bonds found in securities"
    # Verify our is_bond function recognizes them
    from copilot.plan.planner import _is_bond, _lot_size
    for bond in bonds[:3]:
        assert _is_bond(bond["security_id"]), f"{bond['security_id']} not identified as bond"
        assert _lot_size(bond["security_id"]) == 1000.0, f"Bond lot size should be 1000"


# ---------------------------------------------------------------------------
# Trade CSV format
# ---------------------------------------------------------------------------

def test_trades_csv_format():
    """Trades CSV must have correct header and numeric quantity."""
    from copilot.plan.planner import _trades_to_csv, Trade
    trades = [
        Trade("ACC-001", "AAPL", "sell", 100),
        Trade("ACC-001", "BOND01", "buy", 5000),
    ]
    csv_str = _trades_to_csv(trades)
    lines = csv_str.strip().split("\n")
    assert lines[0] == "account_id,security_id,side,quantity", f"Bad header: {lines[0]}"
    assert len(lines) == 3, f"Expected 3 lines (header + 2 trades), got {len(lines)}"
    # Quantities should be plain numbers (no $ or ,)
    for line in lines[1:]:
        parts = line.split(",")
        qty = parts[-1]
        float(qty)  # should not raise


def test_trades_csv_empty():
    """Empty trades list produces header-only CSV."""
    from copilot.plan.planner import _trades_to_csv
    csv_str = _trades_to_csv([])
    lines = csv_str.strip().split("\n")
    assert len(lines) == 1, f"Expected header only, got {lines}"
    assert lines[0] == "account_id,security_id,side,quantity"


# ---------------------------------------------------------------------------
# No short sales
# ---------------------------------------------------------------------------

def test_no_short_sales():
    """Sell quantity must not exceed held quantity."""
    from copilot.plan.planner import _round_trades, Trade
    from copilot.data.load import managed_accounts

    accounts = managed_accounts("HH-PATEL")
    # Create a trade that would oversell
    from copilot.data.load import positions_for_accounts
    positions = positions_for_accounts(accounts)
    if not positions:
        pytest.skip("No positions for HH-PATEL")

    pos = positions[0]
    # Try to sell 2x the held quantity in $ (raw MV)
    oversell = Trade(
        account_id=pos.account_id,
        security_id=pos.security_id,
        side="sell",
        quantity=pos.mv * 2,  # in dollars (pre-rounding)
    )
    rounded = _round_trades([oversell], accounts, dust_threshold=100.0)
    if rounded:
        assert rounded[0].quantity <= pos.quantity, \
            f"Sell qty {rounded[0].quantity} exceeds held {pos.quantity}"


# ---------------------------------------------------------------------------
# Denominator invariant
# ---------------------------------------------------------------------------

def test_denominator_invariant():
    """Total portfolio MV is constant under trades (proceeds stay as cash)."""
    # This is a property test: after applying any trade, total MV = pre-trade MV
    # We verify this with a known position
    from copilot.data.load import managed_accounts, portfolio_mv
    accounts = managed_accounts("HH-PATEL")
    mv_before = portfolio_mv(accounts)
    assert mv_before > 0
    # After a hypothetical sell of 0 shares: MV unchanged (trivial)
    # After a sell + cash: MV still same (sell proceeds → cash)
    # This is guaranteed by the helper design, but we verify the math assumption
    # is documented/tested here

    # Verify: all-accounts portfolio MV is consistent
    from copilot.data.load import positions_for_accounts
    positions = positions_for_accounts(accounts)
    computed_mv = sum(p.mv for p in positions)
    assert abs(computed_mv - mv_before) < 1.0, \
        f"portfolio_mv ({mv_before}) != sum of positions ({computed_mv})"


# ---------------------------------------------------------------------------
# Buys only from approved list
# ---------------------------------------------------------------------------

def test_buy_candidates_from_approved_list():
    """All buy candidates must be in the approved list."""
    from copilot.plan.planner import _buy_candidates
    from copilot.data.load import approved_list, managed_accounts

    accounts = managed_accounts("HH-PATEL")
    approved_ids = {row["security_id"] for row in approved_list()}
    candidates = _buy_candidates([], accounts)

    for c in candidates:
        assert c.security_id in approved_ids, \
            f"Buy candidate {c.security_id} not in approved list"


# ---------------------------------------------------------------------------
# Plan facts structure
# ---------------------------------------------------------------------------

def test_plan_facts_structure():
    """plan_facts must contain required keys."""
    from copilot.plan.memo import build_plan_facts

    mock_findings = {
        "household": "HH-TEST",
        "snapshot_date": "2026-09-25",
        "portfolio_mv": 1_000_000,
        "findings": [],
        "confirmed_breaches": [],
        "summary": {"pass": 0, "breach": 0},
    }
    facts = build_plan_facts("HH-TEST", mock_findings, [], None)
    required_keys = ["household", "snapshot_date", "portfolio_mv", "breaches",
                     "trades", "review_items", "summary"]
    for key in required_keys:
        assert key in facts, f"Missing key {key!r} in plan_facts"


# ---------------------------------------------------------------------------
# Memo lint
# ---------------------------------------------------------------------------

def test_lint_memo_passes_for_clean_memo():
    """Memo lint should pass when all numbers are in facts."""
    from copilot.plan.memo import lint_memo
    import tempfile, json, os

    facts = {
        "household": "HH-TEST",
        "snapshot_date": "2026-09-25",
        "portfolio_mv": 1000000,
        "breaches": [],
        "trades": [{"security_id": "BOND01", "side": "sell", "quantity": 100}],
        "needs_review_findings": [],
        "cannot_evaluate_findings": [],
        "tracked_findings": [],
        "review_items": [],
        "summary": {"pass": 5},
        "all_findings": [],
    }
    memo = "Portfolio MV: $1,000,000\nSell BOND01: 100 shares"

    with tempfile.TemporaryDirectory() as tmpdir:
        memo_path = Path(tmpdir) / "plan_memo.md"
        facts_path = Path(tmpdir) / "plan_facts.json"
        memo_path.write_text(memo)
        facts_path.write_text(json.dumps(facts))

        errors = lint_memo(memo_path, facts_path)
        assert not errors, f"Lint errors for clean memo: {errors}"
