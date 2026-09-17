"""Who may decide a proposal, and that a proposal is decided at most once.

    python -m tests.verify_decision_rights

The firm's rule: proposals that move money on a client's book are approved, modified or
revised only by an adviser or the portfolio team; compliance may veto them (dismiss, or
roll back) but not approve; platform administrators hold neither power; every other
proposal keeps its existing rule of any staff member.

A note on what this can and cannot prove. The exactly-once guarantee rests on a row lock
(SELECT ... FOR UPDATE) that Postgres enforces between concurrent transactions. SQLite
omits FOR UPDATE, so a true race cannot be reproduced here. What is checked is the
behaviour the lock protects: decide() re-reads the status itself and refuses a proposal
that is no longer PROPOSED, rather than trusting the caller to have checked.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from app.core import decision_rights as dr
from app.models.enums import AgentKey, UserRole

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(f"{label}: got {got}, want {want}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def may(role: UserRole, agent: str, decision: str) -> bool:
    try:
        dr.check(role, agent, decision)
        return True
    except dr.DecisionForbidden:
        return False


def rights() -> None:
    drift = AgentKey.DRIFT_REBALANCING.value
    care = AgentKey.CLIENT_CARE.value
    R = UserRole

    print("\n=== approving a trade: adviser and portfolio team only ===")
    for role, want in [(R.ADVISER, True), (R.PORTFOLIO_TEAM, True), (R.COMPLIANCE, False),
                       (R.PARAPLANNER, False), (R.RESEARCH_CIO, False),
                       (R.OPERATIONS, False), (R.BRANCH_LEADER, False),
                       (R.ADMIN, False), (R.SUPERADMIN, False), (R.CLIENT, False)]:
        check(f"{role.value} may approve a trade", may(role, drift, "approve"), want)

    print("\n=== modify and revise follow approve ===")
    for decision in ("modify", "revise"):
        check(f"adviser may {decision} a trade", may(R.ADVISER, drift, decision), True)
        check(f"compliance may not {decision} a trade", may(R.COMPLIANCE, drift, decision), False)
        check(f"admin may not {decision} a trade", may(R.ADMIN, drift, decision), False)

    print("\n=== compliance can veto: dismiss and roll back, but not approve ===")
    for decision in ("dismiss", "rollback"):
        check(f"compliance may {decision} a trade", may(R.COMPLIANCE, drift, decision), True)
        check(f"adviser may {decision} a trade", may(R.ADVISER, drift, decision), True)
        check(f"research may not {decision} a trade", may(R.RESEARCH_CIO, drift, decision), False)
        check(f"admin may not {decision} a trade", may(R.ADMIN, drift, decision), False)

    print("\n=== proposals that do not move the book keep the staff rule ===")
    for role in (R.ADVISER, R.COMPLIANCE, R.OPERATIONS, R.RESEARCH_CIO, R.ADMIN):
        check(f"{role.value} may approve client care", may(role, care, "approve"), True)
    check("a client may not approve client care", may(R.CLIENT, care, "approve"), False)
    check("an adviser-authored skill is not a trade", may(R.OPERATIONS, "skill", "approve"), True)

    print("\n=== an unknown decision on a trade is refused, not waved through ===")
    check("adviser 'escalate' on a trade", may(R.ADVISER, drift, "escalate"), False)

    print("\n=== the refusal explains itself ===")
    try:
        dr.check(R.RESEARCH_CIO, drift, "approve")
        msg = ""
    except dr.DecisionForbidden as exc:
        msg = str(exc)
    check("names who may", "adviser or the portfolio team" in msg, True)
    check("says why", "moves money" in msg, True)

    print("\n=== only book-moving agents are restricted ===")
    check("drift moves the book", dr.moves_the_book(drift), True)
    check("an enum key is understood", dr.moves_the_book(AgentKey.DRIFT_REBALANCING), True)
    check("client care does not", dr.moves_the_book(care), False)


async def once_only() -> None:
    """decide() refuses a proposal that is no longer PROPOSED, before doing anything."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.atlas.runtime import AlreadyDecidedError, decide
    from app.core.db import Base
    from app.models.enums import AgentRunStatus, HumanAction, RecommendationStatus
    from app.models.governance import AgentRun, Recommendation
    from app.models.tenant import Firm

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [Base.metadata.tables[n] for n in ("firm", "agent_run", "recommendation")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(engine, expire_on_commit=False)

    print("\n=== a proposal is decided at most once ===")
    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="F", slug="f")
        s.add(firm)
        await s.flush()
        run = AgentRun(id=uuid.uuid4(), firm_id=firm.id, agent_key="drift_rebalancing",
                       status=AgentRunStatus.COMPLETED, tier="tier_2", trigger="test", context={})
        s.add(run)
        await s.flush()
        rec = Recommendation(id=uuid.uuid4(), firm_id=firm.id, run_id=run.id,
                             agent_key="drift_rebalancing", tier="tier_2",
                             status=RecommendationStatus.APPROVED, title="t", summary="s",
                             rationale="r", confidence=0.9, priority=1, payload={}, evidence={},
                             citations=[])
        s.add(rec)
        await s.commit()

        # A stale in-memory copy that still thinks it is PROPOSED — the situation a second,
        # concurrent approval is in. decide() must read the real status, not trust this.
        stale = Recommendation(id=rec.id)
        stale.status = RecommendationStatus.PROPOSED
        try:
            await decide(s, firm=firm, recommendation=stale, action=HumanAction.APPROVE,
                         actor_id=None, actor_label="second approver")
            check("a second approval is refused", "executed", "AlreadyDecidedError")
        except AlreadyDecidedError as exc:
            check("a second approval is refused", "AlreadyDecidedError", "AlreadyDecidedError")
            check("and reports the status it found", exc.status, RecommendationStatus.APPROVED)
    await engine.dispose()


def main() -> int:
    rights()
    asyncio.run(once_only())
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
