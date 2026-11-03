"""Prove the over-promising rule (FMC Act s22 and its peers) flags a promise and not a denial (B-48): "returns are not
guaranteed" passes, "this fund is guaranteed to return 8%" fails, a phrase quoted or named as an example passes, and a
turn such as "but" ends a denial; and the flags it raised before are counted, and closed only in a compliance person's
name. The rule's cases need no database; the recheck runs on a throwaway SQLite one.

    python -m tests.verify_overpromise
"""
from __future__ import annotations

import asyncio
import sys

from app.compliance import engine
from app.compliance.ontology import FRAMEWORKS
from app.compliance.rules import promises

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


DENIALS = [
    "Returns are not guaranteed.",
    "Past performance is no guarantee of future results.",
    "No investment is risk-free.",
    "We cannot guarantee an outcome.",
    "This does not mean a rebuy carries no risk.",
    "It is not a guarantee of outcome.",
    "Avoid words such as guaranteed or risk-free.",
    'We never use "can\'t lose" in a client letter.',
    "The adviser will review it; nothing here is guaranteed.",
]
PROMISES = {
    "This fund is guaranteed to return 8%.": ["guaranteed"],
    "There is no risk in this trade.": ["no risk"],
    "You can't lose with this allocation.": ["can't lose"],
    "Not perfect, but guaranteed to beat cash.": ["guaranteed"],
    "This won't fail, it is a sure thing.": ["sure thing"],
    "A risk-free income for life.": ["risk-free"],
    "The fund will always outperform the index.": ["always outperform"],
    "We guarantee you will definitely retire early.": ["guarantee", "will definitely"],
}


def main() -> int:
    print("\n=== A denial is not a promise ===")
    for text in DENIALS:
        check(text, promises(text), [])
    print("\n=== A promise is still a promise ===")
    for text, want in PROMISES.items():
        check(text, promises(text), sorted(want))

    print("\n=== Through the engine ===")
    fw = FRAMEWORKS["NZ-FMA"]
    s22 = next(r for r in fw.rules if r.eval == "no_overpromise")

    def assess(text: str) -> str:
        ctx = engine.CheckContext(agent_key="drift_rebalancing", rationale=text, text=text.lower(), confidence=0.9, payload={},
                                  evidence={}, citations=[], mandate_type=None, policy={})
        return next(r["status"] for r in engine.evaluate(fw, ctx)["results"] if r["rule_id"] == s22.id)
    check("a disclaimer passes s22", assess("Markets can fall and returns are not guaranteed; the adviser decides."), "pass")
    check("a promise fails it", assess("This rebalance is guaranteed to lift returns."), "fail")
    asyncio.run(recheck_flags())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


async def recheck_flags() -> None:
    """The open flags the old rule raised: counted, and closed only in a named compliance person's name."""
    import uuid

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.core.db import Base, utcnow
    from app.models.enums import RecommendationStatus, UserRole
    from app.models.governance import AgentRun, LedgerEntry, Recommendation, SurveillanceFlag
    from app.models.identity import User
    from app.models.tenant import Firm
    from seed.overpromise_recheck import recheck

    print("\n=== Rechecking the flags already open ===")
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add(firm)
            await s.flush()
            comp = User(id=uuid.uuid4(), firm_id=firm.id, email="c@t", full_name="Cy", role=UserRole.COMPLIANCE, hashed_password="x")
            run = AgentRun(firm_id=firm.id, agent_key="drift_rebalancing", status="completed", tier="tier_2", trigger="t", started_at=utcnow())
            s.add_all([comp, run])
            await s.flush()

            def flagged(text: str) -> SurveillanceFlag:
                rec = Recommendation(firm_id=firm.id, run_id=run.id, agent_key="drift_rebalancing", tier="tier_2",
                                     status=RecommendationStatus.PROPOSED, title=text[:40], summary="s", rationale=text)
                s.add(rec)
                return rec
            denial, promise = flagged("Returns are not guaranteed."), flagged("This is guaranteed to beat cash.")
            await s.flush()
            for rec in (denial, promise):
                s.add(SurveillanceFlag(firm_id=firm.id, recommendation_id=rec.id, target_agent_key="drift_rebalancing",
                                       severity="medium", category="conduct", finding="FMC Act s22: Over-promising language: guaranteed."))
            await s.flush()
            dry = await recheck(s, firm)
            check("counted: one now passes, one still promises, nothing closed", (dry["open"], dry["now_pass"], dry["still_fail"], dry["resolved"]),
                  (2, 1, 1, 0))
            out = await recheck(s, firm, resolve_as=comp)
            left = (await s.execute(select(SurveillanceFlag).where(SurveillanceFlag.resolved.is_(False)))).scalars().all()
            check("with a compliance person named, only the denial is closed", (out["resolved"], [f.recommendation_id for f in left]), (1, [promise.id]))
            entry = (await s.execute(select(LedgerEntry).where(LedgerEntry.event_type == "flag_resolved"))).scalar_one()
            check("in their name, with the reason, on the ledger", (entry.actor, "B-48" in entry.content["resolution_note"]), ("c@t", True))
    finally:
        await db.dispose()


if __name__ == "__main__":
    sys.exit(main())
