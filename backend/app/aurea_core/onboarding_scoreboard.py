"""The onboarding scoreboard (L200-1 §8, §9).

Each figure is counted from the record, and one with nothing to count says so rather than showing zero: days from
the case starting to its first purchase filling; where accounts stall, by the step they sit at, and how many were
rejected; how often screening raises a hit; net promoter score from what clients answered on the survey, recorded
by a person as they gave it; transfer rejects by code; accounts opened per operations person; and who does what at
each step (responsible, accountable, consulted, informed), written to match what the platform lets each role do.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.aurea_core.onboarding_metrics import _summary
from app.models.onboarding import OnboardingCase
from app.models.onboarding_feedback import OnboardingFeedback
from app.models.tenant import Firm
from app.provenance import ledger

STALLED_DAYS = 30
OPENED_WINDOW_DAYS = 90

# Who does what. R does it, A answers for it (and is the one the platform lets decide), C is asked, I is told.
RACI = [
    {"activity": "Intake and the client's data", "R": "Adviser", "A": "Adviser", "C": "Paraplanner", "I": "Operations"},
    {"activity": "Documents, detail and signatures", "R": "Adviser", "A": "Operations", "C": "Compliance", "I": "Client"},
    {"activity": "Screening hits", "R": "Operations", "A": "Compliance", "C": "Adviser", "I": "Adviser"},
    {"activity": "Risk rating", "R": "Operations", "A": "Compliance (high risk)", "C": "Adviser", "I": "Adviser"},
    {"activity": "Enhanced due diligence", "R": "Operations", "A": "Compliance", "C": "Adviser", "I": "Adviser"},
    {"activity": "Opening at the custodian", "R": "Operations", "A": "Operations", "C": "Custodian", "I": "Adviser"},
    {"activity": "Transfers and funding", "R": "Operations", "A": "Operations", "C": "Delivering firm", "I": "Adviser"},
    {"activity": "First investment", "R": "Portfolio team", "A": "Adviser or portfolio team", "C": "Compliance", "I": "Client"},
]


class ScoreboardError(ValueError):
    """This cannot be recorded, with the reason."""


def _aware(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


def nps(scores: list[int]) -> dict:
    if not scores:
        return {"nps": None, "responses": 0, "says": "Not measured: no survey answers recorded."}
    promoters = sum(1 for s in scores if s >= 9) / len(scores)
    detractors = sum(1 for s in scores if s <= 6) / len(scores)
    return {"nps": round(100 * (promoters - detractors)), "responses": len(scores), "promoters_pct": round(100 * promoters, 1),
            "detractors_pct": round(100 * detractors, 1)}


def build(*, overview: dict, cases: list, fills: dict, parties: list, rejects_by_code: list, feedback: list, operations_staff: int,
          today: date, stalled_days: int = STALLED_DAYS) -> dict:
    to_invest = [d for d in ((_aware(fills[c.id]) - _aware(c.created_at)).total_seconds() / 86400 for c in cases if fills.get(c.id) and c.created_at) if d >= 0]
    live = overview["accounts"]
    stalled = [r for r in live if r["state"] != "invested" and (r["age_days"] or 0) > stalled_days]
    by_step = [{"state": s, "stalled": sum(1 for r in stalled if r["state"] == s), "reached": sum(1 for r in live if r["step"] >= i + 1)}
               for i, s in enumerate(overview["states"])]
    screened = [p for p in parties if (p.screening_status or "not_screened") != "not_screened"]
    with_hits = [p for p in screened if p.screening_hits]
    since = datetime.combine(today - timedelta(days=OPENED_WINDOW_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    opened = sum(1 for c in cases if c.activated_at and _aware(c.activated_at) >= since)
    return {
        "days_to_invest": _summary(to_invest),
        "clocks": overview["clocks"],
        "abandonment": {"stalled_days": stalled_days, "by_step": by_step, "stalled": len(stalled),
                        "rejected": sum(1 for c in cases if str(c.status) == "rejected"), "cases": len(cases)},
        "screening": {"parties_screened": len(screened), "with_hits": len(with_hits),
                      "alert_rate_pct": round(100 * len(with_hits) / len(screened), 1) if screened else None},
        "nps": nps([f.score for f in feedback]),
        "transfer_rejects": rejects_by_code,
        "capacity": {"opened_last_90_days": opened, "operations_staff": operations_staff,
                     "accounts_per_operations_person": round(opened / operations_staff, 1) if operations_staff else None},
        "raci": RACI,
        "as_of": today.isoformat(),
    }


async def prepare_feedback(session: AsyncSession, firm_id: uuid.UUID, *, case_id: uuid.UUID, score, comment: str | None, given_on: date | None,
                           source: str | None) -> dict:
    case = await session.get(OnboardingCase, case_id)
    if case is None or case.firm_id != firm_id:
        raise ScoreboardError("Case not found.")
    try:
        value = int(score)
    except (TypeError, ValueError):
        raise ScoreboardError("The score is the client's answer, 0 to 10.")
    if not 0 <= value <= 10:
        raise ScoreboardError("The score is the client's answer, 0 to 10.")
    today = firm_time.today(await session.get(Firm, firm_id))
    day = given_on or today
    if day > today:
        raise ScoreboardError("An answer cannot be dated ahead.")
    if len((source or "").strip()) < 5:
        raise ScoreboardError("Say where the answer is: the survey export, the client's email.")
    return {"case": case, "score": value, "comment": (comment or "").strip() or None, "on": day, "source": source.strip()}


async def record_feedback(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> OnboardingFeedback:
    r = await prepare_feedback(session, firm_id, **terms)
    row = OnboardingFeedback(firm_id=firm_id, case_id=r["case"].id, score=r["score"], comment=r["comment"], given_on=r["on"], source=r["source"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="onboarding_feedback_recorded", actor=by,
                              content={"case_id": str(r["case"].id), "score": r["score"]})
    return row
