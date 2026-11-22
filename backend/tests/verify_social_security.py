"""Social Security for a couple, the law's arithmetic laid out (B-184).

    python -m tests.verify_social_security

The arithmetic first, then the demo firm from seed/run.py in a throwaway SQLite file (the Morrisons are its US household):
  - a spousal benefit is cut 25/36 of 1% a month for 36 months early and 5/12 of 1% after, and never raised for waiting;
  - it is half the other's full-age benefit less one's own, from when both have filed;
  - the survivor keeps the larger of their own and what the one who died was paid, at least 82.5% of the latter's;
  - pairs of claim ages carry the total to the plan-to ages, discounted, the bridge the portfolio carries, and the
    retirement likelihood with those claims; a benefit already paid is not re-chosen;
  - a household outside the US, or with no statement, is not assessed; one statement lays out one person and says the
    spousal and survivor benefits are not assessed;
  - it changes nothing, and the conversation reads it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_ss_"), "ss.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")

import sqlalchemy.ext.asyncio as sa_async  # noqa: E402

_real_engine = sa_async.create_async_engine


def _engine(url, **kw):
    if str(url).startswith("sqlite"):
        for k in ("pool_size", "max_overflow", "pool_pre_ping"):
            kw.pop(k, None)
    return _real_engine(url, **kw)


sa_async.create_async_engine = _engine

from sqlalchemy import func, select  # noqa: E402

import app.models  # noqa: F401,E402
from app.aurea_core import financial_plan, social_security as ss  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.tools import TOOLS, ToolChangeState  # noqa: E402
from app.llm.executors import ToolExecutors  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.financial_plan import IncomeStream  # noqa: E402
from app.models.graph import Goal, Household, Person  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def arithmetic() -> None:
    print("\n=== The spousal reduction ===")
    full = 67 * 12
    check("at full retirement age, all of it", ss.spousal_factor(full, full), 1.0)
    check("36 months early, three quarters", round(ss.spousal_factor(full, full - 36), 4), 0.75)
    check("60 months early, 65%", round(ss.spousal_factor(full, full - 60), 4), 0.65)
    check("no more for waiting", ss.spousal_factor(full, full + 36), 1.0)

    print("\n=== Own, spousal and survivor ===")
    a = {"id": "a", "name": "A", "birth_year": 1962, "age": 64, "pia": 36_000.0, "full_months": full, "claim_on_file": 67}
    b = {"id": "b", "name": "B", "birth_year": 1962, "age": 64, "pia": 9_600.0, "full_months": full, "claim_on_file": 67}
    at_full = ss.benefits(a, b, 67, 67)
    check("own at full age is the statement's", at_full["own"], {"a": 36_000.0, "b": 9_600.0})
    check("spousal is half the other's less one's own", at_full["spousal"], {"a": 0.0, "b": 8_400.0})
    check("the higher earner gets no spousal", at_full["spousal"]["a"], 0.0)
    late = ss.benefits(a, b, 70, 67)
    check("waiting to 70 raises the own benefit by 24%", late["own"]["a"], round(36_000 * 1.24, 2))
    check("the survivor keeps the larger benefit", late["survivor"]["if_a_dies"], round(36_000 * 1.24, 2))
    early = ss.benefits(a, b, 62, 67)
    check("an early claim's survivor is at least 82.5%", early["survivor"]["if_a_dies"], round(0.825 * 36_000, 2))
    waits = ss.benefits(a, b, 67, 62)
    check("spousal waits for the other to file", waits["spousal"]["b"], 8_400.0)
    life62 = ss.lifetime(a, b, 62, 62, 95, 0.025)
    life70 = ss.lifetime(a, b, 70, 70, 95, 0.025)
    check("to 95, claiming at 70 pays more in total", life70["total_today"] > life62["total_today"], True)
    check("discounting pays less than the total", life70["pv"] < life70["total_today"], True)


async def seeded() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER))).scalars().first()
        us = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Morrison%")))).scalars().first()
        nz = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Chen%")))).scalars().first()
        robert = (await s.execute(select(Person).where(Person.household_id == us.id, Person.full_name.like("Robert%")))).scalars().first()
        patricia = (await s.execute(select(Person).where(Person.household_id == us.id, Person.full_name.like("Patricia%")))).scalars().first()

        print("\n=== Not assessed ===")
        out = await ss.couple(s, nz.id, firm_id=firm.id)
        check("a household outside the US", out["status"], "not_assessed")
        out = await ss.couple(s, us.id, firm_id=firm.id)
        check("no statement on file", (out["status"], sorted(out["without_statement"])),
              ("not_assessed", sorted([robert.preferred_name or robert.full_name, patricia.preferred_name or patricia.full_name])))

        print("\n=== One statement ===")
        await financial_plan.record_stream(s, robert.id, firm_id=firm.id, by="Test", kind="social_security", name="Social Security",
                                           annual_amount=48_000, start_age=70, source_document="SSA statement, Robert, test")
        await s.commit()
        out = await ss.couple(s, us.id, firm_id=firm.id)
        check("one person is laid out", (out["status"], len(out["people"])), ("one_statement", 1))
        check("and the other is named as having no statement", out["without_statement"], [patricia.preferred_name or patricia.full_name])

        print("\n=== The couple ===")
        await financial_plan.record_stream(s, patricia.id, firm_id=firm.id, by="Test", kind="social_security", name="Social Security",
                                           annual_amount=14_400, start_age=67, source_document="SSA statement, Patricia, test")
        await s.commit()
        before = ((await s.execute(select(func.count(IncomeStream.id)))).scalar(),
                  sorted(float(g.target_amount) for g in (await s.execute(select(Goal))).scalars().all()))
        out = await ss.couple(s, us.id, firm_id=firm.id)
        check("both are laid out", (out["status"], len(out["people"])), ("assessed", 2))
        pairs = out["pairs"]
        claims_r = sorted({p["claim"][str(robert.id)] for p in pairs})
        check("Robert, 67 and not yet paid, is laid out from today to 70", claims_r, [67, 70])
        check("Patricia's 62 is past, so from 64", sorted({p["claim"][str(patricia.id)] for p in pairs})[0], 64)
        check("the pair on file is marked", sum(1 for p in pairs if p["on_file"]), 1)
        on_file = next(p for p in pairs if p["on_file"])
        check("Patricia's spousal is half Robert's less her own", on_file["spousal"][str(patricia.id)], round(0.5 * 48_000 - 14_400, 2))
        check("the survivor keeps Robert's larger benefit", on_file["survivor"][f"if_{robert.id}_dies"],
              on_file["own"][str(robert.id)])
        check("every pair carries the retirement likelihood", all(p.get("retirement_success") is not None for p in pairs), True)
        r67 = next(p for p in pairs if p["claim"][str(robert.id)] == 67 and p["claim"][str(patricia.id)] == 67)
        r70 = next(p for p in pairs if p["claim"][str(robert.id)] == 70 and p["claim"][str(patricia.id)] == 67)
        check("waiting to 70 the portfolio carries a longer bridge", r70["bridge_today"] > r67["bridge_today"], True)
        check("and the survivor is paid more", r70["survivor"][f"if_{robert.id}_dies"] > r67["survivor"][f"if_{robert.id}_dies"], True)
        check("what is not modelled is named", "tax_on_benefits" in out["not_modelled"], True)
        check("it decides nothing", out["decides"], "nothing: when to claim is the client's")
        after = ((await s.execute(select(func.count(IncomeStream.id)))).scalar(),
                 sorted(float(g.target_amount) for g in (await s.execute(select(Goal))).scalars().all()))
        check("laying it out changes nothing", after, before)

        print("\n=== The conversation ===")
        check("it is a read", TOOLS["read_social_security"].change_state, ToolChangeState.NO)
        check("no client holds it", UserRole.CLIENT in TOOLS["read_social_security"].roles_required, False)
        read = await ToolExecutors(s, adviser.id, adviser.role, firm.id).read_social_security({"household_id": str(us.id)})
        check("Astra reads the pairs", len(read["pairs"]), len(pairs))
        check("and says the claim is the client's", "the claim is the client's" in read["message"], True)


async def main() -> int:
    arithmetic()
    await seeded()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
