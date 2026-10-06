"""
Segmenter unit tests.

Tests cover:
- Coverage invariant: every non-whitespace character of the IPS appears in exactly one segment
- Stable IDs: same markdown → same segment IDs
- Quote derivation: derive_quote returns an exact substring (invariant I4)
- Heading inheritance: each segment carries its heading_path
- Numbered clause detection: clauses like "4.1 No single issuer…" have kind='clause'
- Sentence splitting: Okafor-style unnumbered multi-sentence paragraphs split correctly
"""

from __future__ import annotations

import pytest
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Helper: load a real IPS document
# ---------------------------------------------------------------------------

def _load_ips(filename: str) -> str:
    return (REPO_ROOT / "data" / "ips" / filename).read_text(encoding="utf-8")


def _all_ips_files() -> List[Path]:
    return list((REPO_ROOT / "data" / "ips").glob("*.md"))


# ---------------------------------------------------------------------------
# Coverage invariant
# ---------------------------------------------------------------------------

class TestCoverage:
    def _check_coverage(self, markdown: str) -> None:
        from copilot.extract.segmenter import segment_document, verify_coverage

        segments = segment_document(markdown)
        errors = verify_coverage(segments, markdown)
        assert errors == [], f"Coverage errors:\n" + "\n".join(errors)

    def test_patel_coverage(self):
        """Every character of patel_household.md is covered."""
        self._check_coverage(_load_ips("patel_household.md"))

    def test_chen_coverage(self):
        """Every character of chen_family_trust.md is covered."""
        self._check_coverage(_load_ips("chen_family_trust.md"))

    def test_brightwater_coverage(self):
        """Every character of brightwater_foundation.md is covered."""
        self._check_coverage(_load_ips("brightwater_foundation.md"))

    def test_okafor_coverage(self):
        """Every character of okafor_retirement.md is covered."""
        self._check_coverage(_load_ips("okafor_retirement.md"))

    def test_synthetic_short_doc(self):
        """Short synthetic document is fully covered."""
        md = "# Header\n\nParagraph one.\n\nParagraph two with more text.\n"
        from copilot.extract.segmenter import segment_document, verify_coverage
        segments = segment_document(md)
        errors = verify_coverage(segments, md)
        assert errors == [], "\n".join(errors)


# ---------------------------------------------------------------------------
# No text lost — segment count sanity
# ---------------------------------------------------------------------------

class TestSegmentCount:
    def test_no_empty_segments(self):
        """No segment should have empty text."""
        from copilot.extract.segmenter import segment_document
        md = _load_ips("patel_household.md")
        segments = segment_document(md)
        empty = [s for s in segments if not s.text.strip()]
        assert empty == [], f"Empty segments: {[s.id for s in empty]}"

    def test_segment_ids_unique(self):
        """All segment IDs are unique within a document."""
        from copilot.extract.segmenter import segment_document
        md = _load_ips("chen_family_trust.md")
        segments = segment_document(md)
        ids = [s.id for s in segments]
        assert len(ids) == len(set(ids)), "Duplicate segment IDs found"

    def test_segment_count_reasonable(self):
        """Patel IPS should produce at least 20 segments (it has 20 clauses)."""
        from copilot.extract.segmenter import segment_document
        md = _load_ips("patel_household.md")
        segments = segment_document(md)
        assert len(segments) >= 20, f"Expected at least 20 segments, got {len(segments)}"


# ---------------------------------------------------------------------------
# Stable IDs — same content → same IDs
# ---------------------------------------------------------------------------

class TestStableIds:
    def test_stable_ids_patel(self):
        """Parsing the same document twice gives the same IDs."""
        from copilot.extract.segmenter import segment_document
        md = _load_ips("patel_household.md")
        segs1 = segment_document(md)
        segs2 = segment_document(md)
        ids1 = [s.id for s in segs1]
        ids2 = [s.id for s in segs2]
        assert ids1 == ids2, "Segment IDs not stable across runs"

    def test_stable_ids_chen(self):
        """Parsing chen_family_trust.md twice gives identical IDs."""
        from copilot.extract.segmenter import segment_document
        md = _load_ips("chen_family_trust.md")
        ids1 = [s.id for s in segment_document(md)]
        ids2 = [s.id for s in segment_document(md)]
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# Quote derivation (invariant I4)
# ---------------------------------------------------------------------------

class TestQuoteDerivation:
    def test_derived_quote_is_substring(self):
        """derive_quote returns the exact substring at the segment's offsets."""
        from copilot.extract.segmenter import segment_document, derive_quote
        md = _load_ips("patel_household.md")
        segments = segment_document(md)
        for seg in segments[:10]:  # spot-check the first 10
            quote = derive_quote(seg, md)
            assert quote in md, f"Segment {seg.id} quote not a substring of the document"
            assert quote.strip() == seg.text.strip(), (
                f"Segment {seg.id}: derive_quote mismatch.\n"
                f"  Expected: {seg.text[:80]!r}\n"
                f"  Got:      {quote[:80]!r}"
            )

    def test_quote_covers_right_text(self):
        """A well-known clause text should appear in its derived quote."""
        from copilot.extract.segmenter import segment_document, derive_quote
        md = _load_ips("patel_household.md")
        segments = segment_document(md)
        # Find segments that contain a known phrase
        known_phrase = "Kestrel Semiconductor"
        matching = [s for s in segments if known_phrase in s.text]
        assert matching, f"Expected to find '{known_phrase}' in at least one segment"
        for seg in matching:
            quote = derive_quote(seg, md)
            assert known_phrase in quote, (
                f"Segment {seg.id}: '{known_phrase}' not in derived quote: {quote[:120]!r}"
            )


# ---------------------------------------------------------------------------
# Heading inheritance
# ---------------------------------------------------------------------------

class TestHeadingInheritance:
    def test_paragraphs_inherit_heading(self):
        """Every non-heading segment should carry the heading_path of its parent heading."""
        from copilot.extract.segmenter import segment_document
        md = "# Top\n\n## Sub\n\nParagraph under sub.\n\n### Deep\n\nDeep paragraph.\n"
        segments = segment_document(md)
        by_kind = {s.kind: s for s in segments}

        # Find paragraphs
        paras = [s for s in segments if s.kind in ("paragraph", "clause", "sentence")]
        assert paras, "Expected at least one paragraph segment"

        # Each paragraph should have a non-empty heading_path
        for p in paras:
            assert p.heading_path, f"Segment {p.id} ('{p.text[:40]}') has empty heading_path"

    def test_all_4_ips_have_heading_paths(self):
        """In all 4 IPS docs, non-heading segments carry a heading_path."""
        from copilot.extract.segmenter import segment_document
        for path in _all_ips_files():
            md = path.read_text(encoding="utf-8")
            segments = segment_document(md)
            non_headings = [s for s in segments if s.kind != "heading"]
            # At least some non-headings should have paths (all IPS have headings)
            with_path = [s for s in non_headings if s.heading_path]
            assert with_path, f"{path.name}: no segments with heading_path found"


# ---------------------------------------------------------------------------
# Numbered clause detection
# ---------------------------------------------------------------------------

class TestClauseKind:
    def test_numbered_clause_kind(self):
        """Numbered clauses like '4.1 No issuer...' get kind='clause'."""
        from copilot.extract.segmenter import segment_document
        md = "## 4. Concentration\n\n4.1 No single issuer may exceed 10% of the portfolio.\n"
        segments = segment_document(md)
        clauses = [s for s in segments if s.kind == "clause"]
        assert clauses, "Expected at least one segment with kind='clause'"
        assert any("4.1" in s.text for s in clauses), "4.1 clause text not found"


# ---------------------------------------------------------------------------
# Sentence splitting (Okafor-style)
# ---------------------------------------------------------------------------

class TestSentenceSplitting:
    def test_multi_sentence_splits(self):
        """A paragraph with multiple sentences should be split into sentence segments."""
        from copilot.extract.segmenter import segment_document
        md = "## Background\n\nThe Okafor household has two members. They retired in 2020. They own a house.\n"
        segments = segment_document(md)
        sentences = [s for s in segments if s.kind == "sentence"]
        assert len(sentences) >= 2, f"Expected ≥2 sentence segments, got {len(sentences)}"

    def test_sentence_split_no_text_lost(self):
        """Sentence splitting must not lose any text from the paragraph."""
        from copilot.extract.segmenter import segment_document, verify_coverage
        md = "## Test\n\nFirst sentence here. Second sentence there. Third one too.\n"
        segments = segment_document(md)
        errors = verify_coverage(segments, md)
        assert errors == [], "\n".join(errors)
