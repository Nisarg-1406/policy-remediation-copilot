"""
Pydantic v2 schema for extraction output.

Invariant I3: no hard-coded tickers, section numbers, thresholds.
The schema defines a vocabulary of rule kinds; the LLM fills in the parameters.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Atomic building blocks
# ---------------------------------------------------------------------------

class Predicate(BaseModel):
    """One test on a security (or derived) attribute."""
    field: str
    """Column of securities.csv or a derived field:
    rating_bucket, years_to_maturity, issuer_fossil_pct, is_fund"""
    op: Literal["eq", "ne", "in", "not_in", "gte", "gt", "lte", "lt", "is_blank", "is_not_blank"]
    value: Optional[Union[str, float, List[str]]] = None
    entity_name: Optional[str] = None
    """Named entity (e.g. 'Kestrel Semiconductor') — code resolves to issuer_id(s)."""
    # After entity resolution, code populates this:
    resolved_issuer_ids: Optional[List[str]] = None

    model_config = {"extra": "ignore"}


class Selector(BaseModel):
    """AND of predicates, with optional OR groups."""
    all_of: List[Predicate] = Field(default_factory=list)
    any_of: List[List[Predicate]] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class Bound(BaseModel):
    value: float
    unit: Literal["percent", "fraction", "usd", "months", "years", "rating"]
    inclusive: bool = True

    def as_fraction(self) -> float:
        """Return the bound value normalized to a fraction [0,1] where applicable."""
        if self.unit == "percent":
            return self.value / 100.0
        elif self.unit == "fraction":
            return self.value
        return self.value  # usd, months, years — returned as-is


class ReserveSpec(BaseModel):
    months: float
    basis: Literal["essential", "total"]
    net_of: List[Literal["pension", "social_security"]] = Field(default_factory=list)
    reserve_assets: Optional[Selector] = None


# ---------------------------------------------------------------------------
# Rule kinds (closed vocabulary — add a new kind via the checklist in AGENTS.md)
# ---------------------------------------------------------------------------

RULE_KIND = Literal[
    "allocation_range",    # equity 55–75%, cash 0–10%
    "exposure_cap",        # no issuer > 5%, no single fund > 15%
    "exclusion",           # no fossil fuel companies, no crypto
    "credit_quality",      # bonds A- or better
    "reserve_requirement", # 12 months net expenses in cash/T-bills
    "instrument_allowlist",# fixed income only via bond funds or Treasuries
    "tracked_target",      # Kestrel < 15% by 2028-12-31
    "needs_external_data", # duration, 3-yr performance — checker returns CANNOT_EVALUATE
]


class Rule(BaseModel):
    rule_id: str
    clause_id: str
    source_segment_ids: List[str]
    kind: RULE_KIND
    measure: Selector = Field(default_factory=Selector)
    """Population of securities being measured (numerator)."""
    group_by: Literal["none", "issuer", "security", "fund", "sector", "account", "asset_class"] = "none"
    aggregate: bool = False
    """True = one number for the whole selected set (not per-group)."""
    exempt: Optional[Selector] = None
    denominator: Literal["portfolio", "asset_class_of_selection", "account", "equity_sleeve"] = "portfolio"
    scope_accounts: Optional[List[str]] = None
    """Raw account descriptors — code resolves to account_ids. None = default IPS scope."""
    lookthrough: Literal["none", "funds"] = "none"
    lookthrough_basis: Literal["explicit", "assumed"] = "explicit"
    multi_asset_treatment: Optional[Literal["predominant", "equity_slice"]] = None
    min: Optional[Bound] = None
    max: Optional[Bound] = None
    informational_target: Optional[float] = None
    """Never enforced; used in memo for context."""
    reserve: Optional[ReserveSpec] = None
    effective_from: Optional[date] = None
    deadline: Optional[date] = None
    data_requirements: List[
        Literal["transactions", "duration", "performance_history",
                "liquidity_attributes", "leverage_data", "client_cashflows"]
    ] = Field(default_factory=list)
    severity: Literal["hard", "advisory"] = "hard"
    readings: Optional[List["Rule"]] = None
    """Alternative interpretations for ambiguous rules (e.g. fossil threshold variants)."""

    # Set by code (validators/entity resolution), not LLM
    source_quotes: List[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def check_bounds_order(self) -> "Rule":
        if self.min is not None and self.max is not None:
            mn = self.min.as_fraction()
            mx = self.max.as_fraction()
            if self.min.unit == self.max.unit and mn > mx:
                raise ValueError(f"Rule {self.rule_id}: min ({mn}) > max ({mx})")
        return self

    @model_validator(mode="after")
    def check_deadline_order(self) -> "Rule":
        if self.effective_from and self.deadline:
            if self.effective_from > self.deadline:
                raise ValueError(
                    f"Rule {self.rule_id}: effective_from ({self.effective_from}) "
                    f"after deadline ({self.deadline})"
                )
        return self


# ---------------------------------------------------------------------------
# Review item and not-constraint
# ---------------------------------------------------------------------------

REVIEW_REASON = Literal[
    "missing_threshold",
    "ambiguous_term",
    "identification_ambiguity",
    "internal_conflict",
    "needs_client_data",
    "unresolvable_entity",
    "extraction_invalid",
    "other",
]


class ReviewItem(BaseModel):
    reason: REVIEW_REASON
    question: str
    """PM-facing, specific, offers example answers."""
    assumption: Optional[str] = None
    """What we'd assume if the PM doesn't answer (used for provisional measurement only)."""
    provisional_rule: Optional[Rule] = None
    """Measured for information, never traded on (unless robust-breach exception applies)."""

    model_config = {"extra": "ignore"}


NOT_CONSTRAINT_TYPE = Literal[
    "objective", "procedure", "background", "scope", "review_process", "structural", "other"
]


class ClauseRecord(BaseModel):
    clause_id: str
    segment_ids: List[str]
    classification: Literal["rule", "needs_review", "not_constraint"]
    not_constraint_type: Optional[NOT_CONSTRAINT_TYPE] = None
    rules: List[Rule] = Field(default_factory=list)
    review: Optional[ReviewItem] = None
    rationale: Annotated[str, Field(max_length=300)]
    """≤ 30 words explaining the classification."""
    injection_suspected: bool = False
    """True if this segment appeared to contain prompt-injection content."""

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def check_consistency(self) -> "ClauseRecord":
        if self.classification == "rule" and not self.rules:
            raise ValueError(f"Clause {self.clause_id}: classification='rule' but rules=[]")
        if self.classification == "needs_review" and self.review is None:
            raise ValueError(f"Clause {self.clause_id}: classification='needs_review' but review=None")
        if self.classification == "not_constraint" and self.not_constraint_type is None:
            raise ValueError(
                f"Clause {self.clause_id}: classification='not_constraint' requires not_constraint_type"
            )
        return self


class ExtractionResult(BaseModel):
    """Complete output from one IPS extraction."""
    ips_id: str
    ips_hash: str
    schema_version: str = "1.0"
    prompt_version: str = "1.0"
    clauses: List[ClauseRecord]
    model_config = {"extra": "ignore"}
