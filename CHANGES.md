# CHANGES.md — Remediation Plan for Reviewer Feedback

This file documents the specific changes made (or planned) in response to the reviewer's six feedback points. Each section states what was wrong, what the fix is, and which files are affected.

---

## Point 1 — Uncertain clauses treated as firm

### What was wrong

Two clauses in the Brightwater IPS were extracted as hard `rule` types when they should have been sent to PM review:

1. **Fossil-fuel exclusion (clause S036):** The clause says "will not invest in fossil fuel companies." The phrase "fossil fuel companies" is not defined in the IPS and is not an enumerated column in `securities.csv`. The system hard-coded a predicate on `fossil_revenue_pct > 10.0` inside `pipeline.py`. This threshold (10%) was invented by the extraction pipeline and has no textual basis. Even if the IPS intent is clear, the *boundary* for what counts as a "fossil fuel company" (revenue threshold, direct vs. look-through, funds-inside-funds) requires a PM decision.

2. **Missing / stale data handling:** The checker currently only marks `cannot_evaluate` when `client_cashflows` data is stale. If a security in a rule's universe is missing from `fund_constituents.csv` (e.g., an ETF whose look-through data is absent), the rule quietly passes with 0% measured instead of reporting `cannot_evaluate`. This is the "vacuous 0.00% pass" problem.

### The fix

**1a — Fossil-fuel exclusion:**
- Remove the hard-coded `fossil_revenue_pct > 10.0` predicate injected in `pipeline.py`.
- The extraction prompt's `decision_guide.md` already states: *"Do NOT produce a hard rule if the threshold requires a PM decision."* The pipeline code was overriding this at post-processing time.
- The clause should become `needs_review` with `reason=missing_threshold` and a question such as: *"What threshold defines a 'fossil fuel company' — revenue ≥ X%, production involvement, or fund look-through? Which data column in securities.csv should we use?"*
- **After the PM answers**, the rule can be made checkable using the new threshold in `config/policy.yaml` (no hard-code in `src/`).

**1b — Missing look-through data:**
- In `engine.py`, check whether any fund in scope has no constituent rows in `fund_constituents.csv`. If `lookthrough=funds` and a fund has no constituent data, the rule should emit `cannot_evaluate` with a reason like: *"Fund XYZ has no constituent data; look-through cannot be computed."*
- Add a helper `has_lookthrough_data(security_id) → bool` in `src/copilot/data/load.py` that checks whether `fund_constituents.csv` contains at least one row for the fund.

**Files affected:**
- `src/copilot/extract/pipeline.py` — remove hard-coded fossil predicate
- `src/copilot/check/engine.py` — add missing-constituent guard in `_eval_exclusion` and `_eval_allocation_range`
- `src/copilot/data/load.py` — add `has_lookthrough_data(security_id)` helper
- `config/policy.yaml` — add a `fossil_revenue_threshold` key so any future PM answer can be stored there (not in `src/`)
- `prompts/decision_guide.md` — clarify that undefined exclusion targets must become `needs_review`, not inferred predicates

---

## Point 2 — Incorrect checker results

### What was wrong

Two categories of incorrect results were identified:

**2a — Vacuous 0.00% pass (wrong answer, not missing data):**
When the exposure engine finds no holdings that match a rule's selector, it currently returns `status=pass` with `measured=0.0` and a note `"No matching holdings found — vacuous pass"`. This is incorrect in two cases:
- If the selector was supposed to match holdings (e.g., look-through of a fund whose constituent data is absent), a zero result is misleading.
- If the rule itself was poorly extracted (wrong selector), the zero pass hides the bug.

**2b — Look-through bug for fund constituents:**
If a fund has no constituent rows, its full market value rolls into `__residual__`. The `__residual__` exposure is then silently excluded from rule evaluation. As a result, any exclusion or cap rule that ought to fire against a fund holding (look-through) will show 0% measured and pass — a false negative.

### The fix

**2a — Vacuous pass:**
- When `groups` is empty in `_eval_exposure_cap`, check whether the empty result is because of missing look-through data. If `lookthrough=funds` and any position is a fund with no constituent data, return `cannot_evaluate` instead of vacuous pass.
- Add a note to all vacuous-pass findings that states: *"Measured value is 0% because no matching direct holdings were found. Verify that look-through data is complete before treating this as passing."*
- Do NOT change `status` to `cannot_evaluate` blindly for all empty selectors — only when there is a detectable reason for the empty result (missing fund data or no holdings of that type at all).

**2b — Look-through residual:**
- In `compute_exposures()`, when a fund has positions but zero constituent rows in `fund_constituents.csv`, instead of dropping into `__residual__`, emit a synthetic constituent row with `security_id = f"__unknown_{fund_id}__"` and the full fund MV. Tag it with `missing_data=True`.
- In the checker, when a rule's exposure set contains any `missing_data=True` constituent and the rule uses `lookthrough=funds`, escalate the finding to `cannot_evaluate` and report the funds with missing data.

**Files affected:**
- `src/copilot/data/load.py` — update `compute_exposures()` to surface missing-constituent funds instead of silently dropping to `__residual__`
- `src/copilot/check/engine.py` — add missing-constituent guard in `_eval_exclusion` and `_eval_allocation_range` / `_eval_exposure_cap`

---

## Point 3 — Hard-coded tickers, issuer names and section numbers in source

### What was wrong

The reviewer noted code in `pipeline.py` that infers selectors from keywords in IPS text. Four patterns were flagged:

1. **Fossil-fuel predicate:** keyword `"fossil"` triggers a hard-coded `fossil_revenue_pct > 10.0` predicate. This is specific to the Brightwater IPS wording.
2. **Crypto/digital asset predicate:** keyword `"crypto"` or `"digital asset"` triggers `sub_asset_class = digital_assets`. This couples the code to the Brightwater document's specific terminology.
3. **Equity ceiling injection:** keywords `"total equity"` + `"ceiling"` or `"exceed 60%"` force-set `aggregate=True` and `group_by=none`. The number 60% appears in the keyword check, which is Brightwater-specific.
4. **Single-stock rule injection:** keyword `"single stock"` injects `security_type = equity`. Again, document-specific language.

None of the above is wrong in isolation for the current data, but all of them would silently produce wrong rules for a new IPS that uses different phrasing.

### The fix

- **Remove all keyword-based selector injection** from `_flat_to_clause_record` in `pipeline.py`. The LLM is responsible for producing the correct predicates. If it fails (empty `measure_all_of` for an exclusion), the validator and checker already handle the degraded path correctly.
- If the LLM consistently fails to produce a predicate for a well-known exclusion type, the correct fix is to improve the **prompt** (`prompts/extract_system.md` or `decision_guide.md`), not the post-processing code.
- Update `decision_guide.md` to remove the crypto/alternative conflation and replace with: *"If a named category (e.g., 'fossil fuel companies') is not an enumerated value in the data dictionary, mark the clause `needs_review` with `reason=identification_ambiguity`."*

**Files affected:**
- `src/copilot/extract/pipeline.py` — remove entirely
- `prompts/decision_guide.md` — remove the `alternative/crypto` conflation; add guidance that undefined exclusion targets require `needs_review`

---

## Point 4 — README / APPROACH.md inconsistencies with actual code

### What was wrong

The README and APPROACH.md described behaviour that did not match the implementation:

- *"The planner uses a linear program to minimise turnover."* — In reality, `planner.py` runs the **greedy heuristic first** and only falls back to a LP when greedy fails. The LP is not the primary solver.
- *"Repair loop ≤ 5 iterations."* — Correct in code, but the README implied the LP was driving the repair loop; the greedy planner actually drives it.
- *"Stale input data triggers cannot_evaluate."* — Only `client_cashflows` staleness is checked. Fund constituent staleness is not checked (see Points 1–2). The README over-stated the system's robustness.

### The fix

Update the following sections of `README.md` and `APPROACH.md`:

**Planner algorithm:**
> The planner uses a greedy heuristic as its primary solver: for each confirmed breach, it sells the most-over-limit holding until the rule passes, then redeploys proceeds into approved fixed-income or cash-equivalent securities. If the greedy result fails re-check (rare for multi-constraint problems), a LP formulation is used as a fallback. All planned trades are validated by `helpers/portfolio.apply_trades` and the checker is re-run; the repair loop runs at most 5 times.

**Stale data scope:** Clarify that only `client_cashflows` staleness is checked against `config/freshness.yaml`. Fund constituent data staleness is a **known limitation** (see Known Issues). The checker does not detect missing fund constituents and will pass rules vacuously if they are absent.

**Files affected:**
- `README.md` — planner section, stale-data scope, known issues
- `APPROACH.md` — same corrections

---

## Point 5 — Memo reasoning quality

### What was wrong

The trades section of the memo used a generic rationale: *"Addresses confirmed breach"* for every row. The reviewer expected each trade to explain:
- Which specific rule/clause it addresses
- Why this quantity (e.g., *"sell 1,105 shares to bring fossil-fuel exposure from 5.69% to 0%"*)
- What assumption underlies the trade, if any

The "Left Alone" section said *"Nothing was deliberately left untouched beyond what was already passing"* — which adds no information for the PM.

Open questions were boilerplate paraphrases of rule text with no concrete options for the PM to choose from.

### The fix

**Trade rationale (template change):**
Update `src/copilot/plan/templates/plan_memo.md.j2` and `_fallback_memo()` in `memo.py` to include per-trade reasoning:
- The `rule_id` and `clause_id` that triggered the trade
- Before/after measured value (from `plan_facts.json`)
- For sells: how much MV is removed and why (*"removes 5.69% fossil-fuel exposure to satisfy the 0% exclusion in clause S036"*)
- For buys: what constraint motivated the redeployment (*"proceeds reinvested to maintain equity allocation within IPS limits"*)
- Any active assumption (*"This trade will be confirmed only after PM clarifies the fossil-fuel revenue threshold"*)

**"Left Alone" section:**
List every rule that checked and passed, with its measured value. For rules in `needs_review`, state the specific question and the default assumption the planner used (or did not use, if no trade was made).

**plan_facts.json as single source of truth:**
`build_plan_facts()` already collects per-finding measured values. Extend it to also store `before_mv` and `after_mv` per finding (captured after the simulator re-run), so the template can render before/after comparisons directly without separate computation.

**Files affected:**
- `src/copilot/plan/templates/plan_memo.md.j2` — richer per-trade rationale block with before/after values
- `src/copilot/plan/memo.py` — `build_plan_facts()` to carry `before_mv` / `after_mv` per finding; `_fallback_memo()` to use richer rationale

---

## Point 6 — Eval completeness (run-to-run variance)

### What was wrong

The BRIEF (Section 4) required:
> *"Run extraction several times on the same input and report what changes between runs."*

The current eval suite runs each test case once per cached LLM response. The `run_index` parameter exists in `call_llm()` but is only used for cache disambiguation. The evals did not report what actually differs between `run_index=0` and `run_index=1`.

The README's *"What evals can't catch"* listed generic items (schema drift, novel IPS patterns) but did not specify what **data** would close those gaps.

### The fix

**Variance reporting:**
- Cache 3 LLM responses for the Patel IPS under `run_index=0`, `1`, `2` (one record-mode run per index; these are now committed).
- Add logic to `evals/run_evals.py` (or a new `evals/eval_variance.py`) that:
  1. Loads all three cached responses for the same IPS.
  2. Compares clause counts, classification distributions, and per-clause `rule_id` lists.
  3. Reports any clause that changed classification across runs and any rule whose bounds or selectors differed.
- Report the results in `output/evals/variance_report.json` and add a brief prose summary to the README.

**"What evals can't catch" — data plan:**
Replace the generic list with specific, actionable items:
- *"Checker correctness on novel IPS patterns"* → collect 5 real-world-style IPS clauses (anonymised) with hand-computed expected outcomes.
- *"Look-through accuracy"* → add synthetic cases where a fund's constituents cross a threshold only when look-through is applied.
- *"Conflicting clause handling"* → add a synthetic case with two contradictory clauses and verify the system escalates both to `needs_review`.

**Files affected:**
- `evals/run_evals.py` — add variance comparison logic (or new `evals/eval_variance.py`)
- `output/evals/variance_report.json` — new output file (generated, committed)
- `README.md` — replace generic "what evals can't catch" with specific data plan
- `cache/llm/` — add `run_index=1` and `run_index=2` cached responses for HH-PATEL
