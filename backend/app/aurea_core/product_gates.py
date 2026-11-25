"""Which product categories open to a household at its recorded assets (L100 India edition §2.2 and §7, B-290).

India gates access by the least ticket a product takes, not by an investor class: a Specialised Investment Fund (SIF) takes Rs 10
lakh, a Portfolio Management Service (PMS) Rs 50 lakh, an Alternative Investment Fund (AIF, Category I, II or III) Rs 1 crore. Each
minimum is a `law_figures` row with its source and starts "not reviewed" in the counsel tracker, because the L100 edition says to
verify every threshold before client use. The PMS figure is the one `pms.py` reads.

The research team puts a product in a category when it records its shelf decision (`shelf.prepare_decision`, `category`), the same
way it says a fund is diversified: an instrument does not say what it is. A product with no category is not gated.

**The household's assets are what is on file, never estimated.** Investable assets are the managed book, what the household holds
elsewhere as recorded (statements, feeds, a recorded total) and cash a person recorded: not a pension, a home or a business, which
cannot buy an AIF. With none of those on file the answer is **needs a fact first**, never a no, and a direct order is then named
and not stopped. With assets on file and under the minimum it is a no, with the minimum and the assets beside it.

**An accredited investor is exempt from the SIF minimum** (`accredited.py`, recorded from a certificate); the PMS and AIF minimums
have no such exemption here. SEBI reads the SIF minimum per PAN across all of a manager's strategies; this reads the household's
assets, which is the larger figure, and says so. An AIF's lower minimum for the manager's own employees and directors is shown beside
the AIF rows and is not applied, because who works for a manager is not on file.

Read in `product_fit` (a line on the mandate's fit) and in `order_guard` (a buy). Nothing here changes a mandate, a holding or an order.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import accredited, balance_sheet, firm_time, law_figures, markets
from app.aurea_core.graph import list_households
from app.models.graph import Mandate, Person
from app.models.portfolio import Instrument
from app.models.shelf import ProductDecision
from app.models.tenant import Firm

# key: (label, the law figure of its minimum, whether an accredited investor is exempt, what it holds as the rule groups it)
CATEGORIES: dict[str, dict] = {
    "sif": {"label": "Specialised Investment Fund", "figure": "in_sif_min_ticket", "accredited_waives": True,
            "about": "A mutual-fund-regulated category above the funds and below PMS, with long-short strategies"},
    "pms": {"label": "Portfolio Management Service", "figure": "in_pms_min_ticket", "accredited_waives": False,
            "about": "A portfolio run for one client in their own demat account"},
    "aif_1": {"label": "AIF Category I", "figure": "in_aif_min_ticket", "accredited_waives": False,
              "about": "Venture capital, SME, social venture and infrastructure funds, as the AIF Regulations group them"},
    "aif_2": {"label": "AIF Category II", "figure": "in_aif_min_ticket", "accredited_waives": False,
              "about": "Private equity and debt funds that use no leverage beyond day-to-day needs, as the AIF Regulations group them"},
    "aif_3": {"label": "AIF Category III", "figure": "in_aif_min_ticket", "accredited_waives": False,
              "about": "Funds that may use leverage or trade derivatives, as the AIF Regulations group them"},
}
EMPLOYEE_FIGURE = "in_aif_min_ticket_employee"
OPEN, BELOW, NEEDS = "open", "below", "needs_fact"
STATE_TEXT = {OPEN: "Opens", BELOW: "Below the minimum", NEEDS: "Needs a fact first"}
LAW_KEYS = ["in_sif_min_ticket", "in_pms_min_ticket", "in_aif_min_ticket", EMPLOYEE_FIGURE]
NOT_ADVICE = ("A category that opens is a category the household may be offered, never a recommendation to buy it. "
              "Each minimum is as the L100 edition reads it and is checked by counsel before a client relies on it.")


class GateError(ValueError):
    """A category that is not one of the five."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def check_category(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if key not in CATEGORIES:
        raise GateError(f"A category is one of {', '.join(CATEGORIES)}.")
    return key


async def investable(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """What a household can put into a product, from what is on file: the managed book, what it holds away and recorded cash.
    amount is None when none of those is on file, which is not a household with nothing."""
    view = await balance_sheet.for_household(session, household_id, firm_id=firm.id)
    if view is None:
        return {"amount": None, "managed": None, "held_away": None, "cash": None, "basis": "not on file"}
    a = view["assets"]
    parts = {"managed": float(a.get("managed", 0)), "held_away": float(a.get("held_away", 0)), "cash": float(a.get("cash", 0))}
    known = any(v > 0 for v in parts.values())
    return {"amount": round(sum(parts.values()), 2) if known else None, **parts,
            "basis": "managed book, held away and recorded cash" if known else "no asset figure on file"}


def _money(firm: Firm, amount: float) -> str:
    return markets.money(amount, firm.base_currency or "INR")


def read(firm: Firm, category: str, assets: float | None, *, accredited_investor: bool, today: date) -> dict:
    """One category against the household's assets: open, below the minimum, or needing a fact first."""
    spec = CATEGORIES[category]
    minimum = float(law_figures.value(spec["figure"], today))
    waived = bool(spec["accredited_waives"] and accredited_investor)
    row = {"category": category, "label": spec["label"], "about": spec["about"], "minimum": minimum, "figure": spec["figure"],
           "waived": waived, "assets": assets}
    if waived:
        return {**row, "state": OPEN, "said": f"{spec['label']} opens: the minimum of {_money(firm, minimum)} is waived for an accredited investor"}
    if assets is None:
        return {**row, "state": NEEDS, "said": f"{spec['label']} takes {_money(firm, minimum)}: no asset figure is on file, so this needs a fact first"}
    if assets >= minimum:
        return {**row, "state": OPEN, "said": f"{spec['label']} opens: recorded assets of {_money(firm, assets)} meet its minimum of {_money(firm, minimum)}"}
    return {**row, "state": BELOW, "said": f"{spec['label']} takes {_money(firm, minimum)}, and the recorded assets are {_money(firm, assets)}"}


async def _household_accredited(session: AsyncSession, firm: Firm, household_id: uuid.UUID, today: date) -> tuple[bool, list[str]]:
    persons = (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.household_id == household_id))).scalars().all()
    ok = await accredited.accredited_persons(session, firm, today)
    names = [p.full_name for p in persons if p.id in ok]
    return bool(names), names


async def open_to(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """Which categories open to this household at its recorded assets: the L100 self-test (Rs 3 crore opens SIF, PMS and AIF)."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "categories": [], "message": "Ticket-size gates are kept for an Indian firm."}
    money = await investable(session, firm, household_id)
    is_accredited, names = await _household_accredited(session, firm, household_id, today)
    rows = [read(firm, key, money["amount"], accredited_investor=is_accredited, today=today) for key in CATEGORIES]
    opens = [r["label"] for r in rows if r["state"] == OPEN]
    return {"applies": True, "household_id": str(household_id), "as_of": today.isoformat(), "assets": money, "accredited": names,
            "categories": rows, "open": [r["category"] for r in rows if r["state"] == OPEN], "open_labels": opens,
            "counts": {s: sum(1 for r in rows if r["state"] == s) for s in (OPEN, BELOW, NEEDS)},
            "employee_minimum": {"figure": EMPLOYEE_FIGURE, "minimum": float(law_figures.value(EMPLOYEE_FIGURE, today)),
                                 "said": "The AIF minimum is lower for the manager's own employees and directors; who works for a manager is not on file, so it is not applied."},
            "figures": law_figures.cited(LAW_KEYS, today), "note": NOT_ADVICE}


async def category_of(session: AsyncSession, firm: Firm, instrument_id: uuid.UUID) -> str | None:
    """The category the research team put a product in: its latest decision's."""
    row = (await session.execute(select(ProductDecision).where(ProductDecision.firm_id == firm.id, ProductDecision.instrument_id == instrument_id)
                                 .order_by(ProductDecision.reviewed_on.desc(), ProductDecision.created_at.desc()).limit(1))).scalars().first()
    return row.category if row is not None and row.category in CATEGORIES else None


async def line_for(session: AsyncSession, firm: Firm, inst: Instrument, *, household_id: uuid.UUID | None, person_id: uuid.UUID | None,
                   category: str | None = None) -> dict | None:
    """The gate's line for one product and one household, or None for a product in no category (or a firm outside India)."""
    if not applies(firm):
        return None
    category = category or await category_of(session, firm, inst.id)
    if category is None:
        return None
    today = firm_time.today(firm)
    ok = await accredited.accredited_persons(session, firm, today) if person_id else set()
    if household_id is None:
        return read(firm, category, None, accredited_investor=person_id in ok, today=today)
    money = await investable(session, firm, household_id)
    return read(firm, category, money["amount"], accredited_investor=person_id in ok if person_id else (await _household_accredited(session, firm, household_id, today))[0],
                today=today)


async def order_review(session: AsyncSession, firm: Firm, mandate: Mandate, orders: list[dict], instruments: dict) -> tuple[list[str], list[str]]:
    """A buy into a gated product the household's recorded assets do not reach is refused; with no asset figure on file it goes ahead,
    named. Sales are never stopped. Returns (refusals, named)."""
    if not applies(firm):
        return [], []
    from app.aurea_core import pre_advice

    household_id = await pre_advice.household_of_mandate(session, mandate)
    refused: list[str] = []
    named: list[str] = []
    for o in orders:
        if o.get("side") != "buy":
            continue
        inst = instruments.get(o["symbol"])
        if inst is None:
            continue
        line = await line_for(session, firm, inst, household_id=household_id, person_id=mandate.person_id)
        if line is None or line["state"] == OPEN:
            continue
        if line["state"] == BELOW:
            refused.append(f"{o['symbol']}: {line['said']}.")
        else:
            named.append(f"{o['symbol']}: {line['said']}")
    return refused, named


async def for_firm(session: AsyncSession, firm: Firm, *, limit: int = 30) -> dict:
    """Every household with the categories open to it, for the Licences page: the self-test run across the book."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "households": []}
    rows = []
    for h in (await list_households(session, firm.id))[:limit]:
        view = await open_to(session, firm, uuid.UUID(h["id"]))
        rows.append({"household_id": h["id"], "household": h["name"], "assets": view["assets"]["amount"], "open": view["open"],
                     "categories": [{k: c[k] for k in ("category", "label", "state", "minimum", "waived")} for c in view["categories"]]})
    rows.sort(key=lambda r: (-(r["assets"] or -1), r["household"]))
    return {"applies": True, "as_of": today.isoformat(), "households": rows, "figures": law_figures.cited(LAW_KEYS, today), "note": NOT_ADVICE}
