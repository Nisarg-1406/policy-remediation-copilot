# Remediation Plan — HH-CHEN
**Snapshot date:** 2026-09-25
**Portfolio MV:** $5,103,329
**Prepared by:** Policy Remediation Copilot v1.0

---

## 1. Findings

### BREACH: R-S041 (clause S041)

> 5.1 **Single issuer.** Exposure to any single issuer shall not exceed 5% of the market value of the Portfolio. Exposure to an issuer includes all of its securities held in the Portfolio, including every class of common or preferred stock and any bonds, as well as indirect exposure through mutual funds and exchange-traded funds held in the Portfolio. Obligations of the United States Treasury and US government agencies are exempt from this limit.

| Field | Value |
|-------|-------|
| Measured (worst group) | 5.31% |
| Limit (max) | 5.00% |
| Margin (distance to limit) | -0.31% |
| Scope accounts | CHEN-TR, CHEN-DC, CHEN-MC |
| Denominator MV | $5,103,329 |

**Top contributors:**

| Account | Security | MV | Share |
|---------|----------|----|-------|
| CHEN-DC | HLDA | $79,956 | 29.5% |
| CHEN-DC | B-HALDEN-2031 | $60,506 | 22.3% |
| CHEN-DC | HLDC | $59,504 | 22.0% |
| CHEN-MC | HLDA | $22,498 | 8.3% |
| CHEN-MC | HLDC | $20,248 | 7.5% |

**Notes:** Worst group: ISS-HALDEN = 0.0531; Breaching groups: ISS-HALDEN (0.0531), ISS-NORTHVALE (0.0529)


### BREACH: R-S042-1 (clause S042)

> 5.2 **Pooled vehicles.** No single mutual fund or exchange-traded fund shall exceed 15% of the market value of the Portfolio, except for the Portfolio's core US broad-market index fund, which may represent up to 30%.

| Field | Value |
|-------|-------|
| Measured (worst group) | 22.53% |
| Limit (max) | 15.00% |
| Margin (distance to limit) | -7.53% |
| Scope accounts | CHEN-TR, CHEN-DC, CHEN-MC |
| Denominator MV | $5,103,329 |

**Top contributors:**

| Account | Security | MV | Share |
|---------|----------|----|-------|
| CHEN-TR | KUSX | $1,150,034 | 100.0% |

**Notes:** Worst group: KUSX = 0.2253; Breaching groups: KUSX (0.2253)



**Needs-review findings (not breaches — pending PM input):**

None.

**Cannot evaluate (data unavailable):**

None.

---

## 2. Trades

| Account | Security | Side | Quantity | Rationale |
|---------|----------|------|----------|-----------|
| CHEN-TR | KUSX | sell | 1,306 | Addresses confirmed breach |
| CHEN-TR | NRVL | sell | 87 | Addresses confirmed breach |
| CHEN-DC | B-HALDEN-2031 | sell | 21,000 | Addresses confirmed breach |

**Trade rationale:** All sells remove confirmed breaches. Buys redeploy proceeds to maintain other allocation constraints. The simulator verified that after these trades, no confirmed breach remains and no new breach is created. See the before/after table below.


---

## 3. Left Alone

| Item | Reason |
|------|--------|
| R-S033 (S033) | Passed — verified within policy limits (measured: 58.75%) |
| R-S034 (S034) | Passed — verified within policy limits (measured: 31.36%) |
| R-S035 (S035) | Passed — verified within policy limits (measured: 2.06%) |
| R-S036 (S036) | Passed — verified within policy limits (measured: 0.00%) |
| R-S037 (S037) | Passed — verified within policy limits (measured: 11.76%) |
| R-S042-2 (S042) | Passed — verified within policy limits |
| R-S045 (S045) | Passed — verified within policy limits (measured: 0.00%) |
| R-S050 (S050) | Passed — verified within policy limits |

---

## 4. Open Questions

**1. S046:** For bond funds, what does “primarily” mean? Examples: (a) at least 0.50 of holdings by market value in investment-grade, (b) at least 0.60, or (c) based on fund’s stated mandate/portfolio composition.
*(Assumption if unanswered: Assume 'primarily' = at least 0.50 investment-grade by market value.)*

**2. S047:** What tolerance should we enforce for “within two years of” the Bloomberg US Aggregate Bond Index duration? Examples: enforce absolute difference <= 2.0 years, or <= 2.0 years for the fixed-income sleeve only, or use a percentage band.
*(Assumption if unanswered: Assume absolute difference <= 2.0 years for the fixed-income allocation.)*

**3. S051:** What data fields are available to detect leverage, margin, short sales, and derivatives usage (e.g., position types, notional, margin balances, fund derivative holdings)? Also, define “diversified mutual funds or ETFs” (any numeric diversification rule or rely on fund classification).
*(Assumption if unanswered: If derivative/leverage/margin/short indicators are unavailable, flag this clause as uncheckable and require PM confirmation.)*

**4. S052:** How should we determine “cannot ordinarily be sold within five business days”? Examples: use a liquidity attribute, security type (private placement vs public), or a vendor liquidity score. Which data source should the checker use?
*(Assumption if unanswered: Use security_type/market listing to approximate liquidity; treat private placements/private funds/LP interests as non-compliant.)*


---

## Appendix: Before / After Checker Results

| Rule | Status Before | Measured Before | Status After | Notes |
|------|---------------|-----------------|--------------|-------|
| R-S033 | PASS | 58.75% | PASS | — |
| R-S034 | PASS | 31.36% | PASS | — |
| R-S035 | PASS | 2.06% | PASS | — |
| R-S036 | PASS | 0.00% | PASS | — |
| R-S037 | PASS | 11.76% | PASS | — |
| R-S041 | **BREACH** | (group) | *(see trades)* | 5.1 **Single issuer.** Exposure to any single issuer shall n… |
| R-S042-1 | **BREACH** | (group) | *(see trades)* | 5.2 **Pooled vehicles.** No single mutual fund or exchange-t… |
| R-S042-2 | PASS | (group) | PASS | — |
| R-S045 | PASS | 0.00% | PASS | — |
| R-S050 | PASS | (group) | PASS | — |

---

## Data Freshness

| Input | Date | Note |
|-------|------|------|
| Prices | 2026-09-25 | Valuation date close |
| Fund constituents | 2026-08-31 | ~25 days before valuation (within 45-day threshold) |
| Client cashflows | 2026-06-30 | Stale if > 180 days |
| Approved list | 2026-09-25 | Static at report date |

*Plan status: UNRESOLVED. Confirmed breaches remain pending PM guidance.*
