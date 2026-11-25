"""Advice kept apart from distribution, and the direct-against-regular cost laid out (L100 India edition sections 3.1, 4.1 and 13, B-297).

An Investment Adviser is paid by the client, and the regulations keep the adviser, and at group level its family of entities, from earning
distribution commission from a client it advises. A mutual fund distributor is paid trail by the fund house out of the scheme's expense ratio.
Whether a group company earns trail from an advised household is a fact a statement shows, not one the platform can see, so:

  group entities   compliance records each company in the firm's group from a document: its name, how it relates to the firm and whether it
                   holds an AMFI ARN (kept as the document states it, never checked against AMFI). Added and never edited; the latest row for
                   a name stands, and an ended entity is a new row.
  trail            compliance records, from a statement, that a group entity earns trail from a household, and later that it ceased. The
                   latest row for a household and entity stands. It is never worked out from what a household holds: a regular plan held
                   does not say who is paid.
  the check        a household an Investment Adviser advises (a mandate on that track) where a recorded group entity that holds an ARN earns
                   trail from it is flagged for compliance. It is a flag and not a finding: whether the family-of-entities rule is breached,
                   who the family is and what to do are for compliance and counsel. An entity recorded as earning trail with no ARN recorded is
                   shown as needing a fact. With no group entity on file the check is "not assessed", which does not mean none exists.
  the comparison   for a mutual fund holding, the expense ratio of the scheme's direct plan beside its regular plan, each recorded from the scheme
                   document and dated, and the difference in percentage points and in rupees a year on the holding's value at its latest real
                   price, as arithmetic and not a projection. A ratio not on file for either plan is "not assessed"; none is estimated, and
                   an indicative figure from a guide is not used. It recommends no plan: whether service is worth the difference is the client's.

The distributor's own back office (ARN and EUIN registration, trail reconciliation, commission statements) stays out of scope: Astra is not a
distributor's system, and a client who is a distributor is a separate piece of work.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, licences, markets
from app.aurea_core.billing import _mandate_households
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Household, Mandate
from app.models.india_batch_c import GroupEntity, GroupTrail, SchemeTer
from app.models.portfolio import Holding, Instrument
from app.models.tenant import Firm
from app.provenance import ledger

ENTITY_RECORDERS = frozenset({UserRole.COMPLIANCE, UserRole.BRANCH_LEADER})
TRAIL_RECORDERS = frozenset({UserRole.COMPLIANCE})
TER_RECORDERS = frozenset({UserRole.RESEARCH_CIO, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE})
RELATIONSHIPS = {"parent": "Parent company", "subsidiary": "Subsidiary", "associate": "Associate", "sister": "Sister company", "other": "Other group company"}
PLANS = ("direct", "regular")
RULE_KEY = "in_ia_group_commission_rule"
OUT_OF_SCOPE = "The distributor's own back office (ARN and EUIN registration, trail reconciliation) is not built here."


class SeparationError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _day(value, label: str, today: date) -> date:
    if value in (None, ""):
        raise SeparationError(f"Give the {label} as YYYY-MM-DD.")
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        raise SeparationError(f"Give the {label} as YYYY-MM-DD.")
    if d > today:
        raise SeparationError(f"The {label} cannot be ahead.")
    return d


def _doc(document: str | None, what: str) -> str:
    if len((document or "").strip()) < 5:
        raise SeparationError(f"Say where {what} is filed.")
    return document.strip()


def _name(value: str | None, what: str = "company") -> str:
    n = " ".join((value or "").split())
    if len(n) < 2:
        raise SeparationError(f"Name the {what}, as the document does.")
    return n


# ── Group entities ───────────────────────────────────────────────────────────────────────────────────────────────────

async def _entity_rows(session: AsyncSession, firm_id: uuid.UUID) -> list[GroupEntity]:
    return list((await session.execute(select(GroupEntity).where(GroupEntity.firm_id == firm_id).order_by(GroupEntity.created_at, GroupEntity.id))).scalars().all())


async def entities(session: AsyncSession, firm_id: uuid.UUID, *, standing_only: bool = True) -> dict[str, GroupEntity]:
    out: dict[str, GroupEntity] = {}
    for r in await _entity_rows(session, firm_id):
        out[r.name.lower()] = r
    return {k: v for k, v in out.items() if v.kind == "registered"} if standing_only else out


async def prepare_entity(session: AsyncSession, firm: Firm, role, *, name: str | None = None, relationship: str | None = None, holds_arn=None, arn: str | None = None,
                         document: str | None = None, ended=False, reason: str | None = None) -> dict:
    if not applies(firm):
        raise SeparationError("The firm's group is recorded for an Indian firm.")
    if role not in ENTITY_RECORDERS:
        raise SeparationError("Compliance or a branch leader records the firm's group entities, from the document.")
    n = _name(name)
    rel = (relationship or "").strip().lower()
    if rel not in RELATIONSHIPS:
        raise SeparationError(f"The relationship is one of {', '.join(RELATIONSHIPS)}.")
    if holds_arn in (None, ""):
        raise SeparationError("Say whether the company holds an AMFI ARN, as the document does.")
    holds = holds_arn if isinstance(holds_arn, bool) else str(holds_arn).strip().lower() in ("true", "yes", "1")
    number = " ".join((arn or "").split()) or None
    if holds and not number:
        raise SeparationError("Give the ARN as the document states it.")
    standing = (await entities(session, firm.id, standing_only=False)).get(n.lower())
    end = ended if isinstance(ended, bool) else str(ended).strip().lower() in ("true", "yes", "1")
    if end:
        if standing is None or standing.kind != "registered":
            raise SeparationError(f"{n} is not on file as a group entity, so there is nothing to end.")
        if not (reason or "").strip():
            raise SeparationError("Say why the company is no longer in the group.")
    elif standing is not None and standing.kind == "registered" and standing.holds_arn == holds and (standing.arn or None) == (number if holds else None) \
            and standing.relationship_to_firm == rel:
        raise SeparationError(f"{n} is already on file as recorded.")
    return {"name": n, "relationship": rel, "holds_arn": holds, "arn": number if holds else None, "kind": "ended" if end else "registered",
            "document": _doc(document, "the document naming the group"), "reason": (reason or "").strip() or None, "before": standing}


async def record_entity(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_entity(session, firm, role, **terms)
    row = GroupEntity(created_at=utcnow(), firm_id=firm.id, name=p["name"], relationship_to_firm=p["relationship"], holds_arn=p["holds_arn"], arn=p["arn"],
                      kind=p["kind"], source_document=p["document"], reason=p["reason"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="group_entity_recorded", actor=by, content={
        "name": row.name, "relationship": row.relationship_to_firm, "holds_arn": row.holds_arn, "arn": row.arn, "kind": row.kind, "document": row.source_document})
    return await overview(session, firm)


# ── Trail from a household ───────────────────────────────────────────────────────────────────────────────────────────

async def _trail_rows(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID | None = None) -> list[GroupTrail]:
    stmt = select(GroupTrail).where(GroupTrail.firm_id == firm_id)
    if household_id is not None:
        stmt = stmt.where(GroupTrail.household_id == household_id)
    return list((await session.execute(stmt.order_by(GroupTrail.statement_on, GroupTrail.created_at, GroupTrail.id))).scalars().all())


def _standing_trail(rows: list[GroupTrail]) -> dict[tuple[uuid.UUID, str], GroupTrail]:
    out: dict[tuple[uuid.UUID, str], GroupTrail] = {}
    for r in rows:
        out[(r.household_id, r.entity_name.lower())] = r
    return out


async def prepare_trail(session: AsyncSession, firm: Firm, role, *, household_id=None, entity: str | None = None, statement_on=None, document: str | None = None,
                        amount_inr=None, ceased=False, reason: str | None = None) -> dict:
    if not applies(firm):
        raise SeparationError("Trail from a household is recorded for an Indian firm.")
    if role not in TRAIL_RECORDERS:
        raise SeparationError("Compliance records that a group entity earns trail from a household, from the statement.")
    try:
        hid = household_id if isinstance(household_id, uuid.UUID) else uuid.UUID(str(household_id))
    except ValueError:
        raise SeparationError("Give the household from the client's page.")
    household = await session.get(Household, hid)
    if household is None or household.firm_id != firm.id:
        raise SeparationError("Household not found.")
    ent = (await entities(session, firm.id)).get(_name(entity, "group entity").lower())
    if ent is None:
        raise SeparationError(f"{' '.join((entity or '').split())} is not on file as a group entity: record the group first.")
    today = firm_time.today(firm)
    day = _day(statement_on, "statement's date", today)
    amount = None
    if amount_inr not in (None, ""):
        try:
            amount = round(float(str(amount_inr).replace(",", "")), 2)
        except ValueError:
            raise SeparationError("Give the trail in rupees, as the statement shows it.")
        if amount < 0:
            raise SeparationError("The trail cannot be negative.")
    standing = _standing_trail(await _trail_rows(session, firm.id, hid)).get((hid, ent.name.lower()))
    end = ceased if isinstance(ceased, bool) else str(ceased).strip().lower() in ("true", "yes", "1")
    if end:
        if standing is None or standing.kind != "earns":
            raise SeparationError(f"{ent.name} is not on file as earning trail from {household.name}, so there is nothing to end.")
        if not (reason or "").strip():
            raise SeparationError("Say how the statement shows the trail has ceased.")
        if day < standing.statement_on:
            raise SeparationError("A later statement stands over an earlier one.")
    elif standing is not None and standing.kind == "earns" and day <= standing.statement_on:
        raise SeparationError(f"A statement of {standing.statement_on.isoformat()} is already on file: a later one stands over it.")
    return {"household": household, "entity": ent, "kind": "ceased" if end else "earns", "statement_on": day, "amount": amount,
            "document": _doc(document, "the statement"), "reason": (reason or "").strip() or None, "before": standing}


async def record_trail(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_trail(session, firm, role, **terms)
    row = GroupTrail(created_at=utcnow(), firm_id=firm.id, household_id=p["household"].id, entity_name=p["entity"].name, kind=p["kind"],
                     statement_on=p["statement_on"], amount_inr=p["amount"], source_document=p["document"], reason=p["reason"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="group_trail_recorded", actor=by, content={
        "household_id": str(row.household_id), "entity": row.entity_name, "kind": row.kind, "statement_on": row.statement_on.isoformat(), "document": row.source_document})
    return await check(session, firm, household_id=p["household"].id)


# ── The check ────────────────────────────────────────────────────────────────────────────────────────────────────────

async def _advised_households(session: AsyncSession, firm: Firm) -> dict[uuid.UUID, str]:
    """Households with a mandate on the Investment Adviser's track, by name."""
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True)))).scalars().all()
    owners = await _mandate_households(session, firm.id, list(mandates))
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    out: dict[uuid.UUID, str] = {}
    for m in mandates:
        track = licences.track_for(firm, m)
        home = owners.get(m.id)
        if track is not None and track.key == "investment_adviser" and home in names:
            out[home] = names[home]
    return out


async def check(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "flags": []}
    group = await entities(session, firm.id)
    advised = await _advised_households(session, firm)
    standing = _standing_trail(await _trail_rows(session, firm.id, household_id))
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    flags, facts_needed = [], []
    for (hid, ent_key), t in standing.items():
        if t.kind != "earns" or hid not in advised:
            continue
        ent = group.get(ent_key)
        base = {"household_id": str(hid), "household": names.get(hid), "entity": t.entity_name, "statement_on": t.statement_on.isoformat(),
                "amount": float(t.amount_inr) if t.amount_inr is not None else None, "document": t.source_document, "recorded_by": t.recorded_by}
        if ent is None:
            facts_needed.append({**base, "needs": "the entity is no longer on file as a group entity: record whether it is still in the group"})
        elif ent.holds_arn:
            flags.append({**base, "arn": ent.arn, "relationship": RELATIONSHIPS.get(ent.relationship_to_firm), "state": "flag",
                          "said": "An advised household, and a group entity that holds an ARN earns trail from it on a statement compliance recorded. A flag for compliance to "
                                  "look at, not a finding: the rule, who counts as the family and what follows are for compliance and counsel."})
        else:
            facts_needed.append({**base, "needs": "the entity is recorded as earning trail but not as holding an ARN: record the ARN, or why it holds none"})
    if household_id is not None:
        flags = [f for f in flags if f["household_id"] == str(household_id)]
    state = "not_assessed" if not group else "assessed"
    return {"applies": True, "as_of": today.isoformat(), "state": state, "flags": sorted(flags, key=lambda f: (f["household"] or "", f["entity"])),
            "facts_needed": facts_needed, "advised_households": len(advised), "group_entities": len(group),
            "rule": law_figures.cited([RULE_KEY], today)[0],
            "said": ("No group entity is on file, so this is not assessed, which does not mean the firm has none." if not group else
                     "Read from what compliance recorded. No trail on file does not mean none is earned."),
            "out_of_scope": OUT_OF_SCOPE}


# ── Expense ratios and the comparison ────────────────────────────────────────────────────────────────────────────────

async def _ter_rows(session: AsyncSession, firm_id: uuid.UUID) -> list[SchemeTer]:
    return list((await session.execute(select(SchemeTer).where(SchemeTer.firm_id == firm_id).order_by(SchemeTer.as_of, SchemeTer.created_at, SchemeTer.id))).scalars().all())


def _standing_ter(rows: list[SchemeTer]) -> dict[tuple[str, str], SchemeTer]:
    out: dict[tuple[str, str], SchemeTer] = {}
    for r in rows:
        out[(r.scheme.lower(), r.plan)] = r
    return out


async def prepare_ter(session: AsyncSession, firm: Firm, role, *, scheme: str | None = None, plan: str | None = None, ter_pct=None, as_of=None,
                      document: str | None = None) -> dict:
    if not applies(firm):
        raise SeparationError("A scheme's expense ratio is recorded for an Indian firm.")
    if role not in TER_RECORDERS:
        raise SeparationError("Research, the portfolio team or compliance records a scheme's expense ratio, from the scheme document.")
    name = _name(scheme, "scheme")
    p = (plan or "").strip().lower()
    if p not in PLANS:
        raise SeparationError("The plan is direct or regular.")
    try:
        ter = round(float(str(ter_pct).replace("%", "").replace(",", "")), 3)
    except (TypeError, ValueError):
        raise SeparationError("Give the total expense ratio as the scheme document states it, a percentage a year.")
    if not (0 < ter <= 5):
        raise SeparationError("An expense ratio is a percentage a year above nothing and not above 5: check the document.")
    day = _day(as_of, "document's date", firm_time.today(firm))
    known = {(i.values_tags or {}).get("scheme") for i in (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    if name not in known:
        raise SeparationError(f"No fund on file here belongs to the scheme {name!r}: use the scheme's name as the fund carries it.")
    before = _standing_ter(await _ter_rows(session, firm.id)).get((name.lower(), p))
    if before is not None and day < before.as_of:
        raise SeparationError(f"The ratio of {before.as_of.isoformat()} is already on file: a later document stands over an earlier one.")
    return {"scheme": name, "plan": p, "ter": ter, "as_of": day, "document": _doc(document, "the scheme document"), "before": before}


async def record_ter(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_ter(session, firm, role, **terms)
    row = SchemeTer(created_at=utcnow(), firm_id=firm.id, scheme=p["scheme"], plan=p["plan"], ter_pct=p["ter"], as_of=p["as_of"], source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="scheme_ter_recorded", actor=by, content={
        "scheme": row.scheme, "plan": row.plan, "ter_pct": float(row.ter_pct), "as_of": row.as_of.isoformat(), "document": row.source_document})
    return await comparison(session, firm)


async def comparison(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    """Each mutual fund holding with its scheme's direct and regular ratios beside it, from what was recorded."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "holdings": []}
    from app.aurea_core.performance import household_account_ids
    from app.aurea_core.valuation import latest_prices
    from app.models.graph import Account

    ters = _standing_ter(await _ter_rows(session, firm.id))
    insts = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    stmt = select(Holding).join(Account, Account.id == Holding.account_id).where(Account.firm_id == firm.id, Holding.quantity > 0)
    if household_id is not None:
        stmt = stmt.where(Holding.account_id.in_(await household_account_ids(session, household_id, firm_id=firm.id) or [uuid.uuid4()]))
    holdings = (await session.execute(stmt)).scalars().all()
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
    prices = await latest_prices(session, firm.id)
    rows = []
    for h in holdings:
        inst = insts.get(h.instrument_id)
        tags = (inst.values_tags or {}) if inst else {}
        if not tags.get("scheme") or tags.get("fund_plan") not in PLANS:
            continue
        price = prices.get(h.instrument_id)
        value = round(float(h.quantity) * float(price.close), 2) if price is not None and price.is_real else None
        d, r = ters.get((tags["scheme"].lower(), "direct")), ters.get((tags["scheme"].lower(), "regular"))
        row = {"symbol": inst.symbol, "fund": inst.name, "scheme": tags["scheme"], "plan": tags["fund_plan"], "account": accounts[h.account_id].name if h.account_id in accounts else None,
               "value": value, "currency": firm.base_currency,
               "direct": {"ter": float(d.ter_pct), "as_of": d.as_of.isoformat(), "document": d.source_document} if d else None,
               "regular": {"ter": float(r.ter_pct), "as_of": r.as_of.isoformat(), "document": r.source_document} if r else None}
        if d is None or r is None:
            missing = [p for p, x in (("direct", d), ("regular", r)) if x is None]
            row.update(state="not_assessed", needs=f"the {' and '.join(missing)} plan's expense ratio from the scheme document")
        else:
            gap = round(float(r.ter_pct) - float(d.ter_pct), 3)
            row.update(state="assessed", gap_points=gap, needs=None,
                       yearly_difference=round(value * gap / 100, 2) if value is not None else None,
                       value_note=None if value is not None else "no real price on file, so the rupees are not worked")
        rows.append(row)
    rows.sort(key=lambda x: (x["state"] != "assessed", -(x.get("yearly_difference") or 0)))
    return {"applies": True, "as_of": today.isoformat(), "holdings": rows, "assessed": sum(1 for x in rows if x["state"] == "assessed"),
            "not_assessed": sum(1 for x in rows if x["state"] == "not_assessed"),
            "said": ("The ratios are those recorded from each scheme's document; none is estimated. The difference is the gap between the two plans' ratios on the holding's "
                     "value at its latest real price, a yearly figure laid out and not a projection. It recommends no plan: a regular plan pays for a distributor's service, "
                     "and whether that is worth it is the client's."),
            "ters": [{"scheme": r.scheme, "plan": r.plan, "ter": float(r.ter_pct), "as_of": r.as_of.isoformat()} for r in _standing_ter(await _ter_rows(session, firm.id)).values()]}


async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "entities": []}
    group = await entities(session, firm.id)
    return {"applies": True, "as_of": today.isoformat(),
            "entities": [{"name": e.name, "relationship": e.relationship_to_firm, "relationship_label": RELATIONSHIPS.get(e.relationship_to_firm), "holds_arn": e.holds_arn,
                          "arn": e.arn, "document": e.source_document, "recorded_by": e.recorded_by} for e in group.values()],
            "check": await check(session, firm, household_id=household_id), "comparison": await comparison(session, firm, household_id=household_id),
            "out_of_scope": OUT_OF_SCOPE}
