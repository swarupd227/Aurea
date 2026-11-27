"""Advice under the Investment Adviser operating model (B-399).

Four things an Indian adviser's advice file is also an audit artefact for:

    the risk profile a recommendation rests on   frozen onto the record when it is made, from the questionnaire completed
                                                 on or before that day. None by then reads "not on file", never a guess.
    retention                                    how long a record is kept, from `law_figures` (assumed, unreviewed),
                                                 shown beside it. Nothing here or anywhere deletes a record.
    capacity                                     the clients on the adviser's track against the line above which an
                                                 individual adviser registers as non-individual (assumed).
    sample audit                                 compliance draws files and records what it found on each. The draw is
                                                 worked from the firm, the draw's number and each file's id, so nobody
                                                 chooses which files are read; files never drawn come first.

The checks beside a drawn file are what the record shows, as facts. A finding is compliance's, in its own words: nothing
here marks a file as meeting a standard, and none of it says the firm complies with anything.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import consents, firm_time, law_figures, licences, pre_advice
from app.models.advice_audit import AdviceAudit, AdviceAuditResult
from app.models.advice_file import AdviceOutcome, AdviceRecommendation
from app.models.consent_registry import ConsentRecord
from app.models.graph import Household, LegalEntity, Mandate, Person
from app.models.risk_assessment import RiskAssessment
from app.models.tenant import Firm
from app.provenance import ledger

ADVISER = pre_advice.ADVISER
RECORDERS = ("compliance",)
READERS = ("adviser", "paraplanner", "portfolio_team", "compliance", "branch_leader")
ITEMS = {"risk_profile": "Risk profile", "suitability": "Suitability", "alternatives": "Alternatives",
         "client_decision": "Client's decision", "paperwork": "Paperwork before advice", "disclosure": "Disclosure",
         "other": "Other"}
OUTCOMES = ("met", "finding")
DEFAULT_SAMPLE = 5
MOST_SAMPLE = 50
DEFAULT_PERIOD_DAYS = 365
NEAR = 0.8                     # the share of the registration line from which the count reads "near"
RETENTION_FIGURE = "in_ia_record_retention_years"
LINE_FIGURE = "in_ia_individual_client_limit"
REGISTRATION_TYPES = ("individual", "non_individual")
REFUSED_ROLE = "The sample audit of advice files is drawn and recorded by compliance."
NOT_APPLICABLE = "The sample audit is for a firm registered as an Investment Adviser."
NOT_A_FINDING = "A drawn file with no result recorded has not been reviewed; it is not a file with no finding."


class AuditError(ValueError):
    """A draw or a result that cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    return firm is not None and any(l.key == ADVISER for l in licences.held(firm))


def _instant(at):
    """A stored instant, with or without its zone (SQLite drops it), so two can be compared."""
    return at.replace(tzinfo=None) if at is not None and at.tzinfo else at


# ── The risk profile a recommendation rests on ───────────────────────────────

async def risk_basis(session: AsyncSession, firm: Firm | None, household_id: uuid.UUID, person_id: uuid.UUID | None, on: date) -> dict:
    """The questionnaire a recommendation made on `on` rests on: the person's own where they were asked for themselves,
    else the household's, else (with no person named) the latest of anyone's in it. Completed on or before that day."""
    rows = [r for r in (await session.execute(select(RiskAssessment).where(
        RiskAssessment.household_id == household_id, RiskAssessment.indicated.is_not(None))
        .order_by(RiskAssessment.completed_at.desc(), RiskAssessment.created_at.desc()))).scalars().all()
        if firm_time.today(firm, r.completed_at) <= on]
    household_wide = next((r for r in rows if r.person_id is None and r.entity_id is None), None)
    if person_id:
        row = next((r for r in rows if r.person_id == person_id), None) or household_wide
    else:
        row = household_wide or (rows[0] if rows else None)
    if row is None:
        return {"status": "not_on_file", "as_of": on.isoformat()}
    subject = None
    if row.person_id:
        subject = await session.get(Person, row.person_id)
        subject = subject.full_name if subject else None
    elif row.entity_id:
        subject = await session.get(LegalEntity, row.entity_id)
        subject = subject.name if subject else None
    answered = firm_time.today(firm, row.completed_at)
    consent = consents.standing([c for c in (await session.execute(select(ConsentRecord).where(
        ConsentRecord.household_id == household_id, ConsentRecord.kind == "risk_profile"))).scalars().all()
        if c.given_on <= on]).get("risk_profile")
    # A consent covers the answers on file when it was given: on a later day, or on the same day and not recorded before
    # them (both stamps are the database's).
    consented = (consent is not None and consent.status == "granted" and (consent.given_on > answered or (
        consent.given_on == answered and _instant(consent.created_at) >= _instant(row.created_at))))
    return {"status": "on_file", "assessment_id": str(row.id), "of": subject or "household", "indicated": row.indicated,
            "tolerance": row.tolerance, "capacity": row.capacity, "answered_on": answered.isoformat(),
            "consent": {"given_on": consent.given_on.isoformat(), "method": consent.method} if consented else None}


def basis_text(basis: dict | None) -> str:
    """The basis as one line for a card."""
    if not basis:
        return "Not kept: recorded before the record carried it"
    if basis["status"] != "on_file":
        return f"Not on file: no complete questionnaire by {firm_time.spoken(date.fromisoformat(basis['as_of']))}"
    return (f"{basis['indicated']} ({basis['of']}), answered {firm_time.spoken(date.fromisoformat(basis['answered_on']))}"
            + (f", consented {firm_time.spoken(date.fromisoformat(basis['consent']['given_on']))}" if basis.get("consent") else ""))


# ── Retention ────────────────────────────────────────────────────────────────

def retention(firm: Firm | None) -> dict | None:
    """How long an advice record is kept, where the firm advises under the Indian licence. Assumed; deletes nothing."""
    if not applies(firm):
        return None
    today = firm_time.today(firm)
    fig = law_figures.get(RETENTION_FIGURE, today)
    return {"years": int(fig.value), "assumed": True, "deletes": False, "from": "the record's latest entry",
            "figure": fig.view(today)}


def keep_until(latest: date, years: int) -> date:
    try:
        return latest.replace(year=latest.year + years)
    except ValueError:            # 29 February
        return latest.replace(year=latest.year + years, day=28)


# ── Capacity ─────────────────────────────────────────────────────────────────

async def capacity(session: AsyncSession, firm: Firm) -> dict | None:
    """The clients on the adviser's track against the individual-registration line. A client is counted as each person
    or entity holding an active advisory mandate (assumed: SEBI's count was not re-read), households beside it."""
    if not applies(firm):
        return None
    homes = (await session.execute(select(Household).where(Household.firm_id == firm.id, Household.left_on.is_(None)))).scalars().all()
    ids = [h.id for h in homes]
    owners: dict[uuid.UUID, uuid.UUID] = {}
    for model in (Person, LegalEntity):
        if ids:
            owners.update({i: h for i, h in (await session.execute(select(model.id, model.household_id).where(model.household_id.in_(ids)))).all()})
    clients: set = set()
    households: set = set()
    for m in (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True)))).scalars().all():
        owner = m.person_id or m.entity_id
        track = licences.track_for(firm, m)
        if owner in owners and track is not None and track.key == ADVISER:
            clients.add(owner)
            households.add(owners[owner])
    today = firm_time.today(firm)
    fig = law_figures.get(LINE_FIGURE, today)
    line = int(fig.value)
    registration = ((firm.settings or {}).get("sebi_registration") or {}).get("ia_registration_type")
    n = len(clients)
    state = ("not_applicable" if registration == "non_individual" else "above" if n > line else
             "near" if n >= line * NEAR else "within")
    return {"clients": n, "households": len(households), "line": line, "share": round(n / line, 4), "state": state,
            "registration": registration, "assumed": True, "counted": "each person or entity holding an active advisory mandate",
            "figure": fig.view(today)}


# ── The sample audit ─────────────────────────────────────────────────────────

def _may(role: Any, roles: tuple, words: str) -> None:
    if role is None or str(role) not in roles:
        raise AuditError(words)


def _day(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise AuditError(f"{label} must be a date, YYYY-MM-DD.")


async def _audits(session: AsyncSession, firm_id: uuid.UUID) -> list[AdviceAudit]:
    return list((await session.execute(select(AdviceAudit).where(AdviceAudit.firm_id == firm_id)
                                       .order_by(AdviceAudit.seq.desc()))).scalars().all())


async def prepare_draw(session: AsyncSession, firm: Firm, *, role: Any, size: Any = None, since: Any = None, until: Any = None) -> dict:
    """What drawing a sample would refuse, refused without writing, and the files it would draw."""
    _may(role, RECORDERS, REFUSED_ROLE)
    if not applies(firm):
        raise AuditError(NOT_APPLICABLE)
    today = firm_time.today(firm)
    end = _day(until, "until") or today
    start = _day(since, "since") or end - timedelta(days=DEFAULT_PERIOD_DAYS)
    if start > end or end > today:
        raise AuditError("The period runs from since to until, and not past today.")
    population = [r for r in (await session.execute(select(AdviceRecommendation.id).where(
        AdviceRecommendation.firm_id == firm.id, AdviceRecommendation.recommended_on >= start,
        AdviceRecommendation.recommended_on <= end))).scalars().all()]
    if not population:
        raise AuditError(f"No advice was put on file from {start.isoformat()} to {end.isoformat()}, so there is nothing to draw.")
    if size in (None, ""):
        n = min(DEFAULT_SAMPLE, len(population))
    else:
        try:
            n = int(float(str(size)))
        except ValueError:
            raise AuditError("The sample size is a whole number of files.")
        if n < 1 or n > min(MOST_SAMPLE, len(population)):
            raise AuditError(f"Draw between 1 and {min(MOST_SAMPLE, len(population))} file(s): {len(population)} were made in the period.")
    earlier = await _audits(session, firm.id)
    seq = (earlier[0].seq if earlier else 0) + 1
    drawn_before: dict[str, int] = {}
    for a in earlier:
        for rid in a.sample or []:
            drawn_before[rid] = drawn_before.get(rid, 0) + 1
    ordered = sorted((str(r) for r in population),
                     key=lambda r: (drawn_before.get(r, 0), hashlib.sha256(f"{firm.id}:{seq}:{r}".encode()).hexdigest()))
    return {"seq": seq, "since": start, "until": end, "population": len(population), "sample": ordered[:n], "today": today,
            "never_drawn": sum(1 for r in ordered[:n] if not drawn_before.get(r))}


async def record_draw(session: AsyncSession, firm: Firm, *, role: Any, by: str, **terms) -> dict:
    p = await prepare_draw(session, firm, role=role, **terms)
    row = AdviceAudit(firm_id=firm.id, seq=p["seq"], drawn_on=p["today"], period_from=p["since"], period_to=p["until"],
                      population=p["population"], sample=p["sample"], drawn_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="advice_audit_drawn", actor=by, content={
        "audit_id": str(row.id), "seq": row.seq, "population": row.population, "sample": row.sample,
        "since": row.period_from.isoformat(), "until": row.period_to.isoformat()})
    return await overview(session, firm)


async def _checks(session: AsyncSession, firm: Firm, rec: AdviceRecommendation, gates: dict) -> dict:
    """What the record shows about one drawn file, as facts."""
    last = (await session.execute(select(AdviceOutcome).where(AdviceOutcome.recommendation_id == rec.id)
                                  .order_by(AdviceOutcome.seq.desc()).limit(1))).scalars().first()
    basis = rec.risk_basis
    if rec.household_id not in gates:
        gates[rec.household_id] = await pre_advice.held_view(session, firm, rec.household_id)
    gate = gates[rec.household_id]
    return {"risk_profile": ("not_kept" if not basis else "not_on_file" if basis["status"] != "on_file" else basis["indicated"]),
            "consented_when_advised": bool(basis and basis.get("consent")),
            "alternatives": len(rec.alternatives or []),
            "decision": last.status if last else None, "evidence_kind": last.evidence_kind if last else None,
            "paperwork_today": "not_applicable" if gate is None else "complete" if gate["met"] else "incomplete"}


async def prepare_result(session: AsyncSession, firm: Firm, *, role: Any, audit_id: Any = None, recommendation_id: Any,
                         outcome: Any, findings: Any = None) -> dict:
    """What recording a drawn file's result would refuse, refused without writing."""
    _may(role, RECORDERS, REFUSED_ROLE)
    if not applies(firm):
        raise AuditError(NOT_APPLICABLE)
    try:
        rid = str(uuid.UUID(str(recommendation_id)))
        aid = uuid.UUID(str(audit_id)) if audit_id not in (None, "") else None
    except (TypeError, ValueError):
        raise AuditError("Give the audit and the recommendation by their ids from read_advice_audit.")
    audits = await _audits(session, firm.id)
    audit = next((a for a in audits if (aid is None or a.id == aid) and rid in (a.sample or [])), None)
    if audit is None:
        raise AuditError("That file was not drawn in that audit: a result is recorded on a drawn file only.")
    rec = await session.get(AdviceRecommendation, uuid.UUID(rid))
    if rec is None or rec.firm_id != firm.id:
        raise AuditError("That recommendation is not on this firm's advice file.")
    done = (await session.execute(select(func.count()).select_from(AdviceAuditResult).where(
        AdviceAuditResult.audit_id == audit.id, AdviceAuditResult.recommendation_id == rec.id))).scalar_one()
    if done:
        raise AuditError("That file's result in this audit is already recorded; results are added and never edited.")
    result = str(outcome or "").strip().lower()
    if result not in OUTCOMES:
        raise AuditError("The result is met (no finding) or finding.")
    found = []
    for item in findings or []:
        if not isinstance(item, dict):
            raise AuditError("Give each finding as {item, words}.")
        key = str(item.get("item") or "").strip().lower().replace(" ", "_")
        words = str(item.get("words") or item.get("finding") or "").strip()
        if key not in ITEMS:
            raise AuditError(f"A finding's item is one of: {', '.join(ITEMS)}.")
        if len(words) < 8:
            raise AuditError("Each finding says what was found, in the reviewer's words.")
        found.append({"item": key, "words": words[:1000]})
    if result == "finding" and not found:
        raise AuditError("A file with a finding names it: the item and what was found.")
    if result == "met" and found:
        raise AuditError("A file with findings is recorded as finding, not met.")
    household = await session.get(Household, rec.household_id)
    return {"audit": audit, "recommendation": rec, "household": household, "outcome": result, "findings": found,
            "checks": await _checks(session, firm, rec, {}), "today": firm_time.today(firm)}


async def record_result(session: AsyncSession, firm: Firm, *, role: Any, by: str, **terms) -> dict:
    p = await prepare_result(session, firm, role=role, **terms)
    audit, rec = p["audit"], p["recommendation"]
    session.add(AdviceAuditResult(firm_id=firm.id, audit_id=audit.id, recommendation_id=rec.id, outcome=p["outcome"],
                                  findings=p["findings"], checks=p["checks"], reviewed_on=p["today"], recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="advice_audit_result", actor=by, content={
        "audit_id": str(audit.id), "recommendation_id": str(rec.id), "outcome": p["outcome"],
        "findings": [f["item"] for f in p["findings"]]})
    return await overview(session, firm)


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """The operating model on one view: capacity, retention, who is held before advice, and the sample audits with each
    drawn file's checks and result."""
    if not applies(firm):
        return {"applies": False, "why": NOT_APPLICABLE}
    today = firm_time.today(firm)
    audits = await _audits(session, firm.id)
    results = (await session.execute(select(AdviceAuditResult).where(AdviceAuditResult.firm_id == firm.id))).scalars().all()
    by_file = {(r.audit_id, str(r.recommendation_id)): r for r in results}
    wanted = [uuid.UUID(i) for a in audits for i in a.sample or []]
    recs = {str(r.id): r for r in (await session.execute(select(AdviceRecommendation).where(
        AdviceRecommendation.firm_id == firm.id, AdviceRecommendation.id.in_(wanted)))).scalars().all()} if wanted else {}
    homes = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    gates: dict = {}
    out = []
    for a in audits:
        files = []
        for rid in a.sample or []:
            rec = recs.get(rid)
            if rec is None:
                continue
            r = by_file.get((a.id, rid))
            files.append({"recommendation_id": rid, "household_id": str(rec.household_id), "household": homes.get(rec.household_id),
                          "category": rec.category, "recommendation": rec.recommendation,
                          "recommended_on": rec.recommended_on.isoformat(), "recorded_by": rec.recorded_by,
                          "checks": r.checks if r else await _checks(session, firm, rec, gates),
                          "result": None if r is None else {"outcome": r.outcome, "findings": r.findings,
                                                            "reviewed_on": r.reviewed_on.isoformat(), "recorded_by": r.recorded_by}})
        reviewed = [f for f in files if f["result"]]
        with_findings = sum(1 for f in reviewed if f["result"]["outcome"] == "finding")
        out.append({"audit_id": str(a.id), "seq": a.seq, "drawn_on": a.drawn_on.isoformat(), "since": a.period_from.isoformat(),
                    "until": a.period_to.isoformat(), "population": a.population, "drawn": len(files), "reviewed": len(reviewed),
                    "with_findings": with_findings, "share": round(with_findings / len(reviewed), 4) if reviewed else None,
                    "drawn_by": a.drawn_by, "files": files})
    reviewed = sum(a["reviewed"] for a in out)
    with_findings = sum(a["with_findings"] for a in out)
    by_item: dict[str, int] = {}
    for r in results:
        for f in r.findings or []:
            by_item[f["item"]] = by_item.get(f["item"], 0) + 1
    gated = await pre_advice.for_firm(session, firm)
    return {"applies": True, "as_of": today.isoformat(), "capacity": await capacity(session, firm), "retention": retention(firm),
            "pre_advice": {"households": len(gated), "held": [{"household_id": g["household_id"], "household": g["household"],
                                                               "missing": g["missing"]} for g in gated if not g["met"]]},
            "audits": out, "metrics": {"audits": len(out), "drawn": sum(a["drawn"] for a in out), "reviewed": reviewed,
                                       "awaiting": sum(a["drawn"] - a["reviewed"] for a in out), "with_findings": with_findings,
                                       "share": round(with_findings / reviewed, 4) if reviewed else None, "by_item": by_item},
            "items": ITEMS, "not_a_finding": NOT_A_FINDING}
