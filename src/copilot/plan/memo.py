"""
Memo generator and lint tool for the plan memo.

build_plan_facts()  → plan_facts.json (every number + its source rule/finding/segment)
render_memo()       → plan_memo.md rendered from a Jinja2 template
lint_memo()         → check every number in the memo is in plan_facts.json
"""

from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Build plan_facts.json
# ---------------------------------------------------------------------------

def build_plan_facts(
    hh_id: str,
    findings: Dict,
    trades: List[Any],
    extraction: Optional[Dict],
    is_clean: bool = True,
) -> Dict[str, Any]:
    """
    Build a structured facts dict that every memo claim must trace to.
    Returns a dict suitable for JSON serialization.
    """
    all_findings = findings.get("findings", [])
    confirmed_breach_ids = set(findings.get("confirmed_breaches", []))

    # Separate findings by status
    breaches = [f for f in all_findings if f["rule_id"] in confirmed_breach_ids]
    needs_review = [f for f in all_findings if f["status"] == "needs_review"]
    cannot_evaluate = [f for f in all_findings if f["status"] == "cannot_evaluate"]
    passes = [f for f in all_findings if f["status"] == "pass" and f["rule_id"] not in confirmed_breach_ids]
    tracked = [f for f in all_findings if f.get("tracked")]

    # Collect review items from extraction (substantive policy questions for the PM)
    review_items = []
    if extraction:
        for clause in extraction.get("clauses", []):
            if clause.get("classification") == "needs_review":
                review = clause.get("review", {})
                if review.get("reason") == "extraction_invalid":
                    continue
                review_items.append({
                    "clause_id": clause["clause_id"],
                    "reason": review.get("reason", ""),
                    "question": review.get("question", ""),
                    "assumption": review.get("assumption"),
                })

    # Trade facts
    trade_facts = []
    for t in trades:
        trade_facts.append({
            "account_id": t.account_id,
            "security_id": t.security_id,
            "side": t.side,
            "quantity": t.quantity,
        })

    # Summary counts
    summary = findings.get("summary", {})
    portfolio_mv = findings.get("portfolio_mv", 0)
    snapshot_date = findings.get("snapshot_date", "2026-09-25")

    facts = {
        "household": hh_id,
        "snapshot_date": snapshot_date,
        "portfolio_mv": portfolio_mv,
        "is_clean": is_clean,
        "breaches": breaches,
        "needs_review_findings": needs_review,
        "cannot_evaluate_findings": cannot_evaluate,
        "pass_findings": passes,
        "tracked_findings": tracked,
        "review_items": review_items,
        "trades": trade_facts,
        "summary": summary,
        "all_findings": all_findings,
    }

    return facts


# ---------------------------------------------------------------------------
# Render memo from Jinja2 template
# ---------------------------------------------------------------------------

def render_memo(hh_id: str, facts: Dict) -> str:
    """Render plan_memo.md from the Jinja2 template."""
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        def _fmt_pct(v):
            if v is None:
                return "N/A"
            if isinstance(v, dict):
                v = max(v.get("__all__", {}).values(), default=0.0)
            try:
                return f"{float(v)*100:.2f}%"
            except (TypeError, ValueError):
                return "N/A"

        def _fmt_usd(v):
            if v is None:
                return "N/A"
            if isinstance(v, dict):
                v = max(v.get("__all__", {}).values(), default=0.0)
            try:
                return f"${float(v):,.0f}"
            except (TypeError, ValueError):
                return "N/A"

        def _fmt_qty(v):
            if v is None:
                return "N/A"
            try:
                f_val = float(v)
                return f"{int(f_val):,}" if f_val == int(f_val) else f"{f_val:,.2f}"
            except (TypeError, ValueError):
                return str(v)

        env.filters["pct"] = _fmt_pct
        env.filters["usd"] = _fmt_usd
        env.filters["qty"] = _fmt_qty

        template = env.get_template("plan_memo.md.j2")
        return template.render(hh_id=hh_id, facts=facts)

    except Exception as e:
        logger.error("Template rendering failed: %s — using fallback memo", e)
        return _fallback_memo(hh_id, facts)


def _fallback_memo(hh_id: str, facts: Dict) -> str:
    """Generate a simple memo without Jinja2 if template fails."""
    lines = [
        f"# Remediation Plan — {hh_id}",
        f"**Snapshot date:** {facts.get('snapshot_date', '2026-09-25')}  ",
        f"**Portfolio MV:** ${facts.get('portfolio_mv', 0):,.0f}  ",
        f"**Prepared by:** Policy Remediation Copilot v1.0",
        "",
        "## 1. Findings",
        "",
    ]

    breaches = facts.get("breaches", [])
    if not breaches:
        lines.append("No confirmed breaches.")
    else:
        for f in breaches:
            measured = f.get("measured")
            limit = f.get("limit", {})
            lines.append(f"### BREACH: {f['rule_id']} (clause {f['clause_id']})")
            lines.append("")
            if f.get("source_quotes"):
                lines.append(f"> {f['source_quotes'][0]}")
                lines.append("")
            if measured is not None:
                if isinstance(measured, dict):
                    worst = max(measured.get("__all__", {}).values(), default=0)
                    lines.append(f"**Measured:** {worst*100:.2f}%")
                else:
                    lines.append(f"**Measured:** {float(measured)*100:.2f}%")
            if "max" in limit:
                lines.append(f"**Limit (max):** {float(limit['max'])*100:.2f}%")
            if "min" in limit:
                lines.append(f"**Limit (min):** {float(limit['min'])*100:.2f}%")
            if f.get("contributors"):
                lines.append("")
                lines.append("**Contributors:**")
                for c in f["contributors"][:5]:
                    lines.append(f"- {c['security_id']} (acct {c['account_id']}): ${c['mv']:,.0f}")
            lines.append("")

    lines += ["", "## 2. Trades", ""]
    trades = facts.get("trades", [])
    if not trades:
        lines.append("No trades proposed.")
    else:
        lines.append("| Account | Security | Side | Quantity |")
        lines.append("|---------|----------|------|----------|")
        for t in trades:
            qty = int(t["quantity"]) if float(t["quantity"]) == int(float(t["quantity"])) else t["quantity"]
            lines.append(f"| {t['account_id']} | {t['security_id']} | {t['side']} | {qty:,} |")

    lines += ["", "## 3. Left Alone", ""]
    nr = facts.get("needs_review_findings", [])
    ce = facts.get("cannot_evaluate_findings", [])
    tracked = facts.get("tracked_findings", [])

    if nr:
        lines.append("**Pending PM review (not traded):**")
        for f in nr:
            lines.append(f"- {f['rule_id']}: {f.get('reason', 'needs_review')}")
    if ce:
        lines.append("")
        lines.append("**Cannot evaluate (data unavailable):**")
        for f in ce:
            lines.append(f"- {f['rule_id']}: {f.get('reason', 'missing data')}")
    if tracked:
        lines.append("")
        lines.append("**Tracked targets (not yet due):**")
        for f in tracked:
            lines.append(f"- {f['rule_id']}: measured {f.get('measured', 'N/A')}")

    lines += ["", "## 4. Open Questions", ""]
    review_items = facts.get("review_items", [])
    if review_items:
        for i, item in enumerate(review_items, 1):
            lines.append(f"{i}. **{item['clause_id']}:** {item['question']}")
            if item.get("assumption"):
                lines.append(f"   *(Assumption if unanswered: {item['assumption']})*")
            lines.append("")
    else:
        lines.append("No open questions.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Memo lint
# ---------------------------------------------------------------------------

def lint_memo(memo_path: Path, facts_path: Path) -> List[str]:
    """
    Check that every number in the memo traces to plan_facts.json.
    Returns a list of error strings (empty = PASS).
    """
    errors: List[str] = []

    memo_text = memo_path.read_text()
    facts = json.loads(facts_path.read_text())

    # Collect all numbers from facts
    fact_numbers = _extract_numbers_from_facts(facts)

    # Extract numbers from memo (percentages, dollar amounts, quantities)
    memo_numbers = _extract_numbers_from_memo(memo_text)

    # Check each memo number appears in facts (with tolerance)
    for num_str, line_num in memo_numbers:
        try:
            raw_str = num_str.replace(",", "").replace("$", "")
            is_pct = "%" in raw_str
            num = float(raw_str.replace("%", ""))
        except ValueError:
            continue

        matched = _number_in_facts(num, fact_numbers)
        if not matched and is_pct:
            matched = _number_in_facts(num / 100.0, fact_numbers)

        if not matched:
            errors.append(f"Line {line_num}: number {num_str!r} not found in plan_facts.json")

    # Check every trade in memo appears in facts
    fact_trade_ids = {
        (t["security_id"], t["side"]) for t in facts.get("trades", [])
    }
    memo_trades = re.findall(r"\|\s*(\w[\w-]*)\s*\|\s*(buy|sell)\s*\|", memo_text, re.IGNORECASE)
    for sid, side in memo_trades:
        if (sid, side.lower()) not in fact_trade_ids:
            errors.append(f"Trade {sid} {side} in memo not in plan_facts.json")

    return errors


def _extract_numbers_from_facts(facts: Any) -> List[float]:
    """Recursively extract all numeric values from facts dict, including date strings."""
    numbers = []
    if isinstance(facts, dict):
        for v in facts.values():
            numbers.extend(_extract_numbers_from_facts(v))
    elif isinstance(facts, list):
        for item in facts:
            numbers.extend(_extract_numbers_from_facts(item))
    elif isinstance(facts, (int, float)) and facts is not True and facts is not False:
        numbers.append(float(facts))
    elif isinstance(facts, str):
        # Extract numeric components from string facts (including comma-separated numbers)
        for m in re.finditer(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", facts):
            try:
                numbers.append(float(m.group().replace(",", "")))
            except ValueError:
                pass
    return numbers


def _extract_numbers_from_memo(memo_text: str) -> List[tuple]:
    """Extract numbers with their line numbers from memo text."""
    numbers = []
    skip_section = False
    for line_num, line in enumerate(memo_text.split("\n"), 1):
        line_stripped = line.strip()

        # Skip markdown section headers (e.g. "## 1. Findings", "## 2. Trades")
        if line_stripped.startswith("#"):
            if line_stripped.startswith("## 4. Open Questions") or line_stripped.startswith("## Data Freshness"):
                skip_section = True
            elif skip_section and line_stripped.startswith("## "):
                skip_section = False
            continue

        if skip_section:
            continue

        # Skip blockquotes quoting legal IPS text
        if line_stripped.startswith(">"):
            continue

        # Match percentages like 71.56%, dollar amounts like $285,000, plain numbers
        for match in re.finditer(r"\$[\d,]+(?:\.\d+)?|\d+(?:\.\d+)?%|\b\d+(?:,\d{3})*(?:\.\d+)?\b", line):
            start, end = match.span()
            matched_str = match.group()

            # Skip numbers that are part of an ISO date or hyphenated identifier (B-HALDEN-2031)
            prefix = line[max(0, start - 1):start]
            suffix = line[end:end + 1]
            if prefix in ("-", "_") or suffix in ("-", "_"):
                continue

            numbers.append((matched_str, line_num))
    return numbers


def _number_in_facts(num: float, fact_numbers: List[float], tol: float = 0.01) -> bool:
    """Check if num is approximately in fact_numbers."""
    for fn in fact_numbers:
        if abs(fn - num) <= tol or (fn != 0 and abs((fn - num) / fn) <= tol):
            return True
    return False
