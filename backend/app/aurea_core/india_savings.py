"""The Indian shelf's other classes and the rule each carries (L100 India edition sections 7 and 8, B-296).

Listed shares, equity funds and ETFs were the Indian book; a household also holds what the Government sets the rate on, a pension account, a
bond, a debenture, a property trust, a gold bond and an insurance policy. This holds each as what a person recorded from its statement, and
beside it the rule that class carries: where it comes from, what the tax treatment is, and the lock-in. The classes are:

  ppf, ssy, scss, nsc   small savings: sovereign, rate set administratively, the rate a recorded fact and never assumed
  nps, epf              retirement: NPS locked in to 60, 60% a tax-free lump sum and 40% an annuity; EPF beside it
  g_sec, listed_bond    RBI Retail Direct government securities and listed bonds (a gain follows the other-listed class in `tax_india_full`)
  mld                   market-linked debenture: a gain is short-term whatever the period (`tax_india.NEVER_LONG` already holds the class)
  reit_invit            listed units: a gain follows listed equity (`tax_india`); a distribution's character is not modelled
  sgb                   Sovereign Gold Bond: exempt at maturity for an individual, 2.5% interest taxable, new issuance discontinued (verify)
  ulip                  unit-linked insurance: lock-in and the fund management charge laid out beside what the policy states it charges

A holding is a held-away line (`held_away.py`), so net worth counts it once: a statement replaces the line for the same person, institution and
class, and `attributes` carry what is specific (the rate the statement states, the maturity or lock-in date, the details). Nothing is
estimated: the value is the statement's, the rate is the statement's, an NPS exit split is the rule's arithmetic on the corpus as stated (not on
a projected corpus), and what the law does not give is not assumed. Nothing here recommends a product or compares one with another.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, held_away, law_figures, markets
from app.core.security import UserRole
from app.models.graph import Person
from app.models.held_away import HeldAwayHolding
from app.models.tenant import Firm

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
NPS_AGE = 60
NPS_LUMP = 0.60

# kind: (label, the asset class the held-away line carries, rule figure keys, administered rate, detail keys it may carry)
KINDS: dict[str, dict] = {
    "ppf": {"label": "Public Provident Fund", "asset_class": "fixed_income", "rules": ["in_ppf_rule"], "administered": True, "details": ["opened_on"]},
    "ssy": {"label": "Sukanya Samriddhi Account", "asset_class": "fixed_income", "rules": ["in_ssy_rule"], "administered": True, "details": ["opened_on"]},
    "scss": {"label": "Senior Citizens' Savings Scheme", "asset_class": "fixed_income", "rules": ["in_scss_rule"], "administered": True, "details": ["opened_on"]},
    "nsc": {"label": "National Savings Certificate", "asset_class": "fixed_income", "rules": ["in_nsc_rule"], "administered": True, "details": ["opened_on"]},
    "nps": {"label": "National Pension System account", "asset_class": "multi_asset", "rules": ["in_nps_rule"], "administered": False, "details": ["tier"]},
    "epf": {"label": "Employees' Provident Fund", "asset_class": "fixed_income", "rules": ["in_epf_rule"], "administered": True, "details": ["employer"]},
    "g_sec": {"label": "Government security (RBI Retail Direct)", "asset_class": "fixed_income", "rules": ["in_gsec_rule"], "administered": False, "details": []},
    "listed_bond": {"label": "Listed bond", "asset_class": "fixed_income", "rules": [], "administered": False, "details": ["issuer"]},
    "mld": {"label": "Market-linked debenture", "asset_class": "fixed_income", "rules": ["in_mld_rule"], "administered": False, "details": ["issuer"]},
    "reit_invit": {"label": "REIT or InvIT units", "asset_class": "property", "rules": ["in_reit_invit_rule"], "administered": False, "details": []},
    "sgb": {"label": "Sovereign Gold Bond", "asset_class": "commodity", "rules": ["in_sgb_rule"], "administered": False, "details": ["grams", "series"]},
    "ulip": {"label": "Unit-linked insurance policy", "asset_class": "multi_asset", "rules": ["in_ulip_lock_in_years", "in_ulip_fmc_cap"], "administered": False,
             "details": ["started_on", "annual_premium", "fund_management_charge", "charges_note"]},
}
LISTED_TAX = {"listed_bond": "Other listed: short-term at slab, long-term at 12.5% without the exemption (tax_india_full)",
              "g_sec": "Interest at slab; a gain on a sale is other listed (tax_india_full)",
              "sgb": "Sold on the exchange: other listed (tax_india_full); redeemed at maturity: exempt for an individual (in_sgb_rule)",
              "reit_invit": "A gain on listed units: listed equity (tax_india)"}


class SavingsError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def rules_table(today: date) -> list[dict]:
    """Every class, its rule or rules with source and as-of day, and whether its rate is administered."""
    out = []
    for key, k in KINDS.items():
        out.append({"kind": key, "label": k["label"], "administered": k["administered"], "asset_class": k["asset_class"],
                    "rules": law_figures.cited(k["rules"], today) if k["rules"] else [], "tax": LISTED_TAX.get(key),
                    "rate_note": "Set by the Government and revised by it: the rate is the statement's, recorded, and never assumed here." if k["administered"] else None})
    return out


def _day(value, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise SavingsError(f"Give {label} as YYYY-MM-DD.")


def _add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def _line_key(kind: str) -> str:
    return KINDS[kind]["label"]


async def prepare(session: AsyncSession, firm: Firm, role, *, household_id, kind: str | None, institution: str | None, value, value_on, document: str | None,
                  person_id=None, rate=None, maturity_on=None, details: dict | None = None, account_label: str | None = None) -> dict:
    if not applies(firm):
        raise SavingsError("These classes are recorded for an Indian firm.")
    if role not in RECORDERS:
        raise SavingsError("An adviser, paraplanner or operations records a holding, from its statement.")
    k = (kind or "").strip().lower()
    if k not in KINDS:
        raise SavingsError(f"The class is one of: {', '.join(KINDS)}.")
    try:
        hid = household_id if isinstance(household_id, uuid.UUID) else uuid.UUID(str(household_id))
    except ValueError:
        raise SavingsError("Give the household from search_households.")
    pid = None
    if person_id not in (None, ""):
        try:
            pid = person_id if isinstance(person_id, uuid.UUID) else uuid.UUID(str(person_id))
        except ValueError:
            raise SavingsError("Give the person from the client's page.")
    rate_value = None
    if rate not in (None, ""):
        try:
            rate_value = float(str(rate).replace("%", ""))
        except ValueError:
            raise SavingsError("The rate is a number, as the statement states it (7.1 for 7.1%).")
        if not 0 <= rate_value <= 30:
            raise SavingsError("The rate is a percentage a year, between 0 and 30, as the statement states it.")
    matures = _day(maturity_on, "the maturity day")
    extra = {key: v for key, v in (details or {}).items() if key in KINDS[k]["details"] and v not in (None, "")}
    if k == "ulip" and "started_on" in extra:
        extra["started_on"] = _day(extra["started_on"], "the policy's start day").isoformat()
    if k == "ulip" and "fund_management_charge" in extra:
        try:
            extra["fund_management_charge"] = float(str(extra["fund_management_charge"]).replace("%", ""))
        except ValueError:
            raise SavingsError("The fund management charge is a percentage a year, as the policy states it.")
    existing = (await session.execute(select(HeldAwayHolding).where(
        HeldAwayHolding.household_id == hid, HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.status == "current",
        HeldAwayHolding.institution == (institution or "").strip(),
        HeldAwayHolding.person_id == pid if pid else HeldAwayHolding.person_id.is_(None)))).scalars().all()
    replaces = next((r for r in existing if (r.attributes or {}).get("savings_kind") == k and (r.account_label or None) == ((account_label or "").strip() or None)), None)
    ready = await held_away.prepare_holding(
        session, hid, firm_id=firm.id, institution=institution, instrument_name=_line_key(k), value=value, value_on=_day(value_on, "the statement's date"),
        source_document=document, person_id=pid, account_label=account_label, asset_class=KINDS[k]["asset_class"],
        replaces_id=replaces.id if replaces else None)
    return {**ready, "kind": k, "label": KINDS[k]["label"], "rate": rate_value, "maturity_on": matures, "details": extra, "replaces_line": replaces}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    result = await held_away.record_holding(
        session, p["household"].id, firm_id=firm.id, by=by, institution=p["institution"], instrument_name=p["label"], value=p["value"], value_on=p["value_on"],
        source_document=p["source"], person_id=p["person"].id if p["person"] else None, account_label=p["account_label"], asset_class=KINDS[p["kind"]]["asset_class"],
        replaces_id=p["replaces_line"].id if p["replaces_line"] else None)
    row = await session.get(HeldAwayHolding, uuid.UUID(result["holding_id"]))
    row.attributes = {"savings_kind": p["kind"], "rate": p["rate"], "rate_on": p["value_on"].isoformat() if p["rate"] is not None else None,
                      "maturity_on": p["maturity_on"].isoformat() if p["maturity_on"] else None, "details": p["details"]}
    await session.flush()
    return await household_view(session, firm, p["household"].id)


async def lines(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID | None = None) -> list[HeldAwayHolding]:
    stmt = select(HeldAwayHolding).where(HeldAwayHolding.firm_id == firm_id, HeldAwayHolding.status == "current")
    if household_id is not None:
        stmt = stmt.where(HeldAwayHolding.household_id == household_id)
    rows = (await session.execute(stmt.order_by(HeldAwayHolding.instrument_name))).scalars().all()
    return [r for r in rows if (r.attributes or {}).get("savings_kind") in KINDS]


def _extras(kind: str, row: HeldAwayHolding, person: Person | None, today: date) -> dict:
    """What the class's rule gives beside the statement, from facts on file; not on file reads not assessed."""
    a = row.attributes or {}
    d = a.get("details") or {}
    out: dict = {}
    value = float(row.value)
    if kind == "nps":
        if person is not None and person.date_of_birth is not None:
            to = _add_years(person.date_of_birth, NPS_AGE)
            out["locked_in_to"] = to.isoformat()
            out["locked_in"] = today < to
        else:
            out["locked_in"] = None
            out["lock_in_note"] = "Not assessed: the person's date of birth is not on file, so the day they reach 60 is not known."
        out["exit_split"] = {"lump_sum": round(value * NPS_LUMP, 2), "annuity": round(value * (1 - NPS_LUMP), 2), "share": NPS_LUMP,
                             "basis": "The rule's split of the corpus as the statement states it, before any growth to the day of exit."}
    if kind == "sgb":
        out["new_issues"] = "discontinued: verify"
        if a.get("maturity_on"):
            out["redeems_on"] = a["maturity_on"]
            out["redeems_exempt"] = True
    if kind == "ulip":
        start = date.fromisoformat(d["started_on"]) if d.get("started_on") else None
        if start:
            end = _add_years(start, int(law_figures.value("in_ulip_lock_in_years")))
            out["locked_in_to"] = end.isoformat()
            out["locked_in"] = today < end
        cap = law_figures.value("in_ulip_fmc_cap")
        fmc = d.get("fund_management_charge")
        if fmc is not None:
            out["fmc"] = {"stated": fmc, "cap": round(cap * 100, 4), "over_cap": fmc / 100 > cap + 1e-12}
        out["economics"] = {"annual_premium": d.get("annual_premium"), "note": d.get("charges_note"),
                            "basis": "What the policy states it charges, laid out as recorded; not compared with any other product."}
    if kind in ("ppf", "ssy", "scss", "nsc") and a.get("maturity_on"):
        out["matures_on"] = a["maturity_on"]
        out["matured"] = date.fromisoformat(a["maturity_on"]) <= today
    return out


async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID | None = None) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "holdings": []}
    rows = await lines(session, firm.id, household_id)
    people = {p.id: p for p in (await session.execute(select(Person).where(Person.id.in_({r.person_id for r in rows if r.person_id} or [uuid.uuid4()])))).scalars().all()}
    holdings = []
    for r in rows:
        kind = r.attributes["savings_kind"]
        person = people.get(r.person_id)
        a = r.attributes
        holdings.append({"id": str(r.id), "household_id": str(r.household_id), "kind": kind, "label": KINDS[kind]["label"], "institution": r.institution, "owner": person.full_name if person else None,
                         "value": float(r.value), "currency": r.currency, "value_on": r.value_on.isoformat(), "document": r.source_document,
                         "rate": a.get("rate"), "rate_on": a.get("rate_on"), "administered": KINDS[kind]["administered"],
                         "rate_note": ("Not on file: the rate is set by the Government and is never assumed here." if KINDS[kind]["administered"] and a.get("rate") is None else None),
                         "maturity_on": a.get("maturity_on"), "details": a.get("details") or {}, **_extras(kind, r, person, today)})
    kinds_here = sorted({h["kind"] for h in holdings})
    return {"applies": True, "as_of": today.isoformat(), "holdings": holdings, "total": round(sum(h["value"] for h in holdings), 2),
            "rules": [t for t in rules_table(today) if t["kind"] in kinds_here],
            "note": "Each is a held-away line, counted once in net worth. The rate and the value are the statement's; nothing is estimated or recommended."}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    view = await household_view(session, firm)
    if view.get("applies"):
        view["all_rules"] = rules_table(firm_time.today(firm))
    return view
