"""
Extraction validators — 6 layers of post-LLM validation.

L1: JSON schema (provider-enforced, implicit)
L2: Pydantic semantic rules (via schema.py validators)
L3: Coverage — every segment ID appears exactly once
L4: Selector grounding — fields exist in securities.csv, enum values valid
L5: Entity resolution — entity_name → issuer_id(s) via rapidfuzz
L6: Quote check — code-derived quotes are exact substrings

Never drops a clause. Repair attempt → downgrade to needs_review if still invalid.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from copilot.extract.schema import (
    ClauseRecord, ExtractionResult, Predicate, ReviewItem, Rule, Selector,
)
from copilot.extract.segmenter import Segment, derive_quote

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known derived fields (valid in selectors even though not in securities.csv)
# ---------------------------------------------------------------------------

DERIVED_FIELDS = {
    "rating_bucket",        # IG | SUB_IG | unrated
    "years_to_maturity",    # from maturity_date
    "issuer_fossil_pct",    # max fossil_revenue_pct across issuer's equities
    "fossil_revenue_pct",   # direct column or derived
    "is_fund",              # True for mutual_fund, etf, money_market, exchange_traded_product
    "is_treasury",          # security_type in (treasury, treasury_bill)
    "is_bond",              # security_type in (corporate_bond, treasury, treasury_bill)
    "is_stock",             # security_type == equity
    "is_cash_like",         # asset_class in (cash, cash_equivalent)
    "entity_name",          # alias for issuer_name
}


# ---------------------------------------------------------------------------
# L3 — Coverage check
# ---------------------------------------------------------------------------

def check_coverage(
    clauses: List[ClauseRecord],
    all_segment_ids: Set[str],
) -> Tuple[bool, List[str]]:
    """
    Every segment must appear in exactly one clause's segment_ids.
    Returns (ok, [error_messages]).
    """
    seen: Dict[str, str] = {}  # seg_id → clause_id
    errors = []

    for clause in clauses:
        for sid in clause.segment_ids:
            if sid not in all_segment_ids:
                errors.append(f"Clause {clause.clause_id}: unknown segment ID {sid}")
            if sid in seen:
                errors.append(
                    f"Segment {sid} appears in both {seen[sid]} and {clause.clause_id}"
                )
            seen[sid] = clause.clause_id

    missing = all_segment_ids - set(seen.keys())
    for sid in sorted(missing):
        errors.append(f"Segment {sid} not covered by any clause")

    return len(errors) == 0, errors


def add_uncovered_segments(
    clauses: List[ClauseRecord],
    missing_ids: Set[str],
    segments: List[Segment],
) -> List[ClauseRecord]:
    """
    Auto-label uncovered segments as needs_review/extraction_invalid.
    Invariant: never drop a segment.
    """
    seg_map = {s.id: s for s in segments}
    for sid in sorted(missing_ids):
        seg = seg_map.get(sid)
        clean_text = (seg.text or "").strip() if seg else ""
        if (
            (seg and seg.kind == "heading")
            or clean_text.startswith("#")
            or clean_text in ("---", "***", "___")
            or clean_text.startswith("|")
            or "signature" in clean_text.lower()
            or clean_text.startswith("/s/")
        ):
            clauses.append(
                ClauseRecord(
                    clause_id=f"HDR-{sid}",
                    segment_ids=[sid],
                    classification="not_constraint",
                    not_constraint_type="structural",
                    rationale=f"Document section heading / structure: {clean_text[:60]}",
                )
            )
            continue

        text_preview = (seg.text[:80] + "…") if seg and len(seg.text) > 80 else (seg.text if seg else "?")
        clauses.append(
            ClauseRecord(
                clause_id=f"AUTO-{sid}",
                segment_ids=[sid],
                classification="needs_review",
                rationale=f"Segment not covered by LLM extraction; auto-labelled for review.",
                review=ReviewItem(
                    reason="extraction_invalid",
                    question=f"This segment was not classified by extraction: '{text_preview}'. Please review.",
                ),
            )
        )
    return clauses


# ---------------------------------------------------------------------------
# L4 — Selector grounding
# ---------------------------------------------------------------------------

def ground_selectors(
    clauses: List[ClauseRecord],
    valid_fields: Set[str],
    valid_enums: Dict[str, Set[str]],
) -> List[str]:
    """
    Check every Predicate.field exists in securities.csv or DERIVED_FIELDS,
    and enum values are valid. Returns list of warnings (not fatal — downgrade to review).
    """
    warnings = []
    all_valid = valid_fields | DERIVED_FIELDS

    def check_pred(p: Predicate, context: str) -> None:
        if p.field == "entity_name":
            p.field = "issuer_name"
            if not p.value and p.entity_name:
                p.value = p.entity_name
        if p.field not in all_valid:
            warnings.append(f"{context}: unknown field '{p.field}'")
        # Check enum values
        if p.field in valid_enums and p.value is not None:
            vals = [p.value] if isinstance(p.value, str) else (p.value if isinstance(p.value, list) else [])
            for v in vals:
                if isinstance(v, str) and v not in valid_enums[p.field]:
                    warnings.append(f"{context}: value '{v}' not in enum for field '{p.field}'")

    def check_selector(sel: Optional[Selector], context: str) -> None:
        if sel is None:
            return
        for pred in sel.all_of:
            check_pred(pred, context)
        for grp in sel.any_of:
            for pred in grp:
                check_pred(pred, context)

    for clause in clauses:
        for rule in clause.rules:
            ctx = f"Rule {rule.rule_id}"
            check_selector(rule.measure, f"{ctx}.measure")
            check_selector(rule.exempt, f"{ctx}.exempt")
            if rule.readings:
                for alt in rule.readings:
                    check_selector(alt.measure, f"{ctx}.reading.measure")

    return warnings


# ---------------------------------------------------------------------------
# L5 — Entity resolution
# ---------------------------------------------------------------------------

def resolve_entities(
    clauses: List[ClauseRecord],
    issuer_lookup: Dict[str, List[str]],  # normalized_name → [issuer_id, ...]
    account_lookup: Dict[str, str],       # normalized_descriptor → account_id
) -> List[str]:
    """
    Resolve entity_name in Predicates to resolved_issuer_ids.
    - 0 hits: entity_unmatched — rule kept with vacuous pass, reported
    - 1 hit: resolved
    - >1 hits: downgrade to review (ambiguous)
    Returns list of warnings.
    """
    from rapidfuzz import process, fuzz

    warnings = []

    def resolve_predicate(p: Predicate, context: str) -> None:
        if p.entity_name is None:
            return
        name_norm = p.entity_name.lower().strip()
        # Exact match first
        if name_norm in issuer_lookup:
            p.resolved_issuer_ids = issuer_lookup[name_norm]
            return
        # Fuzzy match
        candidates = list(issuer_lookup.keys())
        result = process.extractOne(name_norm, candidates, scorer=fuzz.WRatio, score_cutoff=85)
        if result is None:
            warnings.append(
                f"{context}: entity '{p.entity_name}' unresolvable — vacuous pass (reported)"
            )
            p.resolved_issuer_ids = []
        else:
            matched_name, score, _ = result
            p.resolved_issuer_ids = issuer_lookup[matched_name]
            if len(p.resolved_issuer_ids) > 1:
                warnings.append(
                    f"{context}: entity '{p.entity_name}' matches multiple issuers "
                    f"{p.resolved_issuer_ids} — flagged for review"
                )

    def resolve_selector(sel: Optional[Selector], context: str) -> None:
        if sel is None:
            return
        for pred in sel.all_of:
            resolve_predicate(pred, context)
        for grp in sel.any_of:
            for pred in grp:
                resolve_predicate(pred, context)

    for clause in clauses:
        for rule in clause.rules:
            ctx = f"Rule {rule.rule_id}"
            resolve_selector(rule.measure, f"{ctx}.measure")
            resolve_selector(rule.exempt, f"{ctx}.exempt")
            if rule.exempt:
                exempt_preds = list(rule.exempt.all_of)
                for grp in rule.exempt.any_of:
                    exempt_preds.extend(grp)
                for pred in exempt_preds:
                    name_check = pred.entity_name or (pred.value if isinstance(pred.value, str) and pred.field in ("issuer_name", "entity_name") else None)
                    if name_check and name_check.lower() not in issuer_lookup:
                        match = process.extractOne(name_check.lower(), list(issuer_lookup.keys()), scorer=fuzz.WRatio, score_cutoff=85)
                        if not match:
                            clause.classification = "needs_review"
                            clause.review = ReviewItem(
                                reason="identification_ambiguity",
                                question=f"The exemption '{name_check}' could not be unambiguously resolved to a specific security or fund. Please confirm which holding qualifies for exemption.",
                            )
                            clause.rules = []
                            break
            if rule.readings:
                for alt in rule.readings:
                    resolve_selector(alt.measure, f"{ctx}.reading.measure")
            # Resolve scope_accounts
            if rule.scope_accounts:
                resolved = []
                for desc in rule.scope_accounts:
                    desc_norm = desc.lower().strip()
                    if desc_norm in account_lookup:
                        resolved.append(account_lookup[desc_norm])
                    else:
                        # Fuzzy
                        from rapidfuzz import process, fuzz
                        candidates = list(account_lookup.keys())
                        result = process.extractOne(desc_norm, candidates, scorer=fuzz.WRatio, score_cutoff=80)
                        if result:
                            resolved.append(account_lookup[result[0]])
                        else:
                            warnings.append(f"{ctx}: scope account '{desc}' unresolvable")
                            resolved.append(desc)  # keep raw, checker will warn
                rule.scope_accounts = resolved

    return warnings


# ---------------------------------------------------------------------------
# L6 — Quote check
# ---------------------------------------------------------------------------

def attach_and_verify_quotes(
    clauses: List[ClauseRecord],
    segments: List[Segment],
    markdown: str,
) -> List[str]:
    """
    Derive source quotes from segment offsets and attach to rules.
    Verify that every quote is an exact substring of the IPS.
    Errors here are bugs (invariant I4) — not repairable by LLM.
    """
    seg_map = {s.id: s for s in segments}
    errors = []

    for clause in clauses:
        for rule in clause.rules:
            quotes = []
            for sid in rule.source_segment_ids:
                seg = seg_map.get(sid)
                if seg is None:
                    errors.append(f"Rule {rule.rule_id}: segment {sid} not found in segment map")
                    continue
                quote = derive_quote(seg, markdown)
                if quote not in markdown:
                    errors.append(
                        f"Rule {rule.rule_id}: derived quote is NOT a substring of IPS "
                        f"(segment {sid}, start={seg.start}, end={seg.end})"
                    )
                quotes.append(quote)
            rule.source_quotes = quotes

    return errors


# ---------------------------------------------------------------------------
# Unit normalisation
# ---------------------------------------------------------------------------

def normalise_bounds(clauses: List[ClauseRecord]) -> None:
    """Convert percent bounds to fractions in-place."""
    for clause in clauses:
        for rule in clause.rules:
            if rule.min is not None and rule.min.unit == "percent":
                rule.min = rule.min.model_copy(update={"value": rule.min.value / 100.0, "unit": "fraction"})
            if rule.max is not None and rule.max.unit == "percent":
                rule.max = rule.max.model_copy(update={"value": rule.max.value / 100.0, "unit": "fraction"})
