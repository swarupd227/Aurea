"""The consolidation workbench, India (B-389): bringing scattered holdings together without selling them by accident.

India has no automated account transfer. A family's mutual fund folios move by re-mapping who services them (a distributor
or broker code change at the registrar), shares and ETFs by an off-market transfer between demat accounts, a Portfolio
Manager's stock in kind into another's, and only what should stop being held is sold - against the client's gain budget.
This reads the statement lines already on file (`held_away`) and keeps what people did with them:

  discover   a fund-house and folio roll-up of the lines, and the demat accounts beside them
  classify   an adviser or paraplanner marks each holding keep and re-map, transfer, or exit, with the reason; an exit also
             needs a reason about the product itself and a planned day
  track      operations records each step of a code change, a demat transfer, a PMS transition or an exit from its
             document: requested, consented by the client, submitted, confirmed, rejected or withdrawn
  schedule   exits by taxpayer and tax year against the gain budget recorded on the mandate, with the tax estimated by the
             India engine (`tax_india`)
  compare    what redeeming and reinvesting would crystallise, wherever the cost is known

**It moves no holdings and no cash and places no order.** A confirmed transfer changes no line: the old statement line
stands until a newer statement or a person ends it, and the workbench says it is still listed.

A statement line carries no cost, so a person records the lots, the tax class, the exit load and the distributor code from
a document (`record_holding_facts`). Without units, lots covering exactly those units and a tax class, a holding's tax is
**not assessed**, and nothing fills the gap: a tax class is read from the record or from the firm's own instrument, never
guessed from an asset class. Every figure is an estimate and `NOT_MODELLED` travels with it. Rules and rates are
`law_figures` rows, assumed until counsel reviews them. Nothing here says the firm is compliant.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets, tax_india, tax_india_full
from app.core.security import UserRole
from app.models.aggregator import EcasStatement
from app.models.consolidation import ConsolidationDecision, ConsolidationFacts, ConsolidationItem, ConsolidationStep
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.held_away import HeldAwayHolding
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.provenance import ledger

# Who may do what. Classifying is advice: the adviser's or the paraplanner's. A transfer event is operations', as on a
# transfer's file (`record_transfer_event`, B-108) and a custodian's movement (`record_account_transfer`). Facts read off
# a document are recorded by whoever records a statement line (`held_away`).
CLASSIFIERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
TRACKERS = frozenset({UserRole.OPERATIONS})
FACT_RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
READERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE, UserRole.OPERATIONS,
                     UserRole.BRANCH_LEADER, UserRole.ADMIN})

DECISIONS = {"keep_remap": "Keep and re-map", "transfer": "Transfer", "exit": "Exit"}
PRODUCT_REASONS = {"unsuitable": "Does not fit the client's risk profile or goals", "concentration": "Too much in one sector or issuer",
                   "cost": "Costs more than another product with the same exposure", "performance": "Persistent underperformance",
                   "product_change": "The product's mandate changed, or it is merging or closing",
                   "other_product": "Another reason about the product itself"}
KINDS = {"code_change": "Distributor code change", "demat_transfer": "Off-market demat transfer", "pms_transition": "PMS transition in kind",
         "exit": "Taxable exit"}
KIND_DECISION = {"code_change": "keep_remap", "demat_transfer": "transfer", "pms_transition": "transfer", "exit": "exit"}
STEPS = {"requested": "Requested", "consented": "Consented by the client", "submitted": "Submitted", "confirmed": "Confirmed",
         "rejected": "Rejected", "withdrawn": "Withdrawn"}
CONFIRMED_AS = {"code_change": "Confirmed by the registrar", "demat_transfer": "Received, on the depository's statement",
                "pms_transition": "Received in the new PMS demat", "exit": "Redeemed, on the statement"}
NEXT = {"requested": ("consented", "withdrawn"), "consented": ("submitted", "withdrawn"),
        "submitted": ("confirmed", "rejected", "withdrawn"), "rejected": ("submitted", "withdrawn"), "confirmed": (), "withdrawn": ()}
CLOSED = frozenset({"confirmed", "withdrawn"})
TRANSFER_KINDS = {"own_account": "Between the same person's accounts", "gift": "A gift", "sale": "For a consideration", "other": "Another reason"}
FORMS = {"folio": "Mutual fund folio", "demat": "Demat account"}
TAX_CLASSES = {"listed_equity": "Listed share", "equity_fund": "Equity-oriented fund or ETF", "reit_invit": "REIT or InvIT unit",
               "specified_fund": "Specified (debt) fund", "other_listed": "Other listed fund or bond", "mld": "Market-linked debenture",
               "unlisted_debt": "Unlisted debt", "unlisted": "Unlisted share", "foreign_shares": "Foreign share", "physical_gold": "Physical gold",
               "land_building": "Land or building"}
LAW_KEYS = ["in_code_change_rule", "in_same_owner_transfer_rule", "in_stamp_duty_securities_transfer", "in_stamp_duty_no_consideration_rule",
            "in_cg_equity_st_rate", "in_cg_equity_lt_rate", "in_cg_equity_lt_exemption", "in_cg_other_lt_rate", "in_cess"]
NOT_MODELLED = [
    "Days out of the market between a redemption and a reinvestment",
    "Securities transaction tax, stamp duty and charges on a redemption and reinvestment",
    "An exit load where none is recorded from the scheme document",
    "A registrar's consent, cooling-off and trail rules for a code change",
    "Tax on a transfer that is a gift",
    "Gains realised outside the managed book, other than the exits scheduled here",
    "Losses brought forward against a later tax year's exits",
] + tax_india.NOT_MODELLED
# Savings accounts and policies (india_savings) that sit in no folio or demat account: nothing moves them, so they are not listed here.
NOT_CONSOLIDATED = frozenset({"ppf", "ssy", "scss", "nsc", "nps", "epf", "ulip"})
TOLERANCE = 0.001
MAX_LINES = 50


class ConsolidationError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def holding_key(line: HeldAwayHolding) -> str:
    """The holding itself, whichever statement last listed it: owner, institution, account and ISIN (else name)."""
    owner = str(line.person_id or line.entity_id or "joint")
    what = (line.isin or "").upper() or " ".join((line.instrument_name or "").lower().split())
    parts = [owner, " ".join((line.institution or "").lower().split()), " ".join((line.account_label or "").lower().split()), what]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


# -- Small reading --------------------------------------------------------------------------------------------------

def _uuid(value, label: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError):
        raise ConsolidationError(f"Give the {label}, from read_consolidation.")


def _ids(value) -> list[uuid.UUID]:
    if isinstance(value, (str, uuid.UUID)):
        value = str(value).split(",")
    out: list[uuid.UUID] = []
    for v in value or []:
        if str(v).strip():
            i = _uuid(v, "holding's line id")
            if i not in out:
                out.append(i)
    if not out:
        raise ConsolidationError("Name the holding or holdings by their line id, from read_consolidation.")
    if len(out) > MAX_LINES:
        raise ConsolidationError(f"At most {MAX_LINES} holdings at a time.")
    return out


def _day(value, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise ConsolidationError(f"Give the {label} as YYYY-MM-DD.")


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _amount(value, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(str(value).replace(",", ""))
    except ValueError:
        raise ConsolidationError(f"The {label} must be a number.")
    if out < 0 or out != out or out == float("inf"):
        raise ConsolidationError(f"The {label} cannot be negative.")
    return out


LOT = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*@\s*([\d,]+(?:\.\d+)?)\s*(?:on\s+)?(\d{4}-\d{2}-\d{2})\s*$", re.I)


def parse_lots(value, today: date) -> list[dict]:
    """Lots as the document shows them: "units @ cost per unit on YYYY-MM-DD", separated by semicolons."""
    if value in (None, "", []):
        return []
    items = value if isinstance(value, list) else [p for p in re.split(r"[;\n]", str(value)) if p.strip()]
    out = []
    for item in items:
        if isinstance(item, dict):
            units, cost, on = item.get("units"), item.get("cost_per_unit"), item.get("acquired_on")
        else:
            m = LOT.match(str(item))
            if not m:
                raise ConsolidationError(f'Write each lot as "units @ cost per unit on YYYY-MM-DD", separated by semicolons; this one '
                                         f'could not be read: {str(item)[:60]}')
            units, cost, on = m.group(1), m.group(2), m.group(3)
        units, cost = _amount(units, "units of a lot"), _amount(cost, "cost per unit")
        on = _day(on, "day a lot was acquired")
        if not units or not cost:
            raise ConsolidationError("A lot states its units and its cost per unit, both above zero.")
        if on > today:
            raise ConsolidationError("A lot cannot be acquired in the future.")
        out.append({"units": round(units, 6), "cost_per_unit": round(cost, 6), "acquired_on": on.isoformat()})
    return sorted(out, key=lambda l: l["acquired_on"])


# -- Loading one household ------------------------------------------------------------------------------------------

async def _book(session: AsyncSession, firm: Firm, household: Household, today: date) -> dict:
    """Everything the workbench reads for one household, loaded once."""
    hid = household.id
    persons = {p.id: p for p in (await session.execute(select(Person).where(Person.household_id == hid))).scalars().all()}
    entities = {e.id: e for e in (await session.execute(select(LegalEntity).where(LegalEntity.household_id == hid))).scalars().all()}
    lines = (await session.execute(select(HeldAwayHolding).where(
        HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.household_id == hid, HeldAwayHolding.status == "current",
    ).order_by(HeldAwayHolding.institution, HeldAwayHolding.account_label, HeldAwayHolding.instrument_name))).scalars().all()
    savings = [l for l in lines if (l.attributes or {}).get("savings_kind") in NOT_CONSOLIDATED]
    lines = [l for l in lines if l not in savings]
    instruments = (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()
    statements = {str(s.id): s.kind for s in (await session.execute(select(EcasStatement).where(
        EcasStatement.firm_id == firm.id, EcasStatement.household_id == hid))).scalars().all()}
    facts: dict[str, dict] = {}
    for r in (await session.execute(select(ConsolidationFacts).where(
            ConsolidationFacts.firm_id == firm.id, ConsolidationFacts.household_id == hid)
            .order_by(ConsolidationFacts.seq, ConsolidationFacts.created_at))).scalars().all():
        f = facts.setdefault(r.holding_key, {"documents": []})
        for field in ("tax_class", "lots", "exit_load_rate", "exit_load_days", "distributor_code", "form"):
            v = getattr(r, field)
            if v is not None:
                f[field] = float(v) if field == "exit_load_rate" else v
        f["document"], f["recorded_by"], f["recorded_on"] = r.document, r.recorded_by, r.recorded_on.isoformat()
        f["documents"].append(r.document)
    decisions: dict[str, ConsolidationDecision] = {}
    for d in (await session.execute(select(ConsolidationDecision).where(
            ConsolidationDecision.firm_id == firm.id, ConsolidationDecision.household_id == hid)
            .order_by(ConsolidationDecision.seq, ConsolidationDecision.created_at))).scalars().all():
        decisions[d.holding_key] = d
    items = (await session.execute(select(ConsolidationItem).where(
        ConsolidationItem.firm_id == firm.id, ConsolidationItem.household_id == hid)
        .order_by(ConsolidationItem.opened_on, ConsolidationItem.created_at))).scalars().all()
    steps: dict = {i.id: [] for i in items}
    if items:
        for s in (await session.execute(select(ConsolidationStep).where(
                ConsolidationStep.firm_id == firm.id, ConsolidationStep.item_id.in_(list(steps)))
                .order_by(ConsolidationStep.seq))).scalars().all():
            steps[s.item_id].append(s)
    return {"firm": firm, "household": household, "today": today, "persons": persons, "entities": entities, "lines": lines,
            "instruments": instruments, "statements": statements, "facts": facts, "decisions": decisions, "items": items, "steps": steps,
            "fmv": await tax_india_full.fmv_map(session, firm.id), "positions": {}, "budgets": {},
            "base": (firm.base_currency or "INR").upper(), "savings": len(savings)}


def _instrument(book: dict, line: HeldAwayHolding) -> Instrument | None:
    """The firm's own instrument the line names, by symbol or ISIN. Never by name."""
    symbol, isin = (line.symbol or "").upper(), (line.isin or "").upper()
    for i in book["instruments"]:
        if symbol and (i.symbol.upper() == symbol or (i.market_symbol or "").upper() == symbol):
            return i
    for i in book["instruments"]:
        if isin and str((i.values_tags or {}).get("isin") or "").upper() == isin:
            return i
    return None


def _form(book: dict, line: HeldAwayHolding, facts: dict) -> str | None:
    """A folio or a demat account: as recorded, else the kind of statement it came on, else the account's own label."""
    if facts.get("form"):
        return facts["form"]
    kind = book["statements"].get((line.attributes or {}).get("ecas_statement"))
    if kind:
        return "folio" if kind == "cams_kfintech" else "demat"
    label = (line.account_label or "").lower()
    return "folio" if "folio" in label else "demat" if "demat" in label else None


def _owner_name(book: dict, person_id, entity_id) -> str | None:
    if person_id:
        p = book["persons"].get(person_id)
        return p.full_name if p else None
    if entity_id:
        e = book["entities"].get(entity_id)
        return e.name if e else None
    return None


def _snapshot(book: dict, line: HeldAwayHolding) -> dict:
    """A statement line as the workbench reads it."""
    facts = book["facts"].get(holding_key(line)) or {}
    inst = _instrument(book, line)
    tags = (inst.values_tags or {}) if inst else {}
    tax_class = facts.get("tax_class") or tags.get("tax_class")
    return {
        "line_id": str(line.id), "holding_key": holding_key(line),
        "person_id": str(line.person_id) if line.person_id else None, "entity_id": str(line.entity_id) if line.entity_id else None,
        "owner": _owner_name(book, line.person_id, line.entity_id) or "Joint or not stated", "institution": line.institution,
        "account_label": line.account_label, "name": line.instrument_name, "symbol": line.symbol, "isin": line.isin,
        "units": float(line.quantity) if line.quantity is not None else None, "value": float(line.value), "currency": line.currency,
        "value_on": line.value_on.isoformat(), "source_document": line.source_document, "form": _form(book, line, facts),
        "tax_class": tax_class, "tax_class_label": TAX_CLASSES.get(tax_class) if tax_class else None,
        "tax_class_from": ("recorded" if facts.get("tax_class") else "instrument" if tax_class else None),
        "fund_plan": tags.get("fund_plan"), "instrument_id": str(inst.id) if inst else None,
        "distributor_code": facts.get("distributor_code"),
    }


# -- What a sale would crystallise ----------------------------------------------------------------------------------

def assess(book: dict, row: dict, *, sale_on: date, units: float | None = None) -> dict:
    """The gain a sale of this holding (or of `units` of it) on `sale_on` would crystallise, oldest lot first, at the
    value the statement states. Not assessed, with what is missing, unless units, lots covering exactly those units and a
    tax class are on file."""
    facts = book["facts"].get(row["holding_key"]) or {}
    lots = facts.get("lots") or []
    missing = []
    if row["currency"] != book["base"]:
        missing.append("a value in rupees: the statement's currency is not converted")
    if not row["units"]:
        missing.append("units on the statement")
    if not lots:
        missing.append("cost and acquisition date")
    elif row["units"]:
        held = sum(l["units"] for l in lots)
        if abs(held - row["units"]) > TOLERANCE:
            missing.append(f"lots for {row['units']:,.3f} units (recorded: {held:,.3f})")
    if not row["tax_class"]:
        missing.append("tax class")
    selling = units if units is not None else row["units"]
    if units is not None and row["units"] and units > row["units"] + TOLERANCE:
        missing.append("that many units: the statement lists fewer")
    if missing:
        return {"assessed": False, "missing": missing, "units": selling, "proceeds": None, "cost": None, "buckets": None, "gain": None,
                "short_term": None, "long_term": None, "exit_load": None, "long_term_from": None, "grandfather_missing": False}
    price = row["value"] / row["units"]
    klass = row["tax_class"]
    fmv = book["fmv"].get(uuid.UUID(row["instrument_id"])) if row.get("instrument_id") else None
    left, buckets = selling, {b: 0.0 for b in tax_india.BUCKETS}
    proceeds = cost = 0.0
    load_rate, load_days = facts.get("exit_load_rate"), facts.get("exit_load_days")
    load = None if load_rate is None else 0.0
    turns: list[date] = []
    no_fmv = False
    for lot in sorted(lots, key=lambda l: l["acquired_on"]):
        if left <= TOLERANCE:
            break
        take = min(lot["units"], left)
        left -= take
        acquired = date.fromisoformat(lot["acquired_on"])
        if acquired > sale_on:
            continue
        value = take * price
        paid = take * lot["cost_per_unit"]
        if acquired < tax_india.GRANDFATHER_BEFORE and klass in tax_india.EQUITY_CLASSES and fmv is None:
            no_fmv = True
        paid = tax_india.grandfathered_cost(klass, acquired, paid, value, None if fmv is None else float(fmv) * take)
        b = tax_india.bucket(klass, acquired, sale_on)
        buckets[b] += value - paid
        proceeds += value
        cost += paid
        if load is not None and (load_days is None or (sale_on - acquired).days < load_days):
            load += value * load_rate
        begins = tax_india.long_term_from(klass, acquired)
        if begins is not None and begins > sale_on:
            turns.append(begins)
    short = buckets[tax_india.ST_EQUITY] + buckets[tax_india.ST_SLAB]
    long_ = buckets[tax_india.LT_EQUITY] + buckets[tax_india.LT_OTHER]
    return {"assessed": True, "missing": [], "units": round(selling, 6), "proceeds": round(proceeds, 2), "cost": round(cost, 2),
            "buckets": {k: round(v, 2) for k, v in buckets.items()}, "gain": round(short + long_, 2), "short_term": round(short, 2),
            "long_term": round(long_, 2), "exit_load": None if load is None else round(load, 2),
            # The day every unit sold would be long-term, where some would still be short-term on the day.
            "long_term_from": max(turns).isoformat() if turns else None, "grandfather_missing": no_fmv}


async def _position(session: AsyncSession, book: dict, person_id: str | None, entity_id: str | None, on: date) -> dict | None:
    """One taxpayer's year, as the India engine reads it: what the managed book realised and the losses brought forward
    this year. A later year starts empty. None where the line names no holder: nobody's tax can be worked."""
    if not person_id and not entity_id:
        return None
    firm, today = book["firm"], book["today"]
    market = markets.MARKETS["IN"]
    year, now = markets.tax_year(market, on), markets.tax_year(market, today)
    key = (person_id, entity_id, year["label"])
    if key in book["positions"]:
        return book["positions"][key]
    pid, eid = uuid.UUID(person_id) if person_id else None, uuid.UUID(entity_id) if entity_id else None
    inputs = tax_india._tax_inputs(book["persons"].get(pid) if pid else None, book["entities"].get(eid) if eid else None)
    realised, brought = {b: 0.0 for b in tax_india.BUCKETS}, {}
    if year["start"] <= today:
        owner = Mandate.person_id == pid if pid else Mandate.entity_id == eid
        accounts = (await session.execute(select(Account.id).join(Mandate, Mandate.id == Account.mandate_id)
                                          .where(Mandate.firm_id == firm.id, owner))).scalars().all()
        realised = await tax_india.realised_this_year(session, list(accounts), year, book["fmv"])
        if year["label"] == now["label"]:
            brought = await tax_india_full.brought_forward(session, firm.id, person_id=pid, entity_id=eid, today=today,
                                                           fallback=inputs["brought"])
    out = {"tax_year": year["label"], "tax_year_end": year["end"].isoformat(), "realised": realised, "brought": brought,
           "slab_rate": inputs["slab_rate"], "income": inputs["income"]}
    book["positions"][key] = out
    return out


def _tax(position: dict, before: dict, adding: dict) -> tuple[float, list[str]]:
    """What `adding` puts on a year's capital gains tax, on top of the year and `before`."""
    slab, income = position["slab_rate"], position["income"]
    base = tax_india._merge(position["realised"], before)
    was = tax_india.tax_on(tax_india.set_off(base, position["brought"], slab_rate=slab), slab_rate=slab, income=income)
    after = tax_india.tax_on(tax_india.set_off(tax_india._merge(base, adding), position["brought"], slab_rate=slab),
                             slab_rate=slab, income=income)
    return round(after["amount"] - was["amount"], 2), after["incomplete"]


async def _budget(session: AsyncSession, book: dict, person_id: str | None, entity_id: str | None) -> dict | None:
    """The taxpayer's gain budget for a year: what their mandates record, else what the firm itself set as its default.
    Never the platform's default: with neither there is no budget on file."""
    if not person_id and not entity_id:
        return None
    key = (person_id, entity_id)
    if key not in book["budgets"]:
        owner = Mandate.person_id == uuid.UUID(person_id) if person_id else Mandate.entity_id == uuid.UUID(entity_id)
        mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == book["firm"].id, Mandate.is_active.is_(True), owner)
                                          .order_by(Mandate.name))).scalars().all()
        stated = [(m, (m.constraints or {}).get("cgt_budget")) for m in mandates]
        stated = [(m, float(v)) for m, v in stated if v is not None]
        default = ((book["firm"].settings or {}).get("foundation") or {}).get("default_cgt_budget")
        if stated:
            out = {"amount": round(sum(v for _, v in stated), 2), "source": "On the mandate: " + ", ".join(m.name for m, _ in stated)}
        elif default is not None and mandates:
            out = {"amount": float(default), "source": "The firm's default"}
        else:
            out = None
        book["budgets"][key] = out
    return book["budgets"][key]


def _stamp_duty(item: ConsolidationItem, stated: float | None, today: date) -> dict:
    d = item.details or {}
    kind, consideration = d.get("transfer_kind"), d.get("consideration")
    rate = law_figures.value("in_stamp_duty_securities_transfer", today)
    if consideration:
        return {"state": "estimated", "consideration": consideration, "rate": rate, "estimate": round(consideration * rate, 2), "stated": stated,
                "figure": "in_stamp_duty_securities_transfer"}
    if kind in ("own_account", "gift"):
        return {"state": "none_no_consideration", "consideration": 0.0, "rate": None, "estimate": 0.0, "stated": stated,
                "figure": "in_stamp_duty_no_consideration_rule"}
    return {"state": "not_assessed", "consideration": None, "rate": None, "estimate": None, "stated": stated, "figure": None}


def _status(book: dict, item: ConsolidationItem) -> str:
    steps = book["steps"].get(item.id) or []
    return steps[-1].step if steps else "requested"


def _step_label(kind: str, step: str) -> str:
    return CONFIRMED_AS[kind] if step == "confirmed" else STEPS[step]


def _item_view(book: dict, item: ConsolidationItem) -> dict:
    steps = book["steps"].get(item.id) or []
    status = _status(book, item)
    last = steps[-1] if steps else None
    current = {str(l.id) for l in book["lines"]}
    changed = status not in CLOSED and any(
        (d := book["decisions"].get(k)) is None or d.decision != KIND_DECISION[item.kind] for k in item.holding_keys)
    stated = next((float(s.stamp_duty_stated) for s in reversed(steps) if s.stamp_duty_stated is not None),
                  (item.details or {}).get("stamp_duty_stated"))
    ended = status in CLOSED
    until = last.happened_on if ended and last else book["today"]
    return {
        "item_id": str(item.id), "kind": item.kind, "kind_label": KINDS[item.kind], "status": status,
        "status_label": _step_label(item.kind, status), "open": not ended, "next": list(NEXT[status]),
        "owner": _owner_name(book, item.person_id, item.entity_id) or "Joint or not stated", "institution": item.institution,
        "account_label": item.account_label, "value": round(sum(l["value"] for l in item.lines), 2),
        "lines": [{k: l.get(k) for k in ("line_id", "name", "symbol", "units", "value", "currency", "fund_plan")} for l in item.lines],
        "details": item.details or {}, "opened_on": item.opened_on.isoformat(), "days": (until - item.opened_on).days,
        "last_on": last.happened_on.isoformat() if last else None, "reference": last.reference if last else None,
        "reject_reason": last.note if last and last.step == "rejected" else None,
        "classification_changed": changed,
        # Nothing here moves a holding, so a finished transfer or exit may still be listed where it was.
        "old_line_listed": status == "confirmed" and item.kind != "code_change" and any(l["line_id"] in current for l in item.lines),
        "regular_plan": item.kind == "code_change" and any(l.get("fund_plan") == "regular" for l in item.lines),
        "stamp_duty": _stamp_duty(item, stated, book["today"]) if item.kind == "demat_transfer" else None,
        "steps": [{"step": s.step, "label": _step_label(item.kind, s.step), "on": s.happened_on.isoformat(), "evidence": s.evidence,
                   "reference": s.reference, "note": s.note, "recorded_by": s.recorded_by} for s in steps],
    }


def _open_item_for(book: dict, key: str) -> ConsolidationItem | None:
    found = [i for i in book["items"] if key in i.holding_keys and _status(book, i) != "withdrawn"]
    return found[-1] if found else None


def _decision_view(d: ConsolidationDecision | None) -> dict | None:
    if d is None:
        return None
    return {"decision": d.decision, "label": DECISIONS[d.decision], "reason": d.reason, "product_reason": d.product_reason,
            "product_reason_label": PRODUCT_REASONS.get(d.product_reason or ""),
            "planned_on": d.planned_on.isoformat() if d.planned_on else None,
            "units": float(d.units) if d.units is not None else None, "by": d.decided_by, "on": d.decided_on.isoformat()}


# -- The schedule of exits against the gain budget ------------------------------------------------------------------

async def _schedule(session: AsyncSession, book: dict) -> list[dict]:
    entries = []
    done_keys: set[tuple] = set()
    # Exits already redeemed still count against the year's budget, from the holding as it stood when the exit was opened.
    for item in book["items"]:
        if item.kind != "exit" or _status(book, item) != "confirmed":
            continue
        on = book["steps"][item.id][-1].happened_on
        for line in item.lines:
            done_keys.add((line["holding_key"], line["line_id"]))
            entries.append({"row": line, "on": on, "units": line.get("planned_units"), "status": "confirmed", "item_id": str(item.id),
                            "decision": None, "reason": line.get("reason"), "product_reason_label": line.get("product_reason_label")})
    for line in book["lines"]:
        key = holding_key(line)
        d = book["decisions"].get(key)
        if d is None or d.decision != "exit" or (key, str(line.id)) in done_keys:
            continue
        item = _open_item_for(book, key)
        item = item if item is not None and item.kind == "exit" else None
        entries.append({"row": _snapshot(book, line), "on": d.planned_on, "units": float(d.units) if d.units is not None else None,
                        "status": _status(book, item) if item else "scheduled", "item_id": str(item.id) if item else None, "decision": d,
                        "reason": d.reason, "product_reason_label": PRODUCT_REASONS.get(d.product_reason or "")})
    groups: dict[tuple, list] = {}
    for e in entries:
        year = markets.tax_year(markets.MARKETS["IN"], e["on"])["label"]
        groups.setdefault((e["row"]["person_id"], e["row"]["entity_id"], year), []).append(e)
    out = []
    for (pid, eid, year), rows in groups.items():
        rows.sort(key=lambda e: (e["status"] != "confirmed", e["on"], e["row"]["name"]))
        position = await _position(session, book, pid, eid, rows[0]["on"])
        budget = await _budget(session, book, pid, eid)
        realised = round(sum(position["realised"].values()), 2) if position else None
        before = {b: 0.0 for b in tax_india.BUCKETS}
        planned = tax_total = 0.0
        out_rows, unassessed, incomplete = [], 0, set()
        for e in rows:
            a = assess(book, e["row"], sale_on=e["on"], units=e["units"])
            tax = None
            if a["assessed"] and position is not None:
                tax, gaps = _tax(position, before, a["buckets"])
                incomplete |= set(gaps)
                tax_total += tax
                before = tax_india._merge(before, a["buckets"])
                planned += a["gain"]
            else:
                unassessed += 1
            used = max((realised or 0) + planned, 0.0)
            out_rows.append({
                "line_id": e["row"]["line_id"], "item_id": e["item_id"], "name": e["row"]["name"], "institution": e["row"]["institution"],
                "account_label": e["row"]["account_label"], "planned_on": e["on"].isoformat(), "status": e["status"],
                "status_label": "Scheduled" if e["status"] == "scheduled" else _step_label("exit", e["status"]),
                "overdue": e["status"] != "confirmed" and e["on"] < book["today"], "product_reason_label": e["product_reason_label"],
                "reason": e["reason"], **a, "tax": tax,
                "within_budget": (used <= budget["amount"] + 0.005) if budget and a["assessed"] and position is not None else None,
            })
        used = round(max((realised or 0) + planned, 0.0), 2) if realised is not None else None
        out.append({
            "person_id": pid, "entity_id": eid, "taxpayer": rows[0]["row"]["owner"], "tax_year": year, "budget": budget,
            "realised_book": realised, "exit_gain": round(planned, 2), "used": used,
            "left": round(budget["amount"] - used, 2) if budget and used is not None else None,
            "over": round(max(used - budget["amount"], 0.0), 2) if budget and used is not None else None,
            "estimated_tax": round(tax_total, 2) if position is not None else None, "not_assessed": unassessed,
            "incomplete": sorted(incomplete), "rows": out_rows,
        })
    return sorted(out, key=lambda g: (g["tax_year"], g["taxpayer"]))


# -- Reading --------------------------------------------------------------------------------------------------------

async def _view(session: AsyncSession, book: dict) -> dict:
    today = book["today"]
    holdings = []
    trap_by_payer: dict[tuple, list] = {}
    for line in book["lines"]:
        row = _snapshot(book, line)
        key = row["holding_key"]
        facts = book["facts"].get(key)
        d = book["decisions"].get(key)
        trap = assess(book, row, sale_on=today)
        position = await _position(session, book, row["person_id"], row["entity_id"], today) if trap["assessed"] else None
        trap["tax"] = _tax(position, {}, trap["buckets"])[0] if position else None
        trap["cost_of_redeeming"] = round(trap["tax"] + (trap["exit_load"] or 0), 2) if trap["tax"] is not None else None
        item = _open_item_for(book, key)
        row.update({
            "facts": None if not facts else {k: facts.get(k) for k in ("lots", "exit_load_rate", "exit_load_days", "document", "recorded_by", "recorded_on")},
            "decision": _decision_view(d), "redeem": trap,
            "item": None if item is None else {"item_id": str(item.id), "kind": item.kind, "kind_label": KINDS[item.kind],
                                               "status": _status(book, item), "status_label": _step_label(item.kind, _status(book, item))},
        })
        holdings.append(row)
        if d is not None and d.decision in ("keep_remap", "transfer"):
            trap_by_payer.setdefault((row["person_id"], row["entity_id"]), []).append(trap)

    # What redeeming every holding marked keep or transfer would crystallise, taxpayer by taxpayer, set off together.
    trap_total = {"holdings": 0, "assessed": 0, "not_assessed": 0, "value": 0.0, "gain": 0.0, "tax": 0.0, "exit_load": 0.0,
                  "exit_load_not_recorded": 0}
    for (pid, eid), rows in trap_by_payer.items():
        good = [t for t in rows if t["assessed"] and t["tax"] is not None]
        trap_total["holdings"] += len(rows)
        trap_total["assessed"] += len(good)
        trap_total["not_assessed"] += len(rows) - len(good)
        if not good:
            continue
        buckets = {b: sum(t["buckets"][b] for t in good) for b in tax_india.BUCKETS}
        position = await _position(session, book, pid, eid, today)
        trap_total["tax"] += _tax(position, {}, buckets)[0]
        trap_total["gain"] += sum(t["gain"] for t in good)
        trap_total["value"] += sum(t["proceeds"] for t in good)
        trap_total["exit_load"] += sum(t["exit_load"] or 0 for t in good)
        trap_total["exit_load_not_recorded"] += sum(1 for t in good if t["exit_load"] is None)
    for k in ("value", "gain", "tax", "exit_load"):
        trap_total[k] = round(trap_total[k], 2)
    trap_total["total"] = round(trap_total["tax"] + trap_total["exit_load"], 2)

    # The roll-up: fund houses and their folios, then the demat accounts and anything whose form is not stated.
    accounts: dict[tuple, dict] = {}
    for h in holdings:
        k = (h["person_id"], h["entity_id"], h["institution"], h["account_label"])
        a = accounts.setdefault(k, {"owner": h["owner"], "institution": h["institution"], "account_label": h["account_label"], "form": h["form"],
                                    "lines": 0, "value": 0.0, "distributor_codes": [], "decisions": {}, "unclassified": 0})
        a["lines"] += 1
        a["form"] = a["form"] or h["form"]
        if h["currency"] == book["base"]:
            a["value"] = round(a["value"] + h["value"], 2)
        if h["distributor_code"] and h["distributor_code"] not in a["distributor_codes"]:
            a["distributor_codes"].append(h["distributor_code"])
        if h["decision"]:
            a["decisions"][h["decision"]["decision"]] = a["decisions"].get(h["decision"]["decision"], 0) + 1
        else:
            a["unclassified"] += 1
    houses: dict[str, dict] = {}
    for a in accounts.values():
        if a["form"] != "folio":
            continue
        f = houses.setdefault(a["institution"], {"name": a["institution"], "folios": 0, "lines": 0, "value": 0.0, "unclassified": 0})
        f["folios"] += 1
        f["lines"] += a["lines"]
        f["unclassified"] += a["unclassified"]
        f["value"] = round(f["value"] + a["value"], 2)

    items = [_item_view(book, i) for i in book["items"]]
    count = lambda kind: {s: n for s in STEPS if (n := sum(1 for i in items if i["kind"] == kind and i["status"] == s))}  # noqa: E731
    classified = {d: sum(1 for h in holdings if h["decision"] and h["decision"]["decision"] == d) for d in DECISIONS}
    classified["unclassified"] = sum(1 for h in holdings if not h["decision"])
    schedule = await _schedule(session, book)
    forms = [a["form"] for a in accounts.values()]
    return {
        "applies": True, "household_id": str(book["household"].id), "household": book["household"].name, "currency": book["base"],
        "as_of": today.isoformat(),
        "summary": {"lines": len(holdings), "fund_houses": len(houses), "folios": forms.count("folio"), "demat_accounts": forms.count("demat"),
                    "form_not_stated": forms.count(None), "value": round(sum(h["value"] for h in holdings if h["currency"] == book["base"]), 2),
                    "not_converted": sum(1 for h in holdings if h["currency"] != book["base"]), "classified": classified,
                    "code_changes": count("code_change"), "demat_transfers": count("demat_transfer"), "pms_transitions": count("pms_transition"),
                    "exits": count("exit"), "exits_scheduled": sum(len(g["rows"]) for g in schedule),
                    "cost_not_on_file": sum(1 for h in holdings if not h["redeem"]["assessed"]), "savings_not_listed": book["savings"]},
        "fund_houses": sorted(houses.values(), key=lambda f: (-f["value"], f["name"])),
        "accounts": sorted(accounts.values(), key=lambda a: (a["form"] or "z", a["institution"], a["account_label"] or "")),
        "holdings": holdings, "items": items, "schedule": schedule, "redeem_instead": trap_total,
        "law": law_figures.cited(LAW_KEYS, today), "not_modelled": NOT_MODELLED, "estimate": True,
    }


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, today: date | None = None) -> dict | None:
    """One household's workbench, or None if it is not this firm's. Outside India it says it does not apply."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    if not applies(firm):
        return {"applies": False, "household_id": str(household_id), "household": household.name}
    return await _view(session, await _book(session, firm, household, today or firm_time.today(firm)))


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every household with holdings elsewhere on file or work tracked, the most still to do first."""
    firm = await session.get(Firm, firm_id)
    if not applies(firm):
        return {"applies": False, "households": []}
    held = set((await session.execute(select(HeldAwayHolding.household_id).where(
        HeldAwayHolding.firm_id == firm_id, HeldAwayHolding.status == "current").distinct())).scalars().all())
    worked = set((await session.execute(select(ConsolidationItem.household_id).where(ConsolidationItem.firm_id == firm_id).distinct())).scalars().all())
    ids = held | worked
    households = (await session.execute(select(Household).where(
        Household.firm_id == firm_id, Household.id.in_(list(ids)), Household.left_on.is_(None)))).scalars().all() if ids else []
    today = firm_time.today(firm)
    rows = []
    for h in households:
        v = await _view(session, await _book(session, firm, h, today))
        s = v["summary"]
        if not s["lines"] and not v["items"]:
            continue
        rows.append({
            "household_id": v["household_id"], "household": v["household"], "lines": s["lines"], "folios": s["folios"],
            "fund_houses": s["fund_houses"], "demat_accounts": s["demat_accounts"], "value": s["value"], "classified": s["classified"],
            "open_items": sum(1 for i in v["items"] if i["open"]), "rejected": sum(1 for i in v["items"] if i["status"] == "rejected"),
            "confirmed": sum(1 for i in v["items"] if i["status"] == "confirmed"), "exits_scheduled": s["exits_scheduled"],
            "started": bool(v["items"] or any(h2["decision"] for h2 in v["holdings"])),
        })
    rows.sort(key=lambda r: (not r["started"], -r["rejected"], -r["open_items"], -r["value"], r["household"]))
    return {"applies": True, "households": rows, "currency": firm.base_currency, "as_of": today.isoformat(),
            "summary": {"households": len(rows), "started": sum(1 for r in rows if r["started"]),
                        "to_classify": sum(r["classified"]["unclassified"] for r in rows), "open_items": sum(r["open_items"] for r in rows),
                        "rejected": sum(r["rejected"] for r in rows)}}


# -- Recording ------------------------------------------------------------------------------------------------------

async def _opened(session: AsyncSession, firm: Firm, line_ids: list[uuid.UUID]) -> tuple[dict, list[HeldAwayHolding]]:
    """The book of the household these lines belong to, and the lines. All one household, all current, all this firm's."""
    if not applies(firm):
        raise ConsolidationError("The consolidation workbench is for an Indian firm.")
    rows = []
    for i in line_ids:
        row = await session.get(HeldAwayHolding, i)
        if row is None or row.firm_id != firm.id:
            raise ConsolidationError("That holding is not on file here. read_consolidation lists the lines and their ids.")
        if row.status != "current":
            raise ConsolidationError(f"{row.instrument_name} at {row.institution} is no longer a current line. Read the workbench again.")
        rows.append(row)
    if len({r.household_id for r in rows}) != 1:
        raise ConsolidationError("The holdings belong to different households; do one household at a time.")
    household = await session.get(Household, rows[0].household_id)
    if household is None or household.firm_id != firm.id:
        raise ConsolidationError("That household is not this firm's.")
    book = await _book(session, firm, household, firm_time.today(firm))
    by_id = {l.id: l for l in book["lines"]}
    for r in rows:
        if r.id not in by_id:
            raise ConsolidationError(f"{r.instrument_name} is a savings account or policy: it sits in no folio or demat account, so there is "
                                     "nothing to consolidate. read_india_savings shows it.")
    return book, [by_id[r.id] for r in rows]


async def prepare_facts(session: AsyncSession, firm: Firm, role, *, line_id, document: str | None, lots=None, tax_class: str | None = None,
                        exit_load_rate=None, exit_load_days=None, distributor_code: str | None = None, form: str | None = None) -> dict:
    if role not in FACT_RECORDERS:
        raise ConsolidationError("An adviser, a paraplanner or operations records what a document says about a holding.")
    book, (line,) = await _opened(session, firm, [_uuid(line_id, "holding's line id")])
    doc = _text(document, 300)
    if len(doc) < 4:
        raise ConsolidationError("Say which document this is read from. A figure with no source is not recorded.")
    parsed = parse_lots(lots, book["today"])
    if parsed and line.quantity is not None and sum(l["units"] for l in parsed) > float(line.quantity) + TOLERANCE:
        raise ConsolidationError(f"The lots total {sum(l['units'] for l in parsed):,.3f} units and the statement lists "
                                 f"{float(line.quantity):,.3f}. Check them against the document.")
    klass = _text(tax_class, 24).lower() or None
    if klass and klass not in TAX_CLASSES:
        raise ConsolidationError(f"The tax class is one of {', '.join(TAX_CLASSES)}.")
    rate = _amount(exit_load_rate, "exit load")
    if rate is not None and rate > 0.1:
        raise ConsolidationError("Give the exit load as a fraction, as the scheme document states it: 0.01 is 1%. Above 10% is refused.")
    days = _amount(exit_load_days, "exit load's period in days")
    if days is not None and rate is None:
        raise ConsolidationError("An exit load's period goes with its rate.")
    code = _text(distributor_code, 40) or None
    shape = _text(form, 8).lower() or None
    if shape and shape not in FORMS:
        raise ConsolidationError("The form is folio or demat.")
    if not any([parsed, klass, rate is not None, code, shape]):
        raise ConsolidationError("Record at least one of: lots with their cost and date, tax class, exit load, distributor code, or form.")
    fields = {"lots": parsed or None, "tax_class": klass, "exit_load_rate": rate, "exit_load_days": int(days) if days is not None else None,
              "distributor_code": code, "form": shape}
    after = dict(book["facts"].get(holding_key(line)) or {})
    after.update({k: v for k, v in fields.items() if v is not None})
    book["facts"][holding_key(line)] = after
    row = _snapshot(book, line)
    trap = assess(book, row, sale_on=book["today"])
    if trap["assessed"]:
        position = await _position(session, book, row["person_id"], row["entity_id"], book["today"])
        trap["tax"] = _tax(position, {}, trap["buckets"])[0] if position else None
    return {"book": book, "line": line, "household": book["household"], "document": doc, "fields": fields, "row": row, "redeem": trap}


async def record_facts(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_facts(session, firm, role, **terms)
    line, key = p["line"], holding_key(p["line"])
    count = len((await session.execute(select(ConsolidationFacts.id).where(
        ConsolidationFacts.firm_id == firm.id, ConsolidationFacts.household_id == line.household_id,
        ConsolidationFacts.holding_key == key))).all())
    session.add(ConsolidationFacts(firm_id=firm.id, household_id=line.household_id, holding_key=key, document=p["document"], recorded_by=by,
                                   recorded_on=p["book"]["today"], seq=count + 1, **p["fields"]))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="consolidation_facts_recorded", actor=by, content={
        "household_id": str(line.household_id), "line_id": str(line.id), "name": line.instrument_name, "document": p["document"],
        "fields": {k: v for k, v in p["fields"].items() if v is not None}})
    view = await for_household(session, line.household_id, firm_id=firm.id)
    return {**view, "recorded": f"{line.instrument_name} at {line.institution}"}


async def prepare_classification(session: AsyncSession, firm: Firm, role, *, line_ids, decision: str | None, reason: str | None,
                                 product_reason: str | None = None, planned_on=None, units=None) -> dict:
    if role not in CLASSIFIERS:
        raise ConsolidationError("An adviser or a paraplanner classifies a holding: it is advice.")
    book, lines = await _opened(session, firm, _ids(line_ids))
    what = _text(decision, 12).lower()
    if what not in DECISIONS:
        raise ConsolidationError("The decision is keep_remap, transfer or exit.")
    why = _text(reason, 500)
    if len(why) < 5:
        raise ConsolidationError("Give the reason, as the adviser would write it on file.")
    product = _text(product_reason, 24).lower() or None
    amount = _amount(units, "units to exit")
    day = None
    if what == "exit":
        if len(lines) != 1:
            raise ConsolidationError("An exit is classified one holding at a time, with its own reason.")
        if product not in PRODUCT_REASONS:
            raise ConsolidationError("An exit needs a reason about the product itself (consolidating is not one: a code change or a transfer "
                                     f"does that without selling). One of: {', '.join(PRODUCT_REASONS)}.")
        if len(why) < 15:
            raise ConsolidationError("Say in a sentence why this product should no longer be held.")
        if planned_on in (None, ""):
            raise ConsolidationError("Give the planned day of the exit, so it sits on the schedule against the gain budget.")
        day = _day(planned_on, "planned day")
        if day < book["today"]:
            raise ConsolidationError("The planned day cannot be in the past.")
        if amount is not None:
            held = lines[0].quantity
            if held is None:
                raise ConsolidationError("The statement lists no units, so part of the holding cannot be named. Leave units out.")
            if not amount or amount > float(held) + TOLERANCE:
                raise ConsolidationError(f"The units to exit must be above zero and no more than the {float(held):,.3f} held.")
    elif product or planned_on not in (None, "") or amount is not None:
        raise ConsolidationError("A product reason, a planned day and units belong to an exit only.")
    flagged = []
    for line in lines:
        item = _open_item_for(book, holding_key(line))
        if item is not None and _status(book, item) not in CLOSED and KIND_DECISION[item.kind] != what:
            flagged.append(f"{KINDS[item.kind]} for {line.instrument_name}")
    # What the card shows: for an exit, the sale on its day against the budget; otherwise what redeeming instead would cost.
    sale_on = day or book["today"]
    redeem, budget_line = [], None
    for line in lines:
        row = _snapshot(book, line)
        a = assess(book, row, sale_on=sale_on, units=amount)
        position = await _position(session, book, row["person_id"], row["entity_id"], sale_on) if a["assessed"] else None
        a["tax"] = _tax(position, {}, a["buckets"])[0] if position else None
        redeem.append({"row": row, **a})
    if what == "exit":
        row = redeem[0]["row"]
        budget = await _budget(session, book, row["person_id"], row["entity_id"])
        if budget:
            year = markets.tax_year(markets.MARKETS["IN"], day)["label"]
            groups = [g for g in await _schedule(session, book)
                      if g["person_id"] == row["person_id"] and g["entity_id"] == row["entity_id"] and g["tax_year"] == year]
            others = sum(r["gain"] or 0 for g in groups for r in g["rows"] if r["line_id"] != row["line_id"] and r["assessed"])
            position = await _position(session, book, row["person_id"], row["entity_id"], day)
            used = sum(position["realised"].values()) + others + (redeem[0]["gain"] or 0)
            budget_line = {"tax_year": year, "amount": budget["amount"], "source": budget["source"],
                           "left_after": round(budget["amount"] - max(used, 0.0), 2) if redeem[0]["assessed"] else None}
    return {"book": book, "lines": lines, "household": book["household"], "decision": what, "reason": why, "product_reason": product,
            "planned_on": day, "units": amount, "flagged": flagged, "redeem": redeem, "budget": budget_line}


async def record_classification(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_classification(session, firm, role, **terms)
    hid = p["household"].id
    for line in p["lines"]:
        key = holding_key(line)
        count = len((await session.execute(select(ConsolidationDecision.id).where(
            ConsolidationDecision.firm_id == firm.id, ConsolidationDecision.household_id == hid,
            ConsolidationDecision.holding_key == key))).all())
        session.add(ConsolidationDecision(firm_id=firm.id, household_id=hid, holding_key=key, decision=p["decision"], reason=p["reason"],
                                          product_reason=p["product_reason"], planned_on=p["planned_on"], units=p["units"], decided_by=by,
                                          decided_on=p["book"]["today"], seq=count + 1))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="consolidation_classified", actor=by, content={
        "household_id": str(hid), "lines": [str(l.id) for l in p["lines"]], "names": [l.instrument_name for l in p["lines"]],
        "decision": p["decision"], "reason": p["reason"], "product_reason": p["product_reason"],
        "planned_on": p["planned_on"].isoformat() if p["planned_on"] else None, "units": p["units"]})
    view = await for_household(session, hid, firm_id=firm.id)
    return {**view, "recorded": f"{len(p['lines'])} holding(s): {DECISIONS[p['decision']].lower()}"}


async def prepare_item(session: AsyncSession, firm: Firm, role, *, kind: str | None, line_ids, evidence: str | None, on=None,
                       from_code: str | None = None, to_code: str | None = None, to_account: str | None = None,
                       transfer_kind: str | None = None, reason_code: str | None = None, consideration=None, stamp_duty_stated=None,
                       manager_from: str | None = None, manager_to: str | None = None, note: str | None = None) -> dict:
    if role not in TRACKERS:
        raise ConsolidationError("Operations opens and records a transfer, a code change or an exit's steps.")
    what = _text(kind, 16).lower()
    if what not in KINDS:
        raise ConsolidationError(f"The kind is one of {', '.join(KINDS)}.")
    book, given = await _opened(session, firm, _ids(line_ids))
    today = book["today"]
    day = _day(on, "day of the request") if on not in (None, "") else today
    if day > today:
        raise ConsolidationError("A step cannot be dated in the future.")
    proof = _text(evidence, 300)
    if len(proof) < 4:
        raise ConsolidationError("Say which document this request is: the form, instruction or letter, and where it is filed.")
    wanted = KIND_DECISION[what]
    for line in given:
        d = book["decisions"].get(holding_key(line))
        if d is None or d.decision != wanted:
            now = DECISIONS[d.decision].lower() if d else "not classified"
            raise ConsolidationError(f"{line.instrument_name} is {now}. A {KINDS[what].lower()} is opened only for a holding an adviser has "
                                     f"classified {DECISIONS[wanted].lower()}.")
    first = given[0]
    if len({(l.person_id, l.entity_id, l.institution) for l in given}) != 1:
        raise ConsolidationError("One request covers one holder's holdings at one institution.")
    lines = given
    if what == "code_change":
        if not first.account_label:
            raise ConsolidationError("The line names no folio or account, so there is nothing for the registrar to re-map.")
        if len({l.account_label for l in given}) != 1:
            raise ConsolidationError("A code change is one folio at a time.")
        lines = [l for l in book["lines"]
                 if (l.person_id, l.entity_id, l.institution, l.account_label) == (first.person_id, first.entity_id, first.institution, first.account_label)
                 and (d := book["decisions"].get(holding_key(l))) is not None and d.decision == "keep_remap"]
    elif what == "exit" and len(given) != 1:
        raise ConsolidationError("An exit is tracked one holding at a time.")
    for line in lines:
        key = holding_key(line)
        for item in book["items"]:
            if key not in item.holding_keys:
                continue
            status = _status(book, item)
            if status not in CLOSED:
                raise ConsolidationError(f"{line.instrument_name} is already on an open {KINDS[item.kind].lower()} ({STEPS[status].lower()}). "
                                         "Record its next step, or withdraw it first.")
            if status == "confirmed" and item.kind == what and any(l["line_id"] == str(line.id) for l in item.lines):
                raise ConsolidationError(f"A {KINDS[what].lower()} for {line.instrument_name} is already confirmed.")
    details: dict = {}
    if what == "code_change":
        current = _text(from_code, 40) or next((c for l in lines if (c := (book["facts"].get(holding_key(l)) or {}).get("distributor_code"))), "")
        new = _text(to_code, 40)
        if not current:
            raise ConsolidationError("Give the distributor code on the folio now, as the statement shows it.")
        if len(new) < 2:
            raise ConsolidationError("Give the code the folio is to be re-mapped to.")
        if current.lower() == new.lower():
            raise ConsolidationError("The folio already carries that code.")
        details = {"from_code": current, "to_code": new}
    elif what == "demat_transfer":
        target = _text(to_account, 120)
        mode = _text(transfer_kind, 16).lower()
        code = _text(reason_code, 40)
        if len(target) < 3:
            raise ConsolidationError("Name the demat account the securities are to arrive in.")
        if mode not in TRANSFER_KINDS:
            raise ConsolidationError(f"The transfer is one of {', '.join(TRANSFER_KINDS)}.")
        if not code:
            raise ConsolidationError("Give the reason code as it is on the delivery instruction. It is never filled in here.")
        paid = _amount(consideration, "consideration")
        if mode in ("own_account", "gift") and paid:
            raise ConsolidationError("A transfer between a person's own accounts, or a gift, states no consideration.")
        if mode == "sale" and not paid:
            raise ConsolidationError("Give the consideration the delivery instruction states.")
        duty = _amount(stamp_duty_stated, "stamp duty stated")
        details = {"to_account": target, "transfer_kind": mode, "transfer_kind_label": TRANSFER_KINDS[mode], "reason_code": code,
                   "consideration": paid, "stamp_duty_stated": duty}
    elif what == "pms_transition":
        leaving, joining, target = _text(manager_from, 120), _text(manager_to, 120), _text(to_account, 120)
        if len(leaving) < 3 or len(joining) < 3:
            raise ConsolidationError("Name the Portfolio Manager the securities leave and the one they join.")
        if len(target) < 3:
            raise ConsolidationError("Name the client's demat account under the new agreement.")
        details = {"manager_from": leaving, "manager_to": joining, "to_account": target}
    snaps = []
    for line in lines:
        snap = _snapshot(book, line)
        d = book["decisions"][holding_key(line)]
        if what == "exit":
            snap.update({"planned_units": float(d.units) if d.units is not None else None, "reason": d.reason,
                         "product_reason_label": PRODUCT_REASONS.get(d.product_reason or "")})
        snaps.append(snap)
    others = [l.instrument_name for l in book["lines"]
              if what == "code_change" and l not in lines
              and (l.person_id, l.entity_id, l.institution, l.account_label) == (first.person_id, first.entity_id, first.institution, first.account_label)]
    return {"book": book, "household": book["household"], "kind": what, "lines": lines, "snapshots": snaps, "details": details, "on": day,
            "evidence": proof, "note": _text(note, 500) or None, "left_out": others,
            "stamp_duty": _stamp_duty(ConsolidationItem(details=details), details.get("stamp_duty_stated"), today) if what == "demat_transfer" else None}


async def open_item(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_item(session, firm, role, **terms)
    first = p["lines"][0]
    hid = p["household"].id
    # Locked on the household, so two people cannot both open a request over the same holding.
    await session.execute(select(Household.id).where(Household.id == hid).with_for_update())
    p = await prepare_item(session, firm, role, **terms)
    item = ConsolidationItem(firm_id=firm.id, household_id=hid, kind=p["kind"], person_id=first.person_id, entity_id=first.entity_id,
                             institution=first.institution, account_label=first.account_label,
                             holding_keys=[s["holding_key"] for s in p["snapshots"]], lines=p["snapshots"], details=p["details"],
                             opened_by=by, opened_on=p["on"])
    session.add(item)
    await session.flush()
    session.add(ConsolidationStep(firm_id=firm.id, item_id=item.id, seq=1, step="requested", happened_on=p["on"], evidence=p["evidence"],
                                  note=p["note"], recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="consolidation_item_opened", actor=by, content={
        "item_id": str(item.id), "household_id": str(hid), "kind": p["kind"], "lines": [s["name"] for s in p["snapshots"]],
        "details": p["details"], "evidence": p["evidence"], "on": p["on"].isoformat()})
    view = await for_household(session, hid, firm_id=firm.id)
    return {**view, "item_id": str(item.id), "recorded": f"{KINDS[p['kind']]} requested: {first.institution}"}


async def prepare_step(session: AsyncSession, firm: Firm, role, *, item_id, step: str | None, evidence: str | None, on=None,
                       reference: str | None = None, reason: str | None = None, stamp_duty_stated=None) -> dict:
    if role not in TRACKERS:
        raise ConsolidationError("Operations opens and records a transfer, a code change or an exit's steps.")
    if not applies(firm):
        raise ConsolidationError("The consolidation workbench is for an Indian firm.")
    item = await session.get(ConsolidationItem, _uuid(item_id, "request's id"))
    if item is None or item.firm_id != firm.id:
        raise ConsolidationError("That request is not on file here. read_consolidation lists them.")
    household = await session.get(Household, item.household_id)
    book = await _book(session, firm, household, firm_time.today(firm))
    status = _status(book, item)
    what = _text(step, 12).lower()
    if what not in STEPS or what == "requested":
        raise ConsolidationError("The step is consented, submitted, confirmed, rejected or withdrawn.")
    if what not in NEXT[status]:
        allowed = ", ".join(NEXT[status]) or "nothing: it is closed"
        raise ConsolidationError(f"This {KINDS[item.kind].lower()} is {STEPS[status].lower()}; what can follow is {allowed}.")
    last = book["steps"][item.id][-1] if book["steps"].get(item.id) else None
    day = _day(on, "day it happened") if on not in (None, "") else book["today"]
    if day > book["today"]:
        raise ConsolidationError("A step cannot be dated in the future.")
    if last is not None and day < last.happened_on:
        raise ConsolidationError(f"This cannot be before the last step, on {last.happened_on.isoformat()}.")
    proof = _text(evidence, 300)
    if len(proof) < 4:
        raise ConsolidationError("Say which document shows this, and where it is filed.")
    ref = _text(reference, 120) or None
    if what == "confirmed" and not ref:
        raise ConsolidationError("Give the confirmation's reference: the registrar's, the depository's or the statement's.")
    why = _text(reason, 450)
    if what in ("rejected", "withdrawn") and len(why) < 5:
        raise ConsolidationError("Say why it was rejected or withdrawn.")
    if what == "submitted":
        for key in item.holding_keys:
            d = book["decisions"].get(key)
            if d is None or d.decision != KIND_DECISION[item.kind]:
                raise ConsolidationError("The adviser has since classified a holding on this request otherwise. Withdraw it, or have it classified "
                                         "again, before it is submitted.")
    duty = _amount(stamp_duty_stated, "stamp duty stated")
    if duty is not None and item.kind != "demat_transfer":
        raise ConsolidationError("Stamp duty is stated on a demat transfer only.")
    return {"book": book, "household": household, "item": item, "from": status, "step": what, "on": day, "evidence": proof, "reference": ref,
            "note": why or None, "stamp_duty_stated": duty, "view": _item_view(book, item)}


async def record_step(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_step(session, firm, role, **terms)
    # The item is locked and the check made again, so two people cannot both record its next step.
    await session.execute(select(ConsolidationItem).where(ConsolidationItem.id == p["item"].id).with_for_update()
                          .execution_options(populate_existing=True))
    p = await prepare_step(session, firm, role, **terms)
    item = p["item"]
    session.add(ConsolidationStep(firm_id=firm.id, item_id=item.id, seq=len(p["book"]["steps"][item.id]) + 1, step=p["step"], happened_on=p["on"],
                                  evidence=p["evidence"], reference=p["reference"], note=p["note"], stamp_duty_stated=p["stamp_duty_stated"],
                                  recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="consolidation_step_recorded", actor=by, content={
        "item_id": str(item.id), "household_id": str(item.household_id), "kind": item.kind, "step": p["step"], "on": p["on"].isoformat(),
        "evidence": p["evidence"], "reference": p["reference"], "note": p["note"]})
    view = await for_household(session, item.household_id, firm_id=firm.id)
    return {**view, "item_id": str(item.id), "recorded": f"{KINDS[item.kind]}: {_step_label(item.kind, p['step']).lower()}"}
