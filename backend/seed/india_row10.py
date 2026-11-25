"""India row 10 on the demonstration firm: a complaints register with each state on it.

Every record is invented for the demonstration firm and says so in its evidence; the complainants are invented and the SCORES
numbers are marked so. Dates are days before the firm's today, so the register does not age. Run from `seed.india_demo` when
the firm is built and from `seed.india_demo_backfill` on a firm built before row 10; it adds nothing where a complaint is
already on the register.

  open            a fee query, nine days in, acknowledged
  at risk         a complaint about advice, two days from its limit; compliance has a task
  overdue         a SCORES complaint with its Action Taken Report filed and no resolution, past 21 days
  resolved        one inside the limit (not upheld) and one after it (partly upheld)
  withdrawn       one the complainant withdrew
  unacknowledged  a complaint about someone impersonating the firm, newly received
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import complaints, firm_time
from app.core.security import UserRole
from app.models.complaints import Complaint
from app.models.graph import Household
from app.models.tenant import Firm
from seed.india_row6 import COMPLIANCE_OFFICER

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = COMPLIANCE_OFFICER

# (household or None, complainant, source, category, days ago received, summary, scores number, steps (kind, days after receipt, outcome, note))
CASES = [
    ("The Sharma Family", "Meera Sharma", "email", "fees", 9,
     "My quarterly advisory fee looks higher than the agreement says; please explain how it was worked out.", None,
     [("acknowledged", 1, None, None)]),
    ("The Banerjee Household", "Subir Banerjee", "phone", "advice", 19,
     "I was advised to move into a corporate bond fund and I was not told the risks. I want this looked at.", None,
     [("acknowledged", 2, None, None)]),
    ("The D'Souza Household", "Elaine D'Souza", "scores", "service_delay", 30,
     "My request to update my nominee has been pending for six weeks with no reply.", "SIM-SCORES-2026-000123 (simulated)",
     [("acknowledged", 1, None, None), ("atr_filed", 5, None, "SIM-ATR-000123 (simulated)")]),
    ("The Iyer Family", "Karthik Iyer", "letter", "mis_selling", 70,
     "I say the strategy was described to me as low risk and it fell in the year.", None,
     [("acknowledged", 2, None, None),
      ("resolved", 15, "not_upheld", "The signed agreement and the risk profile show the strategy as high risk; the explanation was sent to the client.")]),
    ("The Kapoor Family Trust", "Nandini Kapoor", "email", "data_privacy", 45,
     "A statement meant for the trust was emailed to an old address.", None,
     [("acknowledged", 1, None, None),
      ("resolved", 24, "partly_upheld", "The old address was removed and the trustees were told; the mailing error is being reviewed.")]),
    (None, "A client's relative", "phone", "other", 52, "I asked about my father's account and no one called back.", None,
     [("acknowledged", 3, None, None),
      ("withdrawn", 6, None, "The complainant said by phone that the account manager had called and the matter was closed.")]),
    (None, "A member of the public", "website", "impersonation", 3,
     "Someone using the firm's name is offering guaranteed returns on a messaging app.", None, []),
]


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(Complaint.id).where(Complaint.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    n = 0
    for household, who, source, category, days, summary, scores, steps in CASES:
        if household and household not in homes:
            continue
        received = today - timedelta(days=days)
        out = await complaints.record_complaint(
            s, firm, UserRole.COMPLIANCE, by=BY, complainant=who, source=source, category=category, summary=summary, received_on=received,
            household_id=homes[household].id if household else None, scores_reference=scores,
            document=f"The complainant's {source} message, filed with the register ({SOURCE})")
        ref = out["recorded"]
        n += 1
        for kind, after, outcome, note in steps:
            await complaints.record_event(s, firm, UserRole.COMPLIANCE, by=BY, complaint=ref, kind=kind, happened_on=received + timedelta(days=after),
                                          outcome=outcome, note=note, reference=note if kind == "atr_filed" else None)
            n += 1
    return n
