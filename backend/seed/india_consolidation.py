"""The consolidation workbench on the demonstration firm (B-389): the onboarding module's worked case, on invented data.

  The Sharmas   a couple with fourteen mutual fund folios across five fund houses from a legacy distributor: eleven classified
                keep and re-map, with a distributor code change submitted for each (five confirmed, one rejected, five waiting);
                a sector fund classified exit and scheduled against Rajesh's gain budget; two folios still to classify. Two ETF
                holdings in a legacy demat classified transfer, one off-market transfer submitted and one consented.
  The Kapoors   two shares the family trust holds in another Portfolio Manager's demat moving in kind, consented by the client.

Every record is invented for the demonstration firm and says so in its evidence. Three schemes are the firm's own funds by
their AMFI ISINs, held in invented folios and units; the other two fund houses and their schemes do not exist. Some costs are
left unrecorded on purpose, so those holdings read "not assessed". Run from `seed.india_demo` when the firm is built and from
`seed.india_demo_backfill` on a firm built before it. It adds nothing where the demonstration's classifications are on file;
on a later run it only moves a planned exit day that has come due, and that nobody has acted on, ahead again.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import consolidation, firm_time, held_away
from app.core.security import UserRole
from app.models.consolidation import ConsolidationDecision, ConsolidationItem
from app.models.graph import Household, LegalEntity, Person
from app.models.held_away import HeldAwayHolding
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India consolidation (demonstration)"
SHARMA, KAPOOR = "The Sharma Family", "The Kapoor Family Trust"
TRUST = "Kapoor Family Trust"
FROM_CODE, TO_CODE = "ARN-000111 (invented)", "INA-DEMO-0001 (invented)"
EXIT_IN = 24            # days from the firm's today to the planned exit
EXIT_MOVE_WITHIN = 7    # a planned day this close, or past, is moved ahead again on a later run

HDFC, UTI, PPFAS = "HDFC Mutual Fund", "UTI Mutual Fund", "PPFAS Mutual Fund"
SAH, COR = "Sahyadri Mutual Fund (invented)", "Coromandel Mutual Fund (invented)"
CORP = ("HDFC Corporate Bond Fund - Regular Plan - Growth", "HDFC-CORPBOND-REGULAR", "INF179K01DC2")
NIFTY = ("UTI Nifty 50 Index Fund - Regular Plan - Growth", "UTI-NIFTY50-REGULAR", "INF789F01JN2")
FLEXI_R = ("Parag Parikh Flexi Cap Fund - Regular Plan - Growth", "PPFAS-FLEXI-REGULAR", "INF879O01019")
FLEXI_D = ("Parag Parikh Flexi Cap Fund - Direct Plan - Growth", "PPFAS-FLEXI-DIRECT", "INF879O01027")
SECTOR = "Sahyadri Banking and Financial Services Fund - Regular - Growth (invented)"

# (person, fund house, folio, (name, symbol, isin), units, value per unit, tax class, lots as (units, cost, days ago), decision)
FOLIOS = [
    ("Rajesh Sharma", HDFC, "Folio 1000101/11", CORP, 18000, 34.10, None, [(18000, 28.40, 1990)], "keep_remap"),
    ("Meera Sharma", HDFC, "Folio 1000102/27", CORP, 12000, 34.10, None, [(12000, 31.20, 1385)], "keep_remap"),
    ("Rajesh Sharma", UTI, "Folio 2000201/08", NIFTY, 4200, 162.60, None, [(2500, 98.00, 2580), (1700, 151.00, 190)], "keep_remap"),
    ("Meera Sharma", UTI, "Folio 2000202/54", NIFTY, 2100, 162.60, None, [(2100, 120.50, 1710)], "keep_remap"),
    ("Rajesh Sharma", PPFAS, "Folio 3000301/62", FLEXI_R, 9500, 81.70, None, [(9500, 46.00, 2145)], "keep_remap"),
    ("Meera Sharma", PPFAS, "Folio 3000302/19", FLEXI_R, 6000, 81.70, None, [], "keep_remap"),
    ("Meera Sharma", PPFAS, "Folio 3000303/76", FLEXI_D, 3000, 89.70, None, [(3000, 62.00, 1240)], "keep_remap"),
    ("Rajesh Sharma", SAH, "Folio 4000401/33", (SECTOR, None, None), 12000, 50.00, "equity_fund", [(8000, 31.00, 1100), (4000, 44.00, 200)], "exit"),
    ("Meera Sharma", SAH, "Folio 4000402/90", ("Sahyadri Large Cap Fund - Regular - Growth (invented)", None, None), 5000, 72.00, "equity_fund",
     [(5000, 55.00, 1530)], "keep_remap"),
    ("Rajesh Sharma", SAH, "Folio 4000403/45", ("Sahyadri Short Duration Fund - Regular - Growth (invented)", None, None), 15000, 24.00, None, [], None),
    ("Rajesh Sharma", COR, "Folio 5000501/04", ("Coromandel Midcap Fund - Regular - Growth (invented)", None, None), 4000, 140.00, "equity_fund",
     [(4000, 88.00, 1650)], "keep_remap"),
    ("Meera Sharma", COR, "Folio 5000502/81", ("Coromandel Midcap Fund - Regular - Growth (invented)", None, None), 2500, 140.00, "equity_fund",
     [(2500, 118.00, 640)], "keep_remap"),
    ("Rajesh Sharma", COR, "Folio 5000503/58", ("Coromandel Flexi Cap Fund - Regular - Growth (invented)", None, None), 3000, 95.00, "equity_fund",
     [(3000, 81.00, 410)], "keep_remap"),
    ("Meera Sharma", COR, "Folio 5000504/22", ("Coromandel Balanced Advantage Fund - Regular - Growth (invented)", None, None), 8000, 30.00, None, [], None),
]
LEGACY_DEMAT, LEGACY_LABEL = "Legacy distributor demat (invented)", "Demat DEMO-DP-0001"
# (symbol, name, units, value per unit, lot (cost, days ago), how far its transfer has got)
ETFS = [("NIFTYBEES.NS", "Nippon India Nifty 50 ETF", 1500, 262.00, (205.00, 1320), ("consented", "submitted")),
        ("GOLDBEES.NS", "Nippon India Gold ETF", 2000, 116.00, (62.00, 1575), ("consented",))]
OLD_PMS, PMS_LABEL = "Demonstration Capital PMS (invented)", "PMS demat DEMO-PMS-0001"
PMS_LINES = [("TCS.NS", "Tata Consultancy Services", 120, 2500.00, (2100.00, 820)), ("ICICIBANK.NS", "ICICI Bank", 250, 1400.00, None)]


def _evidence(what: str) -> str:
    return f"{what} ({SOURCE})"


async def _redate(s: AsyncSession, firm: Firm) -> int:
    """Move the demonstration's planned exit ahead again when it has come due and nothing has been recorded against it."""
    today = firm_time.today(firm)
    tracked = {k for i in (await s.execute(select(ConsolidationItem).where(
        ConsolidationItem.firm_id == firm.id, ConsolidationItem.kind == "exit"))).scalars().all() for k in i.holding_keys}
    latest: dict[str, ConsolidationDecision] = {}
    for d in (await s.execute(select(ConsolidationDecision).where(ConsolidationDecision.firm_id == firm.id)
                              .order_by(ConsolidationDecision.seq))).scalars().all():
        latest[d.holding_key] = d
    moved = 0
    for key, d in latest.items():
        if d.decided_by == BY and d.decision == "exit" and key not in tracked and d.planned_on <= today + timedelta(days=EXIT_MOVE_WITHIN):
            d.planned_on = today + timedelta(days=EXIT_IN)
            moved += 1
    await s.flush()
    return moved


async def add(s: AsyncSession, firm: Firm) -> int:
    if not consolidation.applies(firm):
        return 0
    if (await s.execute(select(ConsolidationDecision.id).where(ConsolidationDecision.firm_id == firm.id,
                                                               ConsolidationDecision.decided_by == BY).limit(1))).first():
        return await _redate(s, firm)
    households = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    sharma, kapoor = households.get(SHARMA), households.get(KAPOOR)
    if sharma is None:
        return 0
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    if "Rajesh Sharma" not in people or "Meera Sharma" not in people:
        return 0
    today = firm_time.today(firm)
    stated = today.replace(day=1) - timedelta(days=1)
    ago = lambda n: today - timedelta(days=n)    # noqa: E731
    adviser, ops = UserRole.ADVISER, UserRole.OPERATIONS
    added = 0

    async def line(household, owner, institution, label, name, symbol, isin, units, price, source) -> HeldAwayHolding | None:
        """The statement line, recorded once; where it is already on file it is read as it stands."""
        there = (await s.execute(select(HeldAwayHolding).where(
            HeldAwayHolding.household_id == household.id, HeldAwayHolding.status == "current",
            (HeldAwayHolding.entity_id if isinstance(owner, LegalEntity) else HeldAwayHolding.person_id) == owner.id,
            HeldAwayHolding.institution == institution, HeldAwayHolding.instrument_name == name))).scalars().first()
        if there is not None:
            return there if there.account_label == label else None
        out = await held_away.record_holding(
            s, household.id, firm_id=firm.id, by=BY, institution=institution, instrument_name=name, value=round(units * price, 2),
            value_on=stated, source_document=_evidence(source), account_label=label, symbol=symbol, isin=isin, quantity=units,
            **({"entity_id": owner.id} if isinstance(owner, LegalEntity) else {"person_id": owner.id}))
        return await s.get(HeldAwayHolding, uuid.UUID(out["holding_id"]))

    # The fourteen folios, what their statements show about cost, and the adviser's classification of each.
    folios = []
    for who, house, folio, (name, symbol, isin), units, price, klass, lots, decision in FOLIOS:
        row = await line(sharma, people[who], house, folio, name, symbol, isin, units, price,
                         f"Consolidated account statement, {stated.isoformat()}, from the legacy distributor's file")
        if row is None:
            continue
        added += 1
        await consolidation.record_facts(
            s, firm, ops, by=BY, line_id=row.id, form="folio", distributor_code=FROM_CODE, tax_class=klass,
            lots=[{"units": u, "cost_per_unit": c, "acquired_on": ago(d).isoformat()} for u, c, d in lots],
            exit_load_rate=0.01 if decision == "exit" else None, exit_load_days=365 if decision == "exit" else None,
            document=_evidence(f"Capital gains statement for {folio}" + (" and the scheme document" if decision == "exit" else "")))
        folios.append((row, decision))
    etfs = []
    for symbol, name, units, price, (cost, days), progress in ETFS:
        row = await line(sharma, people["Rajesh Sharma"], LEGACY_DEMAT, LEGACY_LABEL, name, symbol, None, units, price,
                         f"Depository statement, {stated.isoformat()}")
        if row is None:
            continue
        added += 1
        await consolidation.record_facts(s, firm, ops, by=BY, line_id=row.id, form="demat",
                                         lots=[{"units": units, "cost_per_unit": cost, "acquired_on": ago(days).isoformat()}],
                                         document=_evidence("Contract notes for the legacy demat"))
        etfs.append((row, progress))

    remap = [row.id for row, d in folios if d == "keep_remap"]
    if remap:
        await consolidation.record_classification(s, firm, adviser, by=BY, line_ids=remap, decision="keep_remap",
                                                  reason=_evidence("The scheme still fits; only who services the folio changes"))
    if etfs:
        await consolidation.record_classification(s, firm, adviser, by=BY, line_ids=[row.id for row, _ in etfs], decision="transfer",
                                                  reason=_evidence("Held for the long term; moves in kind to the new demat"))
    for row, d in folios:
        if d == "exit":
            await consolidation.record_classification(
                s, firm, adviser, by=BY, line_ids=[row.id], decision="exit", product_reason="concentration",
                reason=_evidence("A sector fund adds banking exposure the family already holds directly in its demat"),
                planned_on=today + timedelta(days=EXIT_IN))

    # Eleven code changes submitted: five confirmed, one rejected, five waiting at the registrar.
    for n, (row, d) in enumerate((f for f in folios if f[1] == "keep_remap")):
        out = await consolidation.open_item(s, firm, ops, by=BY, kind="code_change", line_ids=[row.id], to_code=TO_CODE, on=ago(21),
                                            evidence=_evidence(f"Code change request form for {row.account_label}"))
        item = out["item_id"]
        await consolidation.record_step(s, firm, ops, by=BY, item_id=item, step="consented", on=ago(20),
                                        evidence=_evidence("The holder's signed consent, filed in the client folder"))
        await consolidation.record_step(s, firm, ops, by=BY, item_id=item, step="submitted", on=ago(18),
                                        evidence=_evidence("Registrar's acknowledgement of the request"))
        if n < 5:
            await consolidation.record_step(s, firm, ops, by=BY, item_id=item, step="confirmed", on=ago(8 - n),
                                            reference=f"RTA-DEMO-{n + 1:04d}", evidence=_evidence("Registrar's confirmation letter"))
        elif n == 5:
            await consolidation.record_step(s, firm, ops, by=BY, item_id=item, step="rejected", on=ago(6),
                                            reason="The signature on the form did not match the registrar's record",
                                            evidence=_evidence("Registrar's rejection letter"))

    # The two ETFs, off-market from the legacy demat to Rajesh's demat with the firm.
    for row, progress in etfs:
        out = await consolidation.open_item(
            s, firm, ops, by=BY, kind="demat_transfer", line_ids=[row.id], on=ago(9), to_account="Rajesh Sharma demat",
            transfer_kind="own_account", reason_code="DEMO-OWN", evidence=_evidence("Delivery instruction slip, filed in the client folder"))
        for k, step in enumerate(progress):
            await consolidation.record_step(s, firm, ops, by=BY, item_id=out["item_id"], step=step, on=ago(8 - 2 * k),
                                            evidence=_evidence("The holder's authorisation" if step == "consented" else "Depository participant's acknowledgement"))

    # The Kapoor trust: stock in another manager's demat moving in kind.
    trust = (await s.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id, LegalEntity.name == TRUST))).scalar_one_or_none()
    if kapoor is not None and trust is not None:
        moving = []
        for symbol, name, units, price, lot in PMS_LINES:
            row = await line(kapoor, trust, OLD_PMS, PMS_LABEL, name, symbol, None, units, price, f"PMS holding statement, {stated.isoformat()}")
            if row is None:
                continue
            added += 1
            moving.append(row)
            if lot:
                await consolidation.record_facts(s, firm, ops, by=BY, line_id=row.id, form="demat",
                                                 lots=[{"units": units, "cost_per_unit": lot[0], "acquired_on": ago(lot[1]).isoformat()}],
                                                 document=_evidence("The outgoing manager's cost statement"))
        if moving:
            await consolidation.record_classification(s, firm, adviser, by=BY, line_ids=[r.id for r in moving], decision="transfer",
                                                      reason=_evidence("The shares suit the new strategy and move in kind"))
            out = await consolidation.open_item(
                s, firm, ops, by=BY, kind="pms_transition", line_ids=[r.id for r in moving], on=ago(12), manager_from=OLD_PMS,
                manager_to=firm.name, to_account=f"{TRUST} demat", evidence=_evidence("In-kind transfer letter to the outgoing manager"))
            await consolidation.record_step(s, firm, ops, by=BY, item_id=out["item_id"], step="consented", on=ago(10),
                                            evidence=_evidence("The trustees' signed consent to the in-kind transfer"))
    return added
