"""The book scan runs any agent that works across the whole book (B-15).

    python -m tests.verify_book_scan_route

It checks that POST /api/studio/scan names an agent it does not know instead of running next-best-action, names one
that needs a client, mandate, meeting or case, holds who may start an agent to the conversation's rule
(core/agent_runs.py), and holds a book-moving agent to a proposal as a run a person starts in the conversation is.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from types import SimpleNamespace

from fastapi import HTTPException

from app.api import studio
from app.core import agent_runs
from app.models.enums import AgentKey, UserRole

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def main() -> None:
    firm = SimpleNamespace(id=uuid.uuid4(), name="T")
    ran: list[dict] = []

    async def fake_run(db, **kw):
        ran.append(kw)
        return SimpleNamespace(id=uuid.uuid4())

    class FakeDb:
        async def execute(self, *_a, **_k):
            return SimpleNamespace(scalar_one=lambda: 0)

    studio.run_agent = fake_run

    async def scan(agent: str, role: UserRole):
        try:
            out = await studio.book_scan(agent=agent, user=SimpleNamespace(role=role), firm=firm, db=FakeDb())
            return out["agent"]
        except HTTPException as exc:
            return exc.status_code

    print("\n=== Which agents, and who ===")
    check("an agent that does not exist is named, not run as next-best-action", await scan("robo", UserRole.ADVISER), 422)
    check("one that needs a mandate is named", await scan("drift_rebalancing", UserRole.PORTFOLIO_TEAM), 422)
    check("one that needs a case is named", await scan("onboarding_kyc_aml", UserRole.OPERATIONS), 422)
    check("conduct surveillance is compliance's", await scan("conduct_surveillance", UserRole.ADVISER), 403)
    check("and compliance runs it", await scan("conduct_surveillance", UserRole.COMPLIANCE), "conduct_surveillance")
    check("an adviser runs client care", await scan("client_care", UserRole.ADVISER), "client_care")
    check("and the tax agent, which the scan did not know before", await scan("tax_intelligence", UserRole.ADVISER), "tax_intelligence")
    check("billing has its own tool", await scan("billing", UserRole.OPERATIONS), 403)

    print("\n=== A run a person starts stops at a proposal where it would move a book ===")
    held = {str(kw["agent_key"].value): kw["require_human"] for kw in ran}
    for key, flag in held.items():
        check(f"{key} is held as the conversation holds it", flag, agent_runs.forced_to_proposal(AgentKey(key)))
    check("every firm-wide agent the old scan knew still runs", sorted(k for k in ("client_care", "estate_succession", "next_best_action")
                                                                     if AgentKey(k) not in agent_runs.SUBJECT_REQUIRED),
          ["client_care", "estate_succession", "next_best_action"])


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:\n  " + "\n  ".join(FAIL))
        sys.exit(1)
