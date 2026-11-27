"""The screening lists a firm has recorded, the rescreen a list change calls for, and its backlog (B-390).

L200-1 India §5.3: names are screened against UNSC sanctions lists and domestic lists notified under UAPA, with
ongoing re-screening on list updates. No list feed is connected: a person records each change from its notification
(added and never edited), and every screening after that reads the entries now on the lists beside the mock
provider's own. Recording a change rescreens the book's parties in the same step. A party not screened since a change
was recorded is the backlog, aged from the day the list changed. "The book" is every party of record on a case that
was not rejected; a client with no party of record is counted and said to be unscreened here.

A hit stays a signal: nothing here decides one, and a rescreen keeps what people have decided (aml_review). None of
this is told to a client.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, indian_names, markets, screening
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Person
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.pmla import ScreeningListUpdate
from app.models.tenant import Firm
from app.provenance import ledger

LISTS: dict[str, tuple[str, str]] = {
    "unsc": ("UNSC consolidated sanctions list", "sanctions"),
    "uapa": ("Domestic list notified under UAPA", "sanctions"),
    "pep": ("Politically exposed persons list", "pep"),
}
INVENTED = "invented demonstration entries, not a real list"
RECORDERS = frozenset({UserRole.COMPLIANCE, UserRole.OPERATIONS})
PARTY_RECORDERS = frozenset({UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.ADVISER, UserRole.PARAPLANNER})
NOT_CONNECTED = "No list feed is connected: a list change is what a person recorded from its notification."


class ListError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    return firm is not None and markets.code(firm.jurisdiction) == "IN"


def _aware(at: datetime | None) -> datetime | None:
    return at.replace(tzinfo=timezone.utc) if at is not None and at.tzinfo is None else at


async def updates(session: AsyncSession, firm_id: uuid.UUID) -> list[ScreeningListUpdate]:
    return list((await session.execute(select(ScreeningListUpdate).where(ScreeningListUpdate.firm_id == firm_id)
                                       .order_by(ScreeningListUpdate.created_at, ScreeningListUpdate.published_on))).scalars().all())


def _standing(rows: list) -> dict:
    """The entries on the lists now: each change in the order it was recorded, a removal taking a name off its own list."""
    out: dict = {}
    for u in rows:
        label, category = LISTS[u.list_key]
        source = f"{label}, {u.version}" + (f" ({INVENTED})" if u.demonstration else "")
        for name in u.removed or []:
            out.pop((u.list_key, " ".join(indian_names.words(name))), None)
        for e in u.entries or []:
            out[(u.list_key, " ".join(indian_names.words(e["name"])))] = {**e, "category": category, "list_key": u.list_key, "source": source}
    return out


async def entries(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    return list(_standing(await updates(session, firm_id)).values())


async def screen_names(session: AsyncSession, firm_id: uuid.UUID, names: list[str], *, dobs: dict | None = None,
                       fathers: dict | None = None) -> dict:
    """Names against the provider's watchlist and the firm's recorded lists: what every screening path calls."""
    return screening.screen_parties(names, dobs=dobs, fathers=fathers, entries=await entries(session, firm_id))


def stamp(party: OnboardingParty, listed: list[dict], note: str) -> list[str]:
    """Screen one party and write the result on it. Returns the hits it did not have before, as category:name."""
    before = {f"{h.get('category')}:{h.get('matched_name')}" for h in party.screening_hits or []}
    result = screening.screen(party.legal_name, dob=party.dob, father_name=party.father_name, entries=listed)
    party.screening_status, party.screening_hits, party.screened_at = result.status, result.to_dict()["hits"], utcnow()
    party.disposition_note = note
    return [f"{h.category}:{h.matched_name}" for h in result.hits if f"{h.category}:{h.matched_name}" not in before]


async def _book(session: AsyncSession, firm_id: uuid.UUID) -> list[tuple]:
    """Every party of record on a case that was not rejected, with its case."""
    cases = {c.id: c for c in (await session.execute(select(OnboardingCase).where(
        OnboardingCase.firm_id == firm_id, OnboardingCase.status != "rejected"))).scalars().all()}
    if not cases:
        return []
    rows = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id.in_(list(cases)))
                                  .order_by(OnboardingParty.legal_name))).scalars().all()
    return [(p, cases[p.case_id]) for p in rows]


async def backlog(session: AsyncSession, firm: Firm, rows: list | None = None) -> dict:
    """Who has not been screened since a list changed, oldest change first."""
    today = firm_time.today(firm)
    rows = await updates(session, firm.id) if rows is None else rows
    book = await _book(session, firm.id)
    items = []
    for party, case in book:
        at = _aware(party.screened_at)
        missed = [u for u in rows if at is None or at < _aware(u.created_at)]
        if missed:
            oldest = min(missed, key=lambda u: u.published_on)
            items.append({"party_id": str(party.id), "party": party.legal_name, "case_id": str(case.id), "case": case.prospect_name,
                          "list": LISTS[oldest.list_key][0], "version": oldest.version, "changed_on": oldest.published_on.isoformat(),
                          "age_days": (today - oldest.published_on).days, "screened_on": at.date().isoformat() if at else None,
                          "demonstration": oldest.demonstration})
    items.sort(key=lambda i: (-i["age_days"], i["party"]))
    named = {" ".join(indian_names.words(p.legal_name)) for p, _ in book}
    people = (await session.execute(select(Person.full_name).where(Person.firm_id == firm.id, Person.deceased_on.is_(None)))).scalars().all()
    return {"count": len(items), "oldest_age_days": items[0]["age_days"] if items else None, "parties_in_book": len(book),
            "items": items, "clients_without_party": sum(1 for n in people if " ".join(indian_names.words(n)) not in named)}


async def overview(session: AsyncSession, firm: Firm) -> dict | None:
    """The firm's lists and the backlog. None for a firm that keeps none."""
    rows = await updates(session, firm.id)
    if not rows and not applies(firm):
        return None
    now = _standing(rows)
    lists = []
    for key, (label, category) in LISTS.items():
        mine = [u for u in rows if u.list_key == key]
        last = max(mine, key=lambda u: u.published_on) if mine else None
        lists.append({"list_key": key, "label": label, "category": category, "recorded": bool(mine),
                      "version": last.version if last else None, "changed_on": last.published_on.isoformat() if last else None,
                      "entries": sum(1 for k in now if k[0] == key), "demonstration": any(u.demonstration for u in mine)})
    return {"lists": lists, "backlog": await backlog(session, firm, rows), "not_connected": NOT_CONNECTED,
            "changes": [{"list_key": u.list_key, "label": LISTS[u.list_key][0], "version": u.version, "published_on": u.published_on.isoformat(),
                         "added": len(u.entries or []), "removed": len(u.removed or []), "document": u.source_document,
                         "recorded_by": u.recorded_by, "demonstration": u.demonstration} for u in reversed(rows)][:12]}


# ── Recording a list change ─────────────────────────────────────────────────────────────────────────────────────

def parse_entry(line) -> dict:
    """'name | father's name | date of birth | note', the last three optional."""
    if isinstance(line, dict):
        bits = [line.get("name"), line.get("father_name"), line.get("dob"), line.get("note")]
    else:
        bits = str(line).split("|")
    bits = [str(b or "").strip() for b in bits] + ["", "", ""]
    name, father, dob, note = bits[:4]
    if len(indian_names.words(name)) < 1 or len(name) < 3:
        raise ListError("Each entry starts with the listed name: 'name | father's name | date of birth | note'.")
    if dob:
        try:
            date.fromisoformat(dob)
        except ValueError:
            raise ListError(f"{name}: the date of birth is YYYY-MM-DD, or left empty.")
    return {"name": " ".join(name.split()), "father_name": " ".join(father.split()) or None, "dob": dob or None, "note": note or None}


async def prepare_update(session: AsyncSession, firm: Firm, role, *, list_key: str | None, version: str | None, published_on,
                         entries_added=None, removed=None, source_document: str | None = None, demonstration: bool = False) -> dict:
    if not applies(firm):
        raise ListError("A screening list change is recorded for an Indian firm.")
    if role not in RECORDERS:
        raise ListError("Compliance or operations records a screening list change.")
    key = (list_key or "").strip().lower()
    if key not in LISTS:
        raise ListError(f"The list is one of {', '.join(LISTS)}.")
    label = (version or "").strip()
    if not 2 <= len(label) <= 60:
        raise ListError("Give the change's own reference or version, as the notification names it (up to 60 characters).")
    today = firm_time.today(firm)
    try:
        day = published_on if isinstance(published_on, date) else date.fromisoformat(str(published_on))
    except ValueError:
        raise ListError("Give the day the list changed, as YYYY-MM-DD.")
    if day > today:
        raise ListError("A list change cannot be dated in the future.")
    if len((source_document or "").strip()) < 5:
        raise ListError("Say where the notification is filed.")
    added = [parse_entry(x) for x in entries_added or [] if str(x).strip()]
    gone = [" ".join(str(x).split()) for x in removed or [] if str(x).strip()]
    if not added and not gone:
        raise ListError("Give at least one name added or removed.")
    rows = await updates(session, firm.id)
    if any(u.list_key == key and u.version.lower() == label.lower() for u in rows):
        raise ListError(f"{LISTS[key][0]}, {label}, is already recorded.")
    now = _standing(rows)
    for name in gone:
        if (key, " ".join(indian_names.words(name))) not in now:
            raise ListError(f"{name} is not on the {LISTS[key][0]} as recorded here.")
    book = await _book(session, firm.id)
    new_entries = [{**e, "category": LISTS[key][1], "source": f"{LISTS[key][0]}, {label}"} for e in added]
    matches = []
    for party, _case in book:
        for e in new_entries:
            if screening._listed(party.legal_name, e, party.dob, party.father_name, 0.82) is not None:
                matches.append(f"{party.legal_name} / {e['name']}")
    return {"list_key": key, "label": LISTS[key][0], "version": label, "published_on": day, "entries": added, "removed": gone,
            "source_document": source_document.strip(), "demonstration": bool(demonstration), "parties": len(book), "matches": matches}


async def record_update(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    """The change, and the rescreen it calls for."""
    p = await prepare_update(session, firm, role, **terms)
    row = ScreeningListUpdate(created_at=utcnow(), firm_id=firm.id, list_key=p["list_key"], version=p["version"], published_on=p["published_on"],
                              entries=p["entries"], removed=p["removed"], source_document=p["source_document"],
                              demonstration=p["demonstration"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="screening_list_recorded", actor=by, content={
        "list": p["list_key"], "version": p["version"], "published_on": p["published_on"].isoformat(), "added": len(p["entries"]),
        "removed": len(p["removed"]), "document": p["source_document"]})
    return await rescreen(session, firm, by=by)


async def prepare_rescreen(session: AsyncSession, firm: Firm, role) -> dict:
    if not applies(firm):
        raise ListError("The delta rescreen is run for an Indian firm's recorded lists.")
    if role not in RECORDERS:
        raise ListError("Compliance or operations runs the delta rescreen.")
    waiting = await backlog(session, firm)
    if not waiting["count"]:
        raise ListError("No party is waiting: every party of record has been screened since the last list change.")
    return waiting


async def rescreen(session: AsyncSession, firm: Firm, *, by: str) -> dict:
    """Screen every party waiting since a list change. Decisions already made are kept."""
    waiting = await backlog(session, firm)
    ids = {uuid.UUID(i["party_id"]) for i in waiting["items"]}
    listed = await entries(session, firm.id)
    found: list[dict] = []
    for party, case in await _book(session, firm.id):
        if party.id in ids:
            for hit in stamp(party, listed, "Rescreened after a list change. Hits await a person's decision."):
                found.append({"party": party.legal_name, "case": case.prospect_name, "hit": hit})
    await session.flush()
    if ids:
        await ledger.append_entry(session, firm_id=firm.id, event_type="screening_delta_rescreen", actor=by, content={
            "parties": len(ids), "new_hits": len(found), "oldest_age_days": waiting["oldest_age_days"]})
    return {"rescreened": len(ids), "new_hits": found}


# ── The father's name ───────────────────────────────────────────────────────────────────────────────────────────

async def prepare_father(session: AsyncSession, firm: Firm, role, *, party_id, father_name: str | None, document: str | None) -> dict:
    if role not in PARTY_RECORDERS:
        raise ListError("An adviser, paraplanner, operations or compliance records a party's father's name.")
    party = await session.get(OnboardingParty, party_id)
    if party is None or party.firm_id != firm.id:
        raise ListError("Party not found.")
    name = " ".join((father_name or "").split())
    if not 3 <= len(name) <= 200 or not indian_names.words(name):
        raise ListError("Give the father's name as the document writes it.")
    if len((document or "").strip()) < 5:
        raise ListError("Say which document shows it.")
    if name == (party.father_name or ""):
        raise ListError("Nothing changes: that is the father's name on file.")
    return {"party": party, "case": await session.get(OnboardingCase, party.case_id), "father_name": name, "document": document.strip()}


async def record_father(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_father(session, firm, role, **terms)
    party = p["party"]
    before, party.father_name = party.father_name, p["father_name"]
    stamp(party, await entries(session, firm.id), "Rescreened when the father's name was recorded. Hits await a person's decision.")
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="party_father_name_recorded", actor=by, content={
        "case_id": str(party.case_id), "party_id": str(party.id), "before": before, "after": party.father_name, "document": p["document"]})
    return {"party": party, "case": p["case"]}
