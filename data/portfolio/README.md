# Portfolio data

**Every company, fund and bond in this data is fictional.** Any resemblance to a real name is a coincidence. Use only the data in these files. Do not look up real-world information, and don't rely on what a language model knows about real companies.

This is a snapshot of four client households at Cedar Ridge Wealth Partners (the Firm), also fictional. The valuation date is **2026-09-25**. All amounts are in USD.

## Files at a glance

| File | Rows | What it holds |
|---|---|---|
| households.csv | 4 | Households and the path to each IPS |
| accounts.csv | 12 | Accounts, and whether the Firm manages them |
| holdings.csv | 76 | Positions by account |
| prices.csv | 443 | Closing prices on 2026-09-24 and 2026-09-25 |
| securities.csv | 222 | Security master for everything held, buyable, or held inside a fund |
| approved_list.csv | 200 | Securities the Firm allows new purchases of |
| fund_constituents.csv | 379 | What each fund holds |
| fund_sector_weights.csv | 253 | Sector breakdown of equity and balanced funds |
| client_cashflows.csv | 4 | Planning inputs: expenses, income, grants |

## Conventions

- **Market value.** Market value = quantity x unit value. Unit value depends on `price_quote` in securities.csv:
  - `per_share`: the close is the price of one share.
  - `per_unit`: the close is the value of one unit. Only `CASH_USD` uses this, and its close is always 1.00.
  - `pct_of_par`: the close is a percent of face value. Market value = face value x close / 100. All bonds and Treasury bills use this. Closes are clean prices: ignore accrued interest.
- **Which price.** Use the latest close on or before the valuation date. One security has no 2026-09-25 close. Its latest close is from 2026-09-24.
- **Stable NAV.** `CASH_USD`, KGMXX and KTRXX close at 1.00 on both dates. KPRXX has a floating NAV close to 1.00.
- **International shares** are priced in USD. Securities whose name ends in "ADR" are American depositary receipts. The business description says how many ordinary shares one ADR represents. An ADR and its ordinary shares share an `issuer_id`.
- **Fund data** (constituents and sector weights) is as of 2026-08-31, about a month before the valuation date.
- **Blank and zero.** Blank means unknown or not applicable. Zero means none.

## Files

### households.csv
One row per household.

| Column | Meaning |
|---|---|
| household_id | Primary key |
| household_name | Display name |
| household_type | `joint`, `trust` or `foundation` |
| primary_contact | Client contact |
| advisor | Advisor responsible for the household |
| ips_file | Path to the household's Investment Policy Statement, relative to `data/` |
| ips_effective_date | Date the current IPS took effect |

### accounts.csv
One row per account.

| Column | Meaning |
|---|---|
| account_id | Primary key |
| household_id | Owning household |
| account_name, registration | Descriptive |
| account_type | `taxable`, `trust`, `ira`, `roth_ira`, `401k`, `stock_plan` or `foundation` |
| custodian | Where the assets are held |
| managed_by_firm | `true` if the Firm manages the account. `false` if it is held and managed elsewhere and reported to us for planning. Accounts with `false` can't be traded |

### holdings.csv
One row per account and security.

| Column | Meaning |
|---|---|
| account_id | Account |
| security_id | Security |
| quantity | For bonds and bills, face value in USD. For `CASH_USD`, dollars. For everything else, including money market funds, shares. One share of KGMXX or KTRXX (stable $1.00 NAV) is one dollar |

Each account's uninvested cash is its `CASH_USD` row. An account with no `CASH_USD` row has no cash.

### prices.csv
Closing prices. One row per security and date.

| Column | Meaning |
|---|---|
| security_id | Security |
| price_date | Trade date |
| close | Closing price. See `price_quote` in securities.csv for units |

### securities.csv
Security master. One row per security. Includes securities that are only held inside funds and are neither held directly nor buyable.

| Column | Meaning |
|---|---|
| security_id | Primary key |
| ticker | Exchange ticker. Blank for bonds and cash |
| name | Security name |
| security_type | `equity`, `etf`, `mutual_fund`, `money_market`, `exchange_traded_product`, `corporate_bond`, `treasury`, `treasury_bill`, `cash` |
| asset_class | `equity`, `fixed_income`, `multi_asset`, `cash_equivalent`, `cash`, `alternative` |
| sub_asset_class | Finer classification, for example `us_equity`, `intl_equity`, `us_total_market`, `intl_developed`, `us_corporate`, `us_treasury`, `government_mmf` |
| issuer_id, issuer_name | Legal issuer. Different securities from the same company (share classes, ADRs, bonds) share an issuer_id. Blank for funds and cash |
| sector, industry | GICS-style classification for stocks. Corporate bonds carry the issuer's sector, with industry blank. Treasuries use sector `Government`. Blank for funds (see fund_sector_weights.csv) |
| country | Country of domicile |
| rating_sp, rating_moodys | Current credit ratings for bonds. Long-term scale for notes and corporate bonds. Treasury bills carry short-term ratings (A-1+ / P-1). Blank for funds; a fund's credit quality is described in business_description |
| coupon_pct, maturity_date | Bonds and bills only |
| price_quote | `per_share`, `per_unit` or `pct_of_par`. See Conventions |
| expense_ratio_pct | Funds only. Annual fund expenses as a percent of assets |
| business_description | Short description from the data vendor |
| fossil_revenue_pct | Share of revenue from fossil fuel extraction, transport, refining or power generation, from a third-party ESG vendor. The vendor covers operating companies only, so it is always blank for bonds, funds and cash. For a company, blank means the vendor has no coverage |

### approved_list.csv
The Firm's list of securities approved for new purchases. Anything currently held can be sold, whether or not it is on this list. Buys must come from this list.

| Column | Meaning |
|---|---|
| security_id | Security (a security_id in securities.csv) |
| name | Security name |
| approved_date | When the Firm's investment committee approved it |
| notes | The Firm's internal category for the security. Informational only |

### fund_constituents.csv
Holdings disclosed by each fund, as of the date shown. Funds disclose their largest positions individually and report the rest as residual rows grouped by asset class. Bond funds may name individual bonds.

| Column | Meaning |
|---|---|
| fund_id | Fund (a security_id in securities.csv) |
| as_of_date | Disclosure date |
| constituent_security_id | Underlying security. Blank on residual rows |
| residual_asset_class | Asset class of the residual bucket. Blank on named rows |
| residual_description | What the residual bucket contains |
| weight_pct | Weight in the fund, in percent. Weights for a fund sum to 100 |

### fund_sector_weights.csv
Sector breakdown from fund fact sheets, for equity and balanced funds. Not every fund publishes one.

| Column | Meaning |
|---|---|
| fund_id | Fund |
| as_of_date | Fact sheet date |
| sector | GICS-style sector. Balanced funds also have a row `Not applicable (fixed income and cash)` for their non-equity share |
| weight_pct | Percent of the whole fund. Weights for a fund sum to 100, including any `Not applicable` row |

### client_cashflows.csv
Client financial-planning inputs. One row per household.

| Column | Meaning |
|---|---|
| household_id | Household |
| as_of_date | When the planning data was last updated |
| monthly_essential_expenses | Housing, food, insurance, healthcare, utilities |
| monthly_discretionary_expenses | Travel, dining, gifts, hobbies |
| monthly_pension_income | Defined-benefit pension income currently being received |
| monthly_social_security_income | Social Security currently being received |
| planned_annual_withdrawals | Planned withdrawals from the portfolio, if known |
| planned_annual_grants | Foundations only: approved grant budget |
| notes | Advisor notes |
