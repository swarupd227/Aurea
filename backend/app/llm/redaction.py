"""PII redaction — mask client identifiers before a prompt reaches the model, restore on the way
back (Foundation pillar 'Security & compliance'). Deterministic and reversible, so the de-redacted
answer is faithful but the model never sees raw client PII."""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ACCT = re.compile(r"\b\d{2,4}-\d{3,}\b")          # e.g. 41-2207
_LONGNUM = re.compile(r"\b\d{7,}\b")               # long account / IRD-style numbers


def redact(text: str, terms: list[str] | None = None,
           categories: list[str] | None = None) -> tuple[str, dict[str, str]]:
    """Return (masked_text, mapping token→original). `terms` = known sensitive strings (names);
    `categories` selects which to mask (names/accounts/emails/ids); None = all."""
    if not text:
        return text, {}
    cats = set(categories) if categories is not None else {"names", "accounts", "emails", "ids"}
    mapping: dict[str, str] = {}
    out = text
    n = 0
    if "names" in cats:
        # Named entities first (longest first so 'Wei Chen' is masked before 'Wei').
        for t in sorted({t for t in (terms or []) if t and len(t) > 2}, key=len, reverse=True):
            if t in out:
                n += 1
                tok = f"[PERSON_{n}]"
                out = out.replace(t, tok)
                mapping[tok] = t
    patterns = []
    if "emails" in cats:
        patterns.append(("EMAIL", _EMAIL))
    if "accounts" in cats:
        patterns.append(("ACCT", _ACCT))
    if "ids" in cats:
        patterns.append(("ID", _LONGNUM))
    for label, rx in patterns:
        for m in sorted(set(rx.findall(out)), key=len, reverse=True):
            n += 1
            tok = f"[{label}_{n}]"
            out = out.replace(m, tok)
            mapping[tok] = m
    return out, mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    for tok, original in mapping.items():
        text = text.replace(tok, original)
    return text
