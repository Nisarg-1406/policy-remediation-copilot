"""
Extraction pipeline — orchestrates segmenter → LLM → validators → entity resolution.

One LLM call per IPS (with repair retry if validation fails).
Everything else is deterministic code.

Invariant I2: The prompt never contains holdings, prices, or balances.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from copilot.extract.schema import ClauseRecord, ExtractionResult, ReviewItem
from copilot.extract.segmenter import Segment, segment_document, verify_coverage
from copilot.extract.validators import (
    attach_and_verify_quotes,
    check_coverage,
    add_uncovered_segments,
    ground_selectors,
    normalise_bounds,
    resolve_entities,
)
from copilot.data.load import (
    data_dictionary,
    issuer_lookup,
    account_lookup,
    securities,
)
from copilot.llm.client import call_llm

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
CONFIG_DIR = REPO_ROOT / "config"
PROMPTS_DIR = REPO_ROOT / "prompts"
SCHEMA_VERSION = "1.0"
PROMPT_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Prompt construction (static prefix + dynamic suffix)
# ---------------------------------------------------------------------------

def _load_prompt_file(name: str) -> str:
    path = PROMPTS_DIR / name
    if path.exists():
        return path.read_text()
    return ""


def build_prompt(
    segments: List[Segment],
    hh_id: str,
    schema_json: dict,
) -> list[dict]:
    """
    Build the message list for the LLM.

    Static prefix order (cache-friendly):
    1. Role + rules of engagement
    2. JSON schema
    3. Data dictionary (enumerations only; no holdings)
    4. Decision guide
    5. One worked example (Harlow — not Patel)

    Dynamic suffix:
    6. Numbered segments for this IPS document
    """
    system_prompt = _load_prompt_file("extract_system.md")
    decision_guide = _load_prompt_file("decision_guide.md")
    example = _load_prompt_file("example_harlow.md")
    data_dict = data_dictionary(hh_id)

    schema_str = json.dumps(schema_json, indent=2)

    segments_text = "\n".join(
        f"{seg.id} [{seg.heading_path[-1] if seg.heading_path else 'top'}]: {seg.text}"
        for seg in segments
        if seg.kind != "heading"  # headings are already encoded in heading_path
    )

    system_content = "\n\n".join(filter(None, [
        system_prompt,
        f"## JSON Schema\n```json\n{schema_str}\n```",
        data_dict,
        decision_guide,
        example,
    ]))

    user_content = f"## IPS Segments for {hh_id}\n\n{segments_text}"

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# JSON schema for structured outputs
# ---------------------------------------------------------------------------

def _build_response_schema() -> dict:
    """
    Build a JSON Schema for the structured output format.
    Uses a flat schema with nullable fields for maximum compatibility with
    strict structured outputs.
    """
    return {
        "type": "object",
        "properties": {
            "clauses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clause_id": {"type": "string"},
                        "segment_ids": {"type": "array", "items": {"type": "string"}},
                        "classification": {"type": "string", "enum": ["rule", "needs_review", "not_constraint"]},
                        "not_constraint_type": {"type": ["string", "null"]},
                        "rationale": {"type": "string"},
                        "injection_suspected": {"type": "boolean"},
                        "rules": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "rule_id": {"type": "string"},
                                    "clause_id": {"type": "string"},
                                    "source_segment_ids": {"type": "array", "items": {"type": "string"}},
                                    "kind": {
                                        "type": "string",
                                        "enum": [
                                            "allocation_range", "exposure_cap", "exclusion",
                                            "credit_quality", "reserve_requirement",
                                            "instrument_allowlist", "tracked_target", "needs_external_data"
                                        ]
                                    },
                                    "group_by": {"type": "string"},
                                    "aggregate": {"type": "boolean"},
                                    "denominator": {"type": "string"},
                                    "lookthrough": {"type": "string"},
                                    "lookthrough_basis": {"type": "string"},
                                    "severity": {"type": "string"},
                                    "min_value": {"type": ["number", "null"]},
                                    "min_unit": {"type": ["string", "null"]},
                                    "min_inclusive": {"type": ["boolean", "null"]},
                                    "max_value": {"type": ["number", "null"]},
                                    "max_unit": {"type": ["string", "null"]},
                                    "max_inclusive": {"type": ["boolean", "null"]},
                                    "informational_target": {"type": ["number", "null"]},
                                    "effective_from": {"type": ["string", "null"]},
                                    "deadline": {"type": ["string", "null"]},
                                    "data_requirements": {"type": "array", "items": {"type": "string"}},
                                    "scope_accounts": {"type": ["array", "null"], "items": {"type": "string"}},
                                    "measure_all_of": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "field": {"type": "string"},
                                                "op": {"type": "string"},
                                                "value": {},
                                                "entity_name": {"type": ["string", "null"]},
                                            },
                                            "required": ["field", "op"],
                                        }
                                    },
                                    "exempt_all_of": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "field": {"type": "string"},
                                                "op": {"type": "string"},
                                                "value": {},
                                                "entity_name": {"type": ["string", "null"]},
                                            },
                                            "required": ["field", "op"],
                                        }
                                    },
                                },
                                "required": ["rule_id", "clause_id", "source_segment_ids", "kind"],
                                "additionalProperties": False,
                            }
                        },
                        "review": {
                            "type": ["object", "null"],
                            "properties": {
                                "reason": {"type": "string"},
                                "question": {"type": "string"},
                                "assumption": {"type": ["string", "null"]},
                            },
                            "required": ["reason", "question"],
                        }
                    },
                    "required": ["clause_id", "segment_ids", "classification", "rationale"],
                    "additionalProperties": False,
                }
            }
        },
        "required": ["clauses"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Flat → nested conversion
# ---------------------------------------------------------------------------

def _flat_to_clause_record(raw_clause: dict, markdown: str, seg_map: dict) -> ClauseRecord:
    """Convert flat LLM output to full ClauseRecord with nested Selectors/Bounds."""
    from copilot.extract.schema import (
        Bound, ClauseRecord, Predicate, ReviewItem, Rule, Selector,
    )
    from datetime import date

    valid_group_bys = {"none", "issuer", "security", "fund", "sector", "account", "asset_class"}
    valid_not_constraints = {"objective", "procedure", "background", "scope", "review_process", "structural", "other"}
    not_constraint_synonyms = {
        "administrative": "procedure",
        "purpose": "objective",
        "acknowledgment": "procedure",
        "definition": "background",
        "reporting": "procedure",
        "heading": "structural",
    }

    clause_seg_ids = raw_clause.get("segment_ids", [])
    combined_text = " ".join(seg_map[s].text for s in clause_seg_ids if s in seg_map).lower()
    rationale_text = str(raw_clause.get("rationale", "")).lower()

    rules = []
    for raw_rule in raw_clause.get("rules", []):
        # Normalize Predicate kwargs to valid fields only
        def _clean_pred(p: dict) -> dict:
            d = dict(p)
            if d.get("field") == "entity_name":
                d["field"] = "issuer_name"
                if not d.get("value") and d.get("entity_name"):
                    d["value"] = d.get("entity_name")
            return {
                k: v for k, v in d.items()
                if k in ("field", "op", "value", "entity_name", "resolved_issuer_ids")
            }

        # Reconstruct Selector from flat arrays
        measure_preds = [_clean_pred(p) for p in (raw_rule.get("measure_all_of") or [])]
        exempt_preds = [_clean_pred(p) for p in (raw_rule.get("exempt_all_of") or [])]

        measure = Selector(all_of=[Predicate(**p) for p in measure_preds])
        exempt = Selector(any_of=[[Predicate(**p)] for p in exempt_preds]) if exempt_preds else None

        def _bound(val, unit, inclusive) -> Optional[Bound]:
            if val is None:
                return None
            unit_str = str(unit or "fraction").lower().strip()
            if unit_str in ("year", "yr"):
                unit_str = "years"
            elif unit_str in ("month", "mo"):
                unit_str = "months"
            elif unit_str in ("pct", "percentage"):
                unit_str = "percent"
            elif unit_str not in ("percent", "fraction", "usd", "months", "years", "rating"):
                unit_str = "fraction"
            return Bound(value=val, unit=unit_str, inclusive=True if inclusive is None else inclusive)

        grp = raw_rule.get("group_by", "none")
        if grp not in valid_group_bys:
            grp = "none"

        lt_basis_raw = str(raw_rule.get("lookthrough_basis") or "explicit").lower()
        if "explicit" in lt_basis_raw or "direct" in lt_basis_raw:
            lt_basis = "explicit"
        else:
            lt_basis = "assumed"

        valid_data_reqs = {
            "transactions", "duration", "performance_history",
            "liquidity_attributes", "leverage_data", "client_cashflows"
        }
        data_reqs = [
            req for req in (raw_rule.get("data_requirements") or [])
            if req in valid_data_reqs
        ]


        rule = Rule(
            rule_id=raw_rule["rule_id"],
            clause_id=raw_rule["clause_id"],
            source_segment_ids=raw_rule["source_segment_ids"],
            kind=raw_rule.get("kind", "asset_allocation"),
            measure=measure,
            group_by=grp,
            aggregate=raw_rule.get("aggregate", False),
            exempt=exempt,
            denominator=raw_rule.get("denominator", "portfolio"),
            lookthrough=raw_rule.get("lookthrough", "none"),
            lookthrough_basis=lt_basis,
            severity=raw_rule.get("severity", "hard"),
            min=_bound(raw_rule.get("min_value"), raw_rule.get("min_unit"), raw_rule.get("min_inclusive")),
            max=_bound(raw_rule.get("max_value"), raw_rule.get("max_unit"), raw_rule.get("max_inclusive")),
            informational_target=raw_rule.get("informational_target"),
            deadline=date.fromisoformat(raw_rule["deadline"]) if raw_rule.get("deadline") else None,
            effective_from=date.fromisoformat(raw_rule["effective_from"]) if raw_rule.get("effective_from") else None,
            data_requirements=data_reqs,
            scope_accounts=raw_rule.get("scope_accounts"),
        )
        rules.append(rule)

    classification = raw_clause.get("classification", "not_constraint")
    if classification == "rule" and not rules:
        classification = "not_constraint"

    not_constraint_type = raw_clause.get("not_constraint_type")
    if classification == "not_constraint":
        if not not_constraint_type:
            not_constraint_type = "other"
        elif not_constraint_type not in valid_not_constraints:
            not_constraint_type = not_constraint_synonyms.get(not_constraint_type.lower(), "other")

        if not_constraint_type == "other":
            combined_context = (str(raw_clause.get("rationale", "")) + " " + combined_text).lower()
            if "scope" in combined_context:
                not_constraint_type = "scope"
            elif "risk tolerance" in combined_context or "horizon" in combined_context or "objective" in combined_context or "goal" in combined_context:
                not_constraint_type = "objective"
            elif "procedure" in combined_context or "process" in combined_context or "review" in combined_context or "meeting" in combined_context:
                not_constraint_type = "procedure"
            elif "background" in combined_context or "purpose" in combined_context:
                not_constraint_type = "background"
    else:
        not_constraint_type = None

    valid_review_reasons = {
        "missing_threshold", "ambiguous_term", "identification_ambiguity",
        "internal_conflict", "needs_client_data", "unresolvable_entity",
        "extraction_invalid", "other",
    }
    raw_review = raw_clause.get("review")
    review = None
    if raw_review:
        r_reason = raw_review.get("reason", "other")
        if r_reason not in valid_review_reasons:
            r_reason = "other"
        review = ReviewItem(
            reason=r_reason,
            question=raw_review.get("question", "Please review this clause."),
            assumption=raw_review.get("assumption"),
        )

    return ClauseRecord(
        clause_id=raw_clause["clause_id"],
        segment_ids=raw_clause["segment_ids"],
        classification=classification,
        not_constraint_type=not_constraint_type,
        rationale=raw_clause.get("rationale", ""),
        rules=rules,
        review=review,
        injection_suspected=raw_clause.get("injection_suspected", False),
    )


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------

async def extract_ips(hh_id: str, ips_path: Path, run_index: int = 0, model: Optional[str] = None) -> dict:
    """
    Full extraction pipeline for one IPS.
    Returns a dict suitable for JSON serialization.
    """
    import os

    logger.info("Starting extraction: %s (%s)", hh_id, ips_path)

    # --- Load IPS ---
    markdown = ips_path.read_text(encoding="utf-8")
    ips_hash = hashlib.sha256(markdown.encode()).hexdigest()[:16]

    # --- Segment ---
    segments = segment_document(markdown)
    coverage_errors = verify_coverage(segments, markdown)
    if coverage_errors:
        logger.warning("Coverage issues in segmenter for %s: %s", hh_id, coverage_errors[:3])

    seg_map = {s.id: s for s in segments}
    all_seg_ids = set(seg_map.keys())

    logger.info("%s: %d segments", hh_id, len(segments))

    # --- Load model config ---
    with (CONFIG_DIR / "models.yaml").open() as f:
        model_cfg = yaml.safe_load(f)
    extraction_cfg = model_cfg.get("extraction", {})
    selected_model = (
        model
        or os.environ.get("COPILOT_MODEL")
        or os.environ.get("LLM_MODEL")
        or extraction_cfg.get("model", "gpt-5.4-nano")
    )
    temperature = extraction_cfg.get("temperature")
    max_tokens = extraction_cfg.get("max_tokens", 8192)

    # --- Build prompt ---
    response_schema = _build_response_schema()
    messages = build_prompt(segments, hh_id, response_schema)

    # --- Data dict hash for cache key ---
    data_dict_str = data_dictionary(hh_id)
    data_dict_hash = hashlib.sha256(data_dict_str.encode()).hexdigest()[:16]

    # --- LLM call (possibly cached) ---
    llm_result = await call_llm(
        model=selected_model,
        messages=messages,
        response_schema=response_schema,
        temperature=temperature,
        max_tokens=max_tokens,
        run_index=run_index,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        data_dict_hash=data_dict_hash,
    )

    raw_response = llm_result["response"]
    raw_clauses = raw_response.get("clauses", [])

    # --- Parse into ClauseRecord objects ---
    clause_records: List[ClauseRecord] = []
    parse_errors = []
    for raw_clause in raw_clauses:
        try:
            cr = _flat_to_clause_record(raw_clause, markdown, seg_map)
            clause_records.append(cr)
        except Exception as e:
            parse_errors.append(f"Parse error in clause {raw_clause.get('clause_id', '?')}: {e}")
            logger.warning("Parse error: %s", e)

    # Ensure all structural/heading segments are accounted for as not_constraint
    covered_sids = {sid for cr in clause_records for sid in cr.segment_ids}
    for seg in segments:
        if seg.id not in covered_sids:
            clean_text = seg.text.strip()
            if (
                seg.kind == "heading"
                or clean_text.startswith("#")
                or clean_text in ("---", "***", "___")
                or clean_text.startswith("|")
                or "signature" in clean_text.lower()
                or clean_text.startswith("/s/")
            ):
                clause_records.append(
                    ClauseRecord(
                        clause_id=f"HDR-{seg.id}",
                        segment_ids=[seg.id],
                        classification="not_constraint",
                        not_constraint_type="structural",
                        rationale=f"Document section heading / structure: {clean_text[:60]}",
                    )
                )

    # --- L3: Coverage ---
    ok, cov_errors = check_coverage(clause_records, all_seg_ids)
    if not ok:
        missing_ids = all_seg_ids - {sid for cr in clause_records for sid in cr.segment_ids}
        clause_records = add_uncovered_segments(clause_records, missing_ids, segments)
        logger.warning("%s: %d coverage issues — auto-labelled %d segments", hh_id, len(cov_errors), len(missing_ids))

    # --- L4: Selector grounding ---
    valid_fields = {row_key for s in securities() for row_key in s.keys()}
    valid_enums: dict = {
        "asset_class": {s.get("asset_class", "") for s in securities() if s.get("asset_class")},
        "sub_asset_class": {s.get("sub_asset_class", "") for s in securities() if s.get("sub_asset_class")},
        "security_type": {s.get("security_type", "") for s in securities() if s.get("security_type")},
    }
    grounding_warnings = ground_selectors(clause_records, valid_fields, valid_enums)
    for w in grounding_warnings:
        logger.warning("%s: %s", hh_id, w)

    # --- L5: Entity resolution ---
    i_lookup = issuer_lookup()
    a_lookup = account_lookup(hh_id)
    entity_warnings = resolve_entities(clause_records, i_lookup, a_lookup)
    for w in entity_warnings:
        logger.warning("%s entity: %s", hh_id, w)

    # --- Normalise bounds (percent → fraction) ---
    normalise_bounds(clause_records)

    # --- L6: Quote attachment + verification ---
    quote_errors = attach_and_verify_quotes(clause_records, segments, markdown)
    for e in quote_errors:
        logger.error("%s quote error (BUG): %s", hh_id, e)

    # --- Serialise ---
    result = {
        "ips_id": hh_id,
        "ips_hash": ips_hash,
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": selected_model,
        "cache_hit": llm_result.get("cache_hit", False),
        "usage": llm_result.get("usage", {}),
        "cost_usd": llm_result.get("cost_usd", 0.0),
        "latency_s": llm_result.get("latency_s", 0.0),
        "clauses": [json.loads(cr.model_dump_json()) for cr in clause_records],
        "parse_errors": parse_errors,
        "grounding_warnings": grounding_warnings,
        "entity_warnings": entity_warnings,
        "quote_errors": quote_errors,
        "segment_count": len(segments),
        "summary": {
            "rules": sum(1 for cr in clause_records if cr.classification == "rule"),
            "needs_review": sum(1 for cr in clause_records if cr.classification == "needs_review"),
            "not_constraints": sum(1 for cr in clause_records if cr.classification == "not_constraint"),
            "total_clauses": len(clause_records),
        }
    }

    logger.info(
        "%s extraction complete: %d rules, %d review, %d not_constraint",
        hh_id,
        result["summary"]["rules"],
        result["summary"]["needs_review"],
        result["summary"]["not_constraints"],
    )

    return result
