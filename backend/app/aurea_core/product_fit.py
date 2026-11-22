"""Whether a product fits a client, read only from what is on file (docs/03_Architecture/product-distribution.md).

Each client is read against the product's target market and the rules that already decide what may be bought for
them, and comes out as one of three:

  fits             every gate passes; signals on file say why it may matter now, and order the list
  needs a fact     a gate needs a fact nobody has recorded; that fact is the adviser's next step, never a no
  not for them     a gate fails, with the rule that failed and its evidence

The gates reuse the modules that decide them, so this can never read a client differently from the rebalancer or a
direct order: the shelf (`shelf.barred`, with the licence's plan rule), the IPS and the mandate's exclusions, the
capacity ceiling (`ips.book_after`), departures, and the target market. A household under transaction-monitoring
investigation is left off with no reason anyone outside compliance can read, because monitoring is never told.

The order of those who fit is points per signal, the same for every product. It never reads a fee, the firm's
revenue or anyone's pay. Nothing here writes.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advisers, firm_time, ips, markets, product_distribution as pd, shelf
from app.aurea_core.risk_profile import CAPACITIES, CAPACITY_EQUITY_MAX, on_file
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument
from app.models.tenant import Firm

# What each signal adds to the order. The same for every product, and never a fee.
POINTS = {"idle_cash": 3, "goal_gap": 3, "dearer_class": 3, "concentration": 2, "review_due": 1}


async def households_of_mandates(session: AsyncSession, firm_id: uuid.UUID, mandate_ids: set) -> dict:
    """Each mandate's household, through its person or entity."""
    if not mandate_ids:
        return {}
    people, entities = await advisers.members(session, firm_id)
    rows = (await session.execute(select(Mandate).where(Mandate.id.in_(mandate_ids)))).scalars().all()
    return {m.id: (people.get(m.person_id) if m.person_id else entities.get(m.entity_id)) for m in rows
            if (people.get(m.person_id) if m.person_id else entities.get(m.entity_id))}


async def _context(session: AsyncSession, firm: Firm, household_ids: set | None) -> dict:
    """Everything the fit check reads, for the whole book in a few queries."""
    from app.aurea_core import reviews
    from app.models.aml_monitoring import AmlInvestigation
    from app.models.identity import User

    q = select(Household).where(Household.firm_id == firm.id)
    if household_ids is not None:
        q = q.where(Household.id.in_(household_ids))
    households = (await session.execute(q.order_by(Household.name))).scalars().all()
    people, entities = await advisers.members(session, firm.id)
    persons = {p.id: p for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    mandates: dict[uuid.UUID, list[Mandate]] = {}
    for m in (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True)))).scalars().all():
        home = people.get(m.person_id) if m.person_id else entities.get(m.entity_id)
        if home:
            mandates.setdefault(home, []).append(m)
    accounts = (await session.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()
    held: dict[uuid.UUID, set] = {}
    cash: dict[uuid.UUID, float] = {}
    account_mandate = {a.id: a.mandate_id for a in accounts}
    for a in accounts:
        if a.mandate_id:
            cash[a.mandate_id] = cash.get(a.mandate_id, 0.0) + float(a.cash_balance or 0)
    for h in (await session.execute(select(Holding).where(Holding.account_id.in_(list(account_mandate)), Holding.quantity > 0))).scalars().all():
        mid = account_mandate.get(h.account_id)
        if mid:
            held.setdefault(mid, set()).add(h.instrument_id)
    adviser_of = await advisers.of_households(session, firm.id)
    names = {u.id: u.full_name for u in (await session.execute(select(User).where(User.id.in_(set(adviser_of.values()))))).scalars().all()} if adviser_of else {}
    watched = {i.household_id for i in (await session.execute(select(AmlInvestigation).where(
        AmlInvestigation.firm_id == firm.id, AmlInvestigation.status != "closed"))).scalars().all()}
    due = {r["household_id"]: r for r in (await reviews.for_firm(session, firm.id))["households"]
           if r["status"] in ("overdue", "due_soon")}
    return {"households": households, "persons": persons, "mandates": mandates, "held": held, "cash": cash,
            "adviser_of": adviser_of, "adviser_names": names, "watched": watched, "review_due": due,
            "pitches": await pd.latest_pitches(session, firm.id), "decisions": await shelf.decisions(session, firm.id),
            "today": firm_time.today(firm)}


async def _mandate(session: AsyncSession, firm: Firm, inst: Instrument, tm, mandate: Mandate, household: Household, ctx: dict) -> dict:
    """One mandate against the product: each gate passed, failed or waiting on a fact."""
    lines: list[dict] = []

    def line(criterion: str, result: str, said: str) -> None:
        lines.append({"criterion": criterion, "result": result, "said": said})

    suit = mandate.suitability or {}
    profile, capacity = on_file(suit)
    horizon = pd.horizon_of(suit)
    today = ctx["today"]

    if mandate.exit_hold:
        line("leaving", "gate", f"{mandate.name} is held while a {mandate.exit_hold} is open")
    if tm.mandate_types and str(mandate.mandate_type) not in tm.mandate_types:
        line("mandate_type", "gate", f"{mandate.name} is {mandate.mandate_type}, and the product is for {', '.join(tm.mandate_types)}")
    if tm.distribution == "advised_only" and str(mandate.mandate_type) == "execution_only":
        line("mandate_type", "gate", "it is sold only with advice, and this mandate is execution only")
    market = markets.of_household(firm, household.values)
    if tm.markets and (market is None or market.code not in tm.markets):
        line("market", "gate", f"the household is taxed in {market.code if market else 'a market with no rules here'}, and the product is for {', '.join(tm.markets)}")
    barred = await shelf.barred(session, firm, mandate)
    # The shelf also bars a negative target market; that is said once, as its own line, below.
    if inst.id in barred and not barred[inst.id].startswith("in its negative target market"):
        line("shelf", "gate", f"{inst.symbol} is {barred[inst.id]}")
    policy = await ips.in_force(session, mandate.id)
    if policy and ips.restricted_by(inst, list(policy.restricted or [])):
        line("ips", "gate", f"{inst.symbol} is restricted by IPS version {policy.version}")
    elif ips.restricted_by(inst, ips.exclusions_of(mandate)):
        line("exclusion", "gate", f"{inst.symbol} is excluded by {mandate.name}")

    if profile is None:
        line("risk_profile", "missing", "no risk profile is on file")
    elif profile not in tm.risk_profiles:
        line("risk_profile", "gate", f"the profile on file is {profile}, and the product is for {', '.join(tm.risk_profiles)}")
    else:
        line("risk_profile", "pass", f"the profile on file is {profile}")
    if tm.min_capacity:
        if capacity is None:
            line("capacity", "missing", "no capacity for loss is on file")
        elif capacity in CAPACITIES and CAPACITIES.index(capacity) < CAPACITIES.index(tm.min_capacity):
            line("capacity", "gate", f"capacity for loss on file is {capacity}, under the {tm.min_capacity} it needs")
        else:
            line("capacity", "pass", f"capacity for loss on file is {capacity}")
    needed = max(tm.min_horizon_years or 0, tm.lock_years or 0)
    if needed:
        if horizon is None:
            line("horizon", "missing", "no investment horizon is on file")
        elif horizon < needed:
            line("horizon", "gate", f"the horizon on file is {horizon} years, under the {needed} it needs")
        else:
            line("horizon", "pass", f"the horizon on file is {horizon} years")

    owner = ctx["persons"].get(mandate.person_id) if mandate.person_id else None
    for result, said in pd.negative_findings(tm, profile=profile, capacity=capacity, horizon=horizon,
                                             owner_name=owner.full_name if owner else None,
                                             age=pd.age_on(owner.date_of_birth, today) if owner else None,
                                             is_person=owner is not None):
        line("negative_market", result, said)

    if str(inst.asset_class) == "equity" and capacity in CAPACITY_EQUITY_MAX and tm.min_investment:
        book = await ips.book_after(session, mandate, firm=firm, orders=[{
            "symbol": inst.symbol, "side": "buy", "est_value": float(tm.min_investment), "instrument": inst}])
        ceiling = CAPACITY_EQUITY_MAX[capacity]
        if book["total"] > 0 and book["weights_after"].get("equity", 0.0) > ceiling:
            line("capacity_ceiling", "gate", f"the minimum would take equity to {book['weights_after']['equity']:.0%}, over the {ceiling:.0%} ceiling for {capacity} capacity")
    if inst.id in ctx["held"].get(mandate.id, set()):
        line("holds", "gate", f"{mandate.name} already holds {inst.symbol}: a top-up, not a new offer")

    results = {l["result"] for l in lines}
    verdict = "not_for" if "gate" in results else "needs_fact" if "missing" in results else "fits"
    return {"mandate_id": str(mandate.id), "mandate": mandate.name, "verdict": verdict, "lines": lines}


_RANK = {"fits": 0, "needs_fact": 1, "not_for": 2}


async def _signals(session: AsyncSession, firm: Firm, inst: Instrument, tm, household: Household, mandate_id: str, ctx: dict) -> list[dict]:
    """Why a fitting product may matter to this household now, each from a figure on file."""
    from app.agents._signals import book_signals
    from app.aurea_core.graph import household_brain

    out: list[dict] = []
    brain = await household_brain(session, household.id, firm_id=firm.id)
    decided = ctx["decisions"]
    mine = decided.get(inst.id)
    for sig in (book_signals(brain, ctx["today"]) if brain else []):
        if sig["kind"] == "idle_cash" and (tm.min_investment is None or sig["payload"]["cash"] >= float(tm.min_investment)):
            out.append({"kind": "idle_cash", "said": sig["title"]})
        elif sig["kind"] == "goal_gap" and set(tm.objectives or []) & {"growth", "income"}:
            out.append({"kind": "goal_gap", "said": sig["title"]})
        elif sig["kind"] == "concentration" and mine is not None and mine.diversified:
            out.append({"kind": "concentration", "said": sig["title"]})
    if mine is not None and mine.family and mine.expense_ratio is not None:
        held = ctx["held"].get(uuid.UUID(mandate_id), set())
        dearer = [d for i, d in decided.items() if i in held and i != inst.id and d.family == mine.family
                  and d.expense_ratio is not None and float(d.expense_ratio) > float(mine.expense_ratio)]
        if dearer:
            out.append({"kind": "dearer_class", "said": f"holds a dearer class of {mine.family} ({float(dearer[0].expense_ratio):.2%} against {float(mine.expense_ratio):.2%})"})
    review = ctx["review_due"].get(str(household.id))
    if review:
        out.append({"kind": "review_due", "said": f"a review is {review['status'].replace('_', ' ')}" + (f", next due {review['next_due']}" if review.get("next_due") else "")})
    seen: set[str] = set()
    signals = []
    for s in out:
        if s["kind"] not in seen:
            seen.add(s["kind"])
            signals.append({**s, "points": POINTS[s["kind"]]})
    return signals


async def for_book(session: AsyncSession, firm: Firm, inst: Instrument, *, adviser_id: uuid.UUID | None = None,
                   household_ids: set | None = None, ignore_pitches: bool = False) -> dict:
    """Every client (or one adviser's, or the named households) against one product."""
    state = await pd.offerable(session, firm, inst)
    tm = state["target_market"]
    base = {"symbol": inst.symbol, "product": inst.name, "offerable": state["offerable"], "why_not": state["why_not"],
            "target_market": pd.tm_view(tm), "fits": [], "needs_fact": [], "not_for": [], "withheld": 0}
    if tm is None:
        return {**base, "counts": {"fits": 0, "needs_fact": 0, "not_for": 0, "withheld": 0}, "as_of": firm_time.today(firm).isoformat()}
    ctx = await _context(session, firm, household_ids)
    for household in ctx["households"]:
        owner = ctx["adviser_of"].get(household.id)
        if adviser_id is not None and owner != adviser_id:
            continue
        who = {"household_id": str(household.id), "household": household.name,
               "adviser_id": str(owner) if owner else None, "adviser": ctx["adviser_names"].get(owner)}
        if household.left_on:
            base["not_for"].append({**who, "verdict": "not_for", "mandate_id": None, "mandate": None, "signals": [], "points": 0,
                                    "lines": [{"criterion": "leaving", "result": "gate", "said": f"left on {household.left_on.isoformat()}"}]})
            continue
        if household.id in ctx["watched"]:
            base["withheld"] += 1
            continue
        household_lines: list[dict] = []
        if not ignore_pitches:
            was = ctx["pitches"].get((household.id, inst.id))
            if pd.is_open(was):
                household_lines.append({"criterion": "pitch", "result": "gate", "said": f"already being offered: {was.status.replace('_', ' ')} since {was.as_of.isoformat()}"})
            elif pd.quiet(was, ctx["today"]):
                household_lines.append({"criterion": "pitch", "result": "gate", "said": f"declined on {was.as_of.isoformat()}; not offered again for {pd.DECLINED_QUIET_DAYS} days"})
            others = sum(1 for (h, i), p in ctx["pitches"].items() if h == household.id and i != inst.id and pd.is_open(p))
            if others >= pd.MAX_OPEN_PITCHES:
                household_lines.append({"criterion": "pitch", "result": "gate", "said": f"{others} other products are already being offered"})
        mandates = ctx["mandates"].get(household.id, [])
        if not mandates:
            best = {"mandate_id": None, "mandate": None, "verdict": "needs_fact",
                    "lines": [{"criterion": "mandate", "result": "missing", "said": "no active mandate is on file"}]}
        else:
            assessed = [await _mandate(session, firm, inst, tm, m, household, ctx) for m in mandates]
            best = min(assessed, key=lambda a: (_RANK[a["verdict"]], sum(1 for l in a["lines"] if l["result"] == "gate")))
        lines = household_lines + best["lines"]
        verdict = "not_for" if household_lines else best["verdict"]
        row = {**who, "mandate_id": best["mandate_id"], "mandate": best["mandate"], "verdict": verdict, "lines": lines,
               "signals": [], "points": 0}
        if verdict == "fits":
            row["signals"] = await _signals(session, firm, inst, tm, household, best["mandate_id"], ctx)
            row["points"] = sum(s["points"] for s in row["signals"])
        base[verdict].append(row)
    base["fits"].sort(key=lambda r: (-r["points"], r["household"]))
    return {**base, "counts": {"fits": len(base["fits"]), "needs_fact": len(base["needs_fact"]),
                               "not_for": len(base["not_for"]), "withheld": base["withheld"]},
            "as_of": ctx["today"].isoformat()}


# What the order guard already reads for itself, the negative target market among it (through the shelf's bar);
# the target market adds the rest, which is named and does not stop the order.
_GUARDED = {"leaving", "shelf", "ips", "exclusion", "capacity_ceiling", "holds", "negative_market"}


async def order_check(session: AsyncSession, firm: Firm, mandate: Mandate, inst: Instrument) -> tuple[str | None, str | None]:
    """A buy outside the product's target market, named. A buy into its negative target market is already refused by
    the shelf's bar, which the order guard reads; so the first value is None unless that bar is bypassed. A product
    with no target market on file is not read here, and a fact not on file stops nothing."""
    tm = await pd.target_market(session, firm.id, inst.id)
    if tm is None:
        return None, None
    held = {h.instrument_id for h in (await session.execute(select(Holding).join(Account, Account.id == Holding.account_id).where(
        Account.mandate_id == mandate.id, Holding.quantity > 0))).scalars().all()}
    owner = await session.get(Person, mandate.person_id) if mandate.person_id else None
    household_id = owner.household_id if owner else None
    if household_id is None and mandate.entity_id:
        from app.models.graph import LegalEntity

        entity = await session.get(LegalEntity, mandate.entity_id)
        household_id = entity.household_id if entity else None
    household = await session.get(Household, household_id) if household_id else Household(firm_id=firm.id, name="", values={})
    ctx = {"persons": {owner.id: owner} if owner else {}, "held": {mandate.id: held}, "today": firm_time.today(firm)}
    row = await _mandate(session, firm, inst, tm, mandate, household, ctx)
    gates = [l for l in row["lines"] if l["result"] == "gate" and l["criterion"] not in _GUARDED]
    return None, (f"{inst.symbol}: {gates[0]['said']}" if gates else None)


def snapshot(row: dict, tm) -> dict:
    """What a pitch keeps of the fit it rested on."""
    return {"verdict": row["verdict"], "lines": row["lines"], "signals": row["signals"], "points": row["points"],
            "mandate": row["mandate"], "target_market_version": tm.version if tm else None}
