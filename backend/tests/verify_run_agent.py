"""Starting an agent from a sentence: the confirmation, the three controls kept apart, and that a
run happens once.

    python -m tests.verify_run_agent

What this proves, against a throwaway SQLite database and fake agents (no model, no network):

  - the run_agent tool pauses for confirmation and is held on the server;
  - the confirmation shows the agent, what it will do and the autonomy tier it resolves to, and a
    refusal (paused agent, another firm's client, a role that may not start it) never becomes a card;
  - confirming starts the run and approves nothing: an agent that moves the book stops at a
    proposal even at Tier 3 and its act() is never called, while a Tier 3 agent that does not move
    money may carry out what it proposes;
  - the run is published on the thread when it starts and settled by the agent that ran it;
  - a confirmation runs exactly once, belongs to the person it was put to and to their firm, and
    a failed run is not silently retried;
  - a confirmation survives its request being cancelled.

A note on what this cannot prove: the row lock behind "exactly once" is SELECT ... FOR UPDATE,
which SQLite omits, so a true race between two simultaneous clicks is not reproduced here. What is
checked is the behaviour the lock protects: the second confirmation finds the first one's claim.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.atlas import narration, registry
from app.atlas.base import BaseAgent, RecommendationDraft, Subject
from app.core import agent_runs, decision_rights
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm import gateway as gw_mod
from app.llm.gateway import Gateway, GatewayError
from app.models.enums import AgentKey, AutonomyTier, RecommendationStatus, UserRole
from app.models.governance import AgentRun, Recommendation
from app.models.graph import Household, Mandate, Person
from app.models.identity import User
from app.models.tenant import AgentConfig, AutonomyPolicy, Firm
from app.models.thread import (
    MessageRole, PendingAction, PendingActionStatus, Thread, ThreadKind, ThreadMessage, ToolCall, ToolCallStatus,
)

PASS, FAIL = [], []
R = UserRole


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


class FakeAgent(BaseAgent):
    """An agent that proposes one thing. `act` records that it ran, so a test can say whether the
    runtime carried the proposal out."""

    def __init__(self, key: AgentKey, *, title: str, executed: bool = False, boom: bool = False,
                 slow: float = 0.0, on_think=None):
        self.key = key
        self.title, self.executed, self.boom, self.slow, self.on_think = title, executed, boom, slow, on_think
        self.acted = 0

    async def sense(self, ctx):
        return {"brain": {}}

    async def think(self, ctx, sensed):
        if self.on_think:
            await self.on_think()
        if self.slow:
            await asyncio.sleep(self.slow)
        if self.boom:
            raise RuntimeError("the model is unavailable")
        return [RecommendationDraft(title=self.title, summary="s", rationale="The weights have drifted from target.",
                                    confidence=0.8, priority=1, subject=ctx.subject)]

    async def act(self, ctx, rec):
        self.acted += 1
        return {"executed": self.executed}


def script(*rounds):
    """A stand-in for the model: each call to stream() plays the next round of events."""
    it = iter(rounds)

    async def stream(**_kw):
        for ev in next(it):
            yield ev
    return stream


def tool_round(tool: str, inputs: dict, tid: str = "t1"):
    use = {"id": tid, "name": tool, "input": inputs}
    return [{"type": "done", "tool_uses": [use],
             "content_blocks": [{"type": "tool_use", "id": tid, "name": tool, "input": inputs}]}]


def say(text: str):
    return [{"type": "text", "text": text}, {"type": "done", "tool_uses": [], "content_blocks": []}]


async def main_async() -> None:
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "run_agent.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await scenarios(Session)
    finally:
        await engine.dispose()
        try:
            os.remove(path)
            os.rmdir(tmp)
        except OSError:
            pass


async def scenarios(Session) -> None:
    drift_key, nba_key = AgentKey.DRIFT_REBALANCING, AgentKey.NEXT_BEST_ACTION

    print("\n=== the catalogue: run_agent is a held, state-changing tool ===")
    tool = TOOLS["run_agent"]
    check("it pauses for confirmation", tool.change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    check("a client holds no run_agent", "run_agent" in tools_for_role(R.CLIENT), False)
    check("an administrator holds no run_agent", "run_agent" in tools_for_role(R.ADMIN), False)
    check("an adviser does", "run_agent" in tools_for_role(R.ADVISER), True)
    check("the confirmation says it is not an approval", "not an approval" in tool.confirmation, True)
    check("the description does not offer billing", "billing" in tool.description.split("Agents you can start:")[1], False)
    check("read_agents is a read", TOOLS["read_agents"].change_state, ToolChangeState.NO)

    print("\n=== who may start what ===")
    check("adviser may start drift", agent_runs.may_run(R.ADVISER, drift_key), True)
    check("compliance may not start drift", agent_runs.may_run(R.COMPLIANCE, drift_key), False)
    check("compliance may start conduct surveillance", agent_runs.may_run(R.COMPLIANCE, AgentKey.CONDUCT_SURVEILLANCE), True)
    check("an adviser may not start conduct surveillance", agent_runs.may_run(R.ADVISER, AgentKey.CONDUCT_SURVEILLANCE), False)
    check("nobody starts billing from here", agent_runs.run_roles(AgentKey.BILLING), frozenset())
    check("nor an onboarding-case agent", agent_runs.run_roles(AgentKey.CIP_IDENTITY_VERIFIER), frozenset())
    check("a client may start nothing", agent_runs.runnable_agents(R.CLIENT), [])
    check("an administrator may start nothing", agent_runs.runnable_agents(R.ADMIN), [])
    check("drift is held to a proposal", agent_runs.forced_to_proposal(drift_key), True)
    check("next best action is not", agent_runs.forced_to_proposal(nba_key), False)
    check("a Tier 3 book-moving agent does not 'act on its own' from a conversation",
          agent_runs.acts_on_its_own(drift_key, "tier_3"), False)
    check("a Tier 3 advisory agent may", agent_runs.acts_on_its_own(nba_key, "tier_3"), True)
    check("Tier 2 never does", agent_runs.acts_on_its_own(nba_key, "tier_2"), False)

    drift = FakeAgent(drift_key, title="Rebalance Chen Family")
    nba = FakeAgent(nba_key, title="Review cash drag", executed=True)
    registry.all_agents()
    registry._REGISTRY[drift_key] = drift
    registry._REGISTRY[nba_key] = nba

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Mine", slug="mine")
        other = Firm(id=uuid.uuid4(), name="Theirs", slug="theirs")
        s.add_all([firm, other])
        await s.flush()

        def user(role, f=firm):
            u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{role.value}@{f.slug}", full_name=role.value.title(),
                     hashed_password="x", role=role)
            s.add(u)
            return u
        adviser, compliance, ops, admin = user(R.ADVISER), user(R.COMPLIANCE), user(R.OPERATIONS), user(R.ADMIN)
        paraplanner = user(R.PARAPLANNER)
        outsider = user(R.ADVISER, other)

        hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="Chen Family")
        foreign_hh = Household(id=uuid.uuid4(), firm_id=other.id, name="Their Client")
        s.add_all([hh, foreign_hh])
        await s.flush()
        person = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Chen", kyc={}, profile={})
        foreign_person = Person(id=uuid.uuid4(), firm_id=other.id, household_id=foreign_hh.id, full_name="Their", kyc={}, profile={})
        s.add_all([person, foreign_person])
        await s.flush()
        mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name="Chen Discretionary",
                          mandate_type="discretionary", is_active=True, model_portfolio_id=uuid.uuid4())
        foreign_mandate = Mandate(id=uuid.uuid4(), firm_id=other.id, person_id=foreign_person.id, name="Their Mandate",
                                  mandate_type="discretionary", is_active=True, model_portfolio_id=uuid.uuid4())
        s.add_all([mandate, foreign_mandate])
        # The firm has put both agents at Tier 3 - the case where one confirmation could mean execution.
        for k in (drift_key, nba_key):
            s.add(AutonomyPolicy(firm_id=firm.id, agent_key=k, mandate_type=None, tier=AutonomyTier.TIER_3, guardrails={}))
        await s.commit()

        async def new_thread(u):
            t = Thread(firm_id=u.firm_id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=u.id, title="t")
            s.add(t)
            await s.commit()
            return t

        async def ask(u, thread, tool_name, inputs, then="Done."):
            """Play a model turn that calls the tool, as `u`; returns the stream's events."""
            gw_mod.llm_service.stream = script(tool_round(tool_name, inputs), say(then))
            return [e async for e in Gateway(s, u.id, u.role, u.firm_id).send_message(thread.id, "please")]

        def gateway(u):
            return Gateway(s, u.id, u.role, u.firm_id)

        async def count(model, *where):
            return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

        async def pending_of(thread):
            return (await s.execute(select(PendingAction).where(PendingAction.thread_id == thread.id)
                                    .order_by(PendingAction.created_at.desc()))).scalars().first()

        # ── the confirmation card ───────────────────────────────────────────────────────────────
        print("\n=== asking to run an agent pauses on a confirmation that shows the three controls ===")
        t1 = await new_thread(adviser)
        events = await ask(adviser, t1, "run_agent", {"agent_key": "drift_rebalancing", "household_id": str(hh.id)})
        pend = next((e for e in events if e["type"] == "pending_action"), None)
        check("the turn paused", pend is not None, True)
        pv = pend["preview"]
        check("it names the agent", pv["agent_name"], "Drift & Tax-Managed Rebalancing")
        check("it says what the agent will do", bool(pv["does"]), True)
        check("a household resolves to its managed mandate", pv["subject_label"], "Chen Discretionary")
        check("it shows the tier it will resolve to", pv["autonomy"]["tier"], "tier_3")
        check("the tier is labelled", pv["autonomy"]["label"], "Tier 3 · bounded autonomy")
        check("but says it will be held to a proposal", pv["autonomy"]["held_to_proposal"], True)
        check("and will not act on its own", pv["autonomy"]["acts_on_its_own"], False)
        check("confirming is said to start only", pv["confirms"], "start_only")
        check("decision rights are separate: adviser and portfolio team",
              pv["decision"]["roles"], ["adviser", "portfolio_team"])
        check("the adviser may decide", pv["decision"]["you_may_decide"], True)
        check("nothing ran before the confirmation", await count(AgentRun), 0)
        check("the pause is held on the server", (await pending_of(t1)).status, PendingActionStatus.AWAITING_CONFIRMATION)

        print("\n=== a refusal never becomes a confirmation ===")
        async def refused(u, inputs, why, agent="drift_rebalancing"):
            t = await new_thread(u)
            before = await count(PendingAction)
            ev = await ask(u, t, "run_agent", {"agent_key": agent, **inputs})
            tc = (await s.execute(select(ToolCall).join(ThreadMessage, ToolCall.message_id == ThreadMessage.id)
                                  .where(ThreadMessage.thread_id == t.id))).scalars().first()
            check(f"{why}: no card", [e for e in ev if e["type"] == "pending_action"], [])
            check(f"{why}: no pause row", await count(PendingAction), before)
            check(f"{why}: the tool failed with a reason", (tc.status, bool(tc.error)), (ToolCallStatus.FAILED, True))
            return tc
        tc = await refused(adviser, {"household_id": str(foreign_hh.id)}, "another firm's household")
        check("  and it reads as not found", "not found" in tc.error, True)
        tc = await refused(adviser, {"mandate_id": str(foreign_mandate.id)}, "another firm's mandate")
        check("  and it reads as not found", "not found" in tc.error, True)
        await refused(adviser, {}, "no subject for a mandate agent")
        await refused(adviser, {"household_id": str(hh.id)}, "an onboarding-case agent", agent="cip_identity_verifier")
        tc = await refused(adviser, {}, "billing, which has its own tool", agent="billing")
        check("  and points to the billing tool", "billing run" in tc.error, True)
        tc = await refused(adviser, {}, "a role that may not start it", agent="conduct_surveillance")
        check("  and says so", tc.error, "Your role may not start Conduct Surveillance.")
        await refused(adviser, {}, "an agent that does not exist", agent="no_such_agent")
        # A role the tool is not granted to at all is turned back by the gateway before any of this.
        t = await new_thread(admin)
        ev = await ask(admin, t, "run_agent", {"agent_key": "next_best_action"})
        check("an administrator cannot start an agent", [e["type"] for e in ev][:1], ["error"])

        cfg = AgentConfig(firm_id=firm.id, agent_key=nba_key, enabled=True, default_tier=AutonomyTier.TIER_1,
                          paused=True, paused_reason="Auto-paused by conduct surveillance")
        s.add(cfg)
        await s.commit()
        tc = await refused(adviser, {}, "a paused agent", agent="next_best_action")
        check("  the kill switch is honoured and explained", "paused" in tc.error and "conduct surveillance" in tc.error, True)
        check("nothing ran", await count(AgentRun), 0)
        cfg.paused, cfg.paused_reason = False, None
        await s.commit()

        # ── confirming ──────────────────────────────────────────────────────────────────────────
        print("\n=== a confirmation belongs to the person and firm it was put to ===")
        p1 = await pending_of(t1)
        for who, label in ((paraplanner, "a colleague"), (outsider, "another firm")):
            try:
                await gateway(who).confirm_pending_action(p1.id, True)
                check(f"{label} cannot confirm it", "confirmed", "refused")
            except GatewayError:
                check(f"{label} cannot confirm it", "refused", "refused")
        check("and it is still waiting", (await pending_of(t1)).status, PendingActionStatus.AWAITING_CONFIRMATION)

        print("\n=== the tier is checked against what the user was shown ===")
        try:
            await gateway(adviser).confirm_pending_action(p1.id, True, seen_tier="tier_1")
            check("a changed tier stops the run", "started", "refused")
        except GatewayError as exc:
            check("a changed tier stops the run", "changed from tier_1 to tier_3" in str(exc), True)
        check("and nothing ran", await count(AgentRun), 0)
        check("and it is still waiting", (await pending_of(t1)).status, PendingActionStatus.AWAITING_CONFIRMATION)

        print("\n=== confirming starts the run and approves nothing ===")
        seen_while_running = {}

        async def look():
            async with Session() as peek:
                m = (await peek.execute(select(ThreadMessage).where(
                    ThreadMessage.thread_id == t1.id, ThreadMessage.card_kind == narration.CARD_KIND))).scalars().first()
                seen_while_running["card"] = m.card_data if m else None
                seen_while_running["text"] = m.text if m else None
        drift.on_think = look
        result = await gateway(adviser).confirm_pending_action(p1.id, True, seen_tier="tier_3")
        drift.on_think = None
        check("it was confirmed", result["status"], "confirmed")
        check("while it ran, its card was already on the thread", (seen_while_running["card"] or {}).get("status"), "running")
        check("  naming the agent and the tier", ((seen_while_running["card"] or {}).get("agent_name"),
                                                  (seen_while_running["card"] or {}).get("tier")),
              ("Drift & Tax-Managed Rebalancing", "tier_3"))
        check("  with no words yet - the sentence is the agent's, at the end", seen_while_running["text"], "")
        check("one run was started", await count(AgentRun), 1)
        run = (await s.execute(select(AgentRun))).scalars().one()
        recs = (await s.execute(select(Recommendation))).scalars().all()
        check("it put forward one proposal", len(recs), 1)
        check("held for a human at Tier 3 because it moves the book", recs[0].status, RecommendationStatus.PROPOSED)
        check("the agent's act() was never called", drift.acted, 0)
        check("the run is awaiting approval, not completed", str(run.status), "awaiting_approval")
        check("the tier recorded is the firm's, unchanged", str(run.tier), "tier_3")
        check("no decision was written for anyone", recs[0].decided_by, None)
        msg = (await s.execute(select(ThreadMessage).where(ThreadMessage.thread_id == t1.id,
                                                           ThreadMessage.card_kind == narration.CARD_KIND))).scalars().one()
        check("the agent that ran it speaks", (msg.role, msg.speaking_agent), (MessageRole.AGENT, "drift_rebalancing"))
        check("its card settled as proposed", msg.card_data["status"], "proposed")
        check("with the beats of the run", [b["phase"] for b in msg.card_data["beats"]], ["sense", "think", "check"])
        check("every beat done", {b["state"] for b in msg.card_data["beats"]}, {"done"})
        check("naming the proposal", [p["title"] for p in msg.card_data["proposals"]], ["Rebalance Chen Family"])
        check("the sentence says who decides", "adviser or the portfolio team" in msg.text, True)
        check("and that nothing has been traded", "nothing has been traded or billed" in msg.text, True)
        check("the rationale is a message, not a card field", "weights have drifted" in msg.text, True)
        check("the card carries no prose", "rationale" in msg.card_data or "message" in msg.card_data, False)
        tcall = await s.get(ToolCall, p1.tool_call_id)
        check("the tool call is done and is a Source", tcall.status, ToolCallStatus.DONE)
        check("the confirmation is spent", (await pending_of(t1)).status, PendingActionStatus.CONFIRMED)

        print("\n=== exactly once ===")
        try:
            await gateway(adviser).confirm_pending_action(p1.id, True)
            check("a second confirmation is refused", "ran again", "refused")
        except GatewayError as exc:
            check("a second confirmation is refused", "already confirmed" in str(exc), True)
        check("still one run", await count(AgentRun), 1)
        check("still one proposal", await count(Recommendation), 1)

        print("\n=== the thread serves the card, and an unsettled run goes stale honestly ===")
        from app.api import threads as T
        served = await T.get_thread(t1.id, user=adviser, session=s)
        card_msgs = [m for m in served["messages"] if m["card"]]
        check("the message carries a card", [m["card"]["kind"] for m in card_msgs], ["agent_run"])
        check("the run is a Source on the confirming message", [src for m in served["messages"] for src in m["sources"]], ["run_agent"])
        old = {"status": "running", "started_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()}
        check("a run begun 30 minutes ago and never settled", narration.as_served(old)["status"], "interrupted")
        fresh = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
        check("a run just begun is running", narration.as_served(fresh)["status"], "running")

        print("\n=== Not now changes nothing ===")
        t2 = await new_thread(adviser)
        await ask(adviser, t2, "run_agent", {"agent_key": "drift_rebalancing", "mandate_id": str(mandate.id)})
        p2 = await pending_of(t2)
        out = await gateway(adviser).confirm_pending_action(p2.id, False)
        check("declined", out["status"], "rejected")
        check("no new run", await count(AgentRun), 1)

        print("\n=== the confirmation shows the preview on the thread ===")
        t3 = await new_thread(adviser)
        await ask(adviser, t3, "run_agent", {"agent_key": "next_best_action"})
        served = await T.get_thread(t3.id, user=adviser, session=s)
        pa = next(m["pending_action"] for m in served["messages"] if m["pending_action"])
        check("the pending action carries its preview", pa["preview"]["autonomy"]["tier"], "tier_3")
        check("firm-wide when no client is named", pa["preview"]["subject_label"], "Mine")
        check("a Tier 3 advisory agent says it will act on its own", pa["preview"]["autonomy"]["acts_on_its_own"], True)
        check("any staff may decide its proposals", pa["preview"]["decision"]["any_staff"], True)

        print("\n=== a Tier 3 agent that moves no money may carry out its proposal, and says so ===")
        await gateway(adviser).confirm_pending_action((await pending_of(t3)).id, True, seen_tier="tier_3")
        check("its act() ran once", nba.acted, 1)
        m3 = (await s.execute(select(ThreadMessage).where(ThreadMessage.thread_id == t3.id,
                                                          ThreadMessage.card_kind == narration.CARD_KIND))).scalars().one()
        check("its card settled as acted", m3.card_data["status"], "acted")
        check("the sentence says it was carried out at Tier 3", "carried out on its own at Tier 3" in m3.text, True)

        print("\n=== the kill switch is read again at confirmation ===")
        t4 = await new_thread(adviser)
        await ask(adviser, t4, "run_agent", {"agent_key": "next_best_action"})
        cfg.paused, cfg.paused_reason = True, "Paused by compliance"
        await s.commit()
        runs_before = await count(AgentRun)
        try:
            await gateway(adviser).confirm_pending_action((await pending_of(t4)).id, True)
            check("a pause since the card was shown stops the run", "started", "refused")
        except GatewayError as exc:
            check("a pause since the card was shown stops the run", "paused" in str(exc), True)
        check("no run was started", await count(AgentRun), runs_before)
        check("the confirmation is still open to decline", (await pending_of(t4)).status,
              PendingActionStatus.AWAITING_CONFIRMATION)
        cfg.paused, cfg.paused_reason = False, None
        await s.commit()

        print("\n=== a failed run is narrated, closed, and not silently retried ===")
        t5 = await new_thread(adviser)
        await ask(adviser, t5, "run_agent", {"agent_key": "drift_rebalancing", "mandate_id": str(mandate.id)})
        p5 = await pending_of(t5)
        drift.boom = True
        out = await gateway(adviser).confirm_pending_action(p5.id, True)
        drift.boom = False
        check("the confirmation reports failure", out["status"], "failed")
        m5 = (await s.execute(select(ThreadMessage).where(ThreadMessage.thread_id == t5.id,
                                                          ThreadMessage.card_kind == narration.CARD_KIND))).scalars().one()
        check("the card says failed", m5.card_data["status"], "failed")
        check("with the reason", "model is unavailable" in m5.card_data["error"], True)
        check("and the agent says so in words", m5.text.startswith("Drift & Tax-Managed Rebalancing could not complete"), True)
        failed_run = (await s.execute(select(AgentRun).order_by(AgentRun.created_at.desc()))).scalars().first()
        check("the run row is closed as failed, not left sensing", str(failed_run.status), "failed")
        check("the tool call is failed", (await s.get(ToolCall, p5.tool_call_id)).status, ToolCallStatus.FAILED)
        try:
            await gateway(adviser).confirm_pending_action(p5.id, True)
            check("it is not retried by a second click", "ran again", "refused")
        except GatewayError:
            check("it is not retried by a second click", "refused", "refused")

        print("\n=== a confirmation survives its request being cancelled ===")
        T.SessionLocal = Session
        t6 = await new_thread(adviser)
        await ask(adviser, t6, "run_agent", {"agent_key": "drift_rebalancing", "mandate_id": str(mandate.id)})
        p6 = await pending_of(t6)
        runs_before = await count(AgentRun)
        drift.slow = 0.4
        request = asyncio.ensure_future(T.confirm_pending_action(
            p6.id, {"confirmed": True, "seen_tier": "tier_3"}, user=adviser, session=s))
        await asyncio.sleep(0.15)
        request.cancel()                                  # the tab is closed mid-run
        try:
            await request
        except asyncio.CancelledError:
            pass
        for _ in range(60):                               # the server finishes without it
            await asyncio.sleep(0.25)
            async with Session() as peek:
                m = (await peek.execute(select(ThreadMessage).where(
                    ThreadMessage.thread_id == t6.id, ThreadMessage.card_kind == narration.CARD_KIND))).scalars().first()
            if m is not None and m.card_data["status"] != "running":
                break
        drift.slow = 0.0
        async with Session() as peek:                     # a fresh look, as the reloaded page would take
            m6 = (await peek.execute(select(ThreadMessage).where(
                ThreadMessage.thread_id == t6.id, ThreadMessage.card_kind == narration.CARD_KIND))).scalars().one()
            runs_now = (await peek.execute(select(func.count()).select_from(AgentRun))).scalar_one()
        check("the run still finished", runs_now, runs_before + 1)
        check("and settled on the thread", m6.card_data["status"], "proposed")
        check("with the agent's sentence", bool(m6.text), True)

        print("\n=== reads: the roster tells a role what it may start ===")
        from app.llm.executors import ToolExecutors
        ex = ToolExecutors(s, adviser.id, R.ADVISER, firm.id)
        roster = await ex.execute("read_agents", {})
        by_key = {a["agent_key"]: a for a in roster["agents"]}
        check("drift is startable by an adviser", by_key["drift_rebalancing"]["can_run"], True)
        check("billing is not, and says why", "billing has its own tool" in by_key["billing"]["why_not"], True)
        check("conduct surveillance is not for an adviser", by_key["conduct_surveillance"]["why_not"],
              "your role does not start this agent")
        only = await ex.execute("read_agents", {"runnable_only": "true"})
        check("runnable_only lists exactly what the role may run",
              sorted(a["agent_key"] for a in only["agents"]), sorted(k.value for k in agent_runs.runnable_agents(R.ADVISER)))
        other_ex = ToolExecutors(s, outsider.id, R.ADVISER, other.id)
        check("another firm sees its own roster, not this one's runs",
              sum(a["runs"] for a in (await other_ex.execute("read_agents", {}))["agents"]), 0)
        try:
            await other_ex.execute("run_agent", {"agent_key": "drift_rebalancing", "mandate_id": str(mandate.id)})
            check("another firm cannot run an agent on this firm's mandate", "ran", "refused")
        except Exception as exc:
            check("another firm cannot run an agent on this firm's mandate", "not found" in str(exc), True)

    # The decision-rights table still holds for a run's proposals: starting is not deciding.
    print("\n=== starting is not deciding ===")
    for role in (R.ADVISER, R.PARAPLANNER, R.PORTFOLIO_TEAM, R.BRANCH_LEADER):
        check(f"{role.value} may start drift", agent_runs.may_run(role, drift_key), True)
    check("paraplanner may start drift but may not approve its trade",
          R.PARAPLANNER in decision_rights.roles_for(drift_key, "approve"), False)
    check("branch leader may start drift but may not approve its trade",
          R.BRANCH_LEADER in decision_rights.roles_for(drift_key, "approve"), False)


def main() -> int:
    asyncio.run(main_async())
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
