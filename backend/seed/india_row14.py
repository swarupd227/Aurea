"""India row 14 on the demonstration firm: a governance register with each state on it, and counsel's review of three law figures.

Every record is invented for the demonstration firm and says so in its evidence; the reviewer is invented. Dates are days before the
firm's today, so the register does not age. Run from `seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on a
firm built before row 14; it adds nothing where a policy is already on the register.

  grievance             approved eight months ago: current
  cyber                 approved fourteen months ago: its review is overdue
  business continuity   approved last year, reviewed on a date the document sets: current
  conflict of interest  approved eleven months ago: due within a month
  outsourcing           not recorded: the register says so and does not say the firm has none

  counsel               the fixed-fee cap and the age of majority confirmed; the complaint limit not confirmed. Every other figure reads
                        "not reviewed".
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import counsel_review, firm_time, governance
from app.core.security import UserRole
from app.models.india_governance import GovernancePolicy
from app.models.tenant import Firm
from seed.india_row6 import COMPLIANCE_OFFICER

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = COMPLIANCE_OFFICER

POLICIES = [
    ("grievance", "2.1", "Neha Kulkarni, Compliance Officer", 240, "the Board", None),
    ("cyber", "1.4", "The CISO (not yet named: see registration details)", 425, "the Board's IT committee", None),
    ("business_continuity", "3.0", "Ananya Rao, Principal Officer", 300, "the Board", 365 + 60),
    ("conflict_of_interest", "1.2", "Neha Kulkarni, Compliance Officer", 340, "the Board", None),
]
REVIEWS = [
    ("in_ia_fee_fixed_cap", "Meridian Legal LLP (invented)", 50, "confirmed", None),
    ("in_majority_age", "Meridian Legal LLP (invented)", 50, "confirmed", None),
    ("in_complaint_resolve_days", "Meridian Legal LLP (invented)", 50, "not_confirmed",
     "The circular supplied did not state the 21 days in the clause cited; counsel asked for the circular's own text."),
]


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(GovernancePolicy.id).where(GovernancePolicy.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    n = 0
    for kind, version, owner, ago, by_whom, review_in in POLICIES:
        approved = today - timedelta(days=ago)
        await governance.record_policy(s, firm, UserRole.COMPLIANCE, by=BY, kind=kind, version=version, owner=owner, approved_on=approved,
                                       approved_by=by_whom, next_review_on=(approved + timedelta(days=review_in)) if review_in else None,
                                       document=f"The approved {governance.POLICIES[kind][0].lower()} policy, v{version} ({SOURCE})")
        n += 1
    for key, who, ago, outcome, note in REVIEWS:
        await counsel_review.record(s, firm, UserRole.COMPLIANCE, by=BY, figure_key=key, reviewer=who, reviewed_on=today - timedelta(days=ago),
                                    outcome=outcome, note=note, document=f"Counsel's written note on {key} ({SOURCE})")
        n += 1
    return n
