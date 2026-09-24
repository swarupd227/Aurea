"""Prove the AI use-case inventory (L200-8 §8): risk-tier coverage counts, an overdue
review correctly excluding a retired use case, entitlement violations logged and counted
by type, and the change log recording an entry — the register that ties ownership and
risk tier to the platform's existing eval/usage signals, none of which this module
recomputes itself.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_ai_governance
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import ai_governance as engine
from app.core.db import Base, utcnow
from app.models.ai_governance import AIUseCase
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = (abs(float(got) - float(want)) < 0.01) if isinstance(want, (int, float)) else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "app_user", "ai_use_case", "ai_change_log_entry", "entitlement_violation", "llm_usage"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
        s.add(firm)
        await s.flush()

        now = utcnow()
        s.add_all([
            # High risk, reviewed recently -> not overdue.
            AIUseCase(firm_id=firm.id, use_case_key="drift_rebalancing", name="Drift", category="agent",
                      description="x", owner="PM", risk_tier="high", status="active",
                      review_cadence_days=180, last_reviewed_at=now - timedelta(days=10)),
            # High risk, reviewed long ago -> overdue.
            AIUseCase(firm_id=firm.id, use_case_key="execute_orders", name="Execute Orders", category="tool",
                      description="x", owner="PM", risk_tier="high", status="active",
                      review_cadence_days=180, last_reviewed_at=now - timedelta(days=400)),
            # Medium risk, never reviewed but young (created recently via default) -> not overdue.
            AIUseCase(firm_id=firm.id, use_case_key="meeting_prep", name="Meeting Prep", category="agent",
                      description="x", owner="Adviser", risk_tier="low", status="active",
                      review_cadence_days=180, last_reviewed_at=now - timedelta(days=5)),
            # Retired + overdue by date -> must NOT count as overdue (it's not active).
            AIUseCase(firm_id=firm.id, use_case_key="old_agent", name="Retired Agent", category="agent",
                      description="x", owner="PM", risk_tier="high", status="retired",
                      review_cadence_days=180, last_reviewed_at=now - timedelta(days=900)),
        ])
        await s.flush()

        print("\n=== inventory_summary: risk-tier counts and overdue review (retired excluded) ===")
        summary = await engine.inventory_summary(s, firm.id)
        check("total use cases", summary["total_use_cases"], 4)
        check("active use cases", summary["active"], 3)
        # drift_rebalancing and execute_orders are both active+high; old_agent is high but
        # retired, so it must NOT be the 3rd — this is the exclusion the check proves.
        check("high-risk count (retired one excluded)", summary["by_risk_tier"]["high"], 2)
        overdue_keys = sorted(r["use_case_key"] for r in summary["overdue_review"])
        check("only the truly overdue active use case is flagged", overdue_keys, ["execute_orders"])
        check("retired-but-stale use case never counted as overdue", "old_agent" in overdue_keys, False)

        print("\n=== entitlement violations: logged and counted by type ===")
        await engine.record_violation(
            s, firm.id, actor_user_id=None, actor_role="operations",
            violation_type="tool_role_forbidden", subject_key="execute_orders",
            detail="Role operations may not call 'Execute Orders'.",
        )
        await engine.record_violation(
            s, firm.id, actor_user_id=None, actor_role="research_cio",
            violation_type="decision_rights_forbidden", subject_key="drift_rebalancing",
            detail="Only an adviser or the portfolio team may approve it.",
        )
        await engine.record_violation(
            s, firm.id, actor_user_id=None, actor_role="operations",
            violation_type="tool_role_forbidden", subject_key="execute_orders",
            detail="Role operations may not call 'Execute Orders'.",
        )
        summary2 = await engine.inventory_summary(s, firm.id)
        check("total violations logged", summary2["entitlement_violations"]["total"], 3)
        check("violations counted correctly by type",
              summary2["entitlement_violations"]["by_type"]["tool_role_forbidden"], 2)
        check("the other violation type also counted",
              summary2["entitlement_violations"]["by_type"]["decision_rights_forbidden"], 1)

        print("\n=== change log: an entry is recorded and surfaces in recent_changes ===")
        await engine.log_change(
            s, firm.id, use_case_id=None, change_type="risk_tier",
            description="Drift: risk_tier low -> high", changed_by="admin@test",
        )
        summary3 = await engine.inventory_summary(s, firm.id)
        check("recent change appears", summary3["recent_changes"][0]["description"], "Drift: risk_tier low -> high")
        check("changed_by recorded", summary3["recent_changes"][0]["changed_by"], "admin@test")

        print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
        for f in FAIL:
            print(f"  FAILED: {f}")

    await db.dispose()  # otherwise the process hangs on the open pool at exit
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
