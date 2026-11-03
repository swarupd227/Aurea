"""Prove what the rebalancing, meeting prep and autonomy-policy demo rests on: a rebalance run says what it would do,
class by class, with its orders, gain against budget and cash; a meeting's agenda comes from the record; a policy's
guardrails are enforced, an unenforced key is shown as such, a change needs a reason, a licence refuses a tier above
its ceiling, and every change is on the audit log; and the run tool tells Astra to go straight to the run card.

Runs against a throwaway SQLite database.

    python -m tests.verify_demo_cards
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents.meeting_prep import MeetingPrepAgent
from app.atlas import narration
from app.aurea_core import autonomy
from app.core.db import Base
from app.core.tools import TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AgentKey, MandateType, UserRole
from app.models.identity import AuditEvent, User
from app.models.tenant import AutonomyPolicy, Firm
from app.provenance import policy

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, autonomy.AutonomyError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure() -> None:
    print("\n=== A rebalance says what it would do ===")
    payload = {
        "currency": "NZD", "summary": {"total_value": 1_000_000, "max_drift": 0.163, "drift_band": 0.05, "turnover": 160_000},
        "current_weights": {"equity": 0.663, "fixed_income": 0.175, "alternatives": 0.0, "cash": 0.101, "property": 0.061},
        "target_weights": {"equity": 0.5, "fixed_income": 0.3, "alternatives": 0.1, "property": 0.1},
        "order_set": [{"side": "sell", "symbol": "AAPL", "quantity": 400, "asset_class": "equity", "est_value": 80_000, "est_realised_gain": 12_000},
                      {"side": "buy", "symbol": "AGG", "quantity": 900, "asset_class": "fixed_income", "est_value": 90_000, "est_realised_gain": None}],
        "estimated_realised_gain": 12_000, "cgt_budget": 20_000, "harvested_losses": 0, "ips": None,
    }
    r = narration.rebalance_figures(payload)
    rows = {c["class"]: c for c in r["classes"]}
    check("each class now, target and after the orders", (rows["equity"]["now"], rows["equity"]["target"], rows["equity"]["after"]), (0.663, 0.5, 0.583))
    check("a buy lifts its class", rows["fixed_income"]["after"], 0.265)
    check("cash takes the difference", (r["cash"]["now"], r["cash"]["after"]), (101_000.0, 91_000.0))
    check("a class nothing trades keeps its weight", rows["alternatives"]["after"], 0.0)
    check("the orders, gain against budget and what it was measured against",
          (len(r["orders"]), r["gain"], r["budget"], r["measured_against"]), (2, 12_000, 20_000, "model"))
    check("no total value, no figures", narration.rebalance_figures({**payload, "summary": {}}), None)

    print("\n=== A meeting's agenda comes from the record ===")
    record = {"mandates": [{"mandate": "Trust — Balanced", "measured_against": "Core Balanced", "max_drift": 0.16, "band": 0.05, "outside": True},
                           {"mandate": "Growth", "measured_against": "IPS", "max_drift": None, "band": 0.05, "outside": True},
                           {"mandate": "Income", "measured_against": "model", "max_drift": 0.01, "band": 0.05, "outside": False}],
              "tax": {"flags": 6}, "review": {"unrecorded": {"held_on": "2026-09-18"}}}
    agenda = MeetingPrepAgent()._agenda([{"on_track": True}], [], [], record)
    check("a mandate outside its band, by how much", "Trust — Balanced: 16% from its Core Balanced, outside the 5% band" in agenda, True)
    check("one outside its IPS", "Growth: outside its IPS" in agenda, True)
    check("one within is not raised", any(a.startswith("Income") for a in agenda), False)
    check("tax items and the unrecorded meeting, with its weekday", ("Tax: 6 item(s) to check" in agenda,
          "Record whether the Friday 18 September 2026 meeting was a review" in agenda), (True, True))

    print("\n=== Guardrails are enforced ===")
    orders = {"order_set": [{"est_value": 150_000}, {"est_value": -140_000}]}
    check("an order set over the limit waits", "above the policy's limit" in (policy.held_by_guardrails({"max_trade_value": 250_000}, orders) or ""), True)
    check("one inside it does not", policy.held_by_guardrails({"max_trade_value": 300_000}, orders), None)
    check("compliance first holds every proposal", bool(policy.held_by_guardrails({"requires_compliance": True}, {})), True)
    check("a key nothing reads holds nothing", policy.held_by_guardrails({"requires_client_consent": True}, orders), None)

    print("\n=== Asked to act, Astra goes to the run card ===")
    d = TOOLS["run_agent"].description
    check("rebalance: do not analyse first or ask", ("drift_rebalancing" in d and "do not first analyse the drift yourself" in d), True)
    check("prepare: run meeting prep, do not assemble it", ("meeting_prep" in d and "do not assemble one yourself" in d), True)


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ", settings={})
            india = Firm(id=uuid.uuid4(), name="I", slug="i", base_currency="INR", jurisdiction="IN", settings={"licences": ["investment_adviser"]})
            s.add_all([firm, india])
            await s.flush()
            comp = User(id=uuid.uuid4(), firm_id=firm.id, email="c@t", full_name="Cy", role=UserRole.COMPLIANCE, hashed_password="x")
            boss = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="Ada", role=UserRole.ADMIN, hashed_password="x")
            iboss = User(id=uuid.uuid4(), firm_id=india.id, email="a@i", full_name="Ira", role=UserRole.ADMIN, hashed_password="x")
            s.add_all([comp, boss, iboss])
            s.add(AutonomyPolicy(firm_id=firm.id, agent_key=AgentKey.DRIFT_REBALANCING, mandate_type=MandateType.ADVISORY, tier="tier_2",
                                 guardrails={"requires_client_consent": True}, rationale="Seeded"))
            await s.flush()
            ex = ToolExecutors(s, boss.id, boss.role, firm.id)

            print("\n=== Reading ===")
            out = await ToolExecutors(s, comp.id, comp.role, firm.id).execute("read_autonomy_policies", {})
            g = out["policies"][0]["guardrails"][0]
            check("a stored key nothing enforces is shown as not enforced", (g["key"], g["enforced"]), ("requires_client_consent", False))

            print("\n=== Changing ===")
            ask = {"agent_key": "drift_rebalancing", "mandate_type": "discretionary", "tier": "tier_3",
                   "guardrails": '{"max_trade_value": 250000}', "reason": "Small rebalances may go ahead on their own"}
            await refused("a reason is needed", ex.preview_change("set_autonomy_policy", {**ask, "reason": ""}), "reason")
            await refused("an agent that does not exist", ex.preview_change("set_autonomy_policy", {**ask, "agent_key": "robo"}), "read_agents")
            await refused("a guardrail nothing enforces", ex.preview_change("set_autonomy_policy", {**ask, "guardrails": '{"max_daily_trades": 3}'}),
                          "not a guardrail")
            card = {r["label"]: r["value"] for r in (await ex.preview_change("set_autonomy_policy", ask))["rows"]}
            check("the card names the agent, mandates, before, after and guardrail",
                  (card["Agent"], card["Mandates"], card["Now"].startswith("No policy"), card["Becomes"].startswith("Tier 3"), card["Guardrails"]),
                  ("Drift & Tax-Managed Rebalancing", "discretionary", True, True, "Orders over $250,000 wait for a person"))
            check("and says a run a person starts still stops at a proposal", "Still stop at a proposal" in card.get("Runs a person starts", ""), True)
            await ex.execute("set_autonomy_policy", ask)
            row = (await s.execute(select(AutonomyPolicy).where(AutonomyPolicy.mandate_type == "discretionary"))).scalar_one()
            check("written as asked", (str(row.tier), row.guardrails), ("tier_3", {"max_trade_value": 250000.0}))
            await refused("setting what is already so", ex.preview_change("set_autonomy_policy", ask), "already")
            await ex.execute("set_autonomy_policy", {**ask, "tier": "default", "reason": "Back to supervised"})
            check("default removes it", (await s.execute(select(AutonomyPolicy).where(AutonomyPolicy.mandate_type == "discretionary"))).scalar_one_or_none(), None)
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "autonomy.policy_set"))).scalars().all()
            check("both changes on the audit log with their reasons", sorted(a.detail["reason"] for a in audit),
                  ["Back to supervised", "Small rebalances may go ahead on their own"])

            iex = ToolExecutors(s, iboss.id, iboss.role, india.id)
            await refused("a licence refuses a tier above its ceiling", iex.preview_change("set_autonomy_policy", ask), "licensed")
            check("only an admin holds the change; compliance reads it (B-96)",
                  sorted(str(r) for r in TOOLS["set_autonomy_policy"].roles_required), ["admin"])
            await refused("and compliance is refused even outside the gateway",
                          ToolExecutors(s, comp.id, comp.role, firm.id).execute("set_autonomy_policy", ask), "admin")
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
