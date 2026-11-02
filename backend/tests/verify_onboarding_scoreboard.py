"""Prove the scoreboard counts from the record and says "not measured" where there is nothing to count: days to
invest, stalls by step, the screening alert rate, NPS from survey answers recorded as given, transfer rejects by
code, accounts per operations person, and a RACI that names the roles the platform lets decide.

Runs against a throwaway SQLite database.

    python -m tests.verify_onboarding_scoreboard
"""
from __future__ import annotations

import asyncio
import sys
import types
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import onboarding_scoreboard as sb
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.tenant import Firm

PASS, FAIL = [], []
TODAY = date(2026, 11, 3)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, sb.ScoreboardError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure() -> None:
    print("\n=== Counting ===")
    check("no answers is not measured, not zero", sb.nps([])["nps"], None)
    check("two promoters, a passive and a detractor make 25", sb.nps([10, 9, 8, 3])["nps"], 25)
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    cases = [types.SimpleNamespace(id=1, created_at=t0, status="approved", activated_at=t0 + timedelta(days=10)),
             types.SimpleNamespace(id=2, created_at=t0, status="approved", activated_at=t0 + timedelta(days=20)),
             types.SimpleNamespace(id=3, created_at=t0, status="rejected", activated_at=None)]
    fills = {1: t0 + timedelta(days=30), 2: t0 + timedelta(days=50)}
    overview = {"states": ["draft", "signed", "invested"], "clocks": {"paperwork": {}, "money": {}, "invested": {}},
                "accounts": [{"state": "invested", "step": 3, "age_days": 1}, {"state": "signed", "step": 2, "age_days": 45},
                             {"state": "draft", "step": 1, "age_days": 5}]}
    parties = [types.SimpleNamespace(screening_status="clear", screening_hits=[]), types.SimpleNamespace(screening_status="review", screening_hits=[{"x": 1}]),
               types.SimpleNamespace(screening_status="not_screened", screening_hits=[])]
    out = sb.build(overview=overview, cases=cases, fills=fills, parties=parties, rejects_by_code=[{"code": "title_mismatch", "count": 2}], feedback=[],
                   operations_staff=2, today=TODAY)
    check("days to invest: 30 and 50, median 30 by nearest rank as the metrics count it", (out["days_to_invest"]["n"], out["days_to_invest"]["p50"]), (2, 30.0))
    check("one account stalled at signed", ([s["stalled"] for s in out["abandonment"]["by_step"]], out["abandonment"]["rejected"]), ([0, 1, 0], 1))
    check("everyone reached draft, two got to signed", [s["reached"] for s in out["abandonment"]["by_step"]], [3, 2, 1])
    check("one hit in two screened is 50%", out["screening"]["alert_rate_pct"], 50.0)
    check("two opened in 90 days by two ops people is one each", out["capacity"]["accounts_per_operations_person"], 1.0)
    none = sb.build(overview=overview, cases=cases, fills=fills, parties=[], rejects_by_code=[], feedback=[], operations_staff=0, today=TODAY)
    check("with nobody screened and no ops staff, those say nothing", (none["screening"]["alert_rate_pct"], none["capacity"]["accounts_per_operations_person"]), (None, None))
    raci = {r["activity"]: r for r in out["raci"]}
    check("the RACI's accountable roles are the ones the platform lets decide",
          (raci["Screening hits"]["A"], raci["First investment"]["A"]), ("Compliance", "Adviser or portfolio team"))


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US", settings={})
            s.add(firm)
            await s.flush()
            adv = User(id=uuid.uuid4(), firm_id=firm.id, email="adv@t", full_name="adv", role=UserRole.ADVISER, hashed_password="x")
            ops = User(id=uuid.uuid4(), firm_id=firm.id, email="ops@t", full_name="ops", role=UserRole.OPERATIONS, hashed_password="x")
            s.add_all([adv, ops])
            c = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Ida Moss", registration_type="individual", intake={})
            s.add(c)
            await s.flush()
            s.add(OnboardingParty(firm_id=firm.id, case_id=c.id, role="owner", legal_name="Ida Moss", screening_status="clear", screening_hits=[]))
            await s.flush()
            ex = ToolExecutors(s, adv.id, adv.role, firm.id)

            print("\n=== The board and survey answers ===")
            out = await ex.read_onboarding_scoreboard({})
            check("with no answers NPS is not measured", (out["nps"]["nps"], "not measured" in out["message"].lower()), (None, True))
            check("one operations person on the books", out["capacity"]["operations_staff"], 1)
            await refused("a score is 0 to 10", ex.preview_change("record_onboarding_feedback", {"case_id": str(c.id), "score": 11, "source": "Survey export"}), "0 to 10")
            await refused("an answer says where it is", ex.preview_change("record_onboarding_feedback", {"case_id": str(c.id), "score": 9, "source": ""}), "where the answer")
            card = {r["label"]: r["value"] for r in (await ex.preview_change("record_onboarding_feedback",
                    {"case_id": str(c.id), "score": 9, "comment": "Quicker than my last firm", "source": "Survey export, November"}))["rows"]}
            check("the card shows the score and how it counts", (card["Score"], card["Counts as"]), ("9 of 10", "Promoter"))
            out = await ex.record_onboarding_feedback({"case_id": str(c.id), "score": 9, "comment": "Quicker than my last firm", "source": "Survey export, November"})
            check("one promoter makes 100", (out["nps"]["nps"], out["nps"]["responses"]), (100, 1))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
