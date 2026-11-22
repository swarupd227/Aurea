"""A new product, who it is for, and who it was offered to (docs/03_Architecture/product-distribution.md).

The research team adds a product and reads its target market from the product's own document. A product that is
complex or conflicted is offered only once compliance has signed off its distribution, and the person who recorded
the target market may not sign it off. Nothing is offered until the product is approved on the shelf, has a target
market in force and, where it needs one, that sign-off; and a firm with no market's rules offers nothing.

A pitch is the adviser's record of offering a product to a household and what came of it, with the fit it rested
on. Every record here is added and never edited, and none of it reaches a client: a message to one still goes out
only through `send_client_message`, in a person's name, and a purchase only through the rebalancer or a direct
order, guarded as before.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, licences, markets, shelf
from app.aurea_core.risk_profile import CAPACITIES, PROFILES
from app.models.enums import AssetClass, MandateType, UserRole
from app.models.graph import Household
from app.models.portfolio import Instrument
from app.models.product_distribution import (
    COMPLEXITY, CONFLICTS, DISTRIBUTION, LIQUIDITY, NEGATIVE, OBJECTIVES, PITCH_OPEN, PITCH_STATUSES,
    ProductDistributionSignoff, ProductPitch, ProductTargetMarket,
)
from app.models.tenant import Firm
from app.provenance import ledger

# Who records a product and its target market: the people who keep the shelf.
RECORDERS = (UserRole.RESEARCH_CIO, UserRole.PORTFOLIO_TEAM)
# Who signs off the distribution of a complex or conflicted product.
SIGNERS = (UserRole.COMPLIANCE,)
# Who records what came of a pitch.
PITCHERS = (UserRole.ADVISER, UserRole.PARAPLANNER)
# A household is offered at most this many products at once, so it is not sold to on every visit.
MAX_OPEN_PITCHES = 2
# A product a household declined is not offered again for this long, unless something on file changes.
DECLINED_QUIET_DAYS = 180
# What a person can say became of a pitch, and those that need their reason.
OUTCOMES = ("discussed", "interested", "declined", "not_suitable", "bought")
NEEDS_REASON = ("declined", "not_suitable")


class DistributionError(ValueError):
    """This cannot be recorded, with the reason."""


def _list(value) -> list[str]:
    if value is None or value == "":
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(v).strip().lower() for v in items if str(v).strip()]


def _whole(value, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise DistributionError(f"{name} must be a whole number.")
    if n < 0:
        raise DistributionError(f"{name} cannot be negative.")
    return n


async def instrument(session: AsyncSession, firm_id: uuid.UUID, symbol: str | None) -> Instrument:
    """A product by its symbol, or by its name as a person says it when only one product's name holds those words."""
    said = (symbol or "").strip()
    inst = (await session.execute(select(Instrument).where(
        Instrument.firm_id == firm_id, func.upper(Instrument.symbol) == said.upper()))).scalar_one_or_none()
    if inst is not None:
        return inst
    words = [w for w in said.lower().replace("-", " ").split() if w not in ("the", "fund", "product", "a", "an")]
    if words:
        q = select(Instrument).where(Instrument.firm_id == firm_id)
        for w in words:
            q = q.where(func.lower(Instrument.name).contains(w))
        named = (await session.execute(q.limit(3))).scalars().all()
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            raise DistributionError(f"'{said}' names more than one product: {', '.join(f'{i.symbol} ({i.name})' for i in named)}.")
    raise DistributionError(f"No product with the symbol or name {said}.")


async def target_market(session: AsyncSession, firm_id: uuid.UUID, instrument_id: uuid.UUID) -> ProductTargetMarket | None:
    """The target market in force: the latest version."""
    return (await session.execute(select(ProductTargetMarket).where(
        ProductTargetMarket.firm_id == firm_id, ProductTargetMarket.instrument_id == instrument_id)
        .order_by(ProductTargetMarket.version.desc()).limit(1))).scalars().first()


def needs_signoff(tm: ProductTargetMarket) -> bool:
    return tm.complexity == "complex" or tm.conflict != "none"


async def signoff(session: AsyncSession, tm: ProductTargetMarket) -> ProductDistributionSignoff | None:
    return (await session.execute(select(ProductDistributionSignoff).where(
        ProductDistributionSignoff.target_market_id == tm.id).order_by(ProductDistributionSignoff.created_at.desc()).limit(1))).scalars().first()


def fee_only(firm: Firm) -> bool:
    """A firm licensed in India only as an adviser is paid by its clients, so it offers nothing it is paid to distribute."""
    held = licences.held(firm)
    return bool(held) and all(l.key != "mutual_fund_distributor" for l in held)


async def offerable(session: AsyncSession, firm: Firm, inst: Instrument) -> dict:
    """Whether this product may be put in front of any adviser's clients, and if not, why."""
    tm = await target_market(session, firm.id, inst.id)
    decision = (await shelf.decisions(session, firm.id)).get(inst.id)
    signed = await signoff(session, tm) if tm else None
    why = None
    if markets.for_firm(firm) is None:
        why = "the firm has no market's rules here, so nothing is offered"
    elif decision is None:
        why = "the research team has made no shelf decision on it"
    elif decision.status != "approved":
        why = f"it is {decision.status} on the shelf"
    elif tm is None:
        why = "no target market is on file for it"
    elif needs_signoff(tm) and signed is None:
        why = "compliance has not signed off its distribution"
    elif tm.conflict == "third_party_payment" and fee_only(firm):
        why = "the firm is a fee-only adviser, and offers no product it is paid to distribute"
    return {"offerable": why is None, "why_not": why, "target_market": tm, "decision": decision, "signoff": signed}


def tm_view(tm: ProductTargetMarket | None) -> dict | None:
    if tm is None:
        return None
    return {"id": str(tm.id), "version": tm.version, "objectives": tm.objectives, "risk_profiles": tm.risk_profiles,
            "min_capacity": tm.min_capacity, "min_horizon_years": tm.min_horizon_years,
            "min_investment": float(tm.min_investment) if tm.min_investment is not None else None, "currency": tm.currency,
            "complexity": tm.complexity, "liquidity": tm.liquidity, "lock_years": tm.lock_years, "markets": tm.markets,
            "mandate_types": tm.mandate_types, "negative_market": tm.negative_market, "distribution": tm.distribution,
            "conflict": tm.conflict, "disclosure": tm.disclosure, "document": tm.document, "as_of": tm.as_of.isoformat(),
            "recorded_by": tm.recorded_by, "needs_signoff": needs_signoff(tm)}


# ── Who a product is not for ───────────────────────────────────────────────────────────────────

HORIZON_KEYS = ("time_horizon_years", "investment_horizon", "horizon_years")


def horizon_of(suitability: dict | None) -> int | None:
    """The investment horizon on file, in years, or None."""
    for key in HORIZON_KEYS:
        value = (suitability or {}).get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(str(value).split()[0]))
        except (TypeError, ValueError):
            continue
    return None


def age_on(born: date | None, today: date) -> int | None:
    if born is None:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def negative_findings(tm: ProductTargetMarket, *, profile: str | None, capacity: str | None, horizon: int | None,
                      owner_name: str | None, age: int | None, is_person: bool) -> list[tuple[str, str]]:
    """Each negative-target-market condition as ("gate", why) where a fact on file puts the client in it, or
    ("missing", why) where the fact it needs is not on file. One source for the shelf's bar and the fit check."""
    out: list[tuple[str, str]] = []
    for condition in tm.negative_market or []:
        if condition == "low_capacity":
            if capacity is None:
                out.append(("missing", "no capacity for loss is on file, and the product is not for low capacity"))
            elif capacity == "low":
                out.append(("gate", "it is not for a client with low capacity for loss, which is what is on file"))
        elif condition == "conservative_profile":
            if profile is None:
                out.append(("missing", "no risk profile is on file, and the product is not for a conservative one"))
            elif profile == "conservative":
                out.append(("gate", "it is not for a conservative profile, which is what is on file"))
        elif condition == "aged_over_75" and is_person:
            if age is None:
                out.append(("missing", "no date of birth is on file, and the product is not for someone over 75"))
            elif age > 75:
                out.append(("gate", f"it is not for someone over 75, and {owner_name} is {age}"))
        elif condition == "needs_access_within_lock" and tm.lock_years:
            if horizon is None:
                out.append(("missing", f"no horizon is on file, and the money is locked for {tm.lock_years} years"))
            elif horizon < tm.lock_years:
                out.append(("gate", f"the money is locked for {tm.lock_years} years, past the {horizon}-year horizon on file"))
    return out


async def negative_bars(session: AsyncSession, firm: Firm, mandate) -> dict[uuid.UUID, str]:
    """The products this mandate is in the negative target market of, on facts on file, for `shelf.barred`: so the
    rebalancer and a direct order never buy a product for a client it says it is not for. A fact not on file bars
    nothing here; the fit check names it as one to record."""
    from app.aurea_core.risk_profile import on_file
    from app.models.graph import Person

    rows = (await session.execute(select(ProductTargetMarket).where(ProductTargetMarket.firm_id == firm.id)
                                  .order_by(ProductTargetMarket.version))).scalars().all()
    latest = {r.instrument_id: r for r in rows}
    if not any(r.negative_market for r in latest.values()):
        return {}
    profile, capacity = on_file(mandate.suitability)
    horizon = horizon_of(mandate.suitability)
    owner = await session.get(Person, mandate.person_id) if mandate.person_id else None
    age = age_on(owner.date_of_birth, firm_time.today(firm)) if owner else None
    out: dict[uuid.UUID, str] = {}
    for instrument_id, tm in latest.items():
        gates = [why for result, why in negative_findings(tm, profile=profile, capacity=capacity, horizon=horizon,
                                                          owner_name=owner.full_name if owner else None, age=age,
                                                          is_person=owner is not None) if result == "gate"]
        if gates:
            out[instrument_id] = f"in its negative target market: {gates[0]}"
    return out


# ── Adding a product ───────────────────────────────────────────────────────────────────────────

async def prepare_product(session: AsyncSession, firm_id: uuid.UUID, *, symbol: str | None, name: str | None,
                          asset_class: str | None, currency: str | None = None, market_symbol: str | None = None) -> dict:
    """Everything adding would refuse, refused without writing."""
    firm = await session.get(Firm, firm_id)
    sym, called = (symbol or "").strip().upper(), (name or "").strip()
    if not sym or len(sym) > 32:
        raise DistributionError("Give the product's symbol, up to 32 characters.")
    if len(called) < 3:
        raise DistributionError("Give the product's name.")
    cls = str(asset_class or "").strip().lower()
    if cls not in {c.value for c in AssetClass}:
        raise DistributionError(f"The asset class must be one of {', '.join(c.value for c in AssetClass)}.")
    exists = (await session.execute(select(Instrument.id).where(
        Instrument.firm_id == firm_id, func.upper(Instrument.symbol) == sym))).scalar_one_or_none()
    if exists:
        raise DistributionError(f"{sym} is already a product here. Record its shelf decision or target market instead.")
    ccy = (currency or firm.base_currency or "USD").strip().upper()
    return {"symbol": sym, "name": called, "asset_class": cls, "currency": ccy,
            "market_symbol": (market_symbol or "").strip() or None}


async def add_product(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> Instrument:
    ready = await prepare_product(session, firm_id, **terms)
    inst = Instrument(firm_id=firm_id, symbol=ready["symbol"], name=ready["name"], asset_class=ready["asset_class"],
                      currency=ready["currency"], market_symbol=ready["market_symbol"], values_tags={}, private_attributes={})
    session.add(inst)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="product_added", actor=by, content={
        "symbol": inst.symbol, "name": inst.name, "asset_class": ready["asset_class"], "currency": inst.currency})
    return inst


# ── The target market ──────────────────────────────────────────────────────────────────────────

async def prepare_target_market(session: AsyncSession, firm_id: uuid.UUID, *, symbol: str | None, document: str | None,
                                objectives=None, risk_profiles=None, min_capacity: str | None = None, min_horizon_years=None,
                                min_investment=None, complexity: str | None = None, liquidity: str | None = None, lock_years=None,
                                markets_for=None, mandate_types=None, negative_market=None, distribution: str | None = None,
                                conflict: str | None = None, disclosure: str | None = None, as_of: date | None = None) -> dict:
    """Everything recording would refuse, refused without writing. Every term comes from the named document."""
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    inst = await instrument(session, firm_id, symbol)
    named = (document or "").strip()
    if len(named) < 5:
        raise DistributionError("Name the document the target market is read from: the PDS, KID, prospectus or offer document.")
    aims = _list(objectives)
    if not aims or any(a not in OBJECTIVES for a in aims):
        raise DistributionError(f"Give its objectives, from: {', '.join(OBJECTIVES)}.")
    profiles = _list(risk_profiles)
    if not profiles or any(p not in PROFILES for p in profiles):
        raise DistributionError(f"Give the risk profiles it is for, from: {', '.join(PROFILES)}.")
    capacity = (min_capacity or "").strip().lower() or None
    if capacity is not None and capacity not in CAPACITIES:
        raise DistributionError(f"The minimum capacity for loss must be one of {', '.join(CAPACITIES)}.")
    kind = (complexity or "simple").strip().lower()
    if kind not in COMPLEXITY:
        raise DistributionError(f"Complexity must be one of {', '.join(COMPLEXITY)}.")
    access = (liquidity or "daily").strip().lower()
    if access not in LIQUIDITY:
        raise DistributionError(f"Liquidity must be one of {', '.join(LIQUIDITY)}.")
    lock = _whole(lock_years, "The lock-in")
    if access == "locked" and not lock:
        raise DistributionError("A locked product needs its lock-in in years.")
    where = [m.upper() for m in _list(markets_for)]
    unknown = [m for m in where if m not in markets.MARKETS]
    if unknown:
        raise DistributionError(f"No market here is called {', '.join(unknown)}. Markets are {', '.join(markets.MARKETS)}.")
    types = _list(mandate_types)
    if any(t not in {m.value for m in MandateType} for t in types):
        raise DistributionError(f"Mandate types are {', '.join(m.value for m in MandateType)}.")
    negative = _list(negative_market)
    if any(n not in NEGATIVE for n in negative):
        raise DistributionError(f"Who it is not for is read from what is on file, so it is one of: {', '.join(NEGATIVE)}.")
    sold = (distribution or "advised_only").strip().lower()
    if sold not in DISTRIBUTION:
        raise DistributionError(f"Distribution must be one of {', '.join(DISTRIBUTION)}.")
    paid = (conflict or "none").strip().lower()
    if paid not in CONFLICTS:
        raise DistributionError(f"The conflict must be one of {', '.join(CONFLICTS)}.")
    said = (disclosure or "").strip() or None
    if paid != "none" and (said is None or len(said) < 20):
        raise DistributionError("A conflicted product needs the disclosure every message about it must carry, in full.")
    minimum = None
    if min_investment not in (None, ""):
        try:
            minimum = round(float(min_investment), 2)
        except (TypeError, ValueError):
            raise DistributionError("The minimum investment must be an amount.")
        if minimum < 0:
            raise DistributionError("The minimum investment cannot be negative.")
    day = as_of or today
    if day > today:
        raise DistributionError("The document cannot be dated in the future.")
    was = await target_market(session, firm_id, inst.id)
    return {"instrument": inst, "version": (was.version + 1) if was else 1, "was": was, "objectives": aims, "risk_profiles": profiles,
            "min_capacity": capacity, "min_horizon_years": _whole(min_horizon_years, "The minimum horizon"),
            "min_investment": minimum, "currency": (firm.base_currency or "USD").upper() if minimum is not None else None,
            "complexity": kind, "liquidity": access, "lock_years": lock, "markets": where, "mandate_types": types,
            "negative_market": negative, "distribution": sold, "conflict": paid, "disclosure": said, "document": named, "as_of": day}


async def record_target_market(session: AsyncSession, firm_id: uuid.UUID, *, by: str, role: str, **terms) -> ProductTargetMarket:
    ready = await prepare_target_market(session, firm_id, **terms)
    row = ProductTargetMarket(firm_id=firm_id, instrument_id=ready["instrument"].id, version=ready["version"],
                              recorded_by=by, recorded_by_role=str(role),
                              **{k: ready[k] for k in ("objectives", "risk_profiles", "min_capacity", "min_horizon_years",
                                                       "min_investment", "currency", "complexity", "liquidity", "lock_years",
                                                       "markets", "mandate_types", "negative_market", "distribution",
                                                       "conflict", "disclosure", "document", "as_of")})
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="target_market_recorded", actor=by, content={
        "symbol": ready["instrument"].symbol, "version": row.version, "document": row.document, **tm_view(row)})
    return row


# ── Compliance's sign-off ──────────────────────────────────────────────────────────────────────

async def prepare_signoff(session: AsyncSession, firm_id: uuid.UUID, *, symbol: str | None, reason: str | None, by: str) -> dict:
    inst = await instrument(session, firm_id, symbol)
    tm = await target_market(session, firm_id, inst.id)
    if tm is None:
        raise DistributionError(f"{inst.symbol} has no target market on file to sign off.")
    if not needs_signoff(tm):
        raise DistributionError(f"{inst.symbol} is simple and not conflicted, so its distribution needs no sign-off.")
    if await signoff(session, tm) is not None:
        raise DistributionError(f"Version {tm.version} of {inst.symbol}'s target market is already signed off.")
    if (tm.recorded_by or "").strip().lower() == (by or "").strip().lower():
        raise DistributionError("The person who recorded the target market may not sign off its distribution.")
    why = (reason or "").strip()
    if len(why) < 10:
        raise DistributionError("Give compliance's reason for letting it be offered.")
    return {"instrument": inst, "target_market": tm, "reason": why}


async def record_signoff(session: AsyncSession, firm_id: uuid.UUID, *, by: str, role: str, **terms) -> ProductDistributionSignoff:
    ready = await prepare_signoff(session, firm_id, by=by, **terms)
    firm = await session.get(Firm, firm_id)
    row = ProductDistributionSignoff(firm_id=firm_id, instrument_id=ready["instrument"].id, target_market_id=ready["target_market"].id,
                                     reason=ready["reason"], decided_on=firm_time.today(firm), decided_by=by, decided_by_role=str(role))
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="distribution_signed_off", actor=by, content={
        "symbol": ready["instrument"].symbol, "version": ready["target_market"].version, "reason": row.reason})
    return row


# ── Pitches ────────────────────────────────────────────────────────────────────────────────────

async def latest_pitches(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None,
                         instrument_id: uuid.UUID | None = None) -> dict[tuple[uuid.UUID, uuid.UUID], ProductPitch]:
    """The pitch in force for each (household, product): the latest step recorded."""
    q = select(ProductPitch).where(ProductPitch.firm_id == firm_id)
    if household_id:
        q = q.where(ProductPitch.household_id == household_id)
    if instrument_id:
        q = q.where(ProductPitch.instrument_id == instrument_id)
    out: dict = {}
    for p in (await session.execute(q.order_by(ProductPitch.created_at))).scalars().all():
        out[(p.household_id, p.instrument_id)] = p
    return out


def is_open(p: ProductPitch | None) -> bool:
    return p is not None and p.status in PITCH_OPEN


def quiet(p: ProductPitch | None, today: date) -> bool:
    """A decline still standing: the household is not offered the product again yet."""
    return p is not None and p.status == "declined" and p.as_of + timedelta(days=DECLINED_QUIET_DAYS) > today


def pitch(firm_id: uuid.UUID, *, instrument_id, household_id, status: str, by: str, as_of: date, fit: dict | None = None,
          mandate_id=None, adviser_id=None, target_market_id=None, recommendation_id=None, task_id=None,
          reason: str | None = None) -> ProductPitch:
    if status not in PITCH_STATUSES:
        raise DistributionError(f"A pitch is one of {', '.join(PITCH_STATUSES)}.")
    return ProductPitch(firm_id=firm_id, instrument_id=instrument_id, household_id=household_id, mandate_id=mandate_id,
                        adviser_id=adviser_id, target_market_id=target_market_id, recommendation_id=recommendation_id,
                        task_id=task_id, status=status, reason=reason, fit=fit or {}, as_of=as_of, recorded_by=by)


async def prepare_outcome(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID, symbol: str | None,
                          outcome: str | None, reason: str | None) -> dict:
    """What came of offering a product to a household, refused without writing."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise DistributionError(f"Household {household_id} not found or access denied")
    inst = await instrument(session, firm_id, symbol)
    said = (outcome or "").strip().lower()
    if said not in OUTCOMES:
        raise DistributionError(f"What came of it is one of: {', '.join(OUTCOMES)}.")
    was = (await latest_pitches(session, firm_id, household_id=household.id, instrument_id=inst.id)).get((household.id, inst.id))
    if not is_open(was):
        raise DistributionError(f"{inst.symbol} is not being offered to {household.name}, so there is no outcome to record.")
    why = (reason or "").strip() or None
    if said in NEEDS_REASON and (why is None or len(why) < 5):
        raise DistributionError("Say why, in the client's or your own words: it is kept as the record.")
    return {"household": household, "instrument": inst, "outcome": said, "reason": why, "was": was}


async def record_outcome(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> ProductPitch:
    ready = await prepare_outcome(session, firm_id, **terms)
    firm = await session.get(Firm, firm_id)
    was = ready["was"]
    row = pitch(firm_id, instrument_id=was.instrument_id, household_id=was.household_id, status=ready["outcome"], by=by,
                as_of=firm_time.today(firm), fit=was.fit, mandate_id=was.mandate_id, adviser_id=was.adviser_id,
                target_market_id=was.target_market_id, recommendation_id=was.recommendation_id, task_id=was.task_id,
                reason=ready["reason"])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="pitch_outcome_recorded", actor=by, content={
        "symbol": ready["instrument"].symbol, "household": ready["household"].name, "outcome": row.status, "reason": row.reason})
    return row


async def pitch_views(session: AsyncSession, firm_id: uuid.UUID, rows: list[ProductPitch]) -> list[dict]:
    from app.models.identity import User

    names = {h.id: h.name for h in (await session.execute(select(Household).where(
        Household.id.in_({p.household_id for p in rows})))).scalars().all()} if rows else {}
    symbols = {i.id: (i.symbol, i.name) for i in (await session.execute(select(Instrument).where(
        Instrument.id.in_({p.instrument_id for p in rows})))).scalars().all()} if rows else {}
    who = {p.adviser_id for p in rows if p.adviser_id}
    advisers = {u.id: u.full_name for u in (await session.execute(select(User).where(User.id.in_(who)))).scalars().all()} if who else {}
    return [{"id": str(p.id), "household_id": str(p.household_id), "household": names.get(p.household_id),
             "symbol": symbols.get(p.instrument_id, ("", ""))[0], "product": symbols.get(p.instrument_id, ("", ""))[1],
             "status": p.status, "reason": p.reason, "as_of": p.as_of.isoformat(),
             "adviser": advisers.get(p.adviser_id), "open": is_open(p)} for p in rows]


# ── A message that names a product ─────────────────────────────────────────────────────────────

async def with_disclosures(session: AsyncSession, firm_id: uuid.UUID, body: str) -> str:
    """A message to a client that names a product with a target market carries the product's document, and a
    conflicted product's disclosure word for word, after the adviser's own words. The Confirm card shows the result,
    so the words confirmed are the words sent."""
    import re

    rows = (await session.execute(select(ProductTargetMarket, Instrument).join(
        Instrument, Instrument.id == ProductTargetMarket.instrument_id).where(ProductTargetMarket.firm_id == firm_id)
        .order_by(ProductTargetMarket.version))).all()
    latest = {inst.id: (tm, inst) for tm, inst in rows}
    lines: list[str] = []
    for tm, inst in latest.values():
        by_symbol = len(inst.symbol) >= 3 and re.search(rf"\b{re.escape(inst.symbol)}\b", body)
        by_name = len(inst.name or "") >= 5 and inst.name.lower() in body.lower()
        if not (by_symbol or by_name):
            continue
        document = f"Product document for {inst.name}: {tm.document}."
        if document not in body:
            lines.append(document)
        if tm.conflict != "none" and tm.disclosure and tm.disclosure not in body:
            lines.append(tm.disclosure)
    return body if not lines else body.rstrip() + "\n\n" + "\n".join(lines)


# ── The distribution report ────────────────────────────────────────────────────────────────────

async def report(session: AsyncSession, firm: Firm, inst: Instrument) -> dict:
    """What happened to a product once it could be offered: who was offered it, what came of it, who bought it,
    and who bought it while outside its target market as assessed today. No complaints register is kept here."""
    from app.aurea_core import product_fit
    from app.models.enums import AgentKey, OrderSide
    from app.models.governance import Recommendation
    from app.models.trading import Order

    state = await offerable(session, firm, inst)
    pitches = list((await latest_pitches(session, firm.id, instrument_id=inst.id)).values())
    views = await pitch_views(session, firm.id, pitches)
    by_status = {s: sum(1 for p in pitches if p.status == s) for s in PITCH_STATUSES}
    by_adviser: dict[str, int] = {}
    for v in views:
        by_adviser[v["adviser"] or "unassigned"] = by_adviser.get(v["adviser"] or "unassigned", 0) + 1
    lists = (await session.execute(select(Recommendation.status, func.count()).where(
        Recommendation.firm_id == firm.id, Recommendation.agent_key == AgentKey.PRODUCT_MATCH.value,
        Recommendation.subject_type == "instrument", Recommendation.subject_id == inst.id).group_by(Recommendation.status))).all()
    buys = (await session.execute(select(Order).where(Order.firm_id == firm.id, Order.instrument_id == inst.id,
                                                      Order.side == OrderSide.BUY, Order.filled_quantity > 0))).scalars().all()
    bought_households = await product_fit.households_of_mandates(session, firm.id, {o.mandate_id for o in buys if o.mandate_id})
    outside = []
    if state["target_market"] is not None and bought_households:
        assessed = await product_fit.for_book(session, firm, inst, household_ids=set(bought_households.values()), ignore_pitches=True)
        outside = [r for r in assessed["not_for"]]
    return {"symbol": inst.symbol, "product": inst.name, "offerable": state["offerable"], "why_not": state["why_not"],
            "target_market": tm_view(state["target_market"]),
            "lists": {str(s): n for s, n in lists}, "offered": len(pitches), "by_status": by_status, "by_adviser": by_adviser,
            "bought": len(set(bought_households.values())),
            "bought_outside_target_market": [{"household": r["household"], "why": [l["said"] for l in r["lines"] if l["result"] == "gate"]}
                                             for r in outside],
            "complaints": "No complaints register is kept here.",
            "pitches": views, "as_of": firm_time.today(firm).isoformat()}
