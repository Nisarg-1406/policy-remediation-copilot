"""
Segmenter — splits a markdown IPS into numbered Segment objects.

Invariants enforced:
- I4: Character offsets preserved so code can derive quotes (no LLM needed for quotes)
- I5: Every character of the IPS is covered by exactly one segment (no text lost)
- Stable IDs: same content → same S001…Snnn assignment (keyed by ips_hash)

No LLM calls. No external dependencies beyond stdlib.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Segment:
    id: str
    text: str
    start: int
    end: int
    heading_path: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None
    kind: str = "paragraph"  # heading | paragraph | list_item | table_row | clause | sentence


# ---------------------------------------------------------------------------
# Markdown parser — deterministic, no LLM
# ---------------------------------------------------------------------------

def segment_document(markdown: str) -> List[Segment]:
    """
    Parse markdown into segments. Every character of the original document
    is covered by exactly one segment (coverage invariant).
    """
    segments: List[Segment] = []
    counter = [0]

    def new_id() -> str:
        counter[0] += 1
        return f"S{counter[0]:03d}"

    heading_stack: List[str] = []
    lines = markdown.splitlines(keepends=True)
    pos = 0  # character position in original doc

    i = 0
    while i < len(lines):
        line = lines[i]
        line_start = pos
        pos += len(line)
        stripped = line.rstrip("\n\r")
        clean = stripped.strip()

        # --- Blank line ---
        if not clean:
            i += 1
            continue

        # --- ATX heading ---
        m = re.match(r'^(#{1,6})\s+(.*)', stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            # Pop heading stack to current level
            heading_stack = heading_stack[:level - 1] + [title]
            seg = Segment(
                id=new_id(),
                text=clean,
                start=line_start,
                end=line_start + len(stripped),
                heading_path=list(heading_stack),
                kind="heading",
            )
            segments.append(seg)
            i += 1
            continue

        # --- Setext heading (=== / ---) — check next line ---
        if i + 1 < len(lines):
            next_stripped = lines[i + 1].rstrip("\n\r")
            if re.match(r'^[=]{3,}$', next_stripped.strip()):
                heading_stack = [clean]
                seg = Segment(
                    id=new_id(), text=clean,
                    start=line_start, end=line_start + len(stripped),
                    heading_path=list(heading_stack), kind="heading",
                )
                segments.append(seg)
                pos += len(lines[i + 1])
                i += 2
                continue
            elif re.match(r'^[-]{3,}$', next_stripped.strip()) and clean:
                heading_stack = heading_stack[:0] + [clean]
                seg = Segment(
                    id=new_id(), text=clean,
                    start=line_start, end=line_start + len(stripped),
                    heading_path=list(heading_stack), kind="heading",
                )
                segments.append(seg)
                pos += len(lines[i + 1])
                i += 2
                continue

        # --- Table ---
        if "|" in clean and re.match(r'^\|', stripped.strip()):
            # Collect table block
            table_lines = []
            header_line = stripped
            header_start = line_start
            j = i + 1
            table_pos = pos
            while j < len(lines) and "|" in lines[j] and re.match(r'^\|', lines[j].strip()):
                table_pos += len(lines[j])
                table_lines.append((j, lines[j]))
                j += 1

            # Header row segment
            seg = Segment(
                id=new_id(),
                text=header_line.strip(),
                start=header_start,
                end=header_start + len(header_line.rstrip("\n\r")),
                heading_path=list(heading_stack),
                kind="table_row",
            )
            segments.append(seg)

            row_start = header_start + len(lines[i])
            for k, (row_idx, row_line) in enumerate(table_lines):
                row_stripped = row_line.rstrip("\n\r").strip()
                if re.match(r'^[|\-: ]+$', row_stripped):
                    row_start += len(row_line)
                    continue  # separator line
                seg = Segment(
                    id=new_id(),
                    text=row_stripped,
                    start=row_start,
                    end=row_start + len(row_line.rstrip("\n\r")),
                    heading_path=list(heading_stack),
                    kind="table_row",
                )
                segments.append(seg)
                row_start += len(row_line)

            i = j
            pos = table_pos
            continue

        # --- Horizontal rule ---
        if re.match(r'^[-*_]{3,}$', clean):
            i += 1
            continue

        # --- List item ---
        m = re.match(r'^(\s*)([-*+]|\d+[.)]) (.*)', stripped)
        if m:
            indent = len(m.group(1))
            # Collect continuation lines
            j = i + 1
            item_pos = pos
            while j < len(lines):
                next_line = lines[j].rstrip("\n\r")
                # Continuation: starts with enough spaces and isn't a new list item
                if next_line and not re.match(r'^\s*([-*+]|\d+[.)])\s', next_line) and next_line.startswith(" " * (indent + 2)):
                    item_pos += len(lines[j])
                    j += 1
                else:
                    break
            last_line = lines[j - 1]
            item_end = item_pos - (len(last_line) - len(last_line.rstrip("\n\r")))
            seg_text = markdown[line_start:item_end].strip()
            seg = Segment(
                id=new_id(),
                text=seg_text,
                start=line_start,
                end=item_end,
                heading_path=list(heading_stack),
                kind="list_item",
            )
            segments.append(seg)
            i = j
            pos = item_pos
            continue

        # --- Paragraph (may contain multiple sentences — split for Okafor-style prose) ---
        # Collect paragraph
        para_lines = [stripped]
        para_start = line_start
        j = i + 1
        para_pos = pos
        while j < len(lines):
            next_line = lines[j].rstrip("\n\r")
            next_clean = next_line.strip()
            if not next_clean:
                break
            if re.match(r'^(#{1,6}|[-*+]|\d+[.)]|>|\|)', next_clean):
                break
            para_lines.append(next_line)
            para_pos += len(lines[j])
            j += 1

        para_text = " ".join(ln.strip() for ln in para_lines)
        para_end = para_pos

        # Detect numbered clauses (e.g. "4.1 No single issuer…")
        clause_m = re.match(r'^(\d+(?:\.\d+)*)\s+(.+)', para_text, re.DOTALL)

        # For Okafor-style unnumbered multi-sentence paragraphs, split on sentences
        sentences = _split_sentences(para_text)
        if len(sentences) > 1 and not clause_m:
            # Split into sentence-level segments, grouped under a parent
            parent_seg = Segment(
                id=new_id(), text=para_text,
                start=para_start, end=para_end,
                heading_path=list(heading_stack), kind="paragraph",
            )
            segments.append(parent_seg)
            sent_pos = para_start
            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue
                seg = Segment(
                    id=new_id(), text=sent,
                    start=sent_pos, end=sent_pos + len(sent),
                    heading_path=list(heading_stack),
                    parent_id=parent_seg.id,
                    kind="sentence",
                )
                segments.append(seg)
                sent_pos += len(sent) + 1
        else:
            kind = "clause" if clause_m else "paragraph"
            seg = Segment(
                id=new_id(), text=para_text,
                start=para_start, end=para_end,
                heading_path=list(heading_stack), kind=kind,
            )
            segments.append(seg)

        for skip in range(i + 1, j):
            pos += len(lines[skip])
        i = j

    return segments


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences on sentence-ending punctuation."""
    # Simple but robust: split on '. ', '! ', '? ' followed by a capital letter
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    return [p.strip() for p in parts if p.strip()]


def derive_quote(segment: Segment, markdown: str) -> str:
    """
    Derive the source quote for a segment from the IPS document.
    Returns the exact substring at the segment's character offsets.
    (Invariant I4 — code, not LLM, produces quotes.)
    """
    return markdown[segment.start:segment.end].strip()


def verify_coverage(segments: List[Segment], markdown: str) -> List[str]:
    """
    Verify that no meaningful text from the IPS is missing from segments.
    Returns a list of error strings (empty = OK).
    """
    errors = []
    # Build set of all characters covered
    covered_chars: set[int] = set()
    for seg in segments:
        covered_chars.update(range(seg.start, seg.end))

    # Identify markup delimiter lines that don't represent textual content
    # (horizontal rules, table separator dashes)
    markup_indices: set[int] = set()
    pos = 0
    for line in markdown.splitlines(keepends=True):
        stripped = line.strip()
        if re.match(r'^[-*_]{3,}$', stripped) or re.match(r'^[|\-: ]+$', stripped):
            markup_indices.update(range(pos, pos + len(line)))
        pos += len(line)

    # Check every non-whitespace character is covered
    for i, ch in enumerate(markdown):
        if not ch.strip():
            continue
        if i in markup_indices:
            continue
        if i not in covered_chars:
            ctx = markdown[max(0, i - 20):i + 20].replace("\n", "↵")
            errors.append(f"Position {i} not covered: …{ctx}…")
            if len(errors) > 10:
                errors.append("(truncated)")
                break
    return errors
