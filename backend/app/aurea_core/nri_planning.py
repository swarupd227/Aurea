"""NRI planning at working depth (B-398, India): treaty paperwork, leaving and returning, and how long a returning client
stays not ordinarily resident. Residence itself is what a person recorded (`residency.py`); nothing here changes it.

    certificates   a tax residency certificate or Form 10F, as the document states it, read against today: it covers,
                   it has expired, it is not yet in force, or none is on file. Treaty relief is not modelled.
    moves          a person records that a client is leaving India or returning, from a document. The checklist for that
                   direction is laid out against what is on file, and each item with something on file, and each that
                   applies to every move, becomes a task for the client's recorded adviser. Nothing is redesignated here:
                   the bank, the depository and the registrar do that on the client's forms. The rules are assumed.
    outlook        the tax years after a return, each read against the two not-ordinarily-resident conditions with the
                   law's figures. What is not yet known (days in a year still to come, which earlier years were
                   non-resident) is carried as a range, and a year the range does not settle says so. The assumptions are
                   listed beside it.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import balance_sheet, firm_time, held_away, law_figures, markets, residency, tasks
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.estate_nri import ResidenceMove, TreatyCertificate
from app.models.graph import Account, Household, Mandate, Person
from app.models.health_cover import HealthPolicy
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = residency.RECORDERS
CERTIFICATE_KINDS = {"trc": "Tax residency certificate", "form_10f": "Form 10F"}
DIRECTIONS = {"leaving": "Leaving India", "returning": "Returning to India"}
MOVE_SOURCE = "residence_move"
TASK_DAYS = 30
PLANNED_AHEAD_DAYS = 366
OUTLOOK_YEARS = 4
NOT_MODELLED = "Treaty relief (DTAA) is not modelled: a certificate on file is paperwork, not a figure."

# key, label, what on file it is about (None: every move), the law figure it rests on (None: no rule is claimed).
CHECKLISTS: dict[str, tuple[tuple[str, str, str | None, str | None], ...]] = {
    "leaving": (
        ("bank_nro", "Redesignate resident bank accounts as NRO", "banks", "in_move_leaving_bank"),
        ("demat_nro", "Convert the resident demat and trading accounts", "demats", "in_move_leaving_demat"),
        ("folios", "Change residential status on fund folios with the KRA", "funds", "in_move_leaving_folios"),
        ("small_savings", "Check PPF, EPF and NPS under non-resident rules", "savings", "in_move_leaving_savings"),
        ("insurers", "Tell insurers of the change of residence", "policies", None),
        ("residence", "Record FEMA status and the tax year's days", None, None),
        ("fatca", "Take a new FATCA and CRS self-certification", None, None),
        ("will", "Check nominations and the will against assets abroad", None, None),
    ),
    "returning": (
        ("nri_accounts", "Redesignate NRE and NRO accounts, or move to RFC", "nri_accounts", "in_move_return_accounts"),
        ("fcnr", "Decide FCNR deposits at maturity: resident or RFC", "fcnr", "in_move_return_accounts"),
        ("demat_resident", "Convert the non-resident demat and trading accounts", "demats", "in_move_return_demat"),
        ("folios", "Change residential status on fund folios with the KRA", "funds", "in_move_return_demat"),
        ("foreign", "List assets and income held abroad for the return", "foreign", "in_move_return_foreign"),
        ("residence", "Record FEMA status and the days since return", None, None),
        ("rnor", "Sequence repatriation around the not-ordinarily-resident years", None, "in_res_rnor_years_nonresident"),
        ("fatca", "Take a new FATCA and CRS self-certification", None, None),
    ),
}


class PlanningError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    return residency.applies(firm)


def _need(firm: Firm | None) -> None:
    if not applies(firm):
        raise PlanningError("This is recorded for an Indian firm.")


async def _person(session: AsyncSession, firm: Firm, value) -> Person:
    try:
        pid = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise PlanningError("Give the person's id, from the client's page.")
    p = await session.get(Person, pid)
    if p is None or p.firm_id != firm.id or p.household_id is None:
        raise PlanningError("That person is not a client of this firm.")
    return p


def _day(value, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        raise PlanningError(f"Give the {label} as YYYY-MM-DD.")


def _text(value, label: str, least: int = 2, most: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) < least:
        raise PlanningError(f"Give {label}.")
    return text[:most]


def _document(value) -> str:
    named = (value or "").strip()
    if len(named) < 5:
        raise PlanningError("Say which document this comes from and where it is filed.")
    return named[:300]


# ── Certificates ──────────────────────────────────────────────────────────────

def certificate_state(row: TreatyCertificate, today: date) -> str:
    return "expired" if today > row.valid_to else "not_yet" if today < row.valid_from else "covers"


def _certificate_view(row: TreatyCertificate, today: date) -> dict:
    return {"id": str(row.id), "kind": row.kind, "kind_label": CERTIFICATE_KINDS[row.kind], "country": row.country, "reference": row.reference,
            "valid_from": row.valid_from.isoformat(), "valid_to": row.valid_to.isoformat(), "state": certificate_state(row, today),
            "document": row.source_document, "recorded_by": row.recorded_by}


async def prepare_certificate(session: AsyncSession, firm: Firm, *, person_id, kind: str | None, country: str | None, reference: str | None,
                              valid_from, valid_to, document: str | None) -> dict:
    _need(firm)
    person = await _person(session, firm, person_id)
    what = (kind or "").strip().lower().replace(" ", "_").replace("tax_residency_certificate", "trc")
    if what not in CERTIFICATE_KINDS:
        raise PlanningError("The certificate is a trc (tax residency certificate) or a form_10f.")
    where = _text(country, "the country that issued it or that it is about", most=80)
    if where.lower() in ("india", "in"):
        raise PlanningError("A treaty certificate is for residence in another country.")
    ref = _text(reference, "its number or acknowledgement, as the document states it")
    start, end = _day(valid_from, "day it is valid from"), _day(valid_to, "day it is valid to")
    if end < start:
        raise PlanningError("It is valid from the first day to the last: the last is on or after the first.")
    same = (await session.execute(select(TreatyCertificate).where(
        TreatyCertificate.firm_id == firm.id, TreatyCertificate.person_id == person.id, TreatyCertificate.kind == what,
        TreatyCertificate.reference == ref, TreatyCertificate.valid_to == end))).scalars().first()
    if same is not None:
        raise PlanningError("That certificate is already recorded.")
    return {"person": person, "kind": what, "country": where, "reference": ref, "valid_from": start, "valid_to": end,
            "document": _document(document), "today": firm_time.today(firm)}


async def record_certificate(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    p = await prepare_certificate(session, firm, **terms)
    row = TreatyCertificate(firm_id=firm.id, household_id=p["person"].household_id, person_id=p["person"].id, kind=p["kind"],
                            country=p["country"], reference=p["reference"], valid_from=p["valid_from"], valid_to=p["valid_to"],
                            source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="treaty_certificate_recorded", actor=by, content={
        "person_id": str(p["person"].id), "kind": row.kind, "country": row.country, "valid_to": row.valid_to.isoformat(),
        "document": row.source_document})
    return await household_view(session, firm, p["person"].household_id)


# ── Leaving and returning ─────────────────────────────────────────────────────

async def _on_file(session: AsyncSession, firm: Firm, person: Person) -> dict[str, list[str]]:
    """What is on file for the person that a checklist item is about, each by name."""
    base = (firm.base_currency or "INR").upper()
    out: dict[str, list[str]] = {k: [] for k in ("banks", "demats", "funds", "savings", "policies", "nri_accounts", "fcnr", "foreign")}
    accounts = (await session.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id).where(
        Account.firm_id == firm.id, Account.status != "closed", Mandate.person_id == person.id).order_by(Account.name))).scalars().all()
    out["demats"] = [a.name for a in accounts]
    items = [i for i in await balance_sheet.current_items(session, person.household_id, firm_id=firm.id)
             if i.side == "asset" and i.person_id in (person.id, None)]
    lines = [l for l in await held_away.current_lines(session, person.household_id, firm_id=firm.id)
             if l.person_id in (person.id, None) and l.entity_id is None]
    out["banks"] = [i.name for i in items if i.kind == "cash"]
    out["savings"] = [i.name for i in items if i.kind == "pension"]
    out["funds"] = [i.name for i in items if i.kind == "held_away_investment"] + [f"{l.instrument_name} at {l.institution}" for l in lines]
    out["foreign"] = [i.name for i in items if (i.currency or base).upper() != base] + [
        f"{l.instrument_name} at {l.institution}" for l in lines if (l.currency or base).upper() != base]
    for model in (ProtectionPolicy, HealthPolicy):
        rows = (await session.execute(select(model).where(model.firm_id == firm.id, model.household_id == person.household_id,
                                                          model.status == "in_force"))).scalars().all()
        for r in rows:
            if model is ProtectionPolicy and r.person_id != person.id:
                continue
            if model is HealthPolicy and not any(str(m.get("person_id")) == str(person.id) or m.get("name") == person.full_name for m in r.members or []):
                continue
            out["policies"].append(f"{r.kind.replace('_', ' ')}, {r.provider}")
    facts, _row = residency.standing(await residency.records(session, firm.id, person.id))
    held = [a.upper() for a in facts.get("accounts") or []]
    out["nri_accounts"] = [f"{a} account" for a in held if a in ("NRE", "NRO")]
    out["fcnr"] = ["FCNR(B) deposit"] if "FCNR" in held else []
    return out


async def prepare_move(session: AsyncSession, firm: Firm, *, person_id, direction: str | None, moved_on, country: str | None,
                       document: str | None) -> dict:
    from app.aurea_core import advisers

    _need(firm)
    person = await _person(session, firm, person_id)
    if person.deceased_on:
        raise PlanningError(f"{person.full_name} has died.")
    way = (direction or "").strip().lower()
    if way not in DIRECTIONS:
        raise PlanningError("The move is leaving or returning.")
    day = _day(moved_on, "day of the move, past or planned")
    today = firm_time.today(firm)
    if day > today + timedelta(days=PLANNED_AHEAD_DAYS):
        raise PlanningError("A move is recorded up to a year ahead.")
    where = _text(country, "the country they go to or come from", most=80)
    if where.lower() in ("india", "in"):
        raise PlanningError("Name the other country.")
    if (await session.execute(select(ResidenceMove).where(ResidenceMove.firm_id == firm.id, ResidenceMove.person_id == person.id,
                                                          ResidenceMove.direction == way, ResidenceMove.moved_on == day))).scalars().first():
        raise PlanningError("That move is already recorded, with its tasks.")
    on_file = await _on_file(session, firm, person)
    items = [{"key": key, "label": label, "about": about, "applies_to": on_file.get(about, []) if about else [], "figure": figure,
              "raises": about is None or bool(on_file.get(about))} for key, label, about, figure in CHECKLISTS[way]]
    adviser = await advisers.adviser_of(session, firm.id, person.household_id)
    adviser = adviser if adviser is not None and adviser.is_active else None
    return {"person": person, "direction": way, "moved_on": day, "country": where, "items": items, "document": _document(document),
            "adviser": adviser, "due": max(day, today) + timedelta(days=TASK_DAYS), "today": today}


async def record_move(session: AsyncSession, firm: Firm, *, by: str, by_user: uuid.UUID | None = None, **terms) -> dict:
    """Record a move and raise its checklist's tasks. No account, folio or residence record is changed."""
    p = await prepare_move(session, firm, **terms)
    person, adviser = p["person"], p["adviser"]
    kept = []
    for item in p["items"]:
        task = None
        if item["raises"]:
            detail = f"{DIRECTIONS[p['direction']]} ({p['country']}), {p['moved_on'].isoformat()}."
            if item["applies_to"]:
                detail += " On file: " + "; ".join(item["applies_to"]) + "."
            detail += " The rule is assumed and not reviewed: confirm it before acting."
            task = Task(firm_id=firm.id, household_id=person.household_id, title=f"{item['label']}: {person.full_name}"[:tasks.TITLE_MAX],
                        detail=detail, status=TaskStatus.OPEN, due_date=p["due"], source=MOVE_SOURCE, subject_label=person.full_name,
                        assigned_to=adviser.id if adviser else None, assigned_by=by_user if adviser else None)
            session.add(task)
            await session.flush()
        kept.append({k: item[k] for k in ("key", "label", "applies_to", "figure")} | {"task_id": str(task.id) if task else None})
    row = ResidenceMove(firm_id=firm.id, household_id=person.household_id, person_id=person.id, direction=p["direction"],
                        moved_on=p["moved_on"], country=p["country"], items=kept, source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="residence_move_recorded", actor=by, content={
        "person_id": str(person.id), "direction": row.direction, "moved_on": row.moved_on.isoformat(), "country": row.country,
        "tasks": sum(1 for i in kept if i["task_id"]), "document": row.source_document})
    return await household_view(session, firm, person.household_id)


# ── The not-ordinarily-resident outlook ───────────────────────────────────────

ASSUMED = (
    "The years recorded as non-resident are taken to be the most recent ones before the recorded year",
    "After the year of return, a full year (365 days) in India and resident is assumed",
    "Days in a year still to come, and the year of return, are carried as a range",
    "Residence as a visitor over the income line, and deemed residence, are not projected",
)


def _label(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


def _start_of(day: date) -> int:
    return day.year if day.month >= 4 else day.year - 1


def rnor_outlook(facts: dict, status: dict, today: date, returns_on: date | None = None) -> dict:
    """Each tax year after a return read against the two not-ordinarily-resident conditions. `facts` and `status` are the
    residence record's for its year (residency.standing, residency.tax_status); `returns_on` a recorded return."""
    keys = ["in_res_rnor_years_nonresident", "in_res_rnor_days_prior_seven", "in_res_days_year"]
    base = {"assumed": list(ASSUMED), "law": law_figures.cited(keys, today), "years": []}
    year = facts.get("tax_year")
    n, days7, days = facts.get("nonresident_years_of_prior_ten"), facts.get("days_prior_seven_years"), facts.get("days_in_india")
    resident_now = status.get("status") in ("resident_rnor", "resident_ordinary", "resident")
    if not year or (not resident_now and returns_on is None):
        return {**base, "state": "not_applicable", "why": "No residence as resident and no return on file"}
    missing = [residency.FIELD_LABELS[k] for k, v in (("nonresident_years_of_prior_ten", n), ("days_prior_seven_years", days7),
                                                       ("days_in_india", days)) if v is None]
    if status.get("status") == "not_assessed" or missing:
        return {**base, "state": "not_assessed", "why": "Not on the residence record: " + ", ".join(missing or status.get("needs") or [])}
    y0 = int(year[:4])
    first_resident = y0 if resident_now else max(_start_of(returns_on), y0 + 1)
    if not resident_now and _start_of(returns_on) <= y0:
        return {**base, "state": "not_assessed", "why": "The return falls in the recorded year: record that year's days once it has run"}
    need_nr = int(law_figures.value("in_res_rnor_years_nonresident", today))
    most_days = int(law_figures.value("in_res_rnor_days_prior_seven", today))

    def non_resident(t: int) -> tuple[int, int]:
        """Whether tax year t was non-resident, as (certainly, possibly)."""
        if t < y0:
            back = y0 - t                                  # 1 for the year before the record
            if back > 10:
                return 0, 1
            return (1, 1) if back <= n else (0, 0)
        if t == y0:
            return (0, 0) if resident_now else (1, 1)
        if t < first_resident:
            return 1, 1                                    # still abroad
        if not resident_now and t == first_resident:
            return 0, 1                                    # the year of return
        return 0, 0

    def days_in(t: int) -> tuple[int, int]:
        if t == y0:
            return days, days
        if t < first_resident:
            return 0, int(law_figures.value("in_res_days_year", today)) - 1
        if not resident_now and t == first_resident:
            return 0, 366
        return 365, 365

    rows = []
    for t in range(first_resident, first_resident + OUTLOOK_YEARS + 1):
        returning_year = not resident_now and t == first_resident
        if t == y0:
            rows.append({"year": _label(t), "state": {"resident_rnor": "rnor", "resident_ordinary": "ordinary"}.get(status["status"], "depends"),
                         "nonresident_of_ten": [n, n], "days_in_seven": [days7, days7], "basis": "recorded"})
        else:
            window = [non_resident(x) for x in range(t - 10, t)]
            nr = [sum(a for a, _ in window), sum(b for _, b in window)]
            # The earlier days are known only as a seven-year total, so what has dropped out of the window is a range.
            dropped = min(7, t - y0)
            prior = (max(0, days7 - 366 * dropped), days7) if dropped < 7 else (0, 0)
            later = [days_in(x) for x in range(max(y0, t - 7), t)]
            span = [prior[0] + sum(a for a, _ in later), prior[1] + sum(b for _, b in later)]
            a = True if nr[0] >= need_nr else False if nr[1] < need_nr else None
            b = True if span[1] <= most_days else False if span[0] > most_days else None
            state = "rnor" if a is True or b is True else "ordinary" if a is False and b is False else "depends"
            rows.append({"year": _label(t), "state": state, "nonresident_of_ten": nr, "days_in_seven": span,
                         "basis": "year of return, if resident" if returning_year else "assumed full year"})
        if rows[-1]["state"] == "ordinary":
            break
    sure = 0
    for r in rows:
        if r["state"] != "rnor":
            break
        sure += 1
    return {**base, "state": "laid_out", "years": rows, "certain_years": sure, "from_year": rows[0]["year"],
            "nonresident_line": need_nr, "days_line": most_days, "why": None}


# ── Views ─────────────────────────────────────────────────────────────────────

async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    if not applies(firm):
        return {"applies": False}
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise PlanningError("Household not found.")
    today = firm_time.today(firm)
    people = (await session.execute(select(Person).where(Person.household_id == household_id, Person.firm_id == firm.id)
                                    .order_by(Person.full_name))).scalars().all()
    certificates = (await session.execute(select(TreatyCertificate).where(
        TreatyCertificate.firm_id == firm.id, TreatyCertificate.household_id == household_id)
        .order_by(TreatyCertificate.valid_to.desc()))).scalars().all()
    moves = (await session.execute(select(ResidenceMove).where(
        ResidenceMove.firm_id == firm.id, ResidenceMove.household_id == household_id).order_by(ResidenceMove.moved_on.desc()))).scalars().all()
    ids = [uuid.UUID(i["task_id"]) for m in moves for i in m.items or [] if i.get("task_id")]
    task_rows = {str(t.id): t for t in (await session.execute(select(Task).where(Task.firm_id == firm.id, Task.id.in_(ids)))).scalars().all()} if ids else {}
    cited: set[str] = set()
    rows = []
    for p in people:
        mine = [c for c in certificates if c.person_id == p.id]
        went = [m for m in moves if m.person_id == p.id]
        records = await residency.records(session, firm.id, p.id)
        facts, _row = residency.standing(records)
        status = residency.tax_status(facts, today) if records else None
        abroad = bool(records) and (status["status"] == "non_resident" or facts.get("fema_status") in residency.NON_RESIDENT_FEMA)
        returning = next((m for m in went if m.direction == "returning"), None)
        outlook = None
        if records and (returning is not None or status["status"] == "resident_rnor"):
            outlook = rnor_outlook(facts, status, today, returning.moved_on if returning else None)
        if not (mine or went or abroad or outlook):
            continue
        covering = any(c.kind == "trc" and certificate_state(c, today) == "covers" for c in mine)
        trc = "covers" if covering else "expired" if any(c.kind == "trc" for c in mine) else "not_on_file"
        move_views = []
        for m in went:
            items = []
            for i in m.items or []:
                t = task_rows.get(i.get("task_id") or "")
                if i.get("figure"):
                    cited.add(i["figure"])
                items.append({"key": i["key"], "label": i["label"], "applies_to": i.get("applies_to") or [], "figure": i.get("figure"),
                              "state": "nothing_on_file" if not i.get("task_id") else str(t.status) if t else "task_removed",
                              "task_id": i.get("task_id"), "due_date": t.due_date.isoformat() if t and t.due_date else None})
            move_views.append({"id": str(m.id), "direction": m.direction, "label": DIRECTIONS[m.direction], "moved_on": m.moved_on.isoformat(),
                               "planned": m.moved_on > today, "country": m.country, "items": items,
                               "tasks": sum(1 for i in items if i["task_id"]),
                               "open": sum(1 for i in items if i["state"] == TaskStatus.OPEN.value),
                               "document": m.source_document, "recorded_by": m.recorded_by})
        rows.append({"person_id": str(p.id), "name": p.full_name,
                     "tax_status": status["label"] if status else None, "tax_year": facts.get("tax_year"),
                     "fema_status": residency.FEMA_STATUSES.get(facts.get("fema_status") or ""),
                     "trc": trc if (abroad or mine) else "not_needed_on_file", "certificates": [_certificate_view(c, today) for c in mine],
                     "moves": move_views, "outlook": outlook})
    keys = sorted(cited | {"in_trc_rule"})
    return {"applies": True, "household_id": str(household.id), "household": household.name, "as_of": today.isoformat(), "people": rows,
            "figures": law_figures.cited(keys, today), "not_modelled": NOT_MODELLED,
            "not_on_file": "Nothing on file does not mean resident, or that no certificate exists."}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every household with a client abroad, a certificate, a move or a not-ordinarily-resident outlook."""
    if not applies(firm):
        return {"applies": False, "households": []}
    today = firm_time.today(firm)
    out = []
    for h in (await session.execute(select(Household).where(Household.firm_id == firm.id, Household.left_on.is_(None))
                                    .order_by(Household.name))).scalars().all():
        v = await household_view(session, firm, h.id)
        if v["people"]:
            out.append(v)
    people = [p for v in out for p in v["people"]]
    return {"applies": True, "as_of": today.isoformat(), "households": out,
            "summary": {"people": len(people), "without_covering_trc": sum(1 for p in people if p["trc"] in ("not_on_file", "expired")),
                        "moves": sum(len(p["moves"]) for p in people), "open_tasks": sum(m["open"] for p in people for m in p["moves"])},
            "not_modelled": NOT_MODELLED}
