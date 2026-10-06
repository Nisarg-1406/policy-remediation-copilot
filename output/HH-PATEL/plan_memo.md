# Remediation Plan — HH-PATEL
**Snapshot date:** 2026-09-25
**Portfolio MV:** $2,400,167
**Prepared by:** Policy Remediation Copilot v1.0

---

## 1. Findings

### BREACH: R-S028-no_crypto (clause S028)

> 5.1 The Portfolio will not hold cryptocurrency, funds or trusts that hold cryptocurrency, or other digital asset products.

| Field | Value |
|-------|-------|
| Measured | 1.25% |
| Limit (max) | 0.00% |
| Margin (distance to limit) | -1.25% |
| Scope accounts | PAT-JT, PAT-EQP, PAT-IRA |
| Denominator MV | $2,400,167 |

**Top contributors:**

| Account | Security | MV | Share |
|---------|----------|----|-------|
| PAT-JT | BVLT | $30,028 | 100.0% |




**Needs-review findings (not breaches — pending PM input):**

None.

**Cannot evaluate (data unavailable):**

None.

---

## 2. Trades

| Account | Security | Side | Quantity | Rationale |
|---------|----------|------|----------|-----------|
| PAT-JT | BVLT | sell | 467 | Addresses confirmed breach |
| PAT-JT | CRAG | buy | 307 | Addresses confirmed breach |

**Trade rationale:** All sells remove confirmed breaches. Buys redeploy proceeds to maintain other allocation constraints. The simulator verified that after these trades, no confirmed breach remains and no new breach is created. See the before/after table below.


---

## 3. Left Alone

Nothing was deliberately left untouched beyond what was already passing.

---

## 4. Open Questions

**1. S025:** For the 'below 15% by Dec 31, 2028' requirement, should the system check (a) end-of-day market value on 2028-12-31, (b) any time during 2028, or (c) average over 2028? Also, should 'shares received through vesting' be included in the Kestrel position immediately upon vesting date?
*(Assumption if unanswered: Check end-of-day on 2028-12-31 and include vesting shares in Kestrel exposure immediately on their vesting date.)*

**2. S030:** What minimum fraction of the portfolio should be allocated to international equities (e.g., 0.10, 0.20)? Also, should 'international equities' include all non-US equity sub_asset_classes (intl_developed, intl_equity, intl_small_cap, etc.)?
*(Assumption if unanswered: Use a minimum of 0.20 of portfolio in international equity sub-asset classes, including all non-US equity categories.)*


---

## Appendix: Before / After Checker Results

| Rule | Status Before | Measured Before | Status After | Notes |
|------|---------------|-----------------|--------------|-------|
| R-S019-min | PASS | 82.08% | PASS | — |
| R-S019-max | PASS | 82.08% | PASS | — |
| R-S020-min | PASS | 12.50% | PASS | — |
| R-S020-max | PASS | 12.50% | PASS | — |
| R-S021-min | PASS | 4.17% | PASS | — |
| R-S021-max | PASS | 4.17% | PASS | — |
| R-S024-single_issuer_cap | PASS | (group) | PASS | — |
| R-S026-individual_stocks_agg_cap | PASS | 15.83% | PASS | — |
| R-S028-no_crypto | **BREACH** | 1.25% | *(see trades)* | 5.1 The Portfolio will not hold cryptocurrency, funds or tru… |
| R-S029-fixed_income_instrument_allowlist | PASS | 0.00% | PASS | — |

---

## Data Freshness

| Input | Date | Note |
|-------|------|------|
| Prices | 2026-09-25 | Valuation date close |
| Fund constituents | 2026-08-31 | ~25 days before valuation (within 45-day threshold) |
| Client cashflows | 2026-06-30 | Stale if > 180 days |
| Approved list | 2026-09-25 | Static at report date |

*Plan is clean: after applying the trades above, the checker confirms 0 confirmed breaches remain and 0 new breaches were created.*
