"""Prove a new product is offered only to clients it was made for, on facts on file, and that an adviser decides
every offer (docs/03_Architecture/product-distribution.md, B-226 to B-231).

  - the research team adds a product and records its target market from a document; a complex or conflicted
    product waits for compliance's sign-off, from someone other than the recorder
  - the fit check: gates refuse with their rule, a missing fact is "needs a fact first" and never a no, signals
    order those who fit, a household under monitoring is left off with no reason an adviser can read
  - a buy into the negative target market is refused for the rebalancer and a direct order alike; outside it
    otherwise it goes ahead, named
  - Product Match: one list per adviser per product, refreshed not stacked, forced to a proposal; only the adviser
    it names or a branch leader approves; approving gives tasks and pitches, removing keeps the reason, rolling
    back withdraws; a closed product's list expires
  - pitches: outcomes need reasons, a decline keeps the product away, a message carries the document and the
    disclosure, and the distribution report counts what happened

Runs against a throwaway SQLite database.

    python -m tests.verify_product_distribution
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.atlas import runtime
from app.atlas.base import Subject
from app.aurea_core import firm_time, product_distribution as pd, product_fit, shelf
from app.compliance import rules
from app.core import agent_runs, decision_rights
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.aml_monitoring import AmlInvestigation
from app.models.engagement import Task
from app.models.enums import AgentKey, HumanAction, RecommendationStatus, UserRole
from app.models.governance import Recommendation
from app.models.graph import Account, Household, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.product_distribution import ProductPitch
from app.models.tenant import Firm

PASS, FAIL = [], []
R = UserRole


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, pd.DistributionError, decision_rights.DecisionForbidden) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:140]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)

            def user(name, role, f=firm):
                return User(id=uuid.uuid4(), firm_id=f.id, email=f"{name.split()[0].lower()}@t", full_name=name, role=role, hashed_password="x")
            sophie, ben = user("Sophie Tran", R.ADVISER), user("Ben Hall", R.ADVISER)
            david, rita, bea = user("David Okafor", R.COMPLIANCE), user("Rita Shah", R.RESEARCH_CIO), user("Bea Lead", R.BRANCH_LEADER)
            outsider = user("Out Sider", R.RESEARCH_CIO, other)
            s.add_all([sophie, ben, david, rita, bea, outsider])
            await s.flush()

            async def client(name, adviser, *, profile=None, capacity=None, horizon=None, born=None, cash=5000.0):
                h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
                s.add(h)
                await s.flush()
                p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=f"{name.split()[1]} Lead", date_of_birth=born)
                s.add(p)
                await s.flush()
                suit = {k: v for k, v in (("risk_profile", profile), ("capacity_for_loss", capacity), ("time_horizon_years", horizon)) if v is not None}
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=f"{name} mandate", mandate_type="advisory", suitability=suit, constraints={})
                s.add(m)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} A/C", currency="NZD", cash_balance=cash)
                s.add(a)
                s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id, to_type="household", to_id=h.id))
                await s.flush()
                return h, m, a

            hartley, hartley_m, hartley_a = await client("The Hartley", sophie, profile="balanced", capacity="medium", horizon=10, born=date(1958, 3, 1), cash=150000)
            chen, chen_m, _ = await client("The Chen", sophie, profile="conservative", capacity="low", horizon=15, born=date(1970, 1, 1))
            okafor, _, _ = await client("The Okafor", sophie, born=date(1980, 1, 1))
            wu, _, _ = await client("The Wu", sophie, profile="growth", capacity="high", horizon=20, born=date(1975, 1, 1))
            elder, _, _ = await client("The Elder", sophie, profile="balanced", capacity="medium", horizon=10, born=date(1940, 1, 1))
            ngata, _, _ = await client("The Ngata", ben, profile="growth", capacity="high", born=date(1985, 1, 1))
            patel, patel_m, _ = await client("The Patel", ben, profile="growth", capacity="high", horizon=2, born=date(1990, 1, 1))
            s.add(AmlInvestigation(firm_id=firm.id, household_id=wu.id, detected_on=today, opened_by="david@t"))
            await s.flush()

            async def priced(symbol, name, cls="multi_asset", price=10.0):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=symbol, name=name, asset_class=cls, currency="NZD")
                s.add(i)
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today, close=price, currency="NZD", source="test", is_real=True))
                await s.flush()
                return i

            dear = await priced("INFRR", "Pathfinder Infrastructure Retail")
            s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=hartley_a.id, instrument_id=dear.id, quantity=1000, market_value=10000, cost_basis=9000))
            await s.flush()

            research, compliance, sophie_ex = (ToolExecutors(s, rita.id, rita.role, firm.id), ToolExecutors(s, david.id, david.role, firm.id),
                                               ToolExecutors(s, sophie.id, sophie.role, firm.id))
            ben_ex, bea_ex = ToolExecutors(s, ben.id, ben.role, firm.id), ToolExecutors(s, bea.id, bea.role, firm.id)

            async def card(executor, tool, inputs):
                return {r["label"]: r["value"] for r in (await executor.preview_change(tool, inputs))["rows"]}

            print("\n=== Adding the product ===")
            check("research and the portfolio team add a product", TOOLS["add_product"].roles_required, {R.RESEARCH_CIO, R.PORTFOLIO_TEAM})
            check("  behind a Confirm card", TOOLS["add_product"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("an adviser adds none, records no target market and signs nothing off",
                  [k in tools_for_role(R.ADVISER) for k in ("add_product", "record_target_market", "approve_distribution")], [False, False, False])
            add = {"symbol": "INFRA", "name": "Pathfinder Infrastructure Wholesale", "asset_class": "multi_asset"}
            shown = await card(research, "add_product", add)
            check("the card names it and says it is offered to nobody yet", (shown["Product"], shown["Offered"].startswith("To nobody")),
                  ("INFRA · Pathfinder Infrastructure Wholesale", True))
            await research.add_product(add)
            await refused("the same symbol twice", research.preview_change("add_product", add), "already a product")
            await refused("an asset class that is not one", research.preview_change("add_product", {**add, "symbol": "X1", "asset_class": "gold"}), "asset class")
            infra = await pd.instrument(s, firm.id, "INFRA")
            check("a product is found by its name as a person says it", (await pd.instrument(s, firm.id, "the Pathfinder Infrastructure Wholesale fund")).symbol, "INFRA")
            await refused("a name two products share is not guessed", pd.instrument(s, firm.id, "Pathfinder Infrastructure"), "more than one", "INFRR")
            s.add(Price(firm_id=firm.id, instrument_id=infra.id, as_of=today, close=10.0, currency="NZD", source="test", is_real=True))
            await s.flush()

            print("\n=== Its target market ===")
            market = {"symbol": "INFRA", "document": "Pathfinder Infrastructure PDS, 1 Oct 2026", "objectives": ["growth", "income"],
                      "risk_profiles": ["balanced", "growth"], "min_capacity": "medium", "min_horizon_years": 5, "min_investment": 10000,
                      "complexity": "complex", "negative_market": ["low_capacity", "aged_over_75"], "markets": ["NZ"],
                      "conflict": "proprietary", "disclosure": "Pathfinder is managed by a company in the same group as this firm, which earns its management fee."}
            shown = await card(research, "record_target_market", market)
            check("the card reads the terms back", (shown["Version"], shown["Risk profiles"], shown["Minimum"], shown["Not for"]),
                  ("1", "balanced, growth", "$10,000", "low capacity, aged over 75"))
            check("  and says compliance signs it off first", shown["Sign-off"], "Compliance signs it off before it is offered")
            await refused("no document", research.preview_change("record_target_market", {**market, "document": ""}), "name the document")
            await refused("a profile that is not one", research.preview_change("record_target_market", {**market, "risk_profiles": ["bold"]}), "risk profiles")
            await refused("conflicted with no disclosure", research.preview_change("record_target_market", {**market, "disclosure": ""}), "disclosure")
            await refused("locked with no lock-in", research.preview_change("record_target_market", {**market, "liquidity": "locked"}), "lock-in")
            await refused("a negative market not read from a fact", research.preview_change("record_target_market", {**market, "negative_market": ["nervous"]}), "on file")
            await refused("an unknown market", research.preview_change("record_target_market", {**market, "markets": ["ZZ"]}), "no market")
            await refused("another firm's product", ToolExecutors(s, outsider.id, outsider.role, other.id).preview_change("record_target_market", market), "no product")
            await research.record_target_market(market)

            print("\n=== Offerable only when everything is on file ===")
            state = await pd.offerable(s, firm, infra)
            check("no shelf decision, not offered", (state["offerable"], state["why_not"]), (False, "the research team has made no shelf decision on it"))
            await research.record_product_decision({"symbol": "INFRA", "status": "approved", "rationale": "Due diligence passed", "document": "DD memo 12",
                                                    "family": "Pathfinder Infrastructure", "expense_ratio_pct": 0.45})
            await research.record_product_decision({"symbol": "INFRR", "status": "approved", "rationale": "Legacy retail class", "document": "DD memo 12",
                                                    "family": "Pathfinder Infrastructure", "expense_ratio_pct": 1.2})
            check("approved, but complex and conflicted: waits for compliance", (await pd.offerable(s, firm, infra))["why_not"],
                  "compliance has not signed off its distribution")
            await refused("the recorder may not sign it off", ToolExecutors(s, rita.id, R.COMPLIANCE, firm.id).preview_change(
                "approve_distribution", {"symbol": "INFRA", "reason": "Reviewed the PDS and the conflict"}), "recorded the target market")
            await refused("no reason", compliance.preview_change("approve_distribution", {"symbol": "INFRA", "reason": "ok"}), "reason")
            shown = await card(compliance, "approve_distribution", {"symbol": "INFRA", "reason": "Reviewed the PDS and the conflict"})
            check("the sign-off card names the version and the recorder", (shown["Target market"].startswith("Version 1"), shown["Recorded by"]), (True, "rita@t"))
            await compliance.approve_distribution({"symbol": "INFRA", "reason": "Reviewed the PDS and the conflict"})
            check("now it may be offered", (await pd.offerable(s, firm, infra))["offerable"], True)
            await refused("signed off once per version", compliance.preview_change("approve_distribution", {"symbol": "INFRA", "reason": "Reviewed it again"}), "already signed off")
            nowhere = Firm(id=uuid.uuid4(), name="Z", slug="z", base_currency="NZD", jurisdiction="ZZ")
            check("a firm with no market's rules offers nothing", (await pd.offerable(s, nowhere, infra))["why_not"],
                  "the firm has no market's rules here, so nothing is offered")

            print("\n=== The fit check ===")
            mine = await sophie_ex.read_product_fit({"symbol": "INFRA"})
            names = {k: [r["household"] for r in mine[k]] for k in ("fits", "needs_fact", "not_for")}
            check("an adviser reads their own clients", sorted(sum(names.values(), [])), sorted(["The Hartley", "The Chen", "The Okafor", "The Elder"]))
            check("the Hartleys fit", names["fits"], ["The Hartley"])
            hartley_row = mine["fits"][0]
            check("  with the dearer share class they hold, and idle cash, as signals", sorted(x["kind"] for x in hartley_row["signals"]), ["dearer_class", "idle_cash"])
            check("  the order is points per signal", hartley_row["points"], 6)
            check("no risk profile on file is a fact to record, never a no", names["needs_fact"], ["The Okafor"])
            chen_row = next(r for r in mine["not_for"] if r["household"] == "The Chen")
            check("a conservative profile outside the target market is not for them, with the rule",
                  [l["criterion"] for l in chen_row["lines"] if l["result"] == "gate"], ["risk_profile", "capacity", "negative_market"])
            elder_row = next(r for r in mine["not_for"] if r["household"] == "The Elder")
            check("over 75 is its negative target market, read from the date of birth",
                  [l["said"] for l in elder_row["lines"] if l["criterion"] == "negative_market"][0].startswith("it is not for someone over 75"), True)
            check("a household under monitoring is left off, and the adviser is not told how many", ("withheld" in mine, "The Wu" in str(mine)), (False, False))
            check("  compliance is told how many", (await compliance.read_product_fit({"symbol": "INFRA"}))["withheld"], 1)
            ben_view = await ben_ex.read_product_fit({"symbol": "INFRA"})
            check("another adviser reads theirs: a missing horizon needs a fact; a short one is not for them",
                  ([r["household"] for r in ben_view["needs_fact"]], [r["household"] for r in ben_view["not_for"]]), (["The Ngata"], ["The Patel"]))
            one = await sophie_ex.read_product_fit({"symbol": "INFRA", "household_id": str(chen.id)})
            check("why not one client", (one["scope"], len(one["not_for"])), ("The Chen", 1))
            await refused("another firm reads nothing of it", ToolExecutors(s, outsider.id, outsider.role, other.id).read_product_fit({"symbol": "INFRA"}), "no product")
            check("the order never reads a fee", set(product_fit.POINTS), {"idle_cash", "goal_gap", "dearer_class", "concentration", "review_due"})

            print("\n=== Buying it ===")
            check("the shelf bars it for a client in its negative target market", (await shelf.barred(s, firm, chen_m)).get(infra.id, "").startswith("in its negative target market"), True)
            check("  and not for one it fits", infra.id in await shelf.barred(s, firm, hartley_m), False)

            def buy(mandate):
                return {"mandate_id": str(mandate.id), "reason": "Client asked", "orders": [{"symbol": "INFRA", "side": "buy", "quantity": 10}]}
            await refused("a direct order into the negative target market", sophie_ex.preview_change("execute_orders", buy(chen_m)), "negative target market")
            shown = await card(ben_ex, "execute_orders", buy(patel_m))
            check("outside the target market otherwise, it goes ahead and is named", "horizon" in shown.get("Outside its target market", ""), True)
            check("  inside it, nothing is named", "Outside its target market" in await card(sophie_ex, "execute_orders", buy(hartley_m)), False)

            print("\n=== Product Match ===")
            check("it stops at a proposal whatever its tier", agent_runs.forced_to_proposal(AgentKey.PRODUCT_MATCH), True)
            check("an adviser or a branch leader approves; compliance may only stop it",
                  (sorted(r.value for r in decision_rights.roles_for(AgentKey.PRODUCT_MATCH, "approve")),
                   R.COMPLIANCE in decision_rights.roles_for(AgentKey.PRODUCT_MATCH, "dismiss")), (["adviser", "branch_leader"], True))
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.PRODUCT_MATCH, subject=Subject("firm", firm.id, firm.name), trigger="schedule")
            lists = (await s.execute(select(Recommendation).where(Recommendation.agent_key == AgentKey.PRODUCT_MATCH.value,
                                                                  Recommendation.status == RecommendationStatus.PROPOSED))).scalars().all()
            check("one list, for the adviser whose client fits", [(r.payload["adviser"], [f["household"] for f in r.payload["fits"]]) for r in lists],
                  [("Sophie Tran", ["The Hartley"])])
            check("  and it carries the target market it read", (lists[0].payload["target_market_version"], lists[0].subject_type), (1, "instrument"))
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.PRODUCT_MATCH, subject=Subject("firm", firm.id, firm.name), trigger="schedule")
            again = (await s.execute(select(Recommendation).where(Recommendation.agent_key == AgentKey.PRODUCT_MATCH.value,
                                                                  Recommendation.status == RecommendationStatus.PROPOSED))).scalars().all()
            check("a second run refreshes the list rather than stacking another", len(again), 1)
            rec = again[0]
            approve = {"recommendation_id": str(rec.id), "action": "approve"}
            await refused("another adviser may not approve it", ben_ex.preview_change("decide_recommendation", approve), "Sophie Tran's clients")
            await refused("compliance may not approve it", compliance.preview_change("decide_recommendation", approve), "only the adviser it names")
            check("a branch leader may", "Decision" in await card(bea_ex, "decide_recommendation", approve), True)
            check("the EU rule reads the list's target market", rules.target_market(rules.CheckContext(
                "product_match", "", "", 0.8, rec.payload, {}, [], None, {})).status, "pass")
            await sophie_ex.decide_recommendation(approve)
            tasks = (await s.execute(select(Task).where(Task.source == AgentKey.PRODUCT_MATCH.value))).scalars().all()
            check("approving gives the adviser a task per client who still fits",
                  [(t.title, t.assigned_to == sophie.id) for t in tasks], [("Discuss Pathfinder Infrastructure Wholesale with The Hartley", True)])
            pitch = (await pd.latest_pitches(s, firm.id, household_id=hartley.id))[(hartley.id, infra.id)]
            check("  and records the pitch with the fit it rested on", (pitch.status, pitch.fit["verdict"], pitch.fit["target_market_version"]), ("to_discuss", "fits", 1))
            after = await sophie_ex.read_product_fit({"symbol": "INFRA"})
            check("a client being offered it is not on the next list", ("The Hartley" in [r["household"] for r in after["fits"]],
                                                                       [l["said"] for r in after["not_for"] if r["household"] == "The Hartley" for l in r["lines"]][0].startswith("already being offered")), (False, True))

            print("\n=== The pitch ===")
            outcome = {"household_id": str(hartley.id), "symbol": "INFRA", "outcome": "declined"}
            await refused("a decline needs its reason", sophie_ex.preview_change("record_pitch_outcome", outcome), "say why")
            shown = await card(sophie_ex, "record_pitch_outcome", {**outcome, "reason": "Wants to keep it simple this year"})
            check("the card says what a decline does", shown["Effect"].startswith("Not offered again for 180 days"), True)
            await refused("no pitch open, no outcome", sophie_ex.preview_change("record_pitch_outcome", {**outcome, "household_id": str(chen.id), "reason": "No"}), "not being offered")
            await sophie_ex.record_pitch_outcome({**outcome, "reason": "Wants to keep it simple this year"})
            later = await sophie_ex.read_product_fit({"symbol": "INFRA", "household_id": str(hartley.id)})
            check("a decline keeps the product away", later["not_for"][0]["lines"][0]["said"].startswith("declined on"), True)
            message = {"household_id": str(hartley.id), "body": "Following our talk, here is a note on the Pathfinder Infrastructure Wholesale fund."}
            body = (await card(sophie_ex, "send_client_message", message))["Message"]
            check("a message naming the product carries its document and its disclosure",
                  ("Product document for Pathfinder Infrastructure Wholesale: Pathfinder Infrastructure PDS, 1 Oct 2026." in body, market["disclosure"] in body), (True, True))
            check("  and one that does not is left as written", (await card(sophie_ex, "send_client_message", {**message, "body": "Thanks for today."}))["Message"], "Thanks for today.")

            print("\n=== The distribution report ===")
            report = await compliance.read_distribution({"symbol": "INFRA"})
            check("who was offered it and what came of it", (report["offered"], report["by_status"]["declined"], report["by_adviser"]), (1, 1, {"Sophie Tran": 1}))
            check("  with no complaints register, said", report["complaints"], "No complaints register is kept here.")
            check("one client's pitches", (await sophie_ex.read_distribution({"household_id": str(hartley.id)}))["pitches"][0]["status"], "declined")

            print("\n=== Removing, rolling back and closing ===")
            for p in list((await s.execute(select(ProductPitch).where(ProductPitch.household_id == hartley.id))).scalars()):
                await s.delete(p)
            await s.flush()
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.PRODUCT_MATCH, subject=Subject("firm", firm.id, firm.name), trigger="schedule")
            fresh = (await s.execute(select(Recommendation).where(Recommendation.agent_key == AgentKey.PRODUCT_MATCH.value,
                                                                  Recommendation.status == RecommendationStatus.PROPOSED))).scalars().one()
            removed = {**fresh.payload, "removed": [{"household_id": str(hartley.id), "reason": "Moving to a simpler portfolio"}]}
            await runtime.decide(s, firm=firm, recommendation=fresh, action=HumanAction.MODIFY, actor_id=sophie.id, actor_label="Sophie Tran (adviser)",
                                 modified_payload=removed)
            kept = (await pd.latest_pitches(s, firm.id, household_id=hartley.id))[(hartley.id, infra.id)]
            check("removing a client keeps the adviser's reason, and gives no task", (kept.status, "simpler portfolio" in kept.reason, fresh.payload["execution_result"]["task_ids"]),
                  ("not_suitable", True, []))
            for p in list((await s.execute(select(ProductPitch).where(ProductPitch.household_id == hartley.id))).scalars()):
                await s.delete(p)
            await s.flush()
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.PRODUCT_MATCH, subject=Subject("firm", firm.id, firm.name), trigger="schedule")
            third = (await s.execute(select(Recommendation).where(Recommendation.agent_key == AgentKey.PRODUCT_MATCH.value,
                                                                  Recommendation.status == RecommendationStatus.PROPOSED))).scalars().one()
            await sophie_ex.decide_recommendation({"recommendation_id": str(third.id), "action": "approve"})
            await runtime.rollback(s, firm=firm, recommendation=third, actor_id=david.id, actor_label="David Okafor (compliance)")
            back = (await pd.latest_pitches(s, firm.id, household_id=hartley.id))[(hartley.id, infra.id)]
            task = await s.get(Task, uuid.UUID(third.payload["execution_result"]["task_ids"][0]))
            check("rolling back withdraws the open task and the pitch", (str(task.status), back.status), ("dismissed", "withdrawn"))
            await research.record_product_decision({"symbol": "INFRA", "status": "closed", "rationale": "Manager change under review", "document": "DD memo 13"})
            for p in list((await s.execute(select(ProductPitch).where(ProductPitch.household_id == hartley.id))).scalars()):
                await s.delete(p)
            await s.flush()
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.PRODUCT_MATCH, subject=Subject("firm", firm.id, firm.name), trigger="schedule")
            check("a closed product raises no list", (await s.execute(select(Recommendation).where(
                Recommendation.agent_key == AgentKey.PRODUCT_MATCH.value, Recommendation.status == RecommendationStatus.PROPOSED))).scalars().all(), [])

            print("\n=== India: a fee-only adviser ===")
            firm.settings = {"licences": ["investment_adviser"]}
            check("an investment-adviser firm is fee-only", pd.fee_only(firm), True)
            firm.settings = {"licences": ["investment_adviser", "mutual_fund_distributor"]}
            check("  not when it also distributes", pd.fee_only(firm), False)
            firm.settings = {}

            print("\n=== The rule reads the list ===")
            ctx = rules.CheckContext("product_match", "", "", 0.8, {"signal": "product_match"}, {}, [], None, {})
            check("a list that does not say what it read fails", rules.target_market(ctx).status, "fail")
            check("anything else is not assessed, not passed", rules.target_market(rules.CheckContext(
                "next_best_action", "", "", 0.8, {}, {}, [], "advisory", {})).status, "na")
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
