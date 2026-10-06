# Labeled extraction: Patel household IPS

Source document: `data/ips/patel_household.md`. All companies, funds and bonds are fictional.

This file lists every clause in the Patel IPS and what a correct extraction has to capture. It is written in plain language on purpose, so it implies no schema. Part of your job is to turn it into gold data in your own format and score your pipeline against it.

Every entry has these fields:

- **Source:** the quoted text and its section. Quotes leave out the bold clause headings (such as "Single issuer.") and join list items with semicolons. Table rows are given as their values.
- **Portfolio constraint:**
  - `Yes`: a firm limit on holdings that a snapshot of the data can check.
  - `Partly`: it constrains the portfolio, but it is soft or vague, or it needs data beyond the snapshot (transactions, a threshold from the PM).
  - `No`: scope, background, objectives or procedures.
- **Must capture:** facts any correct extraction has to include. If the extraction misses one of these or gets it wrong, the extraction is wrong.

Entries add these fields where they apply:

- **Reading:** what the clause means.
- **Acceptable handling:** for clauses that are ambiguous or can't be fully checked. Any of the listed approaches is correct.
- **Not acceptable:** handling that makes the extraction wrong.
- **Outcome on the provided data:** the result a correct checker gives for the 2026-09-25 snapshot, for clauses a checker evaluates or tracks. This lets you test end to end.

Percentages are of the Portfolio's market value unless stated otherwise.

---

## P-01

**Source:** §1.1 "This Investment Policy Statement ("IPS") describes how the Firm will manage the investment assets of Priya and Arjun Patel." and §1.2 "This IPS applies to the following accounts (together, the "Portfolio"): the Patel joint brokerage account at Pinnacle Custody; Priya's Kestrel Semiconductor equity plan account at Northstar Plan Services, which the Firm manages under a limited trading authorization; Arjun's rollover IRA at Pinnacle Custody."

**Portfolio constraint:** No. It defines scope, and every other rule depends on it.

**Reading:** The Portfolio is these three accounts. All limits are measured against their combined market value.

**Must capture:**
- The three accounts in scope (PAT-JT, PAT-EQP, PAT-IRA).
- That this defines the denominator for every percentage limit in the document.

**Outcome on the provided data:** Portfolio market value is $2,400,167.

---

## P-02

**Source:** §1.3 "The Patels keep an emergency reserve equal to about six months of household expenses in a savings account at their bank. That reserve is outside the scope of this IPS and the Firm does not manage it."

**Portfolio constraint:** No.

**Reading:** Background. It explains why the IPS has no cash-reserve rule.

**Must capture:**
- Classified as background.
- No rule generated. Generating a "hold six months of expenses in cash" rule is an error.

---

## P-03

**Source:** §2.1 "Priya (41) is a Vice President of Engineering at Kestrel Semiconductor. Arjun (43) is a product designer and works as an independent consultant. They have two children, ages 9 and 6." and §2.2 "The Patels' primary objective is long-term growth of capital. They hope to have the flexibility to stop working full-time by about age 55. They also plan to pay for most of their children's college costs and fund 529 plans separately."

**Portfolio constraint:** No.

**Reading:** Goals and background.

**Must capture:**
- Classified as background and an objective.

---

## P-04

**Source:** §2.3 "The Patels have a high tolerance for risk and a time horizon of more than fifteen years. They do not expect to withdraw from the Portfolio in the next ten years, other than to pay taxes on vesting equity awards."

**Portfolio constraint:** No.

**Reading:** Risk profile and liquidity needs.

**Must capture:**
- Classified as an objective or profile statement.

---

## P-05

**Source:** §3.1 allocation table, Equity row: minimum 75%, target 85%, maximum 95%.

**Portfolio constraint:** Yes.

**Reading:** Equity must be between 75% and 95% of the Portfolio.

**Must capture:**
- Measure: equity market value as a share of the Portfolio.
- Lower bound 75%, upper bound 95%, both inclusive.
- Target 85% is informational only.

**Outcome on the provided data:** 82.08%. Pass.

---

## P-06

**Source:** §3.1 allocation table, Fixed income row: minimum 5%, target 10%, maximum 20%.

**Portfolio constraint:** Yes.

**Must capture:**
- Measure: fixed income as a share of the Portfolio.
- Lower bound 5%, upper bound 20%, both inclusive. Target 10% is informational only.

**Outcome on the provided data:** 12.50%. Pass.

---

## P-07

**Source:** §3.1 allocation table, Cash row: minimum 0%, target 3%, maximum 5%.

**Portfolio constraint:** Yes.

**Must capture:**
- Measure: cash as a share of the Portfolio.
- Lower bound 0%, upper bound 5%, both inclusive. Target 3% is informational only.
- BVLT (asset class `alternative`, 1.25%) falls in no row of the table, so the three rows sum to 98.75%. It is covered by P-14.

**Outcome on the provided data:** 4.17%. Pass.

---

## P-08

**Source:** §3.2 "The Firm will rebalance when an asset class moves outside its range, and will generally use new contributions and proceeds from Kestrel share sales to do so."

**Portfolio constraint:** No.

**Reading:** A procedure the Firm follows. There is nothing to check against holdings.

**Must capture:**
- Classified as a procedure.

---

## P-09

**Source:** §4.1 "No single issuer may exceed 10% of the market value of the Portfolio, except for Kestrel Semiconductor, which is governed by Section 4.2. For this limit, exposure is measured on direct holdings only. Diversified mutual funds and ETFs are not looked through."

**Portfolio constraint:** Yes.

**Reading:** For every issuer except Kestrel, the combined market value of securities it issued and the Portfolio holds directly must not exceed 10%.

**Must capture:**
- Measure: exposure per issuer. All securities of one issuer are grouped together.
- Upper bound 10%.
- Kestrel Semiconductor is excluded and handled by P-10.
- Direct holdings only. No look-through into funds.
- Fund positions are not issuers for this limit. KUSX is 25.00% of the Portfolio and is not a breach.
- Cross-reference to §4.2.

**Acceptable handling:** If you look through any fund under this limit, state which and why. Either reading passes if stated. Looking through AXTL gives Northvale 6.85%.

**Outcome on the provided data:** Largest is Northvale Systems at 6.25%. Pass.

---

## P-10

**Source:** §4.2 "Because a large share of the Patels' net worth and future income depends on Kestrel, the Firm will reduce the Kestrel position to below 15% of the market value of the Portfolio by December 31, 2028."

**Portfolio constraint:** Yes, with a deadline.

**Reading:** A target the Portfolio has to reach by 2028-12-31. Until then, the current weight shows progress.

**Must capture:**
- Measure: Kestrel Semiconductor exposure as a share of the Portfolio.
- Upper bound: below 15% (strict).
- Applies from 2028-12-31. Before then, the current weight is reported as progress toward the target.

**Acceptable handling:** §4.2 doesn't say whether Kestrel held inside funds counts toward the 15%. §4.1 measures direct holdings only, so measuring direct is a reasonable assumption if stated. Measuring with look-through is also acceptable if stated. Flagging the question for the PM is a plus.

**Outcome on the provided data:** 30.00% direct, 31.05% with look-through (KUSX adds $14,402 and AXTL $10,801). Not yet due. Show it as a tracked item with the 2028-12-31 deadline.

---

## P-11

**Source:** §4.2 "The Firm will sell at least one quarter of the Kestrel shares held at the start of each calendar year, subject to Kestrel's trading windows and any blackout periods that apply to Priya."

**Portfolio constraint:** Partly.

**Reading:** An annual minimum sale of Kestrel shares. Checking it needs the share count on January 1 and the year's trades. Neither is in the snapshot.

**Must capture:**
- Minimum 25% of the Kestrel shares held at the start of each calendar year, sold within that year.
- Subject to trading windows and blackout periods.
- Needs transaction history. It can't be evaluated from a single snapshot.

**Acceptable handling:**
- Extract it as a rule and mark it "cannot evaluate: needs start-of-year position and transactions".
- Keep it as a tracked obligation with no automated check.

**Not acceptable:** Reporting it as pass or fail from the snapshot. Dropping it.

**Outcome on the provided data:** Cannot evaluate.

---

## P-12

**Source:** §4.2 "No additional Kestrel shares will be purchased in the Portfolio. Shares received through the vesting of Priya's equity awards are permitted."

**Portfolio constraint:** Partly.

**Reading:** A ban on buying Kestrel, with vesting as an exception. It's about trades, so a holdings snapshot can't show whether it was broken.

**Must capture:**
- Prohibits purchases of Kestrel Semiconductor in the Portfolio.
- Exception: shares received through vesting.
- Needs transaction data to evaluate.

**Acceptable handling:** Same as P-11, and the same "Not acceptable" applies. It could also be modeled as a pre-trade rule if the design supports that. The clause doesn't say whether buying a fund that holds Kestrel counts. Noting that as an open question is a plus.

**Outcome on the provided data:** Cannot evaluate.

---

## P-13

**Source:** §4.3 "Individual stocks other than Kestrel may not exceed 20% of the market value of the Portfolio in aggregate."

**Portfolio constraint:** Yes.

**Must capture:**
- Measure: combined market value of all directly held individual stocks, excluding Kestrel Semiconductor.
- Upper bound 20%.
- Aggregate limit across all such stocks combined.
- Funds are not individual stocks.

**Outcome on the provided data:** 15.83% (NRVL, HLDA, ORBP and AUTM). Pass.

---

## P-14

**Source:** §5.1 "The Portfolio will not hold cryptocurrency, funds or trusts that hold cryptocurrency, or other digital asset products."

**Portfolio constraint:** Yes.

**Reading:** An exclusion. Any holding in these categories is a breach, whatever its size.

**Must capture:**
- Excludes direct cryptocurrency, funds or trusts holding cryptocurrency, and any other digital asset product.
- Limit is zero. Any position is a breach.
- How the rule identifies these holdings from security data, for example the digital-asset classification.

**Outcome on the provided data:** Breach. Bitvault Bitcoin Trust (BVLT), $30,028 (1.25%), in the joint account.

---

## P-15

**Source:** §5.2 "Fixed income will be held only through diversified bond funds or US Treasury securities."

**Portfolio constraint:** Yes.

**Reading:** A rule about which instruments are allowed. Any fixed income holding that is neither a diversified bond fund nor a US Treasury security is a breach. This includes individual corporate bonds.

**Must capture:**
- Applies to fixed income holdings only.
- Allowed: diversified bond funds, US Treasury securities.
- Everything else in fixed income is a breach.

**Outcome on the provided data:** Pass. The fixed income holdings are Crestline US Aggregate Bond ETF (CRAG) and US Treasury 4.375% 02/15/2033 (UST-2033).

---

## P-16

**Source:** §5.3 "The Portfolio should maintain a meaningful allocation to international equities."

**Portfolio constraint:** Partly. What it measures is clear. The limit is not stated.

**Reading:** A minimum international equity allocation, with "meaningful" left undefined.

**Must capture:**
- Measure: international equity as a share of the Portfolio (or of equity, which needs stating).
- Direction: a minimum.
- No numeric threshold appears in the text.
- Marked as needing human input.
- International funds are identified by `sub_asset_class`. A fund's `country` is where the fund itself is domiciled.

**Acceptable handling:**
- Flag for PM review with a specific clarifying question, for example "What minimum international equity allocation should we use? Common choices are 10% of the Portfolio or 20% of equity."
- Extract a partial rule with an assumed threshold, state the assumption, and keep it out of hard breach reporting until a PM approves it.

**Not acceptable:** Enforcing an invented threshold as a hard rule without saying so. Dropping the clause.

**Outcome on the provided data:** International equity is 6.25% of the Portfolio and 7.62% of equity (Lighthouse International Equity Index ETF, LITL). Pending a threshold from the PM.

---

## P-17

**Source:** §6.1 "The Firm will use tax-loss harvesting in the joint account where practical."

**Portfolio constraint:** No.

**Must capture:**
- Classified as a procedure or preference. No rule generated.

---

## P-18

**Source:** §6.2 "Where possible, bond funds will be held in the IRA."

**Portfolio constraint:** Partly. It is a soft preference about account placement.

**Reading:** A preference. "Where possible" means a bond fund outside the IRA does not break the policy.

**Must capture:**
- Classified as a soft preference or guideline.
- If a check is generated, it has to be advisory and must not be reported as a breach.

**Outcome on the provided data:** The only bond fund (CRAG) is in the IRA. Nothing to report.

---

## P-19

**Source:** §6.3 "The Firm will coordinate Kestrel share sales with the Patels' tax preparer so that sales in any year take into account the tax cost and the Patels' other income."

**Portfolio constraint:** No.

**Must capture:**
- Classified as a procedure. No rule generated.

---

## P-20

**Source:** §7.1 "The Firm and the Patels will review the Portfolio and this IPS at least annually, and within 30 days after each Kestrel vesting event." and §7.2 "The Patels will inform the Firm of any change in employment, income or goals, or in Priya's equity awards."

**Portfolio constraint:** No.

**Must capture:**
- Classified as a review procedure. No rule generated.
