"""Tests for helpers/portfolio.py. Run from the folder that contains BRIEF.md: python3 -m unittest helpers/test_portfolio.py"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portfolio import (CASH_ID, Portfolio, Trade, TradeError, apply_trades, load,  # noqa: E402
                       market_values, price, read_trades, unit_value, write_trades)

HH = "HH-OKAFOR"
JT, IRA = "OKF-JT", "OKF-RIRA"


class HelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load()
        cls.book = cls.data.portfolio(HH)

    def test_counts(self):
        self.assertGreaterEqual(len(self.data.households), 4)
        for sid in self.data.approved:
            self.assertIn(sid, self.data.securities)
        for h in self.data.holdings:
            self.assertIn(h["security_id"], self.data.securities)

    def test_bond_is_pct_of_par(self):
        uv, p = unit_value(self.data, "UST-2030")
        self.assertAlmostEqual(uv, p.close / 100.0)

    def test_stale_price_falls_back(self):
        stale = [sid for sid, by_date in self.data.prices.items() if "2026-09-25" not in by_date]
        for sid in stale:
            p = price(self.data, sid)
            self.assertTrue(p.stale)
            self.assertLess(p.price_date, "2026-09-25")

    def test_market_values_sum(self):
        total = sum(p.market_value for p in market_values(self.data, self.book))
        self.assertTrue(1_400_000 < total < 1_600_000, total)

    def test_portfolio_managed_only(self):
        for hh in self.data.households:
            all_accts = set(self.data.portfolio(hh).accounts())
            managed = set(self.data.portfolio(hh, managed_only=True).accounts())
            for a in all_accts - managed:
                self.assertEqual(self.data.accounts[a]["managed_by_firm"], "false")

    def test_sell_then_buy_same_account(self):
        trades = [Trade(JT, "MRDN", "sell", 20), Trade(JT, "UST-2031", "buy", 5000)]
        r = apply_trades(self.data, self.book, trades)
        self.assertEqual(r.errors, [])
        self.assertEqual(r.portfolio.quantity(JT, "MRDN"), self.book.quantity(JT, "MRDN") - 20)
        self.assertEqual(r.portfolio.quantity(JT, "UST-2031"), 5000)

    def test_no_cash_transfer_between_accounts(self):
        trades = [Trade(JT, "KUSX", "sell", 300), Trade(IRA, "CITB", "buy", 1000)]
        r = apply_trades(self.data, self.book, trades)
        self.assertTrue(any("negative cash" in e for e in r.errors))
        self.assertEqual(r.portfolio.quantity(IRA, "CITB"), 0)

    def test_rejects_short_sale(self):
        held = self.book.quantity(JT, "BRCH")
        r = apply_trades(self.data, self.book, [Trade(JT, "BRCH", "sell", held + 1)])
        self.assertTrue(any("short" in e for e in r.errors))

    def test_rejects_unapproved_buy(self):
        not_approved = next(s for s in self.data.securities if s not in self.data.approved and s != CASH_ID)
        r = apply_trades(self.data, self.book, [Trade(JT, not_approved, "buy", 1)])
        self.assertTrue(any("approved" in e for e in r.errors))

    def test_rejects_unknown_security(self):
        r = apply_trades(self.data, self.book, [Trade(JT, "NOPE", "buy", 1)])
        self.assertTrue(any("unknown security" in e for e in r.errors))

    def test_rejects_unmanaged_account(self):
        for a in self.data.accounts.values():
            if a["managed_by_firm"] == "false":
                book = self.data.portfolio(a["household_id"])
                sid = next(s for (acct, s) in book.quantities if acct == a["account_id"])
                r = apply_trades(self.data, book, [Trade(a["account_id"], sid, "sell", 1)])
                self.assertTrue(any("not managed" in e for e in r.errors))

    def test_bond_face_lots(self):
        r = apply_trades(self.data, self.book, [Trade(IRA, "UST-2030", "sell", 1500)])
        self.assertTrue(any("1,000 face" in e for e in r.errors))
        held = self.book.quantity(IRA, "UST-2028")
        r = apply_trades(self.data, self.book, [Trade(IRA, "UST-2028", "sell", held)])
        self.assertEqual(r.errors, [])

    def test_odd_lot_full_exit_in_two_rows(self):
        book = Portfolio({(IRA, "UST-2028"): 148_500.0, (IRA, CASH_ID): 0.0})
        r = apply_trades(self.data, book, [Trade(IRA, "UST-2028", "sell", 100_000),
                                           Trade(IRA, "UST-2028", "sell", 48_500)])
        self.assertEqual(r.errors, [])
        self.assertEqual(r.portfolio.quantity(IRA, "UST-2028"), 0)
        r = apply_trades(self.data, book, [Trade(IRA, "UST-2028", "sell", 48_500),
                                           Trade(IRA, "UST-2028", "sell", 100_000)])
        self.assertEqual(r.errors, [])

    def test_managed_only_without_household(self):
        accts = self.data.portfolio(managed_only=True).accounts()
        for a in accts:
            self.assertEqual(self.data.accounts[a]["managed_by_firm"], "true")

    def test_account_outside_portfolio(self):
        other = next(a for a, acc in self.data.accounts.items() if acc["household_id"] != HH)
        r = apply_trades(self.data, self.book, [Trade(other, "KGMXX", "buy", 1)])
        self.assertTrue(any("not in this portfolio" in e for e in r.errors))
        self.assertNotIn(other, r.portfolio.accounts())

    def test_side_is_case_insensitive(self):
        r = apply_trades(self.data, self.book, [Trade(JT, "MRDN", "SELL", 1)])
        self.assertEqual(r.errors, [])

    def test_whole_shares(self):
        r = apply_trades(self.data, self.book, [Trade(JT, "KUSX", "sell", 1.5)])
        self.assertTrue(any("whole shares" in e for e in r.errors))

    def test_strict_raises(self):
        with self.assertRaises(TradeError):
            apply_trades(self.data, self.book, [Trade(JT, "NOPE", "buy", 1)], strict=True)

    def test_total_value_preserved(self):
        before = sum(p.market_value for p in market_values(self.data, self.book))
        r = apply_trades(self.data, self.book, [Trade(JT, "GRBC", "sell", 100), Trade(JT, "KGMXX", "buy", 5000)])
        self.assertEqual(r.errors, [])
        after = sum(p.market_value for p in market_values(self.data, r.portfolio))
        self.assertAlmostEqual(before, after, places=2)

    def test_read_trades_tolerates_bom_and_spaces(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.csv")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("account_id, security_id, side, quantity\n OKF-JT , MRDN , Sell , 10 \n")
            t = read_trades(path)
            self.assertEqual((t[0].account_id, t[0].security_id, t[0].side, t[0].quantity), (JT, "MRDN", "sell", 10))

    def test_read_trades_header_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.csv")
            with open(path, "w") as f:
                f.write("account_id,security_id,side,quantity\n")
            self.assertEqual(read_trades(path), [])

    def test_read_trades_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.csv")
            open(path, "w").close()
            with self.assertRaises(ValueError):
                read_trades(path)

    def test_write_then_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out", "trades.csv")
            write_trades(path, [Trade(JT, "MRDN", "sell", 10), Trade(IRA, "UST-2030", "sell", 2000)])
            t = read_trades(path)
            self.assertEqual([(x.account_id, x.security_id, x.side, x.quantity) for x in t],
                             [(JT, "MRDN", "sell", 10), (IRA, "UST-2030", "sell", 2000)])

    def test_read_trades_rejects_bad_header(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.csv")
            with open(path, "w") as f:
                f.write("account,security,side,qty\n")
            with self.assertRaises(ValueError):
                read_trades(path)


if __name__ == "__main__":
    unittest.main()
