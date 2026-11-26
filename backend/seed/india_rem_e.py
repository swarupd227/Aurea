"""India batch E on the demonstration firm: what each signed agreement carried (B-324), a recorded mutual fund cut-off figure beside the assumed
ones (B-330), and counsel's determination for one group entity beside one awaiting counsel (B-331). The advertisement citation (B-332) and a client's
language or WhatsApp request (B-322) are made by people in use and are not seeded.

Every record is invented for the demonstration firm and says so in its evidence. Run from `seed.india_demo` when the firm is built and from
`seed.india_demo_backfill` on a firm built before it; it adds nothing where an agreement's contents are already on file for the firm.

  Agreements    each household the gate reads, whose agreement is on file, recorded as carrying Annexure A and the MITC (invented): without it the gate now
                says "agreement incomplete" for every one of them
  Cut-off       the standard cut-off recorded as 15:00 from an invented citation, so the page shows one figure as recorded and three as assumed
  Family        a second group company that holds an ARN (invented) determined outside the family by counsel (invented), and the first left awaiting counsel
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import agreement_contents, distribution_separation as ds, firm_time, mf_figures, pre_advice
from app.core.security import UserRole
from app.models.identity import User
from app.models.india_rem_e import AgreementContent
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India batch E (demonstration)"
ASSOCIATE = "Demonstration Securities Private Limited (invented)"
SUBSIDIARY = "Demonstration Distribution Private Limited (invented)"


async def add(s: AsyncSession, firm: Firm) -> int:
    if not agreement_contents.applies(firm):
        return 0
    if (await s.execute(select(AgreementContent.id).where(AgreementContent.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    n = 0
    # B-324: what each agreement on file carried.
    for st in await pre_advice.for_firm(s, firm):
        check = next((c for c in st["checks"] if c["key"] == "agreement"), None)
        if check is None or check["detail"].startswith("no signed agreement"):
            continue
        await agreement_contents.record(s, firm, UserRole.OPERATIONS, by=BY, household_id=st["household_id"], annexure_a=True, mitc=True,
                                        document=f"Signed advisory agreement with Annexure A and the MITC, filed ({SOURCE})")
        n += 1
    # B-330: one figure recorded from a circular, the other three left assumed.
    compliance = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.COMPLIANCE).order_by(User.email))).scalars().first()
    await mf_figures.record(s, firm, UserRole.COMPLIANCE, by=BY, actor=compliance, figure_key="in_mf_cutoff_standard", value="15:00",
                            source="Demonstration entry: the firm's reading of SEBI's uniform cut-off circular (invented citation, not verified)",
                            as_of=today - timedelta(days=90), reason="Demonstration: shows one figure recorded beside three assumed")
    n += 1
    # B-331: counsel's determination for one entity; the other awaits counsel.
    doc = f"The group structure chart, filed ({SOURCE})"
    await ds.record_entity(s, firm, UserRole.COMPLIANCE, by=BY, name=ASSOCIATE, relationship="associate", holds_arn=True, arn="ARN-000001 (invented)", document=doc)
    await ds.record_determination(s, firm, UserRole.COMPLIANCE, by=BY, entity=ASSOCIATE, determination="outside", counsel_name="Demonstration Counsel (invented)",
                                  determined_on=today - timedelta(days=20), document=f"Counsel's opinion on the family of entities, filed ({SOURCE})")
    n += 2
    advised = await ds._advised_households(s, firm)
    ordered = sorted(advised, key=lambda h: advised[h])
    if len(ordered) > 1:
        await ds.record_trail(s, firm, UserRole.COMPLIANCE, by=BY, household_id=ordered[1], entity=ASSOCIATE, statement_on=today - timedelta(days=35),
                              amount_inr=9_200, document=f"The associate's trail statement, filed ({SOURCE})")
        n += 1
    await s.flush()
    return n
