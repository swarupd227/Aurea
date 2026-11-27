"""Comparing Indian names for screening (B-390, L200-1 India §5.3).

A name written in Latin letters has several honest spellings (Chaudhary, Choudhury), initials stand for given names,
and the father's name is what tells two people of one name apart. This compares them and says how they agree. It is a
signal for the person who decides a hit and nothing else: it clears no hit and confirms none, and a father's name that
differs does not remove one, because either record may be wrong.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Spellings of one name that no sound rule below brings together.
_ALIASES = (
    ("chaudhary", "chaudhari", "choudhary", "choudhury", "chowdhury", "chowdhary", "chaudhry", "choudhry", "chaudhuri", "choudhuri"),
    ("agarwal", "aggarwal", "agrawal", "aggrawal", "agarawal"),
    ("lakshman", "laxman", "lakshmana"),
    ("lakshmi", "laxmi"),
    ("srinivas", "shrinivas", "sreenivas", "shreenivas", "srinivasa"),
    ("mukherjee", "mukherji", "mookerjee", "mukerji"),
    ("banerjee", "banerji", "bannerjee", "banarjee"),
    ("mohammed", "mohammad", "mohamed", "mohamad", "muhammad", "muhammed", "mohd", "md"),
    ("sheikh", "shaikh", "shaik", "sekh"),
    ("syed", "sayed", "saiyed", "sayyed", "saiyad"),
    ("iyer", "aiyar", "ayyar", "aiyer"),
    ("rathore", "rathod", "rathaur"),
    ("kulshreshtha", "kulshrestha", "kulsrestha"),
)
_ALIAS = {spelling: group[0] for group in _ALIASES for spelling in group}
_TITLES = {"shri", "smt", "sri", "mr", "mrs", "ms", "dr", "late", "shrimati", "kumari"}
_SOUNDS = (("ksh", "x"), ("ee", "i"), ("oo", "u"), ("aa", "a"), ("ph", "f"), ("w", "v"), ("sh", "s"), ("th", "t"), ("dh", "d"),
           ("bh", "b"), ("kh", "k"), ("gh", "g"), ("ck", "k"), ("ou", "au"))
CLOSE = 0.9


def words(name: str | None) -> list[str]:
    """A name's words, lower case, without punctuation or titles."""
    return [w for w in re.sub(r"[^a-z ]", " ", str(name or "").lower()).split() if w not in _TITLES]


def fold_word(word: str) -> str:
    word = _ALIAS.get(word, word)
    for written, sound in _SOUNDS:
        word = word.replace(written, sound)
    return re.sub(r"(.)\1+", r"\1", word)


def fold(name: str | None) -> list[str]:
    return [fold_word(w) for w in words(name)]


def _initials(a: list[str], b: list[str]) -> bool:
    """The same last name, and each given name the same or the other's initial, with at least one initial used."""
    if len(a) < 2 or len(a) != len(b) or a[-1] != b[-1]:
        return False
    used = False
    for x, y in zip(a[:-1], b[:-1]):
        if x == y:
            continue
        short, long = sorted((x, y), key=len)
        if len(short) != 1 or not long.startswith(short):
            return False
        used = True
    return used


def compare(ours: str | None, theirs: str | None) -> tuple[str | None, float]:
    """How two names agree: exact, spelling variant, initials, close, or None. With the likeness of the folded names."""
    a, b = words(ours), words(theirs)
    if not a or not b:
        return None, 0.0
    if a == b:
        return "exact", 1.0
    fa, fb = fold(ours), fold(theirs)
    likeness = round(SequenceMatcher(None, " ".join(fa), " ".join(fb)).ratio(), 3)
    if sorted(fa) == sorted(fb):
        return "spelling variant", 1.0
    if _initials(fa, fb):
        return "initials", likeness
    # Close only where the last names sound alike, so a shared given name alone is not a match.
    if likeness >= CLOSE and SequenceMatcher(None, fa[-1], fb[-1]).ratio() >= 0.8:
        return "close", likeness
    return None, likeness


def father(ours: str | None, theirs: str | None) -> str:
    """The father's name on file against the list's: match, spelling variant, differs, or unknown."""
    if not words(ours) or not words(theirs):
        return "unknown"
    how, _ = compare(ours, theirs)
    if how == "exact":
        return "match"
    return "spelling variant" if how else "differs"
