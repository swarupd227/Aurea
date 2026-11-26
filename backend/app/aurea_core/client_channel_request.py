"""A client asks for a language or for WhatsApp on their own Canvas page (B-322).

A client holds no state-changing tool. What they can do is ask, with a button on their page, as they ask for a meeting or say a figure is wrong:
the request becomes a task for the household's recorded adviser (`advisers.adviser_of`, never someone worked out; with none on record the task is
unassigned and says so), and nothing changes until a person records it from the request through the existing Confirm card tools:

  language      "I read Hindi"   -> `record_client_language`, evidence: this request (the task names the day and the page)
  whatsapp      "Message me on WhatsApp" -> `record_consent`, kind `whatsapp`, method and evidence as the person confirms them

The request never writes a language, a consent or a message, and the page says only that it was sent: the client is not told it was granted. A client
who asks again while a task for the same thing is open adds nothing. The client reads what is recorded for them (their language, whether an opt-in is
on file), because those are facts about them; a request is not an opt-in.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advisers, client_language, firm_time, whatsapp
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm

SOURCE = "client_request"
KINDS = ("language", "whatsapp")
TITLE_MAX = 280


class ChannelRequestError(ValueError):
    """The request cannot be made, with the reason."""


def applies(firm: Firm | None) -> bool:
    return client_language.applies(firm)


async def view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """What the client's page shows: the languages they may ask for, what is recorded for them, and requests still open."""
    if not applies(firm):
        return {"applies": False}
    row = await client_language.standing(session, firm.id, household_id)
    code = row.language if row else client_language.DEFAULT
    consent = await whatsapp.opt_in(session, firm.id, household_id)
    open_titles = [t.title for t in (await session.execute(select(Task).where(
        Task.firm_id == firm.id, Task.household_id == household_id, Task.source == SOURCE, Task.status == TaskStatus.OPEN))).scalars().all()]
    return {"applies": True, "language": code, "language_label": client_language.label(code), "language_recorded": bool(row),
            "whatsapp_on_file": bool(consent is not None and consent.status == "granted"),
            "languages": [{"code": c, "label": n, "native": nat} for c, (n, nat) in client_language.LANGUAGES.items()],
            "open": {"language": any(t.startswith("Client asks to be written to in ") for t in open_titles),
                     "whatsapp": any(t.startswith("Client asks for WhatsApp") for t in open_titles)}}


async def prepare(session: AsyncSession, firm: Firm, household: Household, *, kind: str | None, language: str | None = None) -> dict:
    if not applies(firm):
        raise ChannelRequestError("Language and WhatsApp requests are for an Indian firm's clients.")
    k = (kind or "").strip().lower()
    if k not in KINDS:
        raise ChannelRequestError(f"A request is one of {', '.join(KINDS)}.")
    code = None
    if k == "language":
        try:
            code = client_language.clean_code(language)
        except client_language.LanguageError as exc:
            raise ChannelRequestError(str(exc))
        row = await client_language.standing(session, firm.id, household.id)
        if (row.language if row else client_language.DEFAULT) == code:
            raise ChannelRequestError(f"{client_language.label(code)} is already what is recorded for you.")
        title = f"Client asks to be written to in {client_language.label(code)}"
        todo = ("Record it with record_client_language from this request: say who asked, and name this task and the day as the evidence. "
                "Nothing is changed until a person records it; the client has been told only that the request was sent.")
    else:
        consent = await whatsapp.opt_in(session, firm.id, household.id)
        if consent is not None and consent.status == "granted":
            raise ChannelRequestError("WhatsApp is already on file for you.")
        title = "Client asks for WhatsApp"
        todo = ("If the client confirms it, record the opt-in with record_consent (kind whatsapp), from its evidence, and ask compliance for an approved "
                "template where the 24-hour window is closed. Nothing is changed until a person records it; this request is not an opt-in.")
    waiting = (await session.execute(select(Task).where(
        Task.firm_id == firm.id, Task.household_id == household.id, Task.source == SOURCE, Task.title == title, Task.status == TaskStatus.OPEN))).scalars().first()
    if waiting is not None:
        raise ChannelRequestError("You have already asked for this: your adviser has it as an open request.")
    adviser = await advisers.adviser_of(session, firm.id, household.id)
    return {"kind": k, "language": code, "title": title[:TITLE_MAX], "todo": todo, "adviser": adviser if adviser is not None and adviser.is_active else None}


async def create(session: AsyncSession, firm: Firm, household: Household, by: User, *, kind: str | None, language: str | None = None) -> dict:
    """Make the request a task. Records nothing about the client's language or consent."""
    p = await prepare(session, firm, household, kind=kind, language=language)
    who = by.full_name or by.email
    owner = p["adviser"]
    detail = (f"Asked on the client's own page by {who}, {firm_time.today(firm).isoformat()}. {p['todo']}"
              + ("" if owner is not None else " No adviser is on record for this household, so the task is unassigned."))
    task = Task(firm_id=firm.id, household_id=household.id, title=p["title"], detail=detail, status=TaskStatus.OPEN, source=SOURCE,
                subject_label=who, assigned_to=owner.id if owner is not None else None, assigned_by=None)
    session.add(task)
    await session.flush()
    return {"task_id": str(task.id), "title": task.title, "assigned": owner.full_name if owner is not None else None, "recorded": False}
