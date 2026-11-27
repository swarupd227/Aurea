"""India advice under the IA operating model on the demonstration firm (B-399): advice files and one sample audit.

Every record is invented for the demonstration firm and says so. Run from `seed.india_demo` when the firm is built and
from `seed.india_demo_backfill` on a firm built before; it adds nothing where its own records are already on file. A
household the pre-advice gate holds (the Banerjees, whose risk profile is not consented to) is given no recommendation,
which is what the demonstration shows.

  The Sharmas   term life cover, accepted from a meeting note; a will with two witnesses, open.
  D'Souza       a yearly step-up on the retirement SIP, declined from a meeting note.
  Audit         compliance draws two files and records one finding on the first; the second waits for review.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advice_file, advice_practice, firm_time
from app.models.advice_audit import AdviceAudit
from app.models.advice_file import AdviceRecommendation
from app.models.graph import Household
from app.models.identity import User

BY = "seed: india advice (demonstration)"
SOURCE = "Demonstration record, invented for the demonstration firm"
ADVISER_EMAIL = "adviser@astra-india.demo"
SAMPLE = 2

ADVICE = (
    ("The Sharma Family", "protection", "Take pure term life cover on Rajesh Sharma to the computed need",
     "Two children, a home loan and one main income, as on the family's file",
     [("A further endowment policy", "Less cover for the same premium"), ("Wait until the next review", "The need is open today")],
     "accepted", "Meeting note: the family agreed and asked for two quotes", 90),
    ("The Sharma Family", "estate", "Make a will, signed before two witnesses, and tell the executor",
     "No current will for either parent on file; two minor beneficiaries",
     [("Rely on account nominations", "A nominee receives; the will decides who owns")], "open", None, 60),
    ("The D'Souza Household", "retirement", "Step the retirement SIP up by ten per cent a year",
     "A flat monthly saving against a retirement goal fourteen years out, as recorded",
     [("A larger flat amount from today", "Does not fit this year's cash flow"), ("Leave it level", "The goal stays short")],
     "declined", "Meeting note: prefers to decide after the next salary review", 180),
)


async def add(s: AsyncSession, firm) -> int:
    done = (await s.execute(select(AdviceRecommendation.id).where(
        AdviceRecommendation.firm_id == firm.id, AdviceRecommendation.recorded_by == BY).limit(1))).first()
    added = 0
    if not done:
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.email == ADVISER_EMAIL))).scalar_one_or_none()
        today = firm_time.today(firm)
        for name, category, recommendation, facts, alternatives, decision, evidence, due_in in ADVICE:
            home = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == name))).scalar_one_or_none()
            if home is None:
                continue
            try:
                await advice_file.record_recommendation(
                    s, firm.id, recorded_by=BY, role="adviser", by_user=adviser.id if adviser else None, household_id=home.id,
                    category=category, recommendation=recommendation, facts=f"{facts} ({SOURCE})",
                    alternatives=[{"option": o, "why_rejected": w} for o, w in alternatives], decision=decision,
                    evidence_kind="note" if evidence else None, evidence=f"{evidence} ({SOURCE})" if evidence else None,
                    due_on=today + timedelta(days=due_in))
            except advice_file.AdviceError:
                continue            # held before advice, or the household has left: nothing is recorded for them
            added += 1
    if (await s.execute(select(AdviceAudit.id).where(AdviceAudit.firm_id == firm.id).limit(1))).first():
        return added
    try:
        view = await advice_practice.record_draw(s, firm, role="compliance", by=f"Neha Kulkarni ({BY})", size=SAMPLE)
    except advice_practice.AuditError:
        return added                # no advice on file to draw from
    audit = view["audits"][0]
    first = audit["files"][0]
    await advice_practice.record_result(
        s, firm, role="compliance", by=f"Neha Kulkarni ({BY})", audit_id=audit["audit_id"],
        recommendation_id=first["recommendation_id"], outcome="finding",
        findings=[{"item": "suitability", "words": f"The record does not say why this suits the family's cash flow. {SOURCE}."}])
    return added + 2
