# Worked Example — Harlow Household (NOT a real household in the data)

This example is from the assignment brief (BRIEF.md §6). It shows the expected extraction for three illustrative clauses.

The Harlow IPS says:

> A.1 No single stock may exceed 8% of the portfolio.
> A.2 The portfolio should keep a sensible cash cushion.
> A.3 We will meet with the clients each spring.

Portfolio: $1,000,000. Larkspur Industries stock: $94,000 (9.4%), in the joint account at $100/share.

## Expected extraction output (JSON)

```json
{
  "clauses": [
    {
      "clause_id": "A.1",
      "segment_ids": ["S001"],
      "classification": "rule",
      "not_constraint_type": null,
      "rationale": "Hard numeric limit on single-stock exposure, measurable from holdings.",
      "injection_suspected": false,
      "rules": [
        {
          "rule_id": "R-A1",
          "clause_id": "A.1",
          "source_segment_ids": ["S001"],
          "kind": "exposure_cap",
          "group_by": "security",
          "aggregate": false,
          "denominator": "portfolio",
          "lookthrough": "none",
          "lookthrough_basis": "explicit",
          "severity": "hard",
          "min_value": null,
          "min_unit": null,
          "min_inclusive": null,
          "max_value": 0.08,
          "max_unit": "fraction",
          "max_inclusive": true,
          "informational_target": null,
          "effective_from": null,
          "deadline": null,
          "data_requirements": [],
          "scope_accounts": null,
          "measure_all_of": [
            {"field": "security_type", "op": "eq", "value": "equity"}
          ],
          "exempt_all_of": []
        }
      ],
      "review": null
    },
    {
      "clause_id": "A.2",
      "segment_ids": ["S002"],
      "classification": "needs_review",
      "not_constraint_type": null,
      "rationale": "Real constraint but no numeric threshold — 'sensible' is undefined.",
      "injection_suspected": false,
      "rules": [],
      "review": {
        "reason": "missing_threshold",
        "question": "What minimum cash level should we use? For example, 2% or 5% of the portfolio? Cash is 3.1% today.",
        "assumption": "Assume 2% minimum if no answer provided."
      }
    },
    {
      "clause_id": "A.3",
      "segment_ids": ["S003"],
      "classification": "not_constraint",
      "not_constraint_type": "procedure",
      "rationale": "Administrative meeting schedule — not a portfolio holding constraint.",
      "injection_suspected": false,
      "rules": [],
      "review": null
    }
  ]
}
```

## Key lessons from this example

- A.1 → `rule` because the limit (8%) is explicit and measurable against holdings.
- A.2 → `needs_review` because "sensible" is undefined; the question offers example thresholds.
- A.3 → `not_constraint` / `procedure` because it's an administrative clause.
- A.2 does NOT get a provisional rule here (no provisional bound is offered), but the question asks the PM for a concrete answer.
- Larkspur's breach status (9.4% > 8%) is NOT part of the extraction — the checker measures that.
