# Remediation Plan — HH-BRIGHT
**Snapshot date:** 2026-09-25
**Portfolio MV:** $8,439,750
**Prepared by:** Policy Remediation Copilot v1.0

---

## 1. Findings

### BREACH: R-S036 (clause S036)

> 8.1 Consistent with its mission, the Foundation will not invest in fossil fuel companies.

| Field | Value |
|-------|-------|
| Measured | 5.69% |
| Limit (max) | 0.00% |
| Margin (distance to limit) | -5.69% |
| Scope accounts | BWF-MAIN, BWF-RES |
| Denominator MV | $8,439,750 |

**Top contributors:**

| Account | Security | MV | Share |
|---------|----------|----|-------|
| BWF-MAIN | PTRX | $120,003 | 25.0% |
| BWF-MAIN | SVRF | $99,994 | 20.8% |
| BWF-MAIN | B-PETREX-2030 | $90,068 | 18.8% |
| BWF-MAIN | SLTP | $90,005 | 18.7% |
| BWF-MAIN | CNDR | $79,999 | 16.7% |




**Needs-review findings (not breaches — pending PM input):**

None.

**Cannot evaluate (data unavailable):**

None.

---

## 2. Trades

| Account | Security | Side | Quantity | Rationale |
|---------|----------|------|----------|-----------|
| BWF-MAIN | PTRX | sell | 1,105 | Addresses confirmed breach |
| BWF-MAIN | SVRF | sell | 5,063 | Addresses confirmed breach |
| BWF-MAIN | B-PETREX-2030 | sell | 89,000 | Addresses confirmed breach |
| BWF-MAIN | SLTP | sell | 1,265 | Addresses confirmed breach |
| BWF-MAIN | CNDR | sell | 2,925 | Addresses confirmed breach |
| BWF-MAIN | CRAG | buy | 4,908 | Addresses confirmed breach |

**Trade rationale:** All sells remove confirmed breaches. Buys redeploy proceeds to maintain other allocation constraints. The simulator verified that after these trades, no confirmed breach remains and no new breach is created. See the before/after table below.


---

## 3. Left Alone

Nothing was deliberately left untouched beyond what was already passing.

---

## 4. Open Questions

**1. S029:** What measurable concentration limits should we enforce for “excessive concentration” (e.g., max sector %, max industry %, max theme %, or use the existing single-security cap only)? Example answers: (a) sector <= 0.15, industry <= 0.08; (b) only enforce the 4% single-security rule.
*(Assumption if unanswered: Assume no additional diversification limits beyond the explicit single-security cap.)*

**2. S031:** What minimum fraction of fixed income holdings must be investment grade? Example answers: (a) at least 0.80 IG; (b) at least 0.50 IG; (c) no less than 0.75 IG by market value.
*(Assumption if unanswered: Assume at least 0.80 of fixed income market value is investment grade.)*

**3. S033:** How should we compute “next twelve months of approved grants” for the reserve requirement—use the most recently approved grant budget, and is it measured in dollars or months? Example answers: (a) use latest approved annual grant budget prorated to 12 months; (b) use trailing 12 months of grants paid.
*(Assumption if unanswered: Assume reserve is computed from the latest approved grant budget prorated to the next 12 months.)*

**4. S037:** Is there any measurable constraint tied to “strong water stewardship practices” or “water-intensive industries” (e.g., max water-intensive exposure %)? If yes, provide the threshold and the data source/definition. Example answers: (a) no constraint, only reporting; (b) water-intensive industries exposure <= 0.10.
*(Assumption if unanswered: Assume this is advisory/monitoring only and not enforced as a portfolio constraint.)*

**5. S041:** For “underperforms its benchmark by more than 2% a year over a rolling three-year period,” what exact performance metric should we use (annualized total return? calendar-year? net of fees?), and how do we map each fund to its benchmark in the data? Example answers: (a) annualized total return vs stated benchmark; (b) trailing 36-month CAGR vs benchmark CAGR.
*(Assumption if unanswered: Assume annualized total return (net) vs the fund’s stated benchmark over the last 36 months.)*


---

## Appendix: Before / After Checker Results

| Rule | Status Before | Measured Before | Status After | Notes |
|------|---------------|-----------------|--------------|-------|
| R-S024 | PASS | 60.90% | PASS | — |
| R-S025 | PASS | 20.85% | PASS | — |
| R-S026 | PASS | 7.58% | PASS | — |
| R-S030 | PASS | (group) | PASS | — |
| R-S036 | **BREACH** | 5.69% | *(see trades)* | 8.1 Consistent with its mission, the Foundation will not inv… |

---

## Data Freshness

| Input | Date | Note |
|-------|------|------|
| Prices | 2026-09-25 | Valuation date close |
| Fund constituents | 2026-08-31 | ~25 days before valuation (within 45-day threshold) |
| Client cashflows | 2026-06-30 | Stale if > 180 days |
| Approved list | 2026-09-25 | Static at report date |

*Plan is clean: after applying the trades above, the checker confirms 0 confirmed breaches remain and 0 new breaches were created.*
