"""The Indian tax year's two reviews, and the year-end gain-harvest task (B-396, L200-2 India sections 3.2 and 11).

**A person says a review happened.** An adviser or a paraplanner records that a taxpayer's regime comparison or gain and
loss harvest review was held: the day, what was looked at and said in their words, and where the evidence is filed, with
the engine's figures as they stood. Added and never edited; a second review is another row. Nothing is carried across
from a proposal, a task or a meeting, and no record does not mean no review happened.

**Completion** is the share of the firm's taxpayers with the tax year's review on record:

  regime    each living adult in an Indian household with income on file or an account of their own.
  harvest   each taxpayer, a person, an HUF or a trust, with an account of their own.
  both      those with every review that applies to them.

A household that has left is in no count.

**The year-end task.** For each taxpayer the engine's own harvest figures (`tax_india.report`: long-term equity lots at
the front of each first-in first-out queue that would use what is left of the exemption) show something to look at, one
task for the household's recorded adviser, once for the taxpayer and the tax year. The worker raises them from January to
March on the firm's calendar; a person may raise them earlier from the conversation. A taxpayer whose harvest review is
already on record for the year gets none, and a household with no recorded adviser gets an unassigned task that says so.
A task is a reminder to look: it sells nothing, advises no sale or rebuy, and changes no book.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advisers, firm_time, law_figures, markets, tax_india
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.governance import LedgerEntry
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.india_tax_planning import TaxReview
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
RAISERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.BRANCH_LEADER})
KINDS = {"regime": "Regime review", "harvest": "Harvest review"}
SOURCE = "gain_harvest"
RAISED_EVENT = "gain_harvest_task_raised"
SEASON = (1, 2, 3)          # the months of the tax year's last quarter, in which the worker raises the task
ADULT = 18
NOTE = "A review is on record only where a person recorded it. None recorded does not mean none was held."
TASK_NOTE = ("A reminder to look, from the figures on file: it advises no sale and no rebuy, and nothing says a rebuy is safe. "
             "India has no wash-sale rule; selling and buying back raises the same substance question as a loss harvest.")


class TaxYearError(ValueError):
    """This cannot be recorded or raised, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _year(day: date) -> dict:
    return markets.tax_year(markets.MARKETS["IN"], day)


def _label(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


def _indian(firm: Firm, household: Household) -> bool:
    market = markets.of_household(firm, household.values)
    return market is not None and market.code == "IN"


async def _households(session: AsyncSession, firm: Firm, household_id: uuid.UUID | None = None) -> list[Household]:
    """The firm's households taxed in India that have not left, or the one asked for."""
    q = select(Household).where(Household.firm_id == firm.id, Household.left_on.is_(None))
    if household_id is not None:
        q = q.where(Household.id == household_id)
    return [h for h in (await session.execute(q.order_by(Household.name))).scalars().all() if _indian(firm, h)]


async def _taxpayers(session: AsyncSession, firm: Firm, households: list[Household], today: date) -> list[dict]:
    """Every taxpayer of these households, with which of the two reviews applies to each."""
    ids = [h.id for h in households]
    if not ids:
        return []
    names = {h.id: h.name for h in households}
    holders = {x for row in (await session.execute(select(Mandate.person_id, Mandate.entity_id).join(Account, Account.mandate_id == Mandate.id)
                                                   .where(Mandate.firm_id == firm.id))).all() for x in row if x}
    out = []
    for p in (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.household_id.in_(ids),
                                                         Person.deceased_on.is_(None)))).scalars().all():
        born = p.date_of_birth
        if born is not None and today.year - born.year - ((today.month, today.day) < (born.month, born.day)) < ADULT:
            continue
        income = ((p.profile or {}).get("tax") or {}).get("annual_income")
        regime, harvest = income is not None or p.id in holders, p.id in holders
        if regime or harvest:
            out.append({"taxpayer_id": str(p.id), "name": p.full_name, "kind": "individual", "household_id": str(p.household_id),
                        "household": names[p.household_id], "applies": {"regime": regime, "harvest": harvest}})
    for e in (await session.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id, LegalEntity.household_id.in_(ids)))).scalars().all():
        if e.id in holders:
            out.append({"taxpayer_id": str(e.id), "name": e.name, "kind": str(e.entity_type), "household_id": str(e.household_id),
                        "household": names[e.household_id], "applies": {"regime": False, "harvest": True}})
    return sorted(out, key=lambda t: (t["household"], t["kind"] != "individual", t["name"]))


def review_view(row: TaxReview, name: str | None = None) -> dict:
    return {"id": str(row.id), "taxpayer_id": str(row.person_id or row.entity_id), "name": name, "household_id": str(row.household_id),
            "kind": row.kind, "label": KINDS.get(row.kind, row.kind), "tax_year": row.tax_year, "reviewed_on": row.reviewed_on.isoformat(),
            "covered": row.covered, "document": row.source_document, "figures": row.figures or None, "recorded_by": row.recorded_by}


async def _reviews(session: AsyncSession, firm_id: uuid.UUID, tax_year: str | None = None, household_id: uuid.UUID | None = None) -> list[TaxReview]:
    q = select(TaxReview).where(TaxReview.firm_id == firm_id)
    if tax_year:
        q = q.where(TaxReview.tax_year == tax_year)
    if household_id is not None:
        q = q.where(TaxReview.household_id == household_id)
    return list((await session.execute(q.order_by(TaxReview.reviewed_on, TaxReview.created_at, TaxReview.id))).scalars().all())


def _share(done: int, of: int) -> dict:
    return {"done": done, "of": of, "share": round(done / of, 4) if of else None}


# ── The year-end gain-harvest task ───────────────────────────────────────────

async def _raised(session: AsyncSession, firm_id: uuid.UUID, tax_year: str) -> dict[str, str]:
    """taxpayer id -> task id, for every task raised for the year, whatever became of it."""
    rows = (await session.execute(select(LedgerEntry).where(LedgerEntry.firm_id == firm_id, LedgerEntry.event_type == RAISED_EVENT))).scalars().all()
    return {str(c.get("taxpayer_id")): str(c.get("task_id")) for c in (r.content or {} for r in rows) if c.get("tax_year") == tax_year}


async def harvest_queue(session: AsyncSession, firm: Firm, *, today: date | None = None, household_id: uuid.UUID | None = None) -> list[dict]:
    """Each taxpayer whose harvest figures show something to look at this tax year, and where it stands."""
    from app.aurea_core.graph import household_brain

    day = today or firm_time.today(firm)
    if not applies(firm):
        return []
    year = _year(day)["label"]
    raised = await _raised(session, firm.id, year)
    reviewed = {str(r.person_id or r.entity_id) for r in await _reviews(session, firm.id, year, household_id) if r.kind == "harvest"}
    statuses = {str(t.id): str(t.status) for t in (await session.execute(select(Task).where(Task.firm_id == firm.id, Task.source == SOURCE))).scalars().all()}
    out = []
    for household in await _households(session, firm, household_id):
        living = {t["taxpayer_id"] for t in await _taxpayers(session, firm, [household], day)}
        brain = await household_brain(session, household.id, firm_id=firm.id)
        if not brain:
            continue
        owner = None
        for t in (await tax_india.report(session, brain, firm=firm, today=day))["taxpayers"]:
            lots = [o for o in t["opportunities"] if o["kind"] == "exemption"]
            if not lots or t["id"] not in living:
                continue
            owner = owner or await advisers.adviser_of(session, firm.id, household.id)
            task_id = raised.get(t["id"])
            out.append({"taxpayer_id": t["id"], "name": t["name"], "kind": t["kind"], "household_id": str(household.id), "household": household.name,
                        "tax_year": year, "exemption_left": t["position"]["exempt_left"], "gain": round(sum(o["gain"] for o in lots), 2),
                        "tax_saved_later": round(sum(o["tax_saved"] for o in lots), 2),
                        "lots": [{"symbol": o["symbol"], "quantity": o["quantity"], "gain": o["gain"], "account": o["account"]} for o in lots],
                        "adviser": owner.full_name if owner else None, "adviser_id": str(owner.id) if owner else None,
                        "task_id": task_id, "task_status": statuses.get(task_id) if task_id else None,
                        "state": "raised" if task_id else "reviewed" if t["id"] in reviewed else "to_raise"})
    return out


async def prepare_raise(session: AsyncSession, firm: Firm, role, *, household_id=None, today: date | None = None) -> dict:
    if not applies(firm):
        raise TaxYearError("The year-end gain-harvest task is raised for an Indian firm.")
    if role not in RAISERS:
        raise TaxYearError("An adviser, a paraplanner or a branch leader raises the year-end gain-harvest tasks.")
    day = today or firm_time.today(firm)
    hid = None
    if household_id not in (None, ""):
        try:
            hid = household_id if isinstance(household_id, uuid.UUID) else uuid.UUID(str(household_id))
        except ValueError:
            raise TaxYearError("Give the household's id from search_households.") from None
        if not await _households(session, firm, hid):
            raise TaxYearError("That household is not a client of this firm taxed in India.")
    queue = await harvest_queue(session, firm, today=day, household_id=hid)
    ready = [q for q in queue if q["state"] == "to_raise"]
    if not ready:
        raised, reviewed = (sum(1 for q in queue if q["state"] == s) for s in ("raised", "reviewed"))
        raise TaxYearError(f"Nothing to raise: {raised} taxpayer(s) already have this year's task, {reviewed} already have a harvest review on record, "
                           "and no other taxpayer has a long-term equity lot in gain that would use the exemption.")
    year = _year(day)
    return {"ready": ready, "tax_year": year["label"], "due_date": year["end"], "today": day}


async def _write(session: AsyncSession, firm: Firm, ready: list[dict], *, tax_year: str, due: date, today: date, by: str,
                 by_id: uuid.UUID | None) -> list[dict]:
    say = markets.money_for(firm)
    made = []
    for q in ready:
        lots = "; ".join(f"{l['symbol']} {l['quantity']:g} in {l['account']}, gain {say(l['gain'])}" for l in q["lots"])
        detail = (f"As at {firm_time.spoken(today)}, tax year {tax_year}: {say(q['exemption_left'])} of the long-term equity exemption is unused. "
                  f"Lots at the front of their queue that would use it: {lots}. An estimate for a qualified person to check. {TASK_NOTE} "
                  "Record the review when it is held (record_tax_review)."
                  + ("" if q["adviser_id"] else " No adviser is recorded for this household, so the task is unassigned."))
        task = Task(firm_id=firm.id, household_id=uuid.UUID(q["household_id"]),
                    title=f"Year-end gain harvest, tax year {tax_year}: {q['name']}"[:280], detail=detail, status=TaskStatus.OPEN, due_date=due,
                    source=SOURCE, subject_label=q["household"][:200], assigned_to=uuid.UUID(q["adviser_id"]) if q["adviser_id"] else None,
                    assigned_by=by_id if q["adviser_id"] else None)
        session.add(task)
        await session.flush()
        await ledger.append_entry(session, firm_id=firm.id, event_type=RAISED_EVENT, actor=by, content={
            "task_id": str(task.id), "taxpayer_id": q["taxpayer_id"], "household_id": q["household_id"], "tax_year": tax_year,
            "exemption_left": q["exemption_left"], "gain": q["gain"], "assigned_to": q["adviser_id"]})
        made.append({**q, "state": "raised", "task_id": str(task.id), "task_status": str(TaskStatus.OPEN)})
    return made


async def raise_tasks(session: AsyncSession, firm: Firm, role, *, by: str, by_id: uuid.UUID | None = None, household_id=None,
                      today: date | None = None) -> dict:
    ready = await prepare_raise(session, firm, role, household_id=household_id, today=today)
    made = await _write(session, firm, ready["ready"], tax_year=ready["tax_year"], due=ready["due_date"], today=ready["today"], by=by, by_id=by_id)
    return {"tax_year": ready["tax_year"], "due_date": ready["due_date"].isoformat(), "raised": made}


async def raise_due(session: AsyncSession, firm: Firm, *, today: date | None = None) -> dict:
    """The worker's pass: from January to March on the firm's calendar, whatever is not raised yet. A second pass raises nothing."""
    day = today or firm_time.today(firm)
    if not applies(firm):
        return {"raised": 0, "why": "not an Indian firm"}
    if day.month not in SEASON:
        return {"raised": 0, "why": "outside January to March on the firm's calendar"}
    year = _year(day)
    ready = [q for q in await harvest_queue(session, firm, today=day) if q["state"] == "to_raise"]
    made = await _write(session, firm, ready, tax_year=year["label"], due=year["end"], today=day, by="scheduler", by_id=None)
    return {"raised": len(made), "tax_year": year["label"]}


# ── Reviews ──────────────────────────────────────────────────────────────────

async def _figures_now(session: AsyncSession, firm: Firm, household: Household, taxpayer_id: str, kind: str, today: date) -> dict:
    """What the engine showed on the day a review is recorded, kept beside it."""
    from app.aurea_core import tax_india_full
    from app.aurea_core.graph import household_brain

    brain = await household_brain(session, household.id, firm_id=firm.id)
    block = next((t for t in (await tax_india.report(session, brain, firm=firm, today=today))["taxpayers"] if t["id"] == taxpayer_id), None) \
        if brain else None
    if kind == "harvest":
        if block is None:
            return {}
        lots = [o for o in block["opportunities"] if o["kind"] == "exemption"]
        return {"exemption_left": block["position"]["exempt_left"], "gain_to_use_it": round(sum(o["gain"] for o in lots), 2),
                "losses_to_look_at": round(sum(-o["net_loss"] for o in block["opportunities"] if o["kind"] == "loss"), 2),
                "estimated_tax": block["estimated_tax"]}
    comparison = block["engine"]["regime"] if block else None
    if comparison is None:
        person = await session.get(Person, uuid.UUID(taxpayer_id))
        tax = (person.profile or {}).get("tax") or {}
        born = person.date_of_birth
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day)) if born else None
        from app.aurea_core import india_regime

        comparison = tax_india_full.regime_comparison(
            income=tax.get("annual_income"), salary=tax.get("salary_income"), deductions=tax.get("old_regime_deductions"), age=age, gains=0.0,
            is_individual=True, facts=await india_regime.facts(session, firm, person, tax))
    if not comparison.get("assessed"):
        return {"assessed": False, "needs": comparison.get("needs")}
    return {"assessed": True, "new_tax": comparison["new"]["tax"], "old_tax": comparison["old"]["tax"], "cheaper": comparison.get("cheaper"),
            "conclusive": comparison.get("conclusive"), "employer_nps": (comparison.get("employer_nps") or {}).get("recorded_yearly"),
            "switching": (comparison.get("switching") or {}).get("state")}


async def prepare_review(session: AsyncSession, firm: Firm, role, *, person_id=None, entity_id=None, kind=None, covered=None, document=None,
                         reviewed_on=None, tax_year=None) -> dict:
    if not applies(firm):
        raise TaxYearError("A regime or harvest review is recorded for an Indian firm.")
    if role not in RECORDERS:
        raise TaxYearError("An adviser or a paraplanner records that a review happened.")
    if bool(person_id) == bool(entity_id):
        raise TaxYearError("Say whose review it was: one person or one entity.")
    what = str(kind or "").strip().lower()
    if what not in KINDS:
        raise TaxYearError("The review is regime or harvest.")
    if what == "regime" and not person_id:
        raise TaxYearError("The two regimes are compared for a person, not for an HUF or a trust here.")
    try:
        tid = uuid.UUID(str(person_id or entity_id))
    except ValueError:
        raise TaxYearError("Give the taxpayer's id from read_tax_reviews or read_tax_position.") from None
    row = await session.get(Person if person_id else LegalEntity, tid)
    if row is None or row.firm_id != firm.id or not row.household_id:
        raise TaxYearError("That taxpayer is not a client of this firm.")
    households = await _households(session, firm, row.household_id)
    if not households:
        raise TaxYearError("That household is not a current client taxed in India.")
    today = firm_time.today(firm)
    current = _year(today)
    start = current["start"].year
    year = str(tax_year or "").strip() or current["label"]
    if year not in (current["label"], _label(start - 1)):
        raise TaxYearError(f"A review is recorded for this tax year ({current['label']}) or the one before ({_label(start - 1)}).")
    try:
        day = today if reviewed_on in (None, "") else reviewed_on if isinstance(reviewed_on, date) else date.fromisoformat(str(reviewed_on)[:10])
    except ValueError:
        raise TaxYearError("Give the day of the review as YYYY-MM-DD.") from None
    if day > today:
        raise TaxYearError("A review cannot have happened in the future.")
    began = current["start"] if year == current["label"] else date(start - 1, current["start"].month, current["start"].day)
    if day < began:
        raise TaxYearError(f"A review of tax year {year} is on or after its first day, {firm_time.spoken(began)}.")
    said = str(covered or "").strip()
    if len(said) < 5:
        raise TaxYearError("Say what was looked at and said, in your own words.")
    source = str(document or "").strip()
    if len(source) < 5:
        raise TaxYearError("Say where the evidence is filed: the meeting note, the email or the worksheet.")
    if any(r.reviewed_on == day and r.kind == what and r.covered == said[:400] and str(r.person_id or r.entity_id) == str(tid)
           for r in await _reviews(session, firm.id, year, households[0].id)):
        raise TaxYearError("That review is already recorded.")
    # Figures are the engine's today, so none is kept for a past year.
    figures = await _figures_now(session, firm, households[0], str(tid), what, today) if year == current["label"] else {}
    return {"person_id": tid if person_id else None, "entity_id": tid if entity_id else None, "household": households[0],
            "name": row.full_name if person_id else row.name, "kind": what, "tax_year": year, "reviewed_on": day, "covered": said[:400],
            "document": source[:300], "figures": figures}


async def record_review(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_review(session, firm, role, **terms)
    row = TaxReview(created_at=utcnow(), firm_id=firm.id, household_id=p["household"].id, person_id=p["person_id"], entity_id=p["entity_id"],
                    kind=p["kind"], tax_year=p["tax_year"], reviewed_on=p["reviewed_on"], covered=p["covered"], source_document=p["document"],
                    figures=p["figures"], recorded_by=by[:200])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="tax_review_recorded", actor=by, content={
        "review_id": str(row.id), "household_id": str(row.household_id), "taxpayer_id": str(row.person_id or row.entity_id), "kind": row.kind,
        "tax_year": row.tax_year, "reviewed_on": row.reviewed_on.isoformat(), "document": row.source_document})
    return review_view(row, p["name"])


# ── The reading ──────────────────────────────────────────────────────────────

async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    """This tax year's reviews, the completion shares and the gain-harvest queue, for the firm or one household."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat()}
    households = await _households(session, firm, household_id)
    if household_id is not None and not households:
        raise TaxYearError("That household is not a current client taxed in India.")
    year = _year(today)
    people = await _taxpayers(session, firm, households, today)
    rows = await _reviews(session, firm.id, year["label"], household_id)
    latest = {(str(r.person_id or r.entity_id), r.kind): r for r in rows}
    for t in people:
        for kind in KINDS:
            r = latest.get((t["taxpayer_id"], kind))
            t[kind] = ({"state": "not_applicable"} if not t["applies"][kind] else
                       {"state": "done", "reviewed_on": r.reviewed_on.isoformat(), "by": r.recorded_by} if r else {"state": "not_recorded"})
    completion = {kind: _share(sum(1 for t in people if t[kind]["state"] == "done"), sum(1 for t in people if t["applies"][kind])) for kind in KINDS}
    completion["both"] = _share(sum(1 for t in people if all(t[k]["state"] != "not_recorded" for k in KINDS)), len(people))
    queue = await harvest_queue(session, firm, today=today, household_id=household_id)
    names = {t["taxpayer_id"]: t["name"] for t in people}
    return {"applies": True, "as_of": today.isoformat(), "tax_year": year["label"], "tax_year_end": year["end"].isoformat(),
            "days_left": year["days_left"], "scope": "household" if household_id else "firm",
            "household_id": str(household_id) if household_id else None, "household": households[0].name if household_id else None,
            "completion": completion, "taxpayers": people,
            "reviews": [review_view(r, names.get(str(r.person_id or r.entity_id))) for r in reversed(rows)],
            "harvest": {"queue": queue, "counts": {s: sum(1 for q in queue if q["state"] == s) for s in ("to_raise", "raised", "reviewed")},
                        "scheduled_from": date(year["end"].year, SEASON[0], 1).isoformat(), "note": TASK_NOTE},
            "exemption": law_figures.value("in_cg_equity_lt_exemption"), "note": NOTE}
