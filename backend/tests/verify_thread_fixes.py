"""Defects from the last review of the conversation workspace.

    python -m tests.verify_thread_fixes

  (a) the Briefing's "Agents you can run" counted tools, not agents;
  (b) the follow-up suggestions under a reply were three fixed strings nothing could answer;
  (c) a reply streamed to a browser that navigated away was lost, along with the question;
  (d) compliance's Needs-you listed every proposal it could technically approve.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.core import agent_runs, decision_rights as dr
from app.core.db import Base
from app.core.tools import tools_for_role
from app.llm import follow_ups, gateway as gw_mod
from app.llm.gateway import Gateway
from app.models.enums import AgentKey, RecommendationStatus, UserRole
from app.models.governance import AgentRun, Recommendation
from app.models.identity import User
from app.models.tenant import Firm
from app.models.thread import MessageRole, PendingAction, PendingActionStatus, Thread, ThreadKind, ThreadMessage, ToolCall

PASS, FAIL = [], []
R = UserRole


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def script(*rounds):
    it = iter(rounds)

    async def stream(**_kw):
        for ev in next(it):
            yield ev
    return stream


def text_round(*chunks):
    return [{"type": "text", "text": c} for c in chunks] + [{"type": "done", "tool_uses": [], "content_blocks": []}]


def tool_round(tool, inputs, tid="t1"):
    use = {"id": tid, "name": tool, "input": inputs}
    return [{"type": "done", "tool_uses": [use], "content_blocks": [{"type": "tool_use", **use}]}]


def queue_rights() -> None:
    print("\n=== (d) compliance's Needs-you is the proposals where its veto is the control ===")
    drift, care, billing = (AgentKey.DRIFT_REBALANCING.value, AgentKey.CLIENT_CARE.value, AgentKey.BILLING.value)
    check("compliance is asked to veto a trade", dr.queue_action(R.COMPLIANCE, drift), "veto")
    check("compliance is asked to veto a billing run", dr.queue_action(R.COMPLIANCE, billing), "veto")
    check("compliance is not chased about client care", dr.queue_action(R.COMPLIANCE, care), None)
    check("an adviser decides a trade", dr.queue_action(R.ADVISER, drift), "decide")
    check("an adviser decides client care", dr.queue_action(R.ADVISER, care), "decide")
    check("a paraplanner is not asked about a trade", dr.queue_action(R.PARAPLANNER, drift), None)
    check("operations decides a billing run", dr.queue_action(R.OPERATIONS, billing), "decide")
    check("an administrator is asked about nothing it cannot approve", dr.queue_action(R.ADMIN, drift), None)
    # The queue never offers what the server would refuse.
    for role in R:
        for key in (drift, care, billing):
            a = dr.queue_action(role, key)
            if a == "decide":
                check(f"{role.value}/{key}: 'decide' means approve is allowed", role in dr.roles_for(key, "approve"), True)
            if a == "veto":
                check(f"{role.value}/{key}: 'veto' means dismiss is allowed and approve is not",
                      (role in dr.roles_for(key, "dismiss"), role in dr.roles_for(key, "approve")), (True, False))


def suggestions() -> None:
    print("\n=== (b) suggestions follow from what the turn found, and only offer what the role holds ===")
    check("the fixed strings are gone", any("Show details" in s or "Confirm action" in s or s == "Continue"
          for s in follow_ups.derive(R.ADVISER, [])), False)
    held_by = {r: tools_for_role(r) for r in R}
    for role in (R.ADVISER, R.OPERATIONS, R.COMPLIANCE, R.BRANCH_LEADER, R.ADMIN):
        cases = [
            [],
            [{"tool_key": "search_households", "inputs": {"query": "Chen"},
              "result": {"results": [{"household_id": "h", "name": "The Chen Family"}]}}],
            [{"tool_key": "read_recommendations", "inputs": {},
              "result": {"recommendations": [{"title": "Rebalance Chen", "agent_key": "drift_rebalancing",
                                              "can_decide": True, "status": "proposed"}]}}],
            [{"tool_key": "read_agent_history", "inputs": {}, "result": {"by_status": {"failed": 2}}}],
        ]
        for used in cases:
            out = follow_ups.derive(role, used)
            check(f"{role.value}: at most four", len(out) <= 4, True)
            check(f"{role.value}: every suggestion is answerable by a tool the role holds",
                  all(any(s == sent and t in held_by[role] for t, sent in
                          [c for call in used for c in follow_ups.FOLLOWS.get(call['tool_key'], lambda i, r: [])(call.get('inputs') or {}, call.get('result') or {})]
                          + [follow_ups.WAITING, follow_ups.AGENTS]) for s in out), True)
    out = follow_ups.derive(R.ADVISER, [{"tool_key": "search_households", "inputs": {},
                                         "result": {"results": [{"name": "The Chen Family"}]}}])
    check("a household the turn found is named", any("the Chen Family" in s for s in out), True)
    check("and it asks for performance first", out[0], "How has the Chen Family portfolio performed against its benchmark?")
    out = follow_ups.derive(R.ADVISER, [{"tool_key": "read_recommendations", "inputs": {}, "result": {"recommendations": [
        {"title": "Rebalance Chen", "agent_key": "drift_rebalancing", "can_decide": True, "status": "proposed"}]}}])
    check("a proposal the role may decide is offered for approval", out[0], 'Approve the proposal "Rebalance Chen".')
    check("the turn does not re-offer what it just did", "What proposals are waiting for my decision?" in out, False)
    out = follow_ups.derive(R.COMPLIANCE, [{"tool_key": "read_recommendations", "inputs": {}, "result": {"recommendations": [
        {"title": "Rebalance Chen", "agent_key": "drift_rebalancing", "can_decide": False, "status": "proposed"}]}}])
    check("compliance is not offered an approval it cannot give", any(s.startswith("Approve") for s in out), False)
    out = follow_ups.derive(R.ADVISER, [{"tool_key": "read_agent_history", "inputs": {}, "result": {"by_status": {"failed": 2}}}])
    check("a failed run leads to the failed runs", out[0], "Show me the agent runs that failed.")
    out = follow_ups.derive(R.ADVISER, [{"tool_key": "read_agent_history", "inputs": {}, "result": {"by_status": {"completed": 2}}}])
    check("no failure, no failed-runs suggestion", "Show me the agent runs that failed." in out, False)
    check("a result of an unexpected shape suggests nothing wrong",
          isinstance(follow_ups.derive(R.ADVISER, [{"tool_key": "read_recommendations", "inputs": {}, "result": {"recommendations": [{}]}}]), list), True)
    check("a client gets no suggestions", follow_ups.derive(R.CLIENT, []), [])


async def with_db(fn):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "fixes.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await fn(Session)
    finally:
        await engine.dispose()
        try:
            os.remove(path)
            os.rmdir(tmp)
        except OSError:
            pass


async def routes(Session) -> None:
    from app.api import threads as T

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Mine", slug="mine")
        s.add(firm)
        await s.flush()

        def user(role):
            u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{role.value}@x", full_name=role.value, hashed_password="x", role=role)
            s.add(u)
            return u
        adviser, compliance, ops, admin = user(R.ADVISER), user(R.COMPLIANCE), user(R.OPERATIONS), user(R.ADMIN)
        run = AgentRun(id=uuid.uuid4(), firm_id=firm.id, agent_key="drift_rebalancing", status="awaiting_approval",
                       tier="tier_2", trigger="t", context={})
        s.add(run)
        await s.flush()
        for key, title in (("drift_rebalancing", "Rebalance Chen"), ("client_care", "Reach out to Wu"),
                           ("next_best_action", "Cash drag"), ("billing", "Q3 billing run")):
            s.add(Recommendation(id=uuid.uuid4(), firm_id=firm.id, run_id=run.id, agent_key=key, tier="tier_2",
                                 status=RecommendationStatus.PROPOSED, title=title, summary="s", rationale="r",
                                 confidence=0.9, priority=2, payload={}, evidence={}, citations=[]))
        await s.commit()

        print("\n=== (d) what each role's Needs-you shows ===")
        async def titles(u):
            n = await T.needs_you(user=u, session=s)
            return sorted(p["title"] for p in n["proposals"]), {p["action"] for p in n["proposals"]}, n["proposals_count"]
        check("compliance: only the proposals that move money, as vetoes",
              await titles(compliance), (["Q3 billing run", "Rebalance Chen"], {"veto"}, 2))
        check("adviser: all but billing, as decisions",
              await titles(adviser), (["Cash drag", "Reach out to Wu", "Rebalance Chen"], {"decide"}, 3))
        check("operations: the billing run, as a decision",
              await titles(ops), (["Cash drag", "Q3 billing run", "Reach out to Wu"], {"decide"}, 3))
        check("an administrator: nothing", await titles(admin), ([], set(), 0))

        # A real pause still reaches compliance whatever its proposals.
        t = Thread(firm_id=firm.id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=compliance.id, title="p")
        s.add(t)
        await s.flush()
        m = ThreadMessage(thread_id=t.id, role=MessageRole.ASTRA, text="", suggestions=[])
        s.add(m)
        await s.flush()
        tc = ToolCall(message_id=m.id, tool_key="mark_conflict_reviewed", inputs={"conflict_key": "k"})
        s.add(tc)
        await s.flush()
        s.add(PendingAction(thread_id=t.id, tool_call_id=tc.id, confirmation_text="Mark reviewed?",
                            requires_user_id=compliance.id))
        await s.commit()
        n = await T.needs_you(user=compliance, session=s)
        check("a real pause is still there for compliance", n["needs_decision_count"], 1)
        b = await T.briefing(user=compliance, session=s)
        check("and the briefing counts the pause plus the two vetoes", b["rows"][0]["count"], 3)

        print("\n=== (a) the briefing counts agents, not tools ===")
        for u, label in ((adviser, "adviser"), (ops, "operations"), (admin, "admin"), (compliance, "compliance")):
            row = next(r for r in (await T.briefing(user=u, session=s))["rows"] if r["id"] == "agents")
            check(f"{label}: agents you can run", row["count"], len(agent_runs.runnable_agents(u.role)))
            check(f"{label}: and not the size of the toolbox", row["count"] == len(tools_for_role(u.role)), False)
        row = next(r for r in (await T.briefing(user=adviser, session=s))["rows"] if r["id"] == "agents")
        check("an adviser can run thirteen agents", row["count"], 13)

        print("\n=== (b) the suggestions on a real reply ===")
        thread = Thread(firm_id=firm.id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=adviser.id, title="s")
        s.add(thread)
        await s.commit()
        gw_mod.llm_service.stream = script(
            tool_round("read_agents", {}), text_round("You can start thirteen agents."))
        evs = [e async for e in Gateway(s, adviser.id, adviser.role, firm.id).send_message(thread.id, "what can I run")]
        check("the turn finished", evs[-1]["type"], "done")
        reply = (await s.execute(select(ThreadMessage).where(ThreadMessage.thread_id == thread.id,
                                                            ThreadMessage.role == MessageRole.ASTRA))).scalars().one()
        check("the suggestions are not the old fixed three", set(reply.suggestions) & {"Continue", "Show details", "Confirm action"}, set())
        check("they follow from the agent roster", "Have the next-best-action agent scan the book for opportunities." in reply.suggestions, True)
        check("and do not re-offer the roster", "Which agents can I run?" in reply.suggestions, False)


async def navigating_away(Session) -> None:
    """(c) A browser that closes its connection mid-reply does not take the reply with it."""
    from app.api import threads as T
    from app.llm import turns

    T.SessionLocal = Session
    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Away", slug="away")
        s.add(firm)
        await s.flush()
        adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@x", full_name="A", hashed_password="x", role=R.ADVISER)
        s.add(adviser)
        thread = Thread(firm_id=firm.id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=adviser.id, title="away")
        s.add(thread)
        await s.commit()
        thread_id = thread.id

        async def slow(**_kw):
            yield {"type": "text", "text": "The Chen Family is "}
            await asyncio.sleep(0.4)
            yield {"type": "text", "text": "on track."}
            yield {"type": "done", "tool_uses": [], "content_blocks": []}

        async def peek():
            async with Session() as p:
                msgs = (await p.execute(select(ThreadMessage).where(ThreadMessage.thread_id == thread_id)
                                        .order_by(ThreadMessage.created_at))).scalars().all()
                return [(m.role.value, m.text) for m in msgs]

        print("\n=== (c) the reply survives the browser leaving ===")
        gw_mod.llm_service.stream = slow
        response = await T.send_message(thread_id, {"text": "How is the Chen Family?"}, user=adviser, session=s)
        first = await response.body_iterator.__anext__()           # the first token reaches the browser...
        check("a token was streamed", "The Chen Family is" in first, True)
        await response.body_iterator.aclose()                      # ...and then the browser navigates away
        check("the turn is marked as running on the server", turns.is_active(thread_id), True)
        mid = await T.get_thread(thread_id, user=adviser, session=s)
        check("a reload mid-reply says the reply is being written", mid["working"], True)
        check("and shows the question already", [m["text"] for m in mid["messages"]], ["How is the Chen Family?"])

        for _ in range(40):
            await asyncio.sleep(0.1)
            if not turns.is_active(thread_id):
                break
        check("the turn finished without a listener", turns.is_active(thread_id), False)
        check("the whole reply is on the thread, not the first chunk",
              await peek(), [("user", "How is the Chen Family?"), ("astra", "The Chen Family is on track.")])
        async with Session() as fresh:                            # a reload is a new request, a new session
            done = await T.get_thread(thread_id, user=adviser, session=fresh)
        check("a reload afterwards is not 'working'", done["working"], False)
        check("and carries follow-ups for the role", len(done["messages"][-1]["suggestions"]) >= 1, True)

        print("\n=== (c) a turn that fails still leaves a trace on the thread ===")
        async def broken(**_kw):
            raise RuntimeError("upstream unavailable")
            yield  # pragma: no cover
        gw_mod.llm_service.stream = broken
        response = await T.send_message(thread_id, {"text": "And the Wu Family?"}, user=adviser, session=s)
        events = [e async for e in response.body_iterator]
        check("the browser is told", any('"type": "error"' in e for e in events), True)
        last2 = (await peek())[-2:]
        check("the question is on the thread", last2[0], ("user", "And the Wu Family?"))
        check("and so is what happened to it", last2[1][0] == "astra" and "error" in last2[1][1], True)
        check("no turn is left marked running", turns.is_active(thread_id), False)

def main() -> int:
    queue_rights()
    suggestions()
    asyncio.run(with_db(routes))
    asyncio.run(with_db(navigating_away))
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
