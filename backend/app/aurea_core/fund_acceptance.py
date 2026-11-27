"""Which fund houses and schemes take a resident of the United States or Canada (L200-1 India sections 4.1 and 10, B-391).

Fund houses restrict investors resident in the US or Canada because of the reporting their own countries ask of them, and
each decides for itself. So this is a list a person keeps: for a fund house, or one of its schemes, whether it accepts a
resident of that country, accepts on conditions, or does not accept, as the fund house's own document states, with the day
it was stated. Added and never edited; the latest line for a house or a scheme stands, and a scheme's own line outranks its
house's.

`shelf.barred` reads it (`bars`), so the rebalancer and a direct order refuse a purchase a recorded line refuses. Sales are
never stopped. With nothing on file for a fund the answer is "not assessed": it bars nothing, and it does not mean the fund
accepts. No line is ever worked out or assumed for a real fund house.

Whether the rule applies to a client is read from what a person recorded on their residence (`residency.py`): the country of
residence or a declared tax residence of the United States or Canada, or a FATCA declaration as a US person. A US address or
telephone recorded as an indicium alone does not make anyone a US resident; it is named and bars nothing. No residence on
file means not assessed.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.india_agreements import FundAcceptance
from app.models.portfolio import Holding, Instrument
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.RESEARCH_CIO, UserRole.COMPLIANCE, UserRole.OPERATIONS})
COUNTRIES = {"US": "the United States", "CA": "Canada"}
STANCES = {"accepts": "Accepts", "conditions": "Accepts on conditions", "refuses": "Does not accept"}
NOT_ASSESSED = "not_assessed"
_NAMES = {"US": ("us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "america"),
          "CA": ("ca", "can", "canada")}
NOTE = ("A line is what a person recorded from the fund house's document. Nothing on file reads not assessed and bars nothing: it "
        "does not mean the fund house accepts. Only purchases are stopped.")


class AcceptanceError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def country_code(value) -> str | None:
    said = " ".join(str(value or "").split()).casefold()
    return next((code for code, names in _NAMES.items() if said in names), None)


def house_of(instrument: Instrument | None) -> str | None:
    return ((instrument.values_tags or {}).get("amc") or None) if instrument is not None else None


def is_scheme(instrument: Instrument | None) -> bool:
    tags = (instrument.values_tags or {}) if instrument is not None else {}
    return bool(tags.get("amc") or tags.get("amfi_code") or tags.get("fund_plan"))


def _key(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def exposure(facts: dict) -> dict[str, str]:
    """The countries whose rule reaches this person, each with the recorded fact that says so."""
    out: dict[str, str] = {}
    home = country_code(facts.get("country_of_residence"))
    if home:
        out[home] = f"recorded as living in {COUNTRIES[home]}"
    for t in facts.get("tax_residencies") or []:
        code = country_code(t)
        if code:
            out.setdefault(code, f"declared tax residence in {COUNTRIES[code]}")
    if facts.get("us_person") is True:
        out.setdefault("US", "declared a US person for FATCA")
    return out


async def standing(session: AsyncSession, firm_id: uuid.UUID) -> dict[tuple[str, str, str], FundAcceptance]:
    """The line that stands: by ("scheme", symbol, country) and ("house", name, country)."""
    out: dict = {}
    for r in (await session.execute(select(FundAcceptance).where(FundAcceptance.firm_id == firm_id)
                                    .order_by(FundAcceptance.stated_on, FundAcceptance.created_at, FundAcceptance.id))).scalars().all():
        key = ("scheme", _key(r.symbol), r.country) if r.symbol else ("house", _key(r.fund_house), r.country)
        out[key] = r
    return out


def line_for(lines: dict, instrument: Instrument, country: str) -> FundAcceptance | None:
    """The scheme's own line, else its house's."""
    found = lines.get(("scheme", _key(instrument.symbol), country))
    if found is None and house_of(instrument):
        found = lines.get(("house", _key(house_of(instrument)), country))
    return found


async def _holders(session: AsyncSession, firm: Firm, mandate: Mandate) -> list[Person]:
    """The mandate's own holder, and the other holders of a joint account on it where onboarding recorded them."""
    from app.models.enums import PartyRole
    from app.models.onboarding import OnboardingCase, OnboardingParty

    people: dict[uuid.UUID, Person] = {}
    if mandate.person_id:
        p = await session.get(Person, mandate.person_id)
        if p is not None:
            people[p.id] = p
    accounts = (await session.execute(select(Account).where(Account.mandate_id == mandate.id))).scalars().all()
    joint = {str(a.id) for a in accounts if str(a.registration_type or "").startswith("joint_")}
    if joint and people:
        household_id = next(iter(people.values())).household_id
        names: set[str] = set()
        for case in (await session.execute(select(OnboardingCase).where(
                OnboardingCase.firm_id == firm.id, OnboardingCase.activated_at.is_not(None)))).scalars().all():
            if (case.materialized or {}).get("account_id") in joint:
                names |= {_key(p.legal_name) for p in (await session.execute(select(OnboardingParty).where(
                    OnboardingParty.case_id == case.id, OnboardingParty.role.in_([PartyRole.OWNER, PartyRole.JOINT_OWNER])))).scalars().all()}
        if names and household_id:
            for p in (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all():
                if _key(p.full_name) in names:
                    people.setdefault(p.id, p)
    return list(people.values())


async def holder_exposure(session: AsyncSession, firm: Firm, mandate: Mandate) -> list[dict]:
    """Which holders of the mandate the rule reaches. Each is {name, person_id, country, why}."""
    from app.aurea_core import residency

    out = []
    for p in await _holders(session, firm, mandate):
        facts, row = residency.standing(await residency.records(session, firm.id, p.id))
        if row is None:
            continue
        for country, why in exposure(facts).items():
            out.append({"person_id": str(p.id), "name": p.full_name, "country": country, "why": why})
    return out


def _reason(line: FundAcceptance, instrument: Instrument, who: dict) -> str:
    whose = f"{line.fund_house}" if not line.symbol else f"{line.fund_house} for this scheme"
    return (f"not accepted from a resident of {COUNTRIES[line.country]} by {whose} ({line.document}, {line.stated_on.isoformat()}), "
            f"and {who['name']} is {who['why']}")


async def bars(session: AsyncSession, firm: Firm, mandate: Mandate) -> dict[uuid.UUID, str]:
    """Each scheme this mandate may not buy because a recorded line refuses one of its holders, for `shelf.barred`."""
    if not applies(firm):
        return {}
    lines = await standing(session, firm.id)
    if not any(r.stance == "refuses" for r in lines.values()):
        return {}
    reached = await holder_exposure(session, firm, mandate)
    if not reached:
        return {}
    out: dict[uuid.UUID, str] = {}
    for inst in (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all():
        if not is_scheme(inst):
            continue
        for who in reached:
            line = line_for(lines, inst, who["country"])
            if line is not None and line.stance == "refuses":
                out[inst.id] = _reason(line, inst, who)
                break
    return out


async def order_notes(session: AsyncSession, firm: Firm, mandate: Mandate, orders: list[dict], instruments: dict) -> list[str]:
    """What the card of a purchase for a US or Canada resident says short of refusing: the conditions, or that nothing is on file."""
    if not applies(firm):
        return []
    buys = [instruments[o["symbol"]] for o in orders if o["side"] == "buy" and is_scheme(instruments.get(o["symbol"]))]
    if not buys:
        return []
    reached = await holder_exposure(session, firm, mandate)
    if not reached:
        return []
    lines = await standing(session, firm.id)
    notes = []
    for inst in buys:
        for who in reached:
            line = line_for(lines, inst, who["country"])
            if line is None:
                notes.append(f"{inst.symbol}: whether {house_of(inst) or 'the fund house'} takes a resident of {COUNTRIES[who['country']]} "
                             f"is not on file (not assessed, and not stopped); {who['name']} is {who['why']}.")
            elif line.stance == "conditions":
                notes.append(f"{inst.symbol}: {line.fund_house} takes a resident of {COUNTRIES[line.country]} on conditions, as recorded: "
                             f"{line.conditions}")
    return notes


async def order_refusals(session: AsyncSession, firm: Firm, mandate: Mandate, orders: list[dict]) -> list[str]:
    """The buys in a set a recorded line refuses, as a direct order says them. It does not wait for a price: whether the
    fund house takes this client is not a question of what the units cost."""
    stops = await bars(session, firm, mandate)
    return [f"{o['symbol']} is {stops[uuid.UUID(str(o['instrument_id']))]}." for o in orders
            if o["side"] == "buy" and uuid.UUID(str(o["instrument_id"])) in stops]


async def client_answer(session: AsyncSession, firm: Firm, countries: dict[str, str]) -> list[dict]:
    """For one client the rule reaches: each scheme on file read against each country, worst first."""
    lines = await standing(session, firm.id)
    out = []
    order = {"refuses": 0, "conditions": 1, NOT_ASSESSED: 2, "accepts": 3}
    for inst in (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id).order_by(Instrument.symbol))).scalars().all():
        if not is_scheme(inst):
            continue
        for country in countries:
            line = line_for(lines, inst, country)
            out.append({"symbol": inst.symbol, "fund": inst.name, "fund_house": house_of(inst), "country": country,
                        "state": line.stance if line else NOT_ASSESSED, "conditions": line.conditions if line else None,
                        "document": line.document if line else None, "stated_on": line.stated_on.isoformat() if line else None})
    out.sort(key=lambda r: (order[r["state"]], r["symbol"], r["country"]))
    return out


def _line_view(r: FundAcceptance) -> dict:
    return {"id": str(r.id), "fund_house": r.fund_house, "symbol": r.symbol, "country": r.country, "country_label": COUNTRIES[r.country],
            "stance": r.stance, "stance_label": STANCES[r.stance], "conditions": r.conditions, "stated_on": r.stated_on.isoformat(),
            "document": r.document, "recorded_by": r.recorded_by}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """The list in force, every scheme read against both countries, and the clients the rule reaches."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "lines": [], "schemes": [], "clients": []}
    from app.aurea_core import residency

    lines = await standing(session, firm.id)
    held = {row[0] for row in (await session.execute(select(Holding.instrument_id).where(
        Holding.firm_id == firm.id, Holding.quantity > 0))).all()}
    schemes = []
    for inst in (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id).order_by(Instrument.symbol))).scalars().all():
        if not is_scheme(inst):
            continue
        states = {}
        for country in COUNTRIES:
            line = line_for(lines, inst, country)
            states[country] = {"state": line.stance if line else NOT_ASSESSED, "conditions": line.conditions if line else None,
                               "of": ("scheme" if line.symbol else "fund house") if line else None,
                               "document": line.document if line else None}
        schemes.append({"symbol": inst.symbol, "fund": inst.name, "fund_house": house_of(inst), "held": inst.id in held, "countries": states})
    clients = []
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True))
                                      .order_by(Mandate.name))).scalars().all()
    for m in mandates:
        reached = await holder_exposure(session, firm, m)
        if not reached:
            continue
        barred = await bars(session, firm, m)
        person = await session.get(Person, uuid.UUID(reached[0]["person_id"]))
        household = await session.get(Household, person.household_id) if person and person.household_id else None
        clients.append({"mandate": m.name, "mandate_id": str(m.id), "household": household.name if household else None,
                        "household_id": str(household.id) if household else None, "reached": reached, "barred": len(barred),
                        "not_assessed": sum(1 for s in schemes for c in {r["country"] for r in reached}
                                            if s["countries"][c]["state"] == NOT_ASSESSED)})
    rows = sorted(lines.values(), key=lambda r: (r.fund_house.casefold(), r.symbol or "", r.country))
    total = (await session.execute(select(FundAcceptance.id).where(FundAcceptance.firm_id == firm.id))).all()
    return {"applies": True, "as_of": today.isoformat(), "lines": [_line_view(r) for r in rows], "history": len(total), "schemes": schemes,
            "clients": clients, "countries": COUNTRIES, "stances": STANCES, "law": law_figures.cited(["in_amc_us_canada_acceptance"], today),
            "note": NOTE}


async def prepare(session: AsyncSession, firm: Firm, role, *, fund_house: str | None = None, symbol: str | None = None,
                  country: str | None = None, stance: str | None = None, conditions: str | None = None, stated_on=None,
                  document: str | None = None) -> dict:
    if not applies(firm):
        raise AcceptanceError("A fund house's acceptance of US and Canada residents is recorded for an Indian firm.")
    if role not in RECORDERS:
        raise AcceptanceError("The acceptance list is recorded by research, compliance or operations, from the fund house's document.")
    inst = None
    sym = (symbol or "").strip() or None
    if sym:
        inst = (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == sym))).scalar_one_or_none()
        if inst is None:
            raise AcceptanceError(f"No instrument with the symbol {sym}.")
        if not is_scheme(inst):
            raise AcceptanceError(f"{sym} is not a mutual fund scheme on file.")
    house = " ".join((fund_house or "").split()) or house_of(inst)
    if not house or len(house) < 3:
        raise AcceptanceError("Name the fund house, as its document does.")
    if len(house) > 120:
        raise AcceptanceError("A fund house's name is at most 120 characters.")
    if inst is not None and house_of(inst) and _key(house) != _key(house_of(inst)):
        raise AcceptanceError(f"{sym} is a scheme of {house_of(inst)}, not of {house}.")
    code = country_code(country)
    if code is None:
        raise AcceptanceError("The country is US or Canada.")
    said = str(stance or "").strip().lower().replace(" ", "_")
    said = {"does_not_accept": "refuses", "refused": "refuses", "accepts_on_conditions": "conditions",
            "accepts_with_conditions": "conditions"}.get(said, said)
    if said not in STANCES:
        raise AcceptanceError("The answer is accepts, conditions or refuses, as the document states it.")
    terms = " ".join((conditions or "").split()) or None
    if said == "conditions" and (terms is None or len(terms) < 8):
        raise AcceptanceError("Give the conditions as the document states them.")
    today = firm_time.today(firm)
    if isinstance(stated_on, date):
        day = stated_on
    else:
        try:
            day = date.fromisoformat(str(stated_on)) if stated_on not in (None, "") else today
        except ValueError:
            raise AcceptanceError("Give the day the document is dated as YYYY-MM-DD.")
    if day > today:
        raise AcceptanceError("A document cannot be dated in the future.")
    named = (document or "").strip()
    if len(named) < 5:
        raise AcceptanceError("Say which of the fund house's documents states this: its scheme document, notice or letter.")
    if len(named) > 300:
        raise AcceptanceError("The document's description is at most 300 characters.")
    lines = await standing(session, firm.id)
    key = ("scheme", _key(sym), code) if sym else ("house", _key(house), code)
    before = lines.get(key)
    if before is not None and day < before.stated_on:
        raise AcceptanceError(f"A line of {before.stated_on.isoformat()} is already on file: a later document stands over an earlier one.")
    if before is not None and before.stance == said and before.stated_on == day and (before.conditions or None) == terms:
        raise AcceptanceError("That line is already recorded.")
    row = FundAcceptance(firm_id=firm.id, fund_house=house, symbol=sym, country=code, stance=said, conditions=terms, stated_on=day,
                         document=named, recorded_by="")
    # Who the line reaches, read from the book: the schemes it covers, and the clients it would stop or condition.
    covered = [i for i in (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()
               if (sym and i.symbol == sym) or (not sym and _key(house_of(i)) == _key(house))]
    reached = []
    for m in (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True))
                                    .order_by(Mandate.name))).scalars().all():
        if any(w["country"] == code for w in await holder_exposure(session, firm, m)):
            reached.append(m.name)
    return {"row": row, "before": before, "covered": [i.symbol for i in covered], "reached": reached}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row: FundAcceptance = p["row"]
    row.recorded_by = by
    row.created_at = utcnow()
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="fund_acceptance_recorded", actor=by, content={
        "fund_house": row.fund_house, "symbol": row.symbol, "country": row.country, "stance": row.stance, "conditions": row.conditions,
        "stated_on": row.stated_on.isoformat(), "document": row.document})
    return await overview(session, firm)
