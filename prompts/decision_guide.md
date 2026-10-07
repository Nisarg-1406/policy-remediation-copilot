# Decision Guide (prompt version 1.0)

Quick reference for common extraction decisions.

## Look-through

| Text says | Set |
|-----------|-----|
| "including funds", "look-through", "indirect exposure" | `lookthrough=funds` |
| "direct holdings only", "funds are not looked through" | `lookthrough=none` |
| Silent | `lookthrough=none` (default) |

## Grouping and aggregation

| Text says | Set |
|-----------|-----|
| "any single issuer", "per issuer" | `group_by=issuer` |
| "any single security", "per security" | `group_by=security` |
| "any fund", "per fund" | `group_by=fund` |
| "any sector" | `group_by=sector` |
| "combined", "in aggregate", "in total" | `aggregate=true` |
| "except", "other than", "excluding" | `exempt` selector |

## Vague language → needs_review

| Text says | Action |
|-----------|--------|
| "meaningful", "sensible", "reasonable", "appropriate" | `needs_review`, `reason=missing_threshold`, ask for a number with examples |
| "predominantly", "primarily", "mainly" | `needs_review`, `reason=ambiguous_term` |
| "diversified ETF" or similar without a definition | `needs_review`, `reason=identification_ambiguity` |
| Named entity that may not exist in data | `needs_review` if zero or multiple matches, else resolve |

## Severity

| Text says | Set |
|-----------|-----|
| "shall", "must", "will not", "may not", "no X" | `severity=hard` |
| "should", "encourages", "where possible", "where practical", "target" | `severity=advisory` |

## Missing data → needs_external_data kind

Set kind=`needs_external_data` if the rule needs:
- Transaction history → `data_requirements=["transactions"]`
- Duration/maturity band from an index → `data_requirements=["duration"]`
- 3-year benchmark performance → `data_requirements=["performance_history"]`
- Holdings liquidity ratings → `data_requirements=["liquidity_attributes"]`
- Leverage or derivatives usage → `data_requirements=["leverage_data"]`
- Client cashflows (reserve rules) → `data_requirements=["client_cashflows"]`

## Selectors

Build the `measure_all_of` list as conjuncts:
- `{"field": "asset_class", "op": "eq", "value": "equity"}` — selects equity
- `{"field": "sub_asset_class", "op": "in", "value": ["intl_developed", "intl_equity"]}` — selects international equity
- `{"field": "is_fund", "op": "eq", "value": true}` — selects funds only
- `{"field": "issuer_id", "op": "eq", "entity_name": "Kestrel Semiconductor"}` — system resolves entity_name
- `{"field": "fossil_revenue_pct", "op": "gt", "value": 0}` — selects companies with any fossil revenue
If a named category (e.g., 'fossil fuel companies', 'digital assets') is not an enumerated value in the data dictionary, mark the clause `needs_review` with `reason=identification_ambiguity`.

An empty `measure_all_of` list means "all holdings" — rarely correct; use it only for portfolio-wide caps.

## Bound format

- Always express as fraction: 0.10 not 10
- `min_value` / `max_value` are the bounds; set `min_inclusive` / `max_inclusive` to reflect "at least" (inclusive) vs. "more than" (exclusive)
- Strict language "less than 15%" → `max_value=0.15, max_inclusive=false`
- "no more than 10%" → `max_value=0.10, max_inclusive=true`

## Denominator

| Text says | Set |
|-----------|-----|
| "of the Portfolio", "of total assets" (default) | `denominator=portfolio` |
| "of the equity sleeve", "of equities" | `denominator=asset_class_of_selection` |
| "within the account" | `denominator=account` |

## Common errors to avoid

- Do NOT produce a rule that measures "how many months of expenses are held" when the text explicitly says no cash reserve is required (e.g. "reserve is outside this IPS").
- Do NOT set `lookthrough=funds` for rules that explicitly say "direct holdings only".
- Do NOT set `deadline` for a rule that says "by [date]" when the date is in the past — still set it; the checker decides.
- Do NOT produce a hard `rule` if the threshold requires a PM decision. Use `needs_review` instead.
