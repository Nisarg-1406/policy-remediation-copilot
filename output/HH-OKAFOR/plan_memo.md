# Remediation Plan — HH-OKAFOR
**Snapshot date:** 2026-09-25
**Portfolio MV:** $1,499,005
**Prepared by:** Policy Remediation Copilot v1.0

---

## 1. Findings

No confirmed breaches.

**Needs-review findings (not breaches — pending PM input):**

None.

**Cannot evaluate (data unavailable):**

None.

---

## 2. Trades

No trades proposed. The portfolio is currently within policy constraints.

---

## 3. Left Alone

| Item | Reason |
|------|--------|
| R-S035-stock_allocation_band (S035) | Passed — verified within policy limits (measured: 40.69%) |
| R-S037-stock_allocation_band (S037) | Passed — verified within policy limits (measured: 40.69%) |
| R-S040-bonds_min_allocation (S040) | Passed — verified within policy limits (measured: 55.31%) |
| R-S040-bonds_investment_grade_floor (S040) | Passed — verified within policy limits (measured: 0.00%) |
| R-S040-bbb_cap_within_bond_portfolio (S040) | Passed — verified within policy limits (measured: 0.00%) |
| R-S041-bonds_min_allocation (S041) | Passed — verified within policy limits (measured: 55.31%) |
| R-S042-bonds_investment_grade_floor (S042) | Passed — verified within policy limits (measured: 0.00%) |
| R-S043-bbb_cap_within_bond_portfolio (S043) | Passed — verified within policy limits (measured: 0.00%) |
| R-S045-single_stock_cap (S045) | Passed — verified within policy limits |
| R-S051-cash_equivalents_definition (S051) | Passed — verified within policy limits (measured: 0.00%) |

---

## 4. Open Questions

**1. S044:** How should “keep maturities mostly within ten years” be quantified? Example answers: (a) at least 0.80 of bond MV has maturity <= 10 years; (b) no more than 0.20 has maturity > 10 years; (c) use a weighted-average maturity cap.
*(Assumption if unanswered: Assume a weighted-average maturity <= 10 years if no answer is provided.)*

**2. S048:** What numeric “twelve months of living expenses, net of Robert’s pension” should the system use? Example answers: (a) use last refreshed annual expense amount minus expected annual pension; (b) use a fixed dollar amount provided by the PM; (c) use a percentage of portfolio value.
*(Assumption if unanswered: Assume the PM will provide an annual net-expense dollar figure used to compute the required cash-equivalent minimum.)*

**3. S050:** For the reserve test, what is the current “twelve months of living expenses, net of Robert’s pension” amount (in dollars) to compare against cash + cash equivalents? Example answers: $X provided by PM; or compute from an annual expense estimate minus annual pension.
*(Assumption if unanswered: Assume the system uses the latest PM-provided annual net expense figure.)*

**4. S065:** What qualifies as “meaningful” change in health, living situation, or spending? Example answers: (a) spending changes by >= 0.10 (10%); (b) any change that alters annual net expenses by >= $X; (c) PM discretion with documented rationale.
*(Assumption if unanswered: Assume spending changes by at least 0.10 (10%) are “meaningful” if no answer is provided.)*


---

## Appendix: Before / After Checker Results

| Rule | Status Before | Measured Before | Status After | Notes |
|------|---------------|-----------------|--------------|-------|
| R-S035-stock_allocation_band | PASS | 40.69% | PASS | — |
| R-S037-stock_allocation_band | PASS | 40.69% | PASS | — |
| R-S040-bonds_min_allocation | PASS | 55.31% | PASS | — |
| R-S040-bonds_investment_grade_floor | PASS | 0.00% | PASS | — |
| R-S040-bbb_cap_within_bond_portfolio | PASS | 0.00% | PASS | — |
| R-S041-bonds_min_allocation | PASS | 55.31% | PASS | — |
| R-S042-bonds_investment_grade_floor | PASS | 0.00% | PASS | — |
| R-S043-bbb_cap_within_bond_portfolio | PASS | 0.00% | PASS | — |
| R-S045-single_stock_cap | PASS | (group) | PASS | — |
| R-S051-cash_equivalents_definition | PASS | 0.00% | PASS | — |

---

## Data Freshness

| Input | Date | Note |
|-------|------|------|
| Prices | 2026-09-25 | Valuation date close |
| Fund constituents | 2026-08-31 | ~25 days before valuation (within 45-day threshold) |
| Client cashflows | 2026-06-30 | Stale if > 180 days |
| Approved list | 2026-09-25 | Static at report date |

*Plan is clean: after applying the trades above, the checker confirms 0 confirmed breaches remain and 0 new breaches were created.*
