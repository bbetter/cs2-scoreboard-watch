"""Fuzzy nickname matching for OCR output (stdlib only).

Used to correct nicknames read from the scoreboard OCR against nicknames from
the CS2 console and against Steam search results.

A match is returned only when it is unambiguous:
  * similarity to the best candidate >= MIN_SIMILARITY, and
  * the best candidate beats the second-best one by >= MIN_MARGIN.
Otherwise ``best_match_index`` / ``pick_best`` return None and the caller keeps
the OCR nick unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Similarity (0..1) the best candidate must reach.
MIN_SIMILARITY = 0.8
# Required gap between the best and the second-best similarity.
MIN_MARGIN = 0.15
# Query must keep at least this many literal (non-wildcard) chars after normalization.
MIN_LITERAL_CHARS = 3
# Characters the OCR could not read; they match anything, at a partial cost.
WILDCARDS = frozenset("?�")
WILDCARD_COST = 0.5

# Leading rank glyphs: circled digits/letters, dingbats, stars, emoji selectors.
_RANK_GLYPH_RE = re.compile(
    r"^\s*(?:[①-⓿☀-➿⭐-⯿㈀-㋿︀-️]\s*)+"
)
# Leading rank number separated from the name by whitespace ("33 cemolog").
_RANK_NUMBER_RE = re.compile(r"^\s*\d+\s+(?=\S)")

# Visually confusable characters folded to one form for comparison.
_FOLD = str.maketrans({"1": "l", "|": "l", "i": "l", "!": "l", "0": "o"})


def normalize_nick(text: str) -> str:
    """Strip OCR rank prefixes and whitespace. Keeps the original letter case."""
    s = (text or "").strip()
    s = _RANK_GLYPH_RE.sub("", s)
    s = _RANK_NUMBER_RE.sub("", s)
    return s.strip()


def canonical_key(text: str) -> str:
    """Comparison form: normalized, lowercased, confusables folded (1/l/I/|, 0/o)."""
    return normalize_nick(text).lower().translate(_FOLD)


def _weighted_distance(a: str, b: str) -> float:
    prev = [float(j) for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        cur = [float(i)]
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cost = 0.0
            elif ca in WILDCARDS or cb in WILDCARDS:
                cost = WILDCARD_COST
            else:
                cost = 1.0
            cur.append(min(prev[j] + 1.0, cur[j - 1] + 1.0, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def similarity(query: str, candidate: str) -> float:
    """Similarity in 0..1 between two nicks after canonicalization."""
    a = canonical_key(query)
    b = canonical_key(candidate)
    if not a or not b:
        return 0.0
    return max(0.0, 1.0 - _weighted_distance(a, b) / max(len(a), len(b)))


def _literal_count(text: str) -> int:
    return sum(1 for ch in canonical_key(text) if ch not in WILDCARDS)


def best_match_index(query: str, candidates: Sequence[str]) -> int | None:
    """Index of the unambiguous best candidate, or None.

    An exact case-insensitive match (on the raw, un-normalized text) always wins.
    """
    if not query or not candidates:
        return None
    q_raw = query.strip().lower()
    for idx, cand in enumerate(candidates):
        if cand and cand.strip().lower() == q_raw:
            return idx
    if _literal_count(query) < MIN_LITERAL_CHARS:
        return None

    scored = sorted(
        ((similarity(query, c) if c else 0.0, idx) for idx, c in enumerate(candidates)),
        reverse=True,
    )
    best_sim, best_idx = scored[0]
    if best_sim < MIN_SIMILARITY:
        return None
    second_sim = scored[1][0] if len(scored) > 1 else 0.0
    if best_sim - second_sim < MIN_MARGIN:
        return None
    return best_idx


def pick_best(query: str, candidates: Sequence[str]) -> str | None:
    """Unambiguous best-matching candidate string, or None."""
    idx = best_match_index(query, candidates)
    return candidates[idx] if idx is not None else None
