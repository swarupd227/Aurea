"""India's retirement stack inside the projection: EPF, PPF, NPS and SCSS, what goes into them each month, and the EPS pension
(B-395, L200-2 India section 4).

What it reads, each recorded by a person from a document and none of it estimated:

  corpus         the held-away savings lines (`india_savings.py`): the value the statement states, on its date.
  contributions  what goes in each month and who pays it (`record_contribution`), an employer's NPS contribution among
                 them. With none recorded none is assumed; a recorded one stays level, with no raise assumed.
  pensions       the EPS pension and an NPS annuity already recorded as income streams (`financial_plan.py`).
  annuity rate   a rate an insurer quoted (`record_annuity_rate`), or one a person states for a what-if. With neither, the
                 part of an NPS corpus that must buy an annuity is not assessed: it is neither income nor capital here.
  UPS            that a person is in the Unified Pension Scheme. Nothing about it is worked out, so their NPS is left out.

How each enters (`inputs`, read by `retirement.for_household`): each account is its own pot on the assumption set's return
for its class (never the statement's administered rate), moving with the same good and bad years as the managed book, and
joins the portfolio when it can be drawn: EPF and SCSS at retirement, PPF at the later of retirement and its maturity, NPS
Tier I at the later of retirement and 60, where its lump-sum share joins the portfolio and its annuity share pays a level
income at the stated rate. The shares and their tax treatment are `law_figures` rows, assumed and not reviewed.

It is shown apart and is never inside managed or billed totals: the plan gives the answer with and without it. A
household outside India, or with none of it on file, is projected exactly as before. Nothing here advises a contribution,
a withdrawal, an annuity or a product, and the tax on any of it is not worked out.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, goal_tradeoff, india_savings, kyc_india, law_figures, markets, planning
from app.core.security import UserRole
from app.models.balance_sheet import BalanceItem
from app.models.graph import Household, Person
from app.models.retirement_stack import RetirementRecord
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = india_savings.RECORDERS
SCHEMES = ("epf", "ppf", "nps", "scss")
SCHEME_TEXT = {"epf": "EPF", "ppf": "PPF", "nps": "NPS", "scss": "SCSS", "ups": "Unified Pension Scheme"}
# Who may be recorded as paying into each scheme.
PAYERS = {"epf": ("own", "employer"), "ppf": ("own",), "nps": ("own", "employer")}
PAYER_TEXT = {"own": "The member's own", "employer": "The employer's"}
# The income streams that are part of the stack: left out of the answer "without the stack".
STREAM_KINDS = ("eps_pension", "nps_annuity")
FIGURES = ("in_nps_rule", "in_nps_exit_age", "in_nps_lump_share", "in_nps_annuity_share", "in_nps_lump_tax_rule",
           "in_nps_annuity_tax_rule", "in_epf_rule", "in_ppf_rule", "in_ppf_annual_cap", "in_scss_rule", "in_ups_rule")
HORIZON_YEARS = 90
RATE_MAX = 20.0
# The planning module's convention for the liquid defence, shown beside the figure; not a rule of law and not advice.
LIQUID_CONVENTION_YEARS = (1, 2)
NOT_MODELLED = [
    "Tax on contributions, interest, the annuity, pensions and each withdrawal",
    "An NPS exit before 60 or after it, a small corpus taken whole, and the annuity options",
    "Withdrawals and loans before retirement, from EPF or PPF",
    "EPF interest taxable above the yearly contribution line",
    "A PPF extension that is not on file",
    "The EPS pension beyond the amount recorded",
    "The Unified Pension Scheme",
    "A raise in any recorded contribution",
]
WITHDRAWAL_NOT_MODELLED = [
    "Tax on each instalment, which is part capital and part gain, first in first out",
    "Exit loads and securities transaction tax",
    "Which fund each instalment is redeemed from",
    "Good and bad years: one steady return is used here; the likelihood runs them",
    "Tax on the annuity, pension and interest income",
]
NOT_ASSESSED_ANNUITY = "no annuity rate on file from a quote"


class StackError(ValueError):
    """This cannot be recorded or read, with the reason."""


def applies(firm: Firm | None) -> bool:
    return india_savings.applies(firm)


def _clean(text, label: str, *, least: int = 0, most: int = 300) -> str:
    value = str(text or "").strip()
    if len(value) < least:
        raise StackError(f"Say which {label} this comes from.")
    if kyc_india.AADHAAR_NUMBER.search(value):
        raise StackError("Leave out the Aadhaar number: it is never stored.")
    return value[:most]


def _uuid(value, label: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise StackError(f"Give {label} from a tool's result.") from None


def _number(value, label: str) -> float:
    try:
        n = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        raise StackError(f"Give {label} as a number.") from None
    if not math.isfinite(n):
        raise StackError(f"Give {label} as a number.")
    return n


def _day(value, label: str, today: date) -> date:
    if isinstance(value, date):
        day = value
    else:
        try:
            day = date.fromisoformat(str(value or "").strip())
        except ValueError:
            raise StackError(f"Give {label} as YYYY-MM-DD.") from None
    if day > today:
        raise StackError(f"{label.capitalize()} cannot be in the future.")
    return day


# ── Recording ───────────────────────────────────────────────────────────────

async def _member(session: AsyncSession, firm: Firm, role, person_id) -> tuple[Person, Household]:
    if not applies(firm):
        raise StackError("These are recorded for an Indian firm.")
    if role not in RECORDERS:
        raise StackError("An adviser, paraplanner or operations records this, from its document.")
    person = await session.get(Person, _uuid(person_id, "the person"))
    if person is None or person.firm_id != firm.id or person.household_id is None:
        raise StackError("Person not found.")
    if person.deceased_on is not None:
        raise StackError(f"{person.full_name} is recorded as having died.")
    household = await session.get(Household, person.household_id)
    market = markets.of_household(firm, household.values if household else None)
    if market is None or market.code != "IN":
        raise StackError("These are India's accounts; this household is not taxed there.")
    return person, household


async def current_records(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> list[RetirementRecord]:
    return list((await session.execute(select(RetirementRecord).where(
        RetirementRecord.firm_id == firm_id, RetirementRecord.household_id == household_id, RetirementRecord.status == "current")
        .order_by(RetirementRecord.kind, RetirementRecord.scheme, RetirementRecord.payer, RetirementRecord.stated_on))).scalars().all())


def _standing(rows: list[RetirementRecord], person_id, kind: str, scheme: str, payer: str | None) -> RetirementRecord | None:
    return next((r for r in rows if r.person_id == person_id and r.kind == kind and r.scheme == scheme and r.payer == payer), None)


async def prepare_contribution(session: AsyncSession, firm: Firm, role, *, person_id, scheme=None, payer=None, monthly_amount=None,
                               statement_date=None, document=None) -> dict:
    person, household = await _member(session, firm, role, person_id)
    s = str(scheme or "").strip().lower()
    if s not in PAYERS:
        raise StackError("The scheme is epf, ppf or nps. SCSS is a single deposit and takes no monthly contribution.")
    who = str(payer or "").strip().lower() or ("own" if len(PAYERS[s]) == 1 else "")
    who = {"employee": "own", "self": "own", "member": "own"}.get(who, who)
    if who not in PAYERS[s]:
        raise StackError(f"Say who pays it: {' or '.join(PAYERS[s])}.")
    amount = _number(monthly_amount, "the monthly amount")
    if amount <= 0:
        raise StackError("The monthly amount must be above zero. To say a contribution stopped, end its record with the reason.")
    today = firm_time.today(firm)
    if s == "ppf":
        cap = law_figures.value("in_ppf_annual_cap")
        if amount * 12 > cap + 0.005:
            raise StackError(f"{markets.money_for(firm)(amount)} a month is above the Public Provident Fund's yearly limit of "
                             f"{markets.money_for(firm)(cap)} (an assumed figure, not reviewed). Check the passbook.")
    stated = _day(statement_date, "the statement's date", today)
    source = _clean(document, "passbook or statement", least=5)
    before = _standing(await current_records(session, firm.id, household.id), person.id, "contribution", s, who)
    if before is not None and stated < before.stated_on:
        raise StackError(f"A statement of {firm_time.spoken(before.stated_on)} is already on file, which is newer than this one.")
    return {"person": person, "household": household, "kind": "contribution", "scheme": s, "payer": who, "monthly_amount": round(amount, 2),
            "annuity_rate": None, "stated_on": stated, "document": source, "replaces": before, "attributes": {}}


async def prepare_annuity_rate(session: AsyncSession, firm: Firm, role, *, person_id, rate=None, quote_date=None, document=None, terms=None) -> dict:
    person, household = await _member(session, firm, role, person_id)
    value = _number(rate, "the annuity rate")
    if not 0 < value <= RATE_MAX:
        raise StackError(f"The annuity rate is the yearly income as a percentage of the amount paid for it, above 0 and at most {RATE_MAX:g}.")
    stated = _day(quote_date, "the quote's date", firm_time.today(firm))
    source = _clean(document, "insurer's quote", least=5)
    before = _standing(await current_records(session, firm.id, household.id), person.id, "annuity_rate", "nps", None)
    if before is not None and stated < before.stated_on:
        raise StackError(f"A quote of {firm_time.spoken(before.stated_on)} is already on file, which is newer than this one.")
    said = _clean(terms, "terms", most=200) if terms else None
    return {"person": person, "household": household, "kind": "annuity_rate", "scheme": "nps", "payer": None, "monthly_amount": None,
            "annuity_rate": round(value, 4), "stated_on": stated, "document": source, "replaces": before, "attributes": {"terms": said} if said else {}}


async def prepare_ups(session: AsyncSession, firm: Firm, role, *, person_id, statement_date=None, document=None) -> dict:
    person, household = await _member(session, firm, role, person_id)
    stated = _day(statement_date, "the document's date", firm_time.today(firm))
    source = _clean(document, "document", least=5)
    if _standing(await current_records(session, firm.id, household.id), person.id, "ups_membership", "ups", None) is not None:
        raise StackError(f"{person.full_name} is already recorded as being in the Unified Pension Scheme.")
    return {"person": person, "household": household, "kind": "ups_membership", "scheme": "ups", "payer": None, "monthly_amount": None,
            "annuity_rate": None, "stated_on": stated, "document": source, "replaces": None, "attributes": {}}


def _what(row) -> dict:
    return {"kind": row.kind, "scheme": row.scheme, "payer": row.payer,
            "monthly_amount": float(row.monthly_amount) if row.monthly_amount is not None else None,
            "annuity_rate": float(row.annuity_rate) if row.annuity_rate is not None else None}


async def _write(session: AsyncSession, firm: Firm, ready: dict, by: str) -> RetirementRecord:
    old = ready["replaces"]
    row = RetirementRecord(firm_id=firm.id, household_id=ready["household"].id, person_id=ready["person"].id, kind=ready["kind"],
                           scheme=ready["scheme"], payer=ready["payer"], monthly_amount=ready["monthly_amount"], annuity_rate=ready["annuity_rate"],
                           stated_on=ready["stated_on"], source_document=ready["document"], recorded_by=by[:200], status="current",
                           attributes=ready["attributes"])
    session.add(row)
    if old is not None:
        old.status, old.ended_on, old.ended_by, old.ended_reason = "ended", firm_time.today(firm), by[:200], "Replaced by a later document"
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="retirement_record_added", actor=by, content={
        "record_id": str(row.id), "household_id": str(row.household_id), "person_id": str(row.person_id), **_what(row),
        "stated_on": row.stated_on.isoformat(), "document": row.source_document, "replaces": str(old.id) if old else None})
    return row


async def record_contribution(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> RetirementRecord:
    return await _write(session, firm, await prepare_contribution(session, firm, role, **terms), by)


async def record_annuity_rate(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> RetirementRecord:
    return await _write(session, firm, await prepare_annuity_rate(session, firm, role, **terms), by)


async def record_ups(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> RetirementRecord:
    return await _write(session, firm, await prepare_ups(session, firm, role, **terms), by)


async def prepare_end(session: AsyncSession, firm: Firm, role, *, record_id, reason=None) -> dict:
    if not applies(firm):
        raise StackError("These are recorded for an Indian firm.")
    if role not in RECORDERS:
        raise StackError("An adviser, paraplanner or operations ends this, with the reason.")
    row = await session.get(RetirementRecord, _uuid(record_id, "the record"))
    if row is None or row.firm_id != firm.id:
        raise StackError("That record was not found.")
    if row.status != "current":
        raise StackError("That record has already ended.")
    why = _clean(reason, "reason", most=300)
    if len(why) < 3:
        raise StackError("Say why it no longer stands.")
    return {"record": row, "reason": why, "person": await session.get(Person, row.person_id),
            "household": await session.get(Household, row.household_id)}


async def end_record(session: AsyncSession, firm: Firm, role, *, by: str, record_id, reason=None) -> RetirementRecord:
    ready = await prepare_end(session, firm, role, record_id=record_id, reason=reason)
    row = ready["record"]
    row.status, row.ended_on, row.ended_by, row.ended_reason = "ended", firm_time.today(firm), by[:200], ready["reason"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="retirement_record_ended", actor=by,
                              content={"record_id": str(row.id), "household_id": str(row.household_id), **_what(row), "reason": ready["reason"]})
    return row


def record_view(row: RetirementRecord, names: dict) -> dict:
    return {"id": str(row.id), "person_id": str(row.person_id), "person": names.get(str(row.person_id)), **_what(row),
            "stated_on": row.stated_on.isoformat(), "document": row.source_document, "recorded_by": row.recorded_by,
            "terms": (row.attributes or {}).get("terms")}


async def employer_nps(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> list[dict]:
    """Each person's recorded employer contribution to NPS, monthly and for a year, with its statement. What the tax
    comparison reads (B-396); nothing here works out a deduction."""
    rows = [r for r in await current_records(session, firm_id, household_id)
            if r.kind == "contribution" and r.scheme == "nps" and r.payer == "employer"]
    return [{"person_id": str(r.person_id), "monthly": float(r.monthly_amount), "yearly": round(float(r.monthly_amount) * 12, 2),
             "stated_on": r.stated_on.isoformat(), "document": r.source_document} for r in rows]


# ── What the projection reads ───────────────────────────────────────────────

def _tier_two(details: dict) -> bool:
    return bool(re.search(r"\b(ii|2)\b", str(details.get("tier") or "").lower()))


def _years_until(day: date, today: date) -> int:
    return max(math.ceil((day - today).days / 365.25), 0)


async def inputs(session: AsyncSession, firm: Firm | None, brain: dict, *, annuity_rate: float | None = None) -> dict | None:
    """The stack as the retirement engine takes it, with the named inputs behind it; None for a household outside India or
    with nothing of it on file, which is then projected exactly as before."""
    from app.aurea_core import financial_plan

    if not applies(firm):
        return None
    market = markets.of_household(firm, brain["household"].get("values"))
    if market is None or market.code != "IN":
        return None
    hid = uuid.UUID(str(brain["household"]["id"]))
    today = firm_time.today(firm)
    base = (firm.base_currency or "INR").upper()
    lines = [l for l in await india_savings.lines(session, firm.id, hid)
             if l.attributes["savings_kind"] in SCHEMES and (l.currency or base).upper() == base]
    records = await current_records(session, firm.id, hid)
    streams = [s for s in await financial_plan.streams_of(session, hid, firm, today) if s["kind"] in STREAM_KINDS]
    if not lines and not records and not streams:
        return None
    people = {str(p["id"]): p for p in brain.get("persons", [])}
    names = {k: p.get("full_name") for k, p in people.items()}
    ups = {str(r.person_id) for r in records if r.kind == "ups_membership"}
    quotes = {str(r.person_id): r for r in records if r.kind == "annuity_rate"}
    bought = {s["person_id"] for s in streams if s["kind"] == "nps_annuity"}
    cma = planning.cma_of(brain)
    exit_age = int(law_figures.value("in_nps_exit_age"))
    lump, annuity_share = law_figures.value("in_nps_lump_share"), law_figures.value("in_nps_annuity_share")

    groups: dict[tuple, dict] = {}

    def group(scheme: str, person_id: str | None, tier_two: bool = False) -> dict:
        return groups.setdefault((scheme, person_id, tier_two), {"scheme": scheme, "person_id": person_id, "tier_two": tier_two,
                                                                "lines": [], "contributions": []})

    for l in lines:
        kind = l.attributes["savings_kind"]
        group(kind, str(l.person_id) if l.person_id else None, kind == "nps" and _tier_two(l.attributes.get("details") or {}))["lines"].append(l)
    for r in records:
        if r.kind == "contribution":
            group(r.scheme, str(r.person_id))["contributions"].append(r)

    sleeves, items, assumed, excluded = [], [], [], []
    for (scheme, pid, tier_two), g in sorted(groups.items(), key=lambda kv: (SCHEMES.index(kv[0][0]), names.get(kv[0][1]) or "", kv[0][2])):
        person = people.get(pid) if pid else None
        who = names.get(pid) if pid else "Joint"
        label = f"{SCHEME_TEXT[scheme]}{' Tier II' if tier_two else ''} · {who}"
        value = round(sum(float(l.value) for l in g["lines"]), 2)
        monthly = round(sum(float(r.monthly_amount) for r in g["contributions"]), 2)
        details = [l.attributes.get("details") or {} for l in g["lines"]]
        why = None
        unlock, add_years = 0, HORIZON_YEARS
        capital, income_share, rate_used, rate_basis, annuity_why = 1.0, 0.0, None, None, None
        returns = {"fixed_income": 1.0}
        locked_to_age = None
        if person is not None and person.get("deceased_on"):
            why = "recorded as having died"
        elif scheme == "nps":
            age = goal_tradeoff.age_of(person.get("date_of_birth"), today) if person else None
            shares = [(float(d["equity_share_percent"]), float(l.value)) for l, d in zip(g["lines"], details)
                      if d.get("equity_share_percent") not in (None, "")]
            if shares:
                weight = sum(v for _, v in shares) or 1.0
                equity = sum(p * v for p, v in shares) / weight / 100
                returns = {"equity": round(equity, 4), "fixed_income": round(1 - equity, 4)}
            else:
                assumed.append(f"{label}: no equity share on file, run on the fixed income return")
            if pid in ups:
                why = "in the Unified Pension Scheme, which is not worked out"
            elif not tier_two:
                if age is None:
                    why = "no date of birth on file, so the day of exit is not known"
                else:
                    unlock, locked_to_age = max(exit_age - age, 0), exit_age
                    capital = lump
                    if g["lines"] and not any(d.get("tier") for d in details):
                        assumed.append(f"{label}: tier not recorded, read as Tier I")
                    quoted = quotes.get(pid)
                    if pid in bought:
                        annuity_why = "an annuity income is already recorded for this person, which stands"
                    elif annuity_rate is not None:
                        income_share, rate_used, rate_basis = annuity_share, float(annuity_rate), "what_if"
                    elif quoted is not None:
                        income_share, rate_used, rate_basis = annuity_share, float(quoted.annuity_rate), "quote"
                    else:
                        annuity_why = NOT_ASSESSED_ANNUITY
        elif scheme == "ppf":
            matures = [date.fromisoformat(l.attributes["maturity_on"]) for l in g["lines"] if l.attributes.get("maturity_on")]
            if not matures:
                assumed.append(f"{label}: no maturity date on file, read as available at retirement")
            elif max(matures) > today:
                unlock = _years_until(max(matures), today)
                add_years = int((max(matures) - today).days // 365.25)
                if monthly:
                    assumed.append(f"{label}: contributions counted up to the maturity on file, no extension being recorded")
            else:
                add_years = 0
                if monthly:
                    assumed.append(f"{label}: maturity date passed and no extension on file, contributions not counted")
        mu, sigma = planning._blended_assumptions(returns, cma=cma)
        gfc = planning.STRESS_SCENARIOS["gfc_2008"]
        for l in g["lines"]:
            items.append({"type": "corpus", "scheme": scheme, "label": label, "person": who, "institution": l.institution, "value": float(l.value),
                          "value_on": l.value_on.isoformat(), "document": l.source_document, "rate": l.attributes.get("rate"),
                          "counted": why is None, "why": why})
        for r in g["contributions"]:
            items.append({"type": "contribution", "scheme": scheme, "label": label, "person": who, "record_id": str(r.id), "payer": r.payer,
                          "monthly": float(r.monthly_amount), "value_on": r.stated_on.isoformat(), "document": r.source_document,
                          "counted": why is None and add_years > 0, "why": why or (None if add_years > 0 else "no contribution counted after maturity")})
        if why is not None:
            excluded.append({"label": label, "value": value, "monthly": monthly, "why": why})
            continue
        sleeves.append({
            # what the engine reads
            "value": value, "mu": mu, "sigma": sigma, "crash": sum(w * gfc.get(c, 0.0) for c, w in returns.items()),
            "add": [monthly * 12 if y < add_years else 0.0 for y in range(HORIZON_YEARS)], "unlock_after": unlock,
            "capital": capital, "annuity_per_unit": income_share * (rate_used or 0.0) / 100,
            # what is said about it
            "label": label, "scheme": scheme, "person_id": pid, "person": who, "tier_two": tier_two, "monthly": monthly if add_years > 0 else 0.0,
            "contributions_stop_after": add_years if add_years < HORIZON_YEARS else None, "returns": returns,
            "locked_to_age": locked_to_age, "capital_share": capital, "annuity_share": annuity_share if scheme == "nps" and not tier_two else 0.0,
            "annuity_rate": rate_used, "annuity_rate_basis": rate_basis, "annuity_not_assessed": annuity_why,
            "no_corpus_on_file": not g["lines"],
        })
    for s in streams:
        items.append({"type": "pension", "scheme": "eps" if s["kind"] == "eps_pension" else "annuity", "label": f"{s['name']} · {s['person']}",
                      "person": s["person"], "annual": s["planned_annual"], "start_age": s["start_age"], "value_on": s["as_of"],
                      "document": s["document"], "counted": True, "why": None})
    for pid in sorted(ups):
        excluded.append({"label": f"Unified Pension Scheme · {names.get(pid)}", "value": None, "monthly": None,
                         "why": "membership recorded; its assured pension is not worked out"})
    return {"sleeves": sleeves, "items": items, "assumed": assumed, "excluded": excluded,
            "value_on_file": round(sum(float(l.value) for l in lines), 2), "streams": len(streams)}


# ── The layout ──────────────────────────────────────────────────────────────

async def _not_in_stack(session: AsyncSession, firm: Firm, household_id: uuid.UUID, names: dict) -> list[dict]:
    """Pension totals on net worth that no statement line carries: the projection does not read them, and where one is the
    same account as a line it is counted twice in net worth."""
    rows = (await session.execute(select(BalanceItem).where(
        BalanceItem.firm_id == firm.id, BalanceItem.household_id == household_id, BalanceItem.status == "current",
        BalanceItem.kind == "pension"))).scalars().all()
    return [{"item_id": str(r.id), "name": r.name, "person": names.get(str(r.person_id)) if r.person_id else "Joint", "amount": float(r.amount),
             "as_of": r.as_of.isoformat()} for r in rows]


def nps_exit(stack: dict, plan: dict) -> list[dict]:
    """Each NPS Tier I account at exit: today's corpus split by the rule, and the projected corpus at exit (the median of the
    same paths the likelihood runs) split the same way, with the annuity income only at a stated rate."""
    inflation = plan["assumptions"]["inflation"]
    out = []
    for s in stack.get("accounts") or []:
        if s["scheme"] != "nps" or s["tier_two"]:
            continue
        median = s.get("at_release_median")
        years = s.get("release_in_years")
        today_money = None if median is None else median / (1 + inflation) ** years
        row = {"label": s["label"], "person": s["person"], "corpus_today": s["value"], "monthly": s["monthly"],
               "lump_share": s["capital_share"], "annuity_share": s["annuity_share"],
               "lump_today": round(s["value"] * s["capital_share"], 2), "annuity_today": round(s["value"] * s["annuity_share"], 2),
               "exit_in_years": years, "exit_age": None if s.get("release_age_of_holder") is None else s["release_age_of_holder"],
               "corpus_at_exit": None if median is None else round(median),
               "corpus_at_exit_today_money": None if today_money is None else round(today_money),
               "lump_at_exit": None if median is None else round(median * s["capital_share"]),
               "annuity_purchase_at_exit": None if median is None else round(median * s["annuity_share"]),
               "annuity_rate": s["annuity_rate"], "annuity_rate_basis": s["annuity_rate_basis"],
               "annuity_a_year": None, "annuity_a_year_today_money": None, "annuity_not_assessed": s["annuity_not_assessed"],
               "lump_tax": "in_nps_lump_tax_rule", "annuity_tax": "in_nps_annuity_tax_rule"}
        if s["annuity_rate"] is not None and median is not None:
            yearly = median * s["annuity_share"] * s["annuity_rate"] / 100
            row["annuity_a_year"] = round(yearly)
            row["annuity_a_year_today_money"] = round(yearly / (1 + inflation) ** years)
        out.append(row)
    return out


def income_floor(plan: dict, on_file: dict, sheet_items: list[dict], savings: list[dict], today: date) -> dict:
    """Guaranteed income at retirement against the needs, in today's money, before tax. Needs are the essential spending on
    file and the debt payments that outlast work; with no essential spending on file it is not assessed."""
    acc = plan["years_to_retirement"]
    inflation = plan["assumptions"]["inflation"]
    deflate = (1 + inflation) ** acc
    retire_on_year = today.year + acc
    sources, later, not_counted = [], [], []
    for s in on_file["streams"]:
        if s["ends_in"] is not None and s["ends_in"] <= acc:
            continue
        row = {"source": s["name"], "person": s["person"], "kind": s.get("kind"), "stated": round(s["annual"], 2),
               "inflation_linked": s["inflation_linked"]}
        if s["starts_in"] > acc:
            later.append({**row, "starts_in_years": s["starts_in"]})
        else:
            sources.append({**row, "a_year": round(s["annual"] * (1.0 if s["inflation_linked"] else 1 / deflate), 2)})
    for a in (plan.get("stack") or {}).get("accounts") or []:
        if a["scheme"] != "nps" or a["tier_two"]:
            continue
        if a["annuity_rate"] is None:
            not_counted.append({"source": f"NPS annuity · {a['person']}", "why": a["annuity_not_assessed"]})
            continue
        yearly = (a["at_release_median"] or 0) * a["annuity_share"] * a["annuity_rate"] / 100
        row = {"source": f"NPS annuity at {a['annuity_rate']:g}%", "person": a["person"], "kind": "nps_annuity_projected",
               "stated": round(yearly, 2), "inflation_linked": False}
        if a["release_in_years"] > acc:
            later.append({**row, "starts_in_years": a["release_in_years"]})
        else:
            sources.append({**row, "a_year": round(yearly / deflate, 2)})
    for h in savings:
        if h["kind"] != "scss":
            continue
        name = f"SCSS interest · {h['owner'] or 'Joint'}"
        if h["rate"] is None:
            not_counted.append({"source": name, "why": "no rate on the statement"})
        elif not h["maturity_on"]:
            not_counted.append({"source": name, "why": "no maturity date on file"})
        elif date.fromisoformat(h["maturity_on"]) <= today or date.fromisoformat(h["maturity_on"]).year < retire_on_year:
            not_counted.append({"source": name, "why": "the deposit matures before retirement"})
        else:
            yearly = h["value"] * h["rate"] / 100
            sources.append({"source": name, "person": h["owner"], "kind": "scss_interest", "stated": round(yearly, 2), "inflation_linked": False,
                            "until": h["maturity_on"], "a_year": round(yearly / deflate, 2)})
    essential = sum(i["amount"] for i in sheet_items if i["kind"] == "essential_spending")
    other = sum(i["amount"] for i in sheet_items if i["kind"] == "discretionary_spending")
    debts = on_file["debt_payments"]
    floor = round(sum(s["a_year"] for s in sources), 2)
    needs = round(essential + debts, 2) if essential else None
    return {"status": "assessed" if needs else "not_assessed", "why": None if needs else "no essential spending on file",
            "needs": needs, "essential_spending": round(essential, 2) or None, "debt_payments_after_work": round(debts, 2),
            "other_spending": round(other, 2) or None, "income_planned_for": plan.get("income_need"),
            "guaranteed": floor, "coverage": round(floor / needs, 3) if needs else None,
            "short_by": round(max(needs - floor, 0), 2) if needs else None, "sources": sources, "starting_later": later,
            "not_counted": not_counted, "money_basis": "today", "before_tax": True}


def liquid_defence(brain: dict, sheet: dict, lines: list, base: str) -> dict:
    """How many years of essential spending and debt payments the money that can be drawn today would cover: cash in the
    managed accounts, cash recorded on net worth and cash held elsewhere. Nothing locked, invested or deposited counts."""
    book = float(((brain.get("totals") or {}).get("by_asset_class") or {}).get("cash") or 0)
    recorded = float(sheet["assets"]["cash"])
    away = sum(float(l.value) for l in lines if l.asset_class == "cash" and (l.currency or base).upper() == base)
    essential = sum(i["amount"] for i in sheet["items"] if i["kind"] == "essential_spending")
    payments = sheet["cash_flow"]["debt_payments"] or 0
    yearly = essential + payments if essential else 0
    liquid = book + recorded + away
    return {"status": "assessed" if yearly else "not_assessed", "why": None if yearly else "no essential spending on file",
            "liquid": round(liquid, 2),
            "parts": [{"source": "Cash in the managed accounts", "amount": round(book, 2)},
                      {"source": "Cash recorded on net worth", "amount": round(recorded, 2)},
                      {"source": "Cash held elsewhere", "amount": round(away, 2)}],
            "yearly_need": round(yearly, 2) or None, "years": round(liquid / yearly, 2) if yearly else None,
            "convention_years": list(LIQUID_CONVENTION_YEARS),
            "not_counted": ["EPF, PPF, NPS and SCSS", "Shares, equity and debt funds", "Deposits not recorded as cash",
                            "Money already asked to be paid out is not taken off"]}


def withdrawals(plan: dict, monthly: float | None = None) -> dict:
    """The drawing, year by year from retirement, on one steady return: the pot at the start, the money that unlocks, the
    annuity, what is drawn each month, and the pot at the end. An illustration beside the likelihood, not a forecast."""
    acc, dec = plan["years_to_retirement"], plan["years_in_retirement"]
    inflation = plan["assumptions"]["inflation"]
    mu, sigma = plan["assumptions"]["expected_return"], plan["assumptions"]["volatility"]
    rate = mu - sigma ** 2 / 2
    path = (plan.get("stack") or {}).get("path") or []
    pot = float(plan["projected_at_retirement"])
    rows, runs_out = [], None
    for y in range(min(dec, len(path))):
        step = path[y]
        draw = monthly * 12 * (1 + inflation) ** (acc + y) if monthly is not None else step["draw"]
        released = step["released"] if y else 0
        opening = pot + released
        from_portfolio = max(draw - step["annuity"], 0.0)
        paid = min(from_portfolio, max(opening * (1 + rate), 0.0))
        closing = max(opening * (1 + rate) - from_portfolio, 0.0)
        rows.append({"year": y + 1, "age": plan["retirement_age"] + y, "opening": round(pot), "released": round(released),
                     "annuity": round(step["annuity"]), "drawn": round(paid), "monthly": round(paid / 12),
                     "return": round(opening * rate), "closing": round(closing),
                     "short": round(from_portfolio - paid)})
        pot = closing
        if from_portfolio - paid > 0.5 and runs_out is None:
            runs_out = plan["retirement_age"] + y
    return {"basis": "monthly_amount_stated" if monthly is not None else "the_plans_income_less_guaranteed_income",
            "monthly_stated_today": monthly, "return": round(rate, 5), "inflation": inflation, "opening_pot": round(plan["projected_at_retirement"]),
            "first_monthly": rows[0]["monthly"] if rows else None,
            "first_monthly_today": round(rows[0]["monthly"] / (1 + inflation) ** acc) if rows else None,
            "runs_out_at": runs_out, "years": rows, "money_basis": "of_each_year", "illustration": True,
            "not_modelled": WITHDRAWAL_NOT_MODELLED}


async def layout(session: AsyncSession, firm: Firm, household_id: uuid.UUID, *, annuity_rate=None, monthly_withdrawal=None) -> dict | None:
    """One household's retirement stack laid out: what is on file, the retirement answer with and without it, the funded
    ratio, NPS at exit, the income floor, the liquid defence and the drawing. It records and changes nothing."""
    from app.aurea_core import balance_sheet, counsel_review, financial_plan, funded, retirement
    from app.aurea_core.graph import household_brain

    brain = await household_brain(session, household_id, firm_id=firm.id)
    if brain is None:
        return None
    today = firm_time.today(firm)
    market = markets.of_household(firm, brain["household"].get("values"))
    head = {"household_id": str(household_id), "household": brain["household"]["name"], "currency": firm.base_currency, "as_of": today.isoformat()}
    if not applies(firm) or market is None or market.code != "IN":
        return {**head, "applies": False, "status": "not_india"}
    rate = monthly = None
    if annuity_rate not in (None, ""):
        rate = _number(annuity_rate, "the annuity rate")
        if not 0 < rate <= RATE_MAX:
            raise StackError(f"The annuity rate is a percentage a year, above 0 and at most {RATE_MAX:g}.")
    if monthly_withdrawal not in (None, ""):
        monthly = _number(monthly_withdrawal, "the monthly withdrawal")
        if monthly <= 0:
            raise StackError("The monthly withdrawal is an amount in today's money, above zero.")
    plan = await retirement.for_household(session, household_id, firm_id=firm.id, overrides={"annuity_rate": rate} if rate is not None else None)
    names = {str(p["id"]): p.get("full_name") for p in brain.get("persons", [])}
    records = [record_view(r, names) for r in await current_records(session, firm.id, household_id)]
    savings = [h for h in (await india_savings.household_view(session, firm, household_id))["holdings"] if h["kind"] in SCHEMES]
    people = [{"person_id": str(p["id"]), "name": p.get("full_name")} for p in brain.get("persons", []) if not p.get("deceased_on")]
    head.update(applies=True, people=people, records=records, not_in_stack=await _not_in_stack(session, firm, household_id, names),
                what_if={k: v for k, v in (("annuity_rate", rate), ("monthly_withdrawal", monthly)) if v is not None} or None,
                figures=law_figures.cited(FIGURES, today), changes_nothing=True, estimate=True)
    stack = plan.get("stack")
    if stack is None:
        return await counsel_review.annotate(session, firm, {**head, "status": "nothing_on_file"})
    likely = goal_tradeoff.likelihoods(brain, today)
    f = funded.ratio(brain, likely, plan)
    sheet = await balance_sheet.for_household(session, household_id, firm_id=firm.id)
    on_file = await financial_plan.retirement_inputs(session, household_id, firm_id=firm.id, retirement_age=plan["retirement_age"],
                                                     current_age=plan["current_age"])
    lines, _ = await balance_sheet.held_away_lines(session, household_id, firm=firm)
    result = {
        **head, "status": "on_file", "stack": {k: v for k, v in stack.items() if k != "path"},
        "retirement": {k: plan[k] for k in ("retirement_age", "current_age", "longevity_age", "income_need", "income_basis", "target_success")},
        "assumptions_source": plan["assumptions_source"], "assumptions_version": plan["assumptions_version"],
        "funded": {k: f.get(k) for k in ("ratio", "ratio_without_stack", "assets", "stack_assets", "pv_needs", "pv_needs_without_stack",
                                         "discount_rate", "discount_whose", "tiers", "not_counted")},
        "nps_exit": nps_exit(stack, plan),
        "floor": income_floor(plan, on_file, sheet["items"], savings, today),
        "liquidity": liquid_defence(brain, sheet, lines, (firm.base_currency or "INR").upper()),
        "withdrawals": withdrawals(plan, monthly),
        "not_modelled": NOT_MODELLED,
    }
    return await counsel_review.annotate(session, firm, result)


# ── Inside the plan ─────────────────────────────────────────────────────────

HEADLINE = ("success_probability", "on_track", "projected_at_retirement", "projected_at_retirement_today", "sustainable_income",
            "income_target", "income_gap", "guaranteed_at_retirement", "median_depletion_age", "pv_spending")


def block(stack: dict, plan: dict, without: dict, run: dict, holder_ages: dict) -> dict:
    """The stack's block on a retirement plan: its inputs and the answer with and without it."""
    acc = plan["years_to_retirement"]
    inflation = plan["assumptions"]["inflation"]
    accounts = []
    for s, got in zip(stack["sleeves"], run["accounts"]):
        release = got["release_in_years"]
        age = holder_ages.get(s["person_id"])
        accounts.append({**{k: s[k] for k in ("label", "scheme", "person", "tier_two", "value", "monthly", "returns", "locked_to_age",
                                              "capital_share", "annuity_share", "annuity_rate", "annuity_rate_basis", "annuity_not_assessed",
                                              "no_corpus_on_file", "contributions_stop_after")},
                         "expected_return": round(s["mu"], 4), "release_in_years": release,
                         "release_age_of_holder": None if age is None or release is None else age + release,
                         "at_release_median": got["median"],
                         "at_release_today": None if got["median"] is None else round(got["median"] / (1 + inflation) ** release)})
    counted = round(sum(s["value"] * (s["capital"] + (s["annuity_share"] if s["annuity_rate"] is not None else 0.0)) for s in stack["sleeves"]), 2)
    monthly = round(sum(s["monthly"] for s in stack["sleeves"]), 2)
    check = []
    if monthly and plan["contribution"]["first_year"]:
        check.append("A contribution on the retirement goal is counted as well as these: if it includes any of them, it counts twice")
    with_, bare = ({k: p.get(k) for k in HEADLINE} for p in (plan, without))
    at_retirement = run["stack_at_retirement"]
    return {
        "status": "included", "value_on_file": stack["value_on_file"], "capital_counted": counted,
        "monthly_contributions": monthly, "available_at_retirement_today": round(at_retirement / (1 + inflation) ** acc),
        "with": with_, "without": bare,
        "change_points": round((with_["success_probability"] - bare["success_probability"]) * 100),
        "accounts": accounts, "items": stack["items"], "excluded": stack["excluded"], "assumed": stack["assumed"], "check": check,
        "basis": "each account runs on the assumption set's return for its class, with the managed book's good and bad years; "
                 "recorded contributions stay level to retirement",
        "path": run["path"],
    }


def for_client(plan: dict) -> dict:
    """A retirement plan as its client reads it: the answer with and without the stack and what is on file in one figure; no
    holding, institution, statement or record (the held-away rule)."""
    stack = (plan or {}).get("stack")
    if not stack:
        return plan
    keep = ("status", "value_on_file", "monthly_contributions", "available_at_retirement_today", "with", "without", "change_points", "basis")
    return {**plan, "stack": {k: stack[k] for k in keep}}
