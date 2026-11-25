"""The language a client reads, and the translation seam (L100 India edition, B-301 step 1).

A language is what a person recorded from the client's own request: an adviser or paraplanner writes down which language the
household asked to be written to in, and where the request is. It is added and never edited, and the latest stands. No language
on file means the firm's own (English); it does not mean the client reads English. Nothing here guesses a language from a name,
a city or what a client wrote.

A message to a client goes out in a person's name, so a translation is the person's words once they confirm it. The Confirm card
shows the exact words in the client's language and the English original beside them, and the over-promising, advertising and
tipping-off checks run on both. The words in the client's language come from the person (or the assistant drafting for them, which
the person confirms); with none given, `translate` is asked. It is a seam: a connected translator registers at `TRANSLATORS`, and
without one the stub returns the English text marked "translation not connected" and nothing is invented. A message then goes in
English and the card says so. The checks read patterns in English, so a text in another language is checked only as far as those
patterns reach it; the English original is the one a person stands behind.

The assistant's reply to a client in the client's language is a seam too (`reply_language`): the client prompt's rules, that advice
goes to the adviser, are unchanged and apply in every language.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Household
from app.models.india_batch_b import ClientLanguage
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
DEFAULT = "en"
# One small list, in one place: code, name in English, name in the language itself.
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English", "English"), "hi": ("Hindi", "हिन्दी"), "mr": ("Marathi", "मराठी"), "ta": ("Tamil", "தமிழ்"),
    "te": ("Telugu", "తెలుగు"), "kn": ("Kannada", "ಕನ್ನಡ"), "bn": ("Bengali", "বাংলা"), "gu": ("Gujarati", "ગુજરાતી"),
}
NOT_CONNECTED = "translation not connected"


class LanguageError(ValueError):
    """This cannot be recorded or sent, with the reason."""


@dataclass
class Translation:
    text: str
    language: str
    connected: bool
    note: str | None = None


Translator = Callable[[str, str], str]
TRANSLATORS: dict[str, Translator] = {}     # a real translation service registers here; none is connected today


def applies(firm: Firm | None) -> bool:
    return bool(firm) and markets.code(getattr(firm, "jurisdiction", None)) == "IN"


def label(code: str | None) -> str:
    return LANGUAGES.get(code or DEFAULT, (code or DEFAULT, ""))[0]


def native(code: str | None) -> str:
    return LANGUAGES.get(code or DEFAULT, (code or DEFAULT, code or DEFAULT))[1]


def clean_code(value: str | None) -> str:
    """A language by its code or its English name."""
    v = (value or "").strip().lower()
    for code, (name, _) in LANGUAGES.items():
        if v in (code, name.lower()):
            return code
    raise LanguageError(f"A language is one of {', '.join(f'{n} ({c})' for c, (n, _) in LANGUAGES.items())}.")


def translate(text: str, language: str) -> Translation:
    """Text into a language through the connected translator, or the stub that invents nothing."""
    if language == DEFAULT:
        return Translation(text, DEFAULT, True)
    if TRANSLATORS:
        key = sorted(TRANSLATORS)[0]
        return Translation(TRANSLATORS[key](text, language), language, True, key)
    return Translation(text, DEFAULT, False, NOT_CONNECTED)


async def standing(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> ClientLanguage | None:
    rows = (await session.execute(select(ClientLanguage).where(ClientLanguage.firm_id == firm_id, ClientLanguage.household_id == household_id)
                                  .order_by(ClientLanguage.created_at.desc()))).scalars().all()
    return rows[0] if rows else None


async def reply_language(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> str | None:
    """The language the assistant's reply to this client would be in, or None for the firm's own. A seam: nothing reads it yet, and
    the client prompt (advice goes to the adviser) is the same in every language."""
    if not applies(firm):
        return None
    row = await standing(session, firm.id, household_id)
    return row.language if row and row.language != DEFAULT else None


def _view(r: ClientLanguage | None, household: Household) -> dict:
    return {"household_id": str(household.id), "household": household.name, "language": r.language if r else None,
            "label": label(r.language) if r else label(DEFAULT), "native": native(r.language) if r else native(DEFAULT), "recorded": bool(r),
            "asked_by": r.asked_by if r else None, "evidence": r.evidence if r else None, "recorded_by": r.recorded_by if r else None,
            "recorded_on": r.created_at.date().isoformat() if r and r.created_at else None}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    if not applies(firm):
        return {"applies": False, "households": [], "languages": []}
    homes = (await session.execute(select(Household).where(Household.firm_id == firm.id).order_by(Household.name))).scalars().all()
    rows = (await session.execute(select(ClientLanguage).where(ClientLanguage.firm_id == firm.id).order_by(ClientLanguage.created_at.asc()))).scalars().all()
    latest = {r.household_id: r for r in rows}
    out = [_view(latest.get(h.id), h) for h in homes]
    return {"applies": True, "households": out, "recorded": sum(1 for h in out if h["recorded"]),
            "languages": [{"code": c, "label": n, "native": nat} for c, (n, nat) in LANGUAGES.items()],
            "said": "No language on file means the firm's own, English; it does not mean the client reads English."}


async def prepare(session: AsyncSession, firm: Firm, role, *, household_id=None, language: str | None = None, asked_by: str | None = None,
                  evidence: str | None = None) -> dict:
    if not applies(firm):
        raise LanguageError("A client's language is recorded for an Indian firm.")
    if UserRole(role) not in RECORDERS:
        raise LanguageError("A client's language is recorded by their adviser or a paraplanner, from the client's request.")
    try:
        household = await session.get(Household, uuid.UUID(str(household_id)))
    except (ValueError, TypeError):
        raise LanguageError("Give the household's id, from the client's page.")
    if household is None or household.firm_id != firm.id:
        raise LanguageError("No such household at this firm.")
    code = clean_code(language)
    who, where = " ".join((asked_by or "").split()), " ".join((evidence or "").split())
    if len(who) < 2:
        raise LanguageError("Say who in the household asked for it.")
    if len(where) < 5:
        raise LanguageError("Say where the client's request is: the message, the call note, the form.")
    before = await standing(session, firm.id, household.id)
    if before and before.language == code:
        raise LanguageError(f"Nothing changes: {household.name} is already recorded as reading {label(code)}.")
    return {"household": household, "language": code, "asked_by": who, "evidence": where, "before": before}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    session.add(ClientLanguage(firm_id=firm.id, household_id=p["household"].id, language=p["language"], asked_by=p["asked_by"], evidence=p["evidence"],
                               recorded_by=by, created_at=utcnow()))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="client_language_recorded", actor=by, content={
        "household_id": str(p["household"].id), "language": p["language"], "evidence": p["evidence"]})
    return await overview(session, firm)
