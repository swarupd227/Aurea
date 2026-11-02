"""The names on file closest to one that matched nothing, so a typo gets a question, not a dead end.

A likeness is only ever offered: a similar name can be a different client, so nothing here picks one.
Words like "family" say nothing about who is meant and are left out of the comparison.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Hashable, Iterable

NEAR = 0.75
_NOISE = {"the", "family", "household", "and", "of"}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", text.lower()) if len(w) > 1 and w not in _NOISE]


def likeness(query: str, name: str) -> float:
    """How close the nearest pair of words is, 1.0 being the same word."""
    return max((SequenceMatcher(None, a, b).ratio() for a in _words(query) for b in _words(name)), default=0.0)


def closest(query: str, names: Iterable[tuple[Hashable, str]], *, limit: int = 3) -> list[tuple[Hashable, str]]:
    """The keys nearest `query`, closest first, each with the name that came closest. A key may
    appear more than once in `names`, as a household does through its people."""
    best: dict[Hashable, tuple[float, str]] = {}
    for key, name in names:
        score = likeness(query, name)
        if score >= NEAR and score > best.get(key, (0.0, ""))[0]:
            best[key] = (score, name)
    ranked = sorted(best.items(), key=lambda row: (-row[1][0], row[1][1]))
    return [(key, name) for key, (_score, name) in ranked[:limit]]
