"""A check of client-facing words against what an Indian adviser may not say (India row 10).

`check(text, kind)` reads a draft message, a report or marketing copy against patterns encoded below, each with the rule it
rests on, and returns findings. It reports; it never approves. A text with no findings is not "compliant": the patterns are not
exhaustive, they read words and not meaning, and a person in compliance decides what goes out.

What it looks for, and where it is read from (the research report, section 3 and the IA Master Circular as cited there):

  - **A promise of return or safety** ("guaranteed", "assured returns", "risk-free", "no loss", "double your money"): blocks.
    Read through the same denial-aware reading as the over-promising rule (`compliance.rules.asserted`), so "returns are not
    guaranteed" passes. A message to a client of an Indian firm that asserts one is refused at its Confirm card.
  - **A performance claim**: a return stated as a figure ("returned 18%", "18% CAGR", "beat the Nifty"). SEBI allows a firm's or
    strategy's past performance to be claimed only in verified metrics (PaRRVA-verified, or APMI's TWRR for a portfolio
    manager). Nothing here can verify one, so every such claim is a finding; in marketing it blocks, in a message or report it
    is flagged for the person to check that a verified figure backs it, and the past-performance caveat is looked for beside it.
  - **An unqualified superlative** ("the best", "number one", "unbeatable", "highest returns").
  - **Missing words in marketing**: the market-risk and past-performance disclaimers, the registration disclaimer and a
    registration number. The wording of the disclaimers is assumed from usual practice and was not re-read in the circulars.
  - **A testimonial, or pressure** ("limited time", "act now"). The Common Advertisement Code bans testimonials and dark
    patterns but is not operative until 8 April 2027 (the research report, Regs 16G-16P), so before that day these read as
    notices, not breaches.

The Code also excludes a response to a client's request; this check does not know whether a message is one, so it flags and
leaves that to the person. No rule here changes a figure, sends or withdraws anything.
"""
from __future__ import annotations

import re
from datetime import date

from app.aurea_core import firm_time, markets
from app.compliance.rules import asserted

KINDS = {"message": "A message to a client", "report": "A report or note for a client", "marketing": "Marketing copy"}
NOT_EXHAUSTIVE = ("These patterns are not exhaustive and read words, not meaning. A text with no findings is not approved or "
                  "compliant; a person in compliance decides what goes out.")
CODE_OPERATIVE = date(2027, 4, 8)
IA_MC = "SEBI Master Circular for Investment Advisers (6 Feb 2026), as read in the research report"
INTERMEDIARIES = "SEBI (Intermediaries) Regulations 2008, Regs 16D-16F (past performance), as read in the research report"
CODE = "SEBI Common Advertisement Code (Intermediaries Regs 16G-16P), notified, operative 8 April 2027"

PROMISES = ("guarantee", "guaranteed", "guarantees", "assured", "assured return", "assured returns", "risk-free", "riskless",
            "risk free", "no risk", "zero risk", "no loss", "without any risk", "100% safe", "capital is safe", "double your money",
            "certain returns", "certain profit", "sure shot", "sure-shot", "sure thing", "can't lose", "cannot lose", "will definitely")
SUPERLATIVES = ("the best", "best returns", "best performing", "best-performing", "number one", "no. 1", "#1", "unbeatable", "unmatched", "highest returns", "top performing",
                "top-performing", "market-beating", "outperforms everyone", "always outperform")
PRESSURE = ("limited time", "act now", "hurry", "last chance", "only today", "offer ends", "don't miss", "dont miss", "grab it now")
TESTIMONIAL = ("testimonial", "our clients say", "what our clients say", "client reviews", "5 star", "five star", "5-star",
               "rated 5", "i earned")
# "returned 18%", "18% return(s)", "18% p.a.", "18% CAGR", "up 18% last year", "beat the Nifty", "outperformed the benchmark"
PERFORMANCE = re.compile(
    r"(?:\b(?:returned|delivered|generated|gave|earned|grew|up|gained)\b[^.\n]{0,40}?\d+(?:\.\d+)?\s?%)"
    r"|(?:\d+(?:\.\d+)?\s?%\s?(?:returns?|p\.?a\.?|per annum|cagr|xirr|annuali[sz]ed|a year|growth|gain|profit))"
    r"|(?:\b(?:beat|beats|beaten|outperform(?:ed|s)?|outpaced)\s+(?:the\s+)?(?:nifty|sensex|benchmark|index|market|bse|nse)\b)",
    re.IGNORECASE)
PAST_CAVEAT = re.compile(r"past performance[^.\n]{0,60}(?:not|no|may or may not)[^.\n]{0,60}(?:indicat|guarant|sustain|future)", re.I)
MARKET_RISK = re.compile(r"investments? in (?:the )?securities market (?:are|is) subject to market risks?", re.I)
REGISTRATION_DISCLAIMER = re.compile(r"registration granted by sebi[^.\n]{0,120}(?:no way|not)[^.\n]{0,60}(?:guarantee|assurance)", re.I)
REGISTRATION_NUMBER = re.compile(r"\bIN[AHP][0-9]{9}\b|\bINP[0-9]{9}\b", re.I)


def applies(firm) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _excerpt(text: str, start: int, end: int) -> str:
    a, b = max(0, start - 30), min(len(text), end + 30)
    return ("..." if a else "") + " ".join(text[a:b].split()) + ("..." if b < len(text) else "")


def _asserted(text: str, phrases) -> list[str]:
    """`asserted`, with a phrase dropped where a longer one found with it contains it ("assured" within "assured returns")."""
    found = asserted(text, phrases)
    return [p for p in found if not any(p != q and re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", q) for q in found)]


def _find(text: str, word: str) -> tuple[int, int] | None:
    m = re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text.lower())
    return (m.start(), m.end()) if m else None


def _finding(rule: str, label: str, severity: str, matched: str, why: str, citation: str, text: str = "", span=None) -> dict:
    return {"rule": rule, "label": label, "severity": severity, "matched": matched, "why": why, "citation": citation,
            "excerpt": _excerpt(text, *span) if span else None}


def check(text: str | None, kind: str = "message", *, today: date | None = None) -> dict:
    """The findings for a text read as `kind` (a message, a report or marketing). Pure: it writes and sends nothing."""
    body = (text or "").strip()
    kind = kind if kind in KINDS else "message"
    day = today or date.today()
    code_on = day >= CODE_OPERATIVE
    findings: list[dict] = []

    for phrase in _asserted(body, PROMISES):
        findings.append(_finding("promise_of_return", "Promises a return or safety", "block", phrase,
                                 "A return or the safety of capital is never promised: markets move and an adviser cannot assure an outcome.",
                                 f"{IA_MC}; the firm's over-promising rule", body, _find(body, phrase)))

    for phrase in _asserted(body, SUPERLATIVES):
        findings.append(_finding("superlative", "Unqualified superlative", "flag", phrase,
                                 "A claim to be the best or top needs a verified, dated basis; none is on this text.", CODE, body, _find(body, phrase)))

    perf = PERFORMANCE.search(body)
    if perf:
        caveat = bool(PAST_CAVEAT.search(body))
        findings.append(_finding(
            "performance_claim", "States a past return", "block" if kind == "marketing" else "flag", perf.group(0).strip(),
            ("Past performance may be claimed only in a verified metric (PaRRVA-verified, or APMI's TWRR for a portfolio manager); "
             "nothing here can verify this figure, so a person confirms that one backs it before it goes out."
             + ("" if caveat else " No past-performance caveat is beside it.")), INTERMEDIARIES, body, perf.span()))
        if not caveat:
            findings.append(_finding("past_performance_caveat", "No past-performance caveat", "flag", "",
                                     "A return is not stated without saying that past performance may not be sustained.", INTERMEDIARIES))

    for phrase in _asserted(body, TESTIMONIAL):
        findings.append(_finding("testimonial", "A testimonial", "flag" if code_on else "notice", phrase,
                                 "The Common Advertisement Code bans testimonials" + ("" if code_on else "; it is not operative until 8 April 2027, so this is a notice"),
                                 CODE, body, _find(body, phrase)))
    for phrase in _asserted(body, PRESSURE):
        findings.append(_finding("pressure", "Pressure to act", "flag" if code_on else "notice", phrase,
                                 "The Common Advertisement Code bans dark patterns such as manufactured urgency"
                                 + ("" if code_on else "; it is not operative until 8 April 2027, so this is a notice"), CODE, body, _find(body, phrase)))

    if kind == "marketing":
        if not MARKET_RISK.search(body):
            findings.append(_finding("market_risk_disclaimer", "No market-risk disclaimer", "flag", "",
                                     "Marketing carries 'Investments in securities market are subject to market risks'. ASSUMED wording, from usual practice.",
                                     IA_MC))
        if not PAST_CAVEAT.search(body):
            findings.append(_finding("past_performance_disclaimer", "No past-performance disclaimer", "flag", "",
                                     "Marketing says past performance may or may not be sustained in future. ASSUMED wording, from usual practice.", IA_MC))
        if not REGISTRATION_DISCLAIMER.search(body):
            findings.append(_finding("registration_disclaimer", "No registration disclaimer", "flag", "",
                                     "Marketing says registration with SEBI does not guarantee performance or assure returns. ASSUMED wording, from usual practice.",
                                     IA_MC))
        if not REGISTRATION_NUMBER.search(body):
            findings.append(_finding("registration_number", "No SEBI registration number", "flag", "",
                                     "Marketing shows the firm's SEBI registration number. None was found in the text.", IA_MC))

    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("block", "flag", "notice")}
    return {"kind": kind, "kind_label": KINDS[kind], "findings": findings, "counts": counts, "blocks": counts["block"],
            "length": len(body), "not_exhaustive": True, "note": NOT_EXHAUSTIVE, "code_operative": code_on,
            "code_operative_on": CODE_OPERATIVE.isoformat()}


def for_firm(firm, text: str | None, kind: str = "message") -> dict:
    """The check on the firm's own day. For a firm outside India it reads nothing and says so."""
    if not applies(firm):
        return {"applies": False, "kind": kind, "findings": [], "counts": {}, "blocks": 0, "not_exhaustive": True,
                "note": "The advertising check is for an Indian firm; this firm's market is not India."}
    return {"applies": True, **check(text, kind, today=firm_time.today(firm))}


def refusal(firm, text: str | None) -> str | None:
    """Why a message to a client of an Indian firm may not go: it promises a return or safety. Only that refuses; every other
    finding is shown on its Confirm card for the person to weigh."""
    if not applies(firm):
        return None
    out = check(text, "message", today=firm_time.today(firm))
    blocks = [f for f in out["findings"] if f["severity"] == "block" and f["rule"] == "promise_of_return"]
    if not blocks:
        return None
    words = ", ".join(sorted({f["matched"] for f in blocks}))
    return (f"This message promises a return or safety ({words}). An adviser may not guarantee an outcome; reword it, and a "
            f"person in compliance decides what is permitted. {NOT_EXHAUSTIVE}")


def summary(result: dict) -> str | None:
    """One line for a Confirm card, or None when the check found nothing."""
    if not result.get("applies", True):
        return None
    c = result.get("counts") or {}
    if not any(c.values()):
        return "No pattern found (the check is not exhaustive and does not approve a text)"
    bits = [f"{n} {name}" for name, n in (("to fix", c.get("block", 0)), ("to weigh", c.get("flag", 0)), ("notice", c.get("notice", 0))) if n]
    return f"{', '.join(bits)}: " + "; ".join(sorted({f['label'] for f in result['findings']}))
