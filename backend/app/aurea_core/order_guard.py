"""What a direct order answers to before it is placed.

A proposal is measured by its agent and again at approval. An adviser's direct order from the conversation
has no proposal, so it answers here, before its Confirm card and again when it is confirmed: what the
account can do, whether the client is leaving, what the mandate excludes, the client's IPS, and the household's tax window. The rules stay
in their own modules, so a direct order and the rebalancer cannot come to read them differently.

A refusal covers the whole set, as it does at approval: a set is read as a whole, and a sale may be funding
a buy. An Indian rebuy after a loss is a flag, not a refusal, as it is on a proposal.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import exits, firm_time, harvest_guard, ips, wash_sale
from app.aurea_core import orders as order_engine
from app.conduit.execution import lot_method_for_firm
from app.models.graph import Account, Household, Mandate
from app.models.portfolio import Holding, InvestmentPolicy, TaxLot
from app.models.tenant import Firm


async def _estimate_sales(session: AsyncSession, firm: Firm, account: Account, sells: list[dict]) -> list[str]:
    """Each sale's gain, from the lots settlement would relieve, stamped on the order. A sale settlement
    would refuse is refused here: by then the venue has filled it, and that is a break, not a rejection."""
    refused, method = [], lot_method_for_firm(firm)
    for o in sells:
        holding = (await session.execute(select(Holding).where(
            Holding.account_id == account.id, Holding.instrument_id == uuid.UUID(str(o["instrument_id"]))))).scalar_one_or_none()
        quantity = Decimal(str(o["quantity"]))
        if holding is None or Decimal(str(holding.quantity)) <= 0:
            refused.append(f"{account.name} does not hold {o['symbol']}.")
            continue
        if quantity > Decimal(str(holding.quantity)):
            refused.append(f"{account.name} holds {float(holding.quantity):g} {o['symbol']}, so it cannot sell {float(quantity):g}.")
            continue
        lots = (await session.execute(select(TaxLot).where(TaxLot.holding_id == holding.id))).scalars().all()
        try:
            _, cost = order_engine._relieve(lots, quantity, method)
        except order_engine.SettlementError:
            refused.append(f"The tax lots on {o['symbol']} do not cover this sale, so it could not settle.")
            continue
        o["est_realised_gain"] = round(o["est_value"] - float(cost), 2)
    return refused


def _cash_short(account: Account, filling: list[dict]) -> list[str]:
    """In the order given, because that is the order they fill in and a later sale cannot pay for an earlier buy."""
    cash = float(account.cash_balance or 0)
    for o in filling:
        cash += o["est_value"] if o["side"] == "sell" else -o["est_value"]
        if cash < 0:
            return [f"{account.name} does not hold the cash for {o['symbol']}: it would be short by {-cash:,.2f}. "
                    "List a sale that funds it first."]
    return []


def _said(found: dict, policy: InvestmentPolicy) -> str:
    v = f"IPS version {policy.version}"
    if found["kind"] == "restricted":
        return f"{found['symbol']} is restricted by {v}."
    if found["kind"] == "position":
        return f"{found['symbol']} would be {found['weight']:.1%} of this mandate, over the {found['limit']:.1%} limit in {v}."
    if found["kind"] == "cash":
        return f"Cash would be {found['weight']:.1%} of this mandate, under the {found['limit']:.1%} floor in {v}."
    return (f"{found['asset_class'].replace('_', ' ').capitalize()} would be {found['weight']:.1%} of this mandate, "
            f"outside the {found['low']:.1%} to {found['high']:.1%} band in {v}.")


def _wash_said(clash: dict) -> str:
    if clash["side"] == "buy":
        return (f"Buying {clash['symbol']} would be a wash sale: it was sold at a loss in {clash['account']} on "
                f"{clash['on']}. It can be bought from {clash['clear_on']}.")
    return (f"Selling {clash['symbol']} at a loss would be a wash sale: it was bought in {clash['account']} on "
            f"{clash['on']}. The loss can be taken from {clash['clear_on']}.")


async def review(session: AsyncSession, *, firm: Firm, mandate: Mandate, account: Account, orders: list[dict],
                 instruments: dict, unpriced: list[str]) -> dict:
    """Everything standing against a set of orders: {"refused": sentences, "flags", "policy"}. Stamps each
    sale's estimated gain on its order and writes nothing else. An order with no real price will not fill,
    so it moves nothing and is measured against nothing."""
    held = exits.order_refusal(mandate, orders)
    if held:
        return {"refused": [held], "flags": [], "policy": None}
    symbols = [o["symbol"] for o in orders]
    twice = sorted({s for s in symbols if symbols.count(s) > 1})
    if twice:
        return {"refused": [f"{', '.join(twice)} is in this set twice. Give each instrument one order."], "flags": [], "policy": None}

    filling = [o for o in orders if o["symbol"] not in unpriced]
    refused = await _estimate_sales(session, firm, account, [o for o in filling if o["side"] == "sell"])
    refused += _cash_short(account, filling)

    policy, breaches = await ips.orders_against(session, mandate, firm=firm, orders=[
        {**o, "instrument": instruments[o["symbol"]]} for o in filling])
    in_ips = list(policy.restricted or []) if policy else []
    excluded = ips.exclusions_of(mandate)
    refused += [f"{o['symbol']} is excluded by this mandate." for o in filling if o["side"] == "buy"
                and ips.restricted_by(instruments[o["symbol"]], excluded)
                and not ips.restricted_by(instruments[o["symbol"]], in_ips)]
    refused += [_said(found, policy) for found in breaches]

    today = firm_time.today(firm)
    household_id = await wash_sale.household_of(session, mandate)
    household = await session.get(Household, household_id) if household_id else None
    flags: list[dict] = []
    if harvest_guard.applies(firm, household.values if household else None):
        rules = await harvest_guard.guard(session, firm, mandate, today=today)
        refused += [c["say"] for c in harvest_guard.conflicts(filling, rules)]
        flags = harvest_guard.flags(filling, rules)
    refused += [_wash_said(c) for c in wash_sale.conflicts(filling, await wash_sale.guard(session, firm, mandate, today=today))]
    return {"refused": refused, "flags": flags, "policy": policy}
