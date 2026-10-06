# AGENTS.md — Policy Remediation Copilot

Read BRIEF.md, data/portfolio/README.md and IMPLEMENTATION_PLAN.md before coding. BRIEF.md wins on conflicts.

## Commands
- `make test`      → unit tests + helper tests (must pass before any LLM call)
- `make run`       → extract → check → plan (replay; no API key needed)
- `make eval`      → extraction evals (replay)
- `make lint`      → memo lint (every number in memo must be in plan_facts.json)
- `LLM_MODE=record make run`  → only with OPENAI_API_KEY; writes cache/llm/

## Non-negotiable rules
1. **Checker, planner, memo: NO LLM calls.** The LLM lives only in `src/copilot/extract/` via `src/copilot/llm/`.
2. **The LLM never receives holdings, prices or balances**, and never produces numbers used in findings/trades/memo.
3. **No hard-coded household IDs, tickers, issuer names, section numbers or thresholds** in `src/`. Use data + `config/*.yaml`.
4. **Quotes are derived from segment offsets by code.** Every IPS segment ends up as `rule / needs_review / not_constraint`.
5. **Ambiguous or un-measurable ⇒ `needs_review`** with a specific question. Never trade to cure a pending-review rule.
6. **Never write trades that `helpers/portfolio.apply_trades` rejects.** Verify, re-check, iterate (≤5 loops).
7. **Cache every LLM call** (`cache/llm/`); default `LLM_MODE=replay`; budget cap in `config/policy.yaml`. Don't spend API money without asking.
8. **Never put Patel gold labels into prompts or few-shots.** Use the Harlow example from BRIEF §6.
9. **Python ≥ 3.9.** Small, typed, tested functions. Add a test with every behavior change.
10. Percentages are **fractions** internally. Dates ISO. Deterministic output ordering. No magic numbers.

## Adding a rule kind
1. Add to `kind` enum in `src/copilot/extract/schema.py`
2. Add validator in `src/copilot/extract/validators.py`
3. Add evaluator in `src/copilot/check/kinds/<kind>.py`
4. Add linearizer rows in `src/copilot/plan/linearize.py`
5. Add synthetic eval case in `evals/gold/synthetic_cases.yaml`
6. Update README with new kind description

## Style
- Percentages are fractions internally (0.10, not 10%).
- Dates: ISO 8601 strings (`"2028-12-31"`).
- Deterministic output ordering (sort all lists by stable key).
- No magic numbers: every threshold in `config/*.yaml`.
- Type annotations on every function.
