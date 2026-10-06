# Take-home: Policy Remediation Copilot

**Every company, fund and bond in this assignment is fictional.** So are the households, the advisor firm and its people.

> **Your task in short**
>
> - **Build:** a Python system that reads each client's investment policy, finds what the portfolio breaks, and proposes trades that bring it back into policy. A simulator checks the trades before anything reaches the portfolio manager. Anything the system isn't sure about goes to the portfolio manager as a question.
> - **Input:** four investment policy documents, the portfolio data and labels for one IPS in `data/`, and a data helper in `helpers/`.
> - **Output:** a remediation proposal for two households (Chen and Brightwater): a `trades.csv` and a plan memo. Plus the code, extraction evals and a README.
> - **Generality:** it must run successfully on Chen and Brightwater, and the same code must work for any household and IPS that fits the framework in section 2.
> - **Time:** about 6 to 7 hours.
> - **Submit:** a git repository within 7 days of receiving this brief. Questions to hpachpor@cogniuminc.com.

---

## 1. Background

Cognium builds AI-native tools for wealth managers. A wealth manager looks after the savings of client households. Each household has an **Investment Policy Statement (IPS)**, written by the advisor and agreed with the client. It says what the portfolio may hold, in what proportions, and what it must avoid.

Today a portfolio manager (PM) reads each IPS, checks the portfolio by hand, and decides what to trade to fix any problems. That's slow, and it's easy to miss things. We want a copilot that does the reading, checking and first draft of the trades. The PM stays in charge: nothing the copilot is unsure about gets acted on.

You would own this kind of work end to end at Cognium.

### Terms you'll need

You don't need finance experience. These cover most of what you'll see. `data/portfolio/README.md` explains every file and column.

| Term | Meaning |
|---|---|
| Household | A client, such as a couple, a family trust or a foundation. It has one IPS and one or more accounts |
| Account | A brokerage account, IRA, trust account and so on. It holds positions in securities and its own cash |
| Market value and weight | Market value is quantity times price. A position's **weight** is its market value divided by the value of the portfolio the IPS covers |
| Asset class | A broad category: equity (stocks), fixed income (bonds), cash, and so on. Allocation limits are usually set per asset class |
| Issuer | The company or government behind a security. One issuer can have several securities, for example its stock and its bonds |
| Fund and look-through | A mutual fund or ETF holds many securities itself. Owning a fund gives you indirect exposure to what's inside it. Counting that exposure is called **look-through** |
| Credit rating and investment grade | An agency's grade for a bond, for example AA or BBB. On S&P's scale, investment grade is BBB- and above. On Moody's scale, it is Baa3 and above |
| Breach | The portfolio breaks a rule in the IPS |
| Approved list | The securities the advisor firm allows new purchases of (`approved_list.csv`) |
| Trade | A buy or sell of a quantity of one security in one account |

---

## 2. What you're building

```
                                       Portfolio data
                                             │
 IPS ──► [1] Extraction ──► Rules ──► [2] Checker ──► Findings ──► [3] Planner ──► Remediation proposal
           (LLM)                        (code)                         │   ▲        (trades.csv + plan memo)
             │                            ▲                            ▼   │
             ▼                            └──────── Simulator: apply trades with the helper,
        [4] Evals                                   then re-run the checker
```

### What we mean by a rule

A clause is text in the IPS. A rule is what your checker runs: a structured statement your code can evaluate against the portfolio data without further interpretation. One clause can produce several rules or none, and a rule can depend on more than one clause. What goes into a rule is your schema design. If deciding pass or fail would still take a judgment call, the rule isn't ready and should go to PM review.

### What "fits the framework" means

A new IPS fits the framework if:

- it is a plain-English document (markdown) for a household whose accounts and holdings are in the same data files and columns;
- its constraints are of the kinds in the four documents you have: asset-class and sub-asset-class ranges, concentration limits by issuer, security, fund or sector, exclusions, credit quality, liquidity or reserve requirements, rules that depend on client data, and targets with deadlines;
- limits can be measured against the whole portfolio, an asset class or a single account, and a clause can take effect on a later date or be amended by an attached resolution;
- its wording, structure, numbers and section numbering can differ in any way.

### Component 1: Extraction

An LLM pipeline that reads an IPS and produces structured constraints. You design the schema. There is no required format for it. We will read your extraction code and its outputs.

- **Input:** one IPS document (markdown).
- **Output:** every clause classified as one of three things:
  - **A checkable rule.**
  - **A rule that needs PM review.** It carries the reason, and either a clarifying question or a stated assumption.
  - **Not a portfolio constraint.** Objectives, procedures, background. Keep these so a reader can see nothing was skipped.
- Every rule links to its exact source text.
- Run extraction on all four IPS documents.

### Component 2: Checker

Deterministic code with no LLM calls.

- **Input:** rules and the portfolio data for one household.
- **Output:** one status per rule, with the measured value and the holdings behind it.
- Run the checker on all four households.

| Status | Meaning |
|---|---|
| Pass | Checked and within the limit |
| Breach | Checked and outside the limit |
| Needs review | Waiting on a PM answer before it can be decided |
| Cannot evaluate | Required data is missing, or too out of date to support a result. Report the dates of the inputs you used |

### Component 3: Remediation planner

Proposes trades. You submit proposals for the **Chen and Brightwater households only**. The planner must not be specific to them. Given another household's findings, it should produce a proposal the same way.

- **Input:** the checker's findings, the portfolio data and `approved_list.csv`.
- **Output:** a remediation proposal (see section 5).
- The planner must:
  - verify its plan by applying the trades with the helper (`helpers/portfolio.py`) and re-running your checker on the result;
  - iterate until the plan is clean, or explain why it can't be;
  - never trade to cure a rule that is still pending PM review.

You decide which uncertain clauses your system can apply under a stated assumption and which must wait for the PM. If a trade depends on an assumption, the memo must say so.

How you split the work between the LLM and code is up to you: an LLM, an optimizer, plain rules, or a mix. Simple heuristic sizing is fine. We care that the plan is correct and that the memo explains it.

### Component 4: Evals (extraction only)

- **Input:** your extraction pipeline, the Patel labels and cases you write.
- **Output:** scores and an explanation of the failures, in your repo and summarized in the README.
- **Convert the Patel labels into gold data.** `data/labeled/patel_household_expected.md` describes, clause by clause, what a correct extraction of the Patel IPS must capture. It's in plain language and doesn't assume any schema. Score your pipeline per clause and per field, and explain the failures.
- **Write about 10 targeted synthetic cases.** For example: reword a clause, change a number, flip "direct holdings only" to "including funds", add a conflicting sentence, or turn a firm limit into a vague one.
- **Run extraction several times on the same input** and report what changes between runs.
- **In the README,** say what your evals can't catch and what data you'd collect first to close that gap.

You don't need plan evals. We score plans with our own validator.

---

## 3. The data helper

`helpers/portfolio.py` is a small tested module (standard library only). It:

- loads the CSVs;
- values positions with the price conventions applied;
- applies a list of trades and returns the new portfolio, rejecting invalid trades: unknown accounts or securities, accounts outside the portfolio or not managed by the firm, short sales, negative cash, buys that aren't on the approved list, wrong lot sizes and trades in `CASH_USD`.

It does not compute exposures, look-through, issuer grouping, asset-class totals or percentage denominators. Those are yours. It needs Python 3.9 or later. Run its tests with `python3 -m unittest helpers/test_portfolio.py` from the folder that contains this brief.

You can use, wrap or replace it. Our validator applies your `trades.csv` with this helper, so any trade it rejects counts as invalid.

## 4. Trading assumptions

- Trades execute at the valuation-date close (2026-09-25). No settlement delay, slippage or commissions. If a security has no close on that date, the helper uses its latest earlier close and warns.
- Whole shares for stocks, funds and money market funds. Bonds and bills trade in $1,000 face amounts. A sale that closes a whole position is always allowed.
- Within an account, all sells settle before any buy. Row order doesn't matter.
- Cash can't move between accounts. Each account funds its own buys. No short sales and no negative cash.
- Anything currently held in an account the firm manages can be sold. Accounts with `managed_by_firm` set to `false` can't be traded. Buys come only from `approved_list.csv`.
- No tax lots and no trading blackouts.

## 5. The remediation proposal

For each of Chen (`HH-CHEN`) and Brightwater (`HH-BRIGHT`), write:

**`output/<household_id>/trades.csv`**, with `output/` at the root of your repository, in exactly this format:

```
account_id,security_id,side,quantity
CHEN-TR,EXMP,sell,100
```

- `side` is `buy` or `sell`.
- `quantity` is a plain number with no separators or currency symbols. It is shares for stocks, funds and money market funds, and face value in dollars for bonds and bills.
- A file with only the header row is a valid answer if the right plan is to trade nothing.

**`output/<household_id>/plan_memo.md`**, written for the PM. It covers:

1. **Findings:** what's out of policy, with the measured value, the limit and the source clause.
2. **Trades:** each trade, and the finding and clause it addresses.
3. **Left alone:** what the plan deliberately didn't touch, and why.
4. **Open questions** for the PM or the client.

Every claim in the memo should trace to a number your code computed or a clause you quote.

---

## 6. A worked example

The household below is invented and isn't in `data/`. Its clauses are lettered so you don't look for them in the real documents. The example describes outcomes in plain English. How you represent them is up to you.

The IPS for the Harlow household says:

> A.1 No single stock may exceed 8% of the portfolio.
> A.2 The portfolio should keep a sensible cash cushion.
> A.3 We will meet with the clients each spring.

The portfolio is worth $1,000,000. Larkspur Industries stock is worth $94,000, all in the joint account, at $100 a share.

- **A.1 produces a finding.** Larkspur is 9.4% of the portfolio against an 8% limit, so it's a breach.
- **The trade that cures it.** Sell 160 shares of Larkspur in the joint account. After the trade, Larkspur is $78,000, or 7.8%. Total value doesn't change, because the $16,000 of proceeds stays in the joint account as cash. Re-running the checker shows A.1 passing and no new breach. The memo would say which clause the sale addresses and why it stops at 160 shares.
- **A.2 is left for PM review.** It's a real constraint with no number. The plan doesn't trade on it. The memo asks: "What minimum cash level should we use, for example 2% or 5% of the portfolio? Cash is 3.1% today, and 4.7% after the Larkspur sale."
- **A.3 is not a portfolio constraint.** It's a procedure. Your extraction should still record it. The memo can leave it out.

---

## 7. What success looks like

1. One command produces both remediation proposals.
2. The same code, with no changes, can run extraction, checking and planning for any household and IPS that fits the framework.
3. After applying the trades, no confirmed breaches remain and none are created. A confirmed breach is a breach of a rule that isn't waiting on the PM.
4. Nothing is traded to cure a rule that is pending PM review.
5. Every claim in the memo traces to a computed number or a quoted clause.
6. Every IPS clause is accounted for in extraction, and every rule links to its source text.
7. The checker is correct on the provided data.
8. The evals tell a reader how good extraction is and where it fails.
9. Everything runs without an API key, from cached LLM outputs you commit.

---

## 8. Ground rules

- **Python** is required. Use any libraries you like.
- **Any LLM provider and model**, with your own API key. Tell us what you chose and why.
- **Everything must run without an API key** from cached LLM outputs that you commit.
- **AI coding assistants are fine.** You own every design decision, so record the reasoning in your README.
- **Use only the provided data.** All companies, funds and bonds are fictional. Don't look up real-world information.
- **Design for IPS documents you haven't seen.** Don't hard-code anything to these four documents or households: no household IDs, section numbers, tickers or thresholds in the code.
- **Make assumptions where you need to, and write them down.** Some clauses are open to interpretation on purpose. We'll answer questions about the intent of the assignment. We won't tell you how to interpret a specific clause.

## 9. Scope

This should take about 6 to 7 hours. A smaller scope that works end to end and is well explained is what we want. You will have to choose what to leave out. Write those choices down in the README: what you decided not to do, why, and what you'd do with more time.

No UI. The plan memo is what the PM reads.

## 10. What you get

```
BRIEF.md            this file
data/
  ips/              4 IPS documents (markdown)
  portfolio/        CSVs and a README describing every column
  labeled/          Expected extraction for the Patel IPS, in plain language
helpers/
  __init__.py
  portfolio.py      data loading, valuation and trade simulation
  test_portfolio.py tests for the helper
```

The valuation date for everything is **2026-09-25**. `households.csv` maps each household to its IPS file. Client planning inputs such as expenses and grant budgets are in `client_cashflows.csv`.

## 11. What to submit

A git repository **within 7 days of receiving this brief**. Either share a private GitHub repository (email hpachpor@cogniuminc.com for the account to share it with), or email a zip of the repository including its `.git` folder. It should contain:

- [ ] Code for everything you built.
- [ ] Cached LLM outputs for every model call the pipeline and the evals make, including the synthetic cases and the repeated runs.
- [ ] Outputs from your last run: `output/HH-CHEN/` and `output/HH-BRIGHT/` with `trades.csv` and `plan_memo.md`, extraction results for all four IPS documents, checker results for all four households, and eval results.
- [ ] **README.md** covering:
  - how to run everything (ideally one command each for the pipeline and the evals), and how to run it on a new household or IPS;
  - your schema and where the LLM stops and code takes over, and why;
  - model and prompting choices, and what you tried that didn't work;
  - how you handle ambiguous, conflicting or uncheckable clauses;
  - eval results, and what the evals can't catch;
  - assumptions you made;
  - known issues, including any results you think are wrong;
  - what you decided not to do, and what you'd do with more time;
  - what you'd change for production (reliability, cost, latency, monitoring, human review).

We'll run your submission from a clean checkout by following your README. Write it for an engineer who will maintain this after you.

## 12. How we evaluate

In order of weight (some areas carry equal weight):

1. **Plan correctness.** We apply your `trades.csv` with our validator: confirmed breaches remaining, new breaches created, invalid trades, and trades that act on rules pending review.
2. **Uncertainty handling.** What goes to the PM, how it's framed, and whether anything uncertain gets treated as firm.
3. **Schema and model boundary.** How rules are represented, where the LLM stops, and how you keep its output reliable.
4. **Plan judgment.** Read from the memo: why these trades, what was left alone, what was asked.
5. **Checker correctness and traceability.**
6. **Evals.** Case choice, honesty about failures, run-to-run variance, and your data plan.
7. **README.**

## 13. After you submit

We'll review your submission and email you about next steps.
