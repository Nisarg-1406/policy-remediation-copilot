"""Portfolio data helper for the take-home.

Standard library only. Python 3.9 or later.

What it does:
  - load the CSVs in data/portfolio/
  - value positions with the price conventions applied
  - apply a list of trades and return the new portfolio, rejecting invalid trades

What it does not do: exposures, look-through, issuer grouping, asset-class
totals or percentage denominators. Those are part of the assignment.

Quick start:

    from helpers.portfolio import load, market_values, read_trades, apply_trades

    data = load()                                   # finds data/portfolio/ next to helpers/
    book = data.portfolio("HH-CHEN")                # all of the household's accounts
    for p in market_values(data, book):
        print(p.account_id, p.security_id, round(p.market_value, 2))

    trades = read_trades("output/HH-CHEN/trades.csv")
    result = apply_trades(data, book, trades)
    if result.errors:
        print(result.errors)
    new_book = result.portfolio
"""

import csv
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

VALUATION_DATE = "2026-09-25"
CASH_ID = "CASH_USD"
CASH_TOLERANCE = 0.005  # dollars; absorbs float rounding

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(_HERE, "..", "data", "portfolio")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _read(path: str) -> List[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class Dataset:
    """All CSVs as plain Python structures. Values are strings except where noted."""
    households: Dict[str, dict]
    accounts: Dict[str, dict]
    securities: Dict[str, dict]
    prices: Dict[str, Dict[str, float]]          # security_id -> {price_date: close}
    holdings: List[dict]                          # account_id, security_id, quantity (float)
    fund_constituents: Dict[str, List[dict]]      # fund_id -> rows
    fund_sector_weights: Dict[str, List[dict]]    # fund_id -> rows
    client_cashflows: Dict[str, dict]
    approved: Dict[str, dict]                     # security_id -> approved_list row

    def accounts_for(self, household_id: str, managed_only: bool = False) -> List[str]:
        out = []
        for a in self.accounts.values():
            if a["household_id"] != household_id:
                continue
            if managed_only and a["managed_by_firm"] != "true":
                continue
            out.append(a["account_id"])
        return out

    def portfolio(self, household_id: Optional[str] = None, managed_only: bool = False) -> "Portfolio":
        """Holdings for one household (or all households) as a Portfolio."""
        keep = None
        if household_id is not None:
            if household_id not in self.households:
                raise KeyError(f"unknown household {household_id}")
            keep = set(self.accounts_for(household_id, managed_only))
        elif managed_only:
            keep = {a for a, r in self.accounts.items() if r["managed_by_firm"] == "true"}
        q: Dict[Tuple[str, str], float] = {}
        for h in self.holdings:
            if keep is not None and h["account_id"] not in keep:
                continue
            key = (h["account_id"], h["security_id"])
            q[key] = q.get(key, 0.0) + h["quantity"]
        return Portfolio(q)


def load(data_dir: Optional[str] = None) -> Dataset:
    d = data_dir or DEFAULT_DATA_DIR
    prices: Dict[str, Dict[str, float]] = {}
    for r in _read(os.path.join(d, "prices.csv")):
        prices.setdefault(r["security_id"], {})[r["price_date"]] = float(r["close"])
    holdings = []
    for r in _read(os.path.join(d, "holdings.csv")):
        holdings.append({"account_id": r["account_id"], "security_id": r["security_id"],
                         "quantity": float(r["quantity"])})
    cons: Dict[str, List[dict]] = {}
    for r in _read(os.path.join(d, "fund_constituents.csv")):
        cons.setdefault(r["fund_id"], []).append(r)
    sw: Dict[str, List[dict]] = {}
    for r in _read(os.path.join(d, "fund_sector_weights.csv")):
        sw.setdefault(r["fund_id"], []).append(r)
    return Dataset(
        households={r["household_id"]: r for r in _read(os.path.join(d, "households.csv"))},
        accounts={r["account_id"]: r for r in _read(os.path.join(d, "accounts.csv"))},
        securities={r["security_id"]: r for r in _read(os.path.join(d, "securities.csv"))},
        prices=prices,
        holdings=holdings,
        fund_constituents=cons,
        fund_sector_weights=sw,
        client_cashflows={r["household_id"]: r for r in _read(os.path.join(d, "client_cashflows.csv"))},
        approved={r["security_id"]: r for r in _read(os.path.join(d, "approved_list.csv"))},
    )


# ---------------------------------------------------------------------------
# Portfolio and valuation
# ---------------------------------------------------------------------------
@dataclass
class Portfolio:
    """Quantities by (account_id, security_id).

    Bonds and bills: face value in dollars. CASH_USD: dollars. Everything else,
    including money market funds: shares. (A stable $1.00 NAV fund's shares equal dollars.)
    """
    quantities: Dict[Tuple[str, str], float] = field(default_factory=dict)

    def copy(self) -> "Portfolio":
        return Portfolio(dict(self.quantities))

    def quantity(self, account_id: str, security_id: str) -> float:
        return self.quantities.get((account_id, security_id), 0.0)

    def accounts(self) -> List[str]:
        return sorted({a for a, _ in self.quantities})

    def rows(self) -> List[dict]:
        """Rows in holdings.csv shape. Zero positions are dropped."""
        return [{"account_id": a, "security_id": s, "quantity": q}
                for (a, s), q in sorted(self.quantities.items()) if abs(q) > 1e-9]


@dataclass
class Price:
    close: float
    price_date: str
    stale: bool


def price(data: Dataset, security_id: str, as_of: str = VALUATION_DATE) -> Price:
    """Latest close on or before as_of. stale=True when that close is from an earlier date."""
    by_date = data.prices.get(security_id)
    if not by_date:
        raise KeyError(f"no prices for {security_id}")
    dates = sorted(dt for dt in by_date if dt <= as_of)
    if not dates:
        raise KeyError(f"no price for {security_id} on or before {as_of}")
    dt = dates[-1]
    return Price(by_date[dt], dt, dt != as_of)


def unit_value(data: Dataset, security_id: str, as_of: str = VALUATION_DATE) -> Tuple[float, Price]:
    """Dollar value of one unit of quantity. Applies pct_of_par for bonds and bills."""
    sec = data.securities[security_id]
    p = price(data, security_id, as_of)
    if sec["price_quote"] == "pct_of_par":
        return p.close / 100.0, p
    return p.close, p


@dataclass
class Position:
    account_id: str
    security_id: str
    quantity: float
    price: float
    price_date: str
    stale_price: bool
    market_value: float


def market_values(data: Dataset, portfolio: Portfolio, as_of: str = VALUATION_DATE) -> List[Position]:
    out = []
    for (acct, sid), q in sorted(portfolio.quantities.items()):
        if abs(q) < 1e-9:
            continue
        uv, p = unit_value(data, sid, as_of)
        out.append(Position(acct, sid, q, p.close, p.price_date, p.stale, q * uv))
    return out


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------
TRADE_COLUMNS = ["account_id", "security_id", "side", "quantity"]


@dataclass
class Trade:
    account_id: str
    security_id: str
    side: str
    quantity: float
    line: int = 0  # line number in the source file (the header is line 1); 0 if built in code


def read_trades(path: str) -> List[Trade]:
    """Read a trades.csv file.

    The header must be account_id,security_id,side,quantity (case-insensitive).
    A UTF-8 byte-order mark, spaces around values, blank lines and any casing of
    `side` are accepted. A file with only the header returns an empty list.
    Raises ValueError for an empty file, a wrong header, a row with the wrong
    number of fields, or a quantity that isn't a finite number.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            header = [c.strip().lower() for c in next(reader)]
        except StopIteration:
            raise ValueError(f"{path}: file is empty (it needs at least the header row)")
        if header != TRADE_COLUMNS:
            raise ValueError(f"{path}: header must be exactly {','.join(TRADE_COLUMNS)}")
        out = []
        for row in reader:
            line = reader.line_num
            if not any(c.strip() for c in row):
                continue
            if len(row) != len(TRADE_COLUMNS):
                raise ValueError(f"{path} line {line}: expected {len(TRADE_COLUMNS)} fields, got {len(row)}")
            acct, sid, side, qty = (c.strip() for c in row)
            try:
                q = float(qty)
            except ValueError:
                raise ValueError(f"{path} line {line}: quantity {qty!r} is not a number")
            if not math.isfinite(q):
                raise ValueError(f"{path} line {line}: quantity {qty!r} is not a finite number")
            out.append(Trade(acct, sid, side.lower(), q, line))
        return out


def write_trades(path: str, trades: List[Trade]) -> None:
    """Write trades in the required format. Creates the parent folder if needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(TRADE_COLUMNS)
        for t in trades:
            if not math.isfinite(t.quantity):
                raise ValueError(f"quantity {t.quantity!r} is not a finite number")
            q = int(t.quantity) if float(t.quantity).is_integer() else f"{t.quantity:.6f}".rstrip("0").rstrip(".")
            w.writerow([t.account_id, t.security_id, t.side.lower(), q])


@dataclass
class TradeResult:
    portfolio: Portfolio
    errors: List[str]
    warnings: List[str]
    applied: List[Trade]
    cash_flows: Dict[str, float]  # account_id -> net cash change (sells positive)

    @property
    def ok(self) -> bool:
        return not self.errors


class TradeError(Exception):
    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _num(q: float) -> str:
    return f"{q:,.0f}" if float(q).is_integer() else f"{q:,.6f}".rstrip("0").rstrip(".")


def _label(t: Trade) -> str:
    where = f"line {t.line}" if t.line else "trade"
    return f"{where} ({t.account_id} {t.side} {_num(t.quantity)} {t.security_id})"


def _lot_error(data: Dataset, t: Trade, held: float) -> Optional[str]:
    """Lot-size check. `held` is the account's quantity at the moment the trade settles."""
    sec = data.securities[t.security_id]
    q = t.quantity
    full_exit = t.side == "sell" and abs(q - held) < 1e-6
    if full_exit:
        return None
    if sec["price_quote"] == "pct_of_par":
        if abs(q / 1000.0 - round(q / 1000.0)) > 1e-9:
            return "bonds and bills trade in multiples of $1,000 face"
    elif not float(q).is_integer():
        return "stocks and funds trade in whole shares"
    return None


def apply_trades(data: Dataset, portfolio: Portfolio, trades: List[Trade], as_of: str = VALUATION_DATE,
                 strict: bool = False) -> TradeResult:
    """Apply trades at the as_of close and return the new portfolio.

    Rules:
      - Each account funds its own buys from its own CASH_USD balance. Cash never moves between accounts.
      - Within an account, all sells settle before any buy.
      - Sells: any security currently held, up to the quantity held. No short sales.
      - Buys: only securities on the approved list.
      - Accounts not managed by the firm cannot be traded, and every account must be in `portfolio`.
      - CASH_USD itself cannot be traded.
      - No account may end with negative cash.
      - Bonds and bills trade in $1,000 face. Stocks and funds trade in whole shares.
        Sells that together close a whole position are always allowed, in any row order.

    Invalid trades are skipped and listed in errors. If an account would end with
    negative cash, none of that account's buys are applied. With strict=True,
    raises TradeError if any trade is invalid.
    """
    new = portfolio.copy()
    errors: List[str] = []
    warnings: List[str] = []
    applied: List[Trade] = []
    flows: Dict[str, float] = {}

    valid: List[Trade] = []
    in_book = set(portfolio.accounts())
    for t in trades:
        t = Trade(t.account_id, t.security_id, t.side.strip().lower(), t.quantity, t.line)
        lab = _label(t)
        if t.account_id not in data.accounts:
            errors.append(f"{lab}: unknown account")
            continue
        if t.account_id not in in_book:
            errors.append(f"{lab}: account is not in this portfolio")
            continue
        if t.security_id not in data.securities:
            errors.append(f"{lab}: unknown security")
            continue
        if t.side not in ("buy", "sell"):
            errors.append(f"{lab}: side must be buy or sell")
            continue
        if data.accounts[t.account_id]["managed_by_firm"] != "true":
            errors.append(f"{lab}: account is not managed by the firm and cannot be traded")
            continue
        if t.security_id == CASH_ID:
            errors.append(f"{lab}: cash is not traded directly")
            continue
        if t.side == "buy" and t.security_id not in data.approved:
            errors.append(f"{lab}: not on the approved list")
            continue
        if not (t.quantity > 0) or not math.isfinite(t.quantity):
            errors.append(f"{lab}: quantity must be a positive number")
            continue
        if t.side == "buy":
            msg = _lot_error(data, t, 0.0)
            if msg:
                errors.append(f"{lab}: {msg}")
                continue
        try:
            unit_value(data, t.security_id, as_of)
        except KeyError as e:
            errors.append(f"{lab}: {e}")
            continue
        valid.append(t)

    by_account: Dict[str, List[Trade]] = {}
    for t in valid:
        by_account.setdefault(t.account_id, []).append(t)

    for acct in sorted(by_account):
        ts = by_account[acct]
        sells = [t for t in ts if t.side == "sell"]
        buys = [t for t in ts if t.side == "buy"]
        cash = new.quantity(acct, CASH_ID)
        start_cash = cash
        sold: Dict[str, float] = {}
        start_held = {t.security_id: new.quantity(acct, t.security_id) for t in sells}
        total_sell: Dict[str, float] = {}
        for t in sells:
            total_sell[t.security_id] = total_sell.get(t.security_id, 0.0) + t.quantity
        closing = {sid for sid, q in total_sell.items() if abs(q - start_held[sid]) < 1e-6}
        for t in sells:
            held = new.quantity(acct, t.security_id)
            if t.quantity > held + 1e-6:
                errors.append(f"{_label(t)}: sells {_num(t.quantity)} but the account holds {_num(held)} (no short sales)")
                continue
            msg = None if t.security_id in closing else _lot_error(data, t, held)
            if msg:
                errors.append(f"{_label(t)}: {msg}")
                continue
            uv, p = unit_value(data, t.security_id, as_of)
            if p.stale:
                warnings.append(f"{_label(t)}: no {as_of} close, priced at the {p.price_date} close of {p.close}")
            new.quantities[(acct, t.security_id)] = held - t.quantity
            cash += t.quantity * uv
            sold[t.security_id] = sold.get(t.security_id, 0.0) + t.quantity
            applied.append(t)
        cost = 0.0
        priced = []
        for t in buys:
            uv, p = unit_value(data, t.security_id, as_of)
            if p.stale:
                warnings.append(f"{_label(t)}: no {as_of} close, priced at the {p.price_date} close of {p.close}")
            cost += t.quantity * uv
            priced.append((t, uv))
        if cost > cash + CASH_TOLERANCE:
            errors.append(f"{acct}: buys cost ${cost:,.2f} but the account has ${cash:,.2f} after sells "
                          f"(negative cash; cash cannot come from another account). None of this account's buys were applied.")
        else:
            for t, uv in priced:
                key = (acct, t.security_id)
                new.quantities[key] = new.quantity(acct, t.security_id) + t.quantity
                cash -= t.quantity * uv
                applied.append(t)
        if abs(cash) < CASH_TOLERANCE:
            cash = 0.0
        new.quantities[(acct, CASH_ID)] = cash
        flows[acct] = cash - start_cash

    for key in [k for k, q in new.quantities.items() if abs(q) < 1e-9 and k[1] != CASH_ID]:
        del new.quantities[key]

    if strict and errors:
        raise TradeError(errors)
    return TradeResult(new, errors, warnings, applied, flows)
