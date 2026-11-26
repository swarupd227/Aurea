"""The nominations check on Indian accounts (B-385): every demat account or fund folio a person holds, and whether it has a
nomination or a signed opt-out.

An account is covered by the nominees on it, or by what a person recorded from the form for that holder and an account of that
name (`india_estate.record_nomination`): nominees, or an opt-out with the day its declaration was signed. An account an HUF, a
trust or a company holds is listed as not checked, never counted as covered. With nothing checked the answer is "nothing
checked", never that all is covered. A nomination is not inheritance (`nominations.py`). Reads only.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, nominations
from app.models.graph import Account, AccountBeneficiary, LegalEntity, Mandate, Person
from app.models.india_family import AssetNomination
from app.models.tenant import Firm

NONE_ON_FILE = "no nomination or signed opt-out on file"


async def audit(session: AsyncSession, firm: Firm, account_ids: list[uuid.UUID] | None) -> dict:
    """`account_ids` limits it to one household's accounts; None reads the firm's."""
    query = select(Account).where(Account.firm_id == firm.id, Account.status == "active").order_by(Account.name)
    if account_ids is not None:
        query = query.where(Account.id.in_(account_ids or [uuid.uuid4()]))
    accounts = (await session.execute(query)).scalars().all()
    mandates = {m.id: m for m in (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()}
    people = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    entities = {e.id: e for e in (await session.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id))).scalars().all()}
    designated: dict = {}
    if accounts:
        for b in (await session.execute(select(AccountBeneficiary).where(
                AccountBeneficiary.account_id.in_([a.id for a in accounts])))).scalars().all():
            designated.setdefault(b.account_id, []).append(b)
    recorded: dict = {}      # the latest record for a holder and an account's name
    for r in (await session.execute(select(AssetNomination).where(
            AssetNomination.firm_id == firm.id, AssetNomination.asset_kind.in_(nominations.OPT_OUT_KINDS))
            .order_by(AssetNomination.created_at, AssetNomination.id))).scalars().all():
        recorded[(r.person_id, r.label.strip().lower())] = r
    cap = nominations.cap_of_kind("demat", firm_time.today(firm))

    gaps, not_checked, nominated, opted_out, checked = [], [], 0, 0, 0
    for a in accounts:
        mandate = mandates.get(a.mandate_id)
        person_id = mandate.person_id if mandate else None
        if person_id is None:
            held = entities.get(mandate.entity_id) if mandate else None
            not_checked.append({"account_id": str(a.id), "account_name": a.name, "holder": held.name if held else None,
                                "reason": f"held by an entity ({str(held.entity_type).upper() if str(held.entity_type) == 'huf' else held.entity_type})"
                                if held else "no holder on file"})
            continue
        checked += 1
        rows = designated.get(a.id, [])
        record = recorded.get((person_id, a.name.strip().lower()))
        issue = None
        if rows:
            primary = round(sum(float(b.percentage) for b in rows if b.designation_class == "primary"), 2)
            if primary != 100.0:
                issue = f"primary designations sum to {primary:g}%, not 100%"
            elif cap is not None and len(rows) > cap:
                issue = f"{len(rows)} nominees, and at most {cap} are taken"
            else:
                nominated += 1
        elif record is not None and record.status == "opted_out":
            if record.declaration_on is None:
                issue = "opted out, with no signed declaration dated on file"
            else:
                opted_out += 1
        elif record is not None and record.nominees:
            shares = [n.get("share") for n in record.nominees]
            if all(s is not None for s in shares) and abs(sum(float(s) for s in shares) - 100) > 0.01:
                issue = f"the shares add to {sum(float(s) for s in shares):g}%, not 100%"
            elif cap is not None and len(record.nominees) > cap:
                issue = f"{len(record.nominees)} nominees, and at most {cap} are taken"
            else:
                nominated += 1
        else:
            issue = NONE_ON_FILE
        if issue:
            gaps.append({"account_id": str(a.id), "account_name": a.name, "holder": people.get(person_id),
                         "registration_type": a.registration_type or "not recorded", "issue": issue, "beneficiary_count": len(rows)})
    return {"market": "IN", "accounts_total": len(accounts), "accounts_checked": checked, "accounts_covered": nominated + opted_out,
            "nominated": nominated, "opted_out": opted_out, "gaps": gaps, "not_checked": not_checked}
