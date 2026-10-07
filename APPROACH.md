# Architecture & Engineering Approach — Policy Remediation Copilot

> Companion document to [README.md](README.md) detailing the architectural philosophy, mathematical formulation, and system design decisions.

---

## 1. Problem Framing & Core Design Philosophy

Wealth managers at Cedar Ridge oversee complex multi-account portfolios governed by written Investment Policy Statements (IPS). Manual remediation is slow, error-prone, and doesn't scale.

### The Guiding Invariant: "Reading English is hard; arithmetic is easy."
Many naive LLM applications fail in financial compliance because they prompt an LLM to read the contract, inspect holdings, calculate percentages, and suggest trades. This approach fails because:
1. **Arithmetic Unreliability:** LLMs hallucinate numbers, miscalculate portfolio-level percentage weights, and round inconsistently.
2. **Security Vulnerability:** Malicious IPS text can inject prompt instructions to bypass concentration limits or liquidate unauthorized assets.
3. **Audit Impossibility:** Compliance auditors require mathematical proof that every trade is necessary and valid under the contract.

**Our Architectural Boundary:**
```
Unstructured English (IPS Markdown)
              │
              ▼
    [1] EXTRACTION (LLM)  <── The ONLY stage where an LLM is used
              │ Typed Schema
              ▼
    [2] CHECKER (Deterministic Python)  <── Arithmetic, Look-Through, Holdings
              │ Findings (PASS / BREACH / NEEDS_REVIEW / CANNOT_EVALUATE)
              ▼
    [3] PLANNER (Greedy Rebalancer + LP Fallback + Simulator) <── Pure Optimization & Helper Simulation
              │
              ▼
    trades.csv + plan_memo.md + plan_facts.json (Rendered via Jinja2)
```

- **The LLM never touches holdings, prices, or account balances.**
- **The Checker, Planner, and Memo generator make ZERO LLM calls.**
- **Every number traces to deterministic code and is validated by `make lint`.**

---

## 2. Stage-by-Stage Engineering Approach

### Stage 1: Extraction Engine (Semantic Parsing into Closed Schema)

1. **Deterministic Segmentation:**
   - Rather than relying on LLM sentence tokenization, `src/copilot/extract/segmenter.py` uses markdown AST parsing (headers `#`, `##`, `###`, blank lines, table rows) to segment the IPS text into discrete units (`S001`, `S002`, ...).
   - Records character start/end offsets for exact quote verification.
2. **Closed Schema Extraction:**
   - The LLM (`gpt-5.4-nano` with `temperature: 0`) is constrained by OpenAI JSON Schema mode.
   - The vocabulary of rule kinds is strictly closed:
     - `allocation_range`, `exposure_cap`, `exclusion`, `credit_quality`, `reserve_requirement`, `instrument_allowlist`, `tracked_target`, `needs_external_data`.
   - The model cannot invent field names or rule types.
3. **The Uncertainty Contract (`needs_review`):**
   - If a clause requires external judgment, trading window calendars, or subjective thresholds (e.g. "primarily hold investment-grade bonds"), the schema **mandates** a `review` object.
   - The system **never trades** to cure a rule pending review.
4. **Deterministic Validation Post-Processing:**
   - `src/copilot/extract/validators.py` verifies 100% segment coverage, validates entity names against `securities.csv`, and asserts that quotes exist character-for-character in the source segment.

---

### Stage 2: Compliance Checker Engine (Deterministic Arithmetic)

The checker evaluates extracted rules against real-world holdings as of `2026-09-25`:
1. **Multi-Account Aggregation:** Evaluates rules across all accounts in a household simultaneously (e.g. aggregating Halden stock in `CHEN-DC` and `CHEN-MC`).
2. **Multi-Asset Issuer Rollups:** Combines debt and equity of the same parent issuer (`ISS-HALDEN`).
3. **Full Fund Look-Through:** Multiplies ETF/mutual fund holdings by underlying weights from `fund_constituents.csv` to expose hidden concentrations:
   $$\text{Total Issuer MV} = \text{Direct MV} + \sum_{\text{funds}} \left( \text{Fund MV} \times \text{Constituent Weight} \right)$$
4. **Data Freshness Gating:** If cash flows or grant budgets are older than 180 days, reserve rules switch to `cannot_evaluate` rather than guessing compliance.
5. **Exact Attribution:** Every finding lists the exact margin and top contributing positions with dollar values and percentage shares.

---

### Stage 3: Remediation Planner (Greedy Solver + LP Fallback + Helper Simulator)

When confirmed breaches occur, the planner calculates remediation trades:

#### 1. Primary Solver (Greedy Heuristic)
- For each confirmed breach, the solver identifies the highest-contributing position and generates sell orders to bring exposure within the limit.
- Reinvests proceeds per account into approved core holdings from `approved_list.csv`.

#### 2. Fallback Solver (SciPy LP)
- If greedy trades fail re-check (e.g. complex multi-constraint sleeves), a continuous linear program (`scipy.optimize.linprog` with HiGHS) is invoked:
  - **Decision Variables:** $s_{a,i} \ge 0$ (sells), $b_{a,j} \ge 0$ (buys).
  - **Objective:** $\min \sum_{a,i} s_{a,i} + \sum_{a,j} b_{a,j}$ (minimize turnover).
  - **Constraints:** Cure breaches, preserve cash non-negativity, maintain passing sleeves, and protect unmanaged accounts.

#### 3. Discrete Lot Rounding
- Equities and mutual funds are rounded to whole integer shares: $\lfloor s_{a,i} / P_i \rfloor$.
- Bonds and Treasury bills trade exclusively in integer multiples of **$1,000 face value**.
- Buy quantities are adjusted downwards if necessary to guarantee cash non-negativity.

#### 4. Simulation & Re-Check Loop (`helpers/portfolio.py`)
- Trades are simulated using the canonical clearinghouse helper (`apply_trades`).
- The entire checker re-evaluates the simulated portfolio.
- If any breach remains or a new breach was created by discrete rounding, the optimizer adjusts constraints and re-solves (iterates up to 5 times).

---

### Stage 4: Evaluation & Anti-Overfitting Safeguards

The evaluation framework consists of two independent tracks:
1. **Patel Gold Benchmark (P-01 to P-20):**
   - 20 clauses from `patel_household.md` evaluated against human gold annotations.
   - Evaluates classification accuracy, field match, and the **False-Firm Canary** (confirming that ambiguous rules are never marked as firm).
2. **Synthetic Mutation Suite (SYN-01 to SYN-10):**
   - 10 synthetically mutated clauses testing:
     - Alternate phrasing & double negatives (`SYN-01`)
     - Number variations (`SYN-02`)
     - Scope & look-through inversions (`SYN-03`, `SYN-09`)
     - Vague threshold degradation (`SYN-04`)
     - Internal rule conflicts (`SYN-05`)
     - Words & mixed units (`SYN-06`)
     - Unnumbered narrative prose (`SYN-07`)
     - Future effective dates (`SYN-08`)
     - Adversarial prompt injection resistance (`SYN-10`)

---

## 3. Generalization Guarantee (Why It Does Not Overfit)

1. **Zero Hard-Coding in `src/`:** No household names, tickers, issuer IDs, or section numbers exist in source code. All logic is config- and data-driven.
2. **Disjoint Few-Shot Signal:** The extraction prompt uses exclusively the fictional **Harlow** example. It never sees Patel, Chen, Brightwater, or Okafor labels.
3. **Universal Applicability:** Running the pipeline on Okafor or Patel produces valid, verified outputs (clean pass for Okafor, crypto remediation for Patel) without special-casing.
4. **Data-Agnostic Entity Resolution:** Mapping company names to issuer IDs is performed by dynamic lookup against `securities.csv`.

---

## 4. Production Roadmap

1. **Execution Model:**
   - LLM extraction is run **once per IPS contract version** (e.g. annually or on amendment) and stored in PostgreSQL.
   - Daily compliance checks and trade generation run in milliseconds on standard CPU.
2. **Human-in-the-Loop UI:**
   - A compliance review queue surfaces pending questions from `review.question` with multi-choice options for the PM.
   - PM decisions persist to `pm_decisions.yaml` and deterministically re-run the checker and planner.
3. **Observability:**
   - OpenTelemetry traces tracking prompt tokens, latency, cache hit rates, and canary breach regressions.
