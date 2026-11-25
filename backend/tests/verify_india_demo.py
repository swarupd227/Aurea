"""Prove the Indian demonstration firm works end to end, with no New Zealand in it.

Builds the firm from seed/india_demo.py, with invented prices and no network, then does what a demo
does: reads a family's tax, raises a proposal on each track, approves both, and records a client's
answer.

Runs against a throwaway SQLite database; the model and the research store are stubbed.

    python -m tests.verify_india_demo
"""
from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import _signals
from app.agents import drift_rebalancing as drift
from app.agents import tax_intelligence as tax_agent
from app.atlas import runtime, tax_refresh
from app.atlas.base import Subject
from app.aurea_core import disclosures, firm_time, licences, protection, tax_intelligence
from app.aurea_core.graph import household_brain
from app.compliance import ontology
from app.core.db import Base
from app.core.personas import INDIA_PERSONAS
from app.llm.executors import ToolExecutors
from app.models.enums import AgentKey, AutonomyTier, HumanAction, RecommendationStatus, UserRole
from app.models.governance import Recommendation
from app.models.governance import AgentRun
from app.models.graph import Goal, Household, LegalEntity, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Price
from app.models.tenant import AgentConfig
from app.models.trading import Order
from app.provenance import policy
from seed import india_demo, india_demo_backfill

PASS, FAIL = [], []
FOREIGN = ("KiwiSaver", "PIE", "bright-line", "NZD", "IRA", "Roth", "ISA ", "401(k)", "IRD ", "$")


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            async def _no_research(*a, **k):
                return []

            calls = []

            async def _fallback(ctx, *, fallback, **k):
                calls.append(k)
                return fallback

            drift.knowledge.retrieve = _no_research
            drift.narrate = _fallback
            tax_agent.narrate = _fallback

            firm = await india_demo.build(s)
            await s.commit()

            print("\n=== The firm ===")
            check("it is India's", (firm.jurisdiction, firm.base_currency, ontology.framework_for(firm).regime),
                  ("IN", "INR", "IN-SEBI"))
            check("marked as a demonstration", firm.settings["demonstration"], True)
            check("it holds both licences", [l.key for l in licences.held(firm)], ["investment_adviser", "pms_discretionary"])
            logins = (await s.execute(select(func.count()).select_from(User).where(User.firm_id == firm.id))).scalar_one()
            check("one sign-in per persona", logins, len(INDIA_PERSONAS))
            pte = (await s.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id,
                                                             AgentConfig.agent_key == AgentKey.ROLLOVER_PTE_DOCUMENTER))).scalar_one()
            check("the US rollover agent is switched off", pte.enabled, False)
            tier, _ = await policy.resolve_tier(s, firm.id, AgentKey.DRIFT_REBALANCING, None)
            check("no agent runs alone", tier in (AutonomyTier.TIER_1, AutonomyTier.TIER_2), True)
            invented = (await s.execute(select(func.count()).select_from(Price).where(
                Price.firm_id == firm.id, Price.is_real.is_(False)))).scalar_one()
            listed = [sym for sym, spec in india_demo.INSTRUMENTS.items() if not spec[4].get("amfi_code")]
            check("prices with no feed are marked as invented", invented, len(listed))
            check("the document set is India's", disclosures.required_for(firm.jurisdiction, "individual")[0], "in_advisory_agreement")

            print("\n=== A family's tax ===")
            sharma = (await s.execute(select(Household).where(Household.firm_id == firm.id,
                                                              Household.name == "The Sharma Family"))).scalar_one()
            r = await tax_intelligence.for_household(s, sharma.id, firm.jurisdiction, firm_id=firm.id)
            check("three taxpayers", sorted((t["name"], t["kind"]) for t in r["taxpayers"]),
                  [("Meera Sharma", "individual"), ("Rajesh Sharma", "individual"), ("Rajesh Sharma HUF", "huf")])
            rajesh = next(t for t in r["taxpayers"] if t["name"] == "Rajesh Sharma")
            check("Rajesh sold earlier this year", rajesh["realised"]["lt_equity"] > 0, True)
            check("his brought-forward loss is read", rajesh["brought_forward"]["long_term"], 40000)
            check("and he has a loss to harvest", any(o["kind"] == "loss" for o in rajesh["opportunities"]), True)
            meera = next(t for t in r["taxpayers"] if t["name"] == "Meera Sharma")
            check("Meera can use her exemption", any(o["kind"] == "exemption" for o in meera["opportunities"]), True)
            huf = next(t for t in r["taxpayers"] if t["kind"] == "huf")
            check("the HUF has a loss about to go long-term", any(o["kind"] == "turning_long" for o in huf["opportunities"]), True)
            text = json.dumps(r)
            check("nothing from another country in it", [w for w in FOREIGN if w in text], [])

            reliance = next(o for o in huf["opportunities"] if o["kind"] == "loss" and o["symbol"] == "RELIANCE.NS")
            check("a loss with no gain to meet carries forward", reliance["carries_forward"], -reliance["net_loss"])
            infosys = next(o for o in rajesh["opportunities"] if o["kind"] == "loss")
            check("one the year absorbs carries nothing forward", infosys["carries_forward"], 0)
            check("a saving is split by where it comes from",
                  round(r["summary"]["saving_from_losses"] + r["summary"]["saving_from_exemption"], 2),
                  r["summary"]["estimated_tax_saving"])

            print("\n=== Goals ===")
            on_track = {}
            for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all():
                brain = await household_brain(s, h.id, firm_id=firm.id)
                for g in _signals.goal_projections(brain, firm_time.today(firm)):
                    on_track[f"{h.name}: {g['name']}"] = g["on_track"]
            # India's suggested assumptions (row 5) move these: the check is the mix, not a count.
            met = sum(on_track.values())
            check("a book with some goals met and some to talk about", 0 < met < len(on_track), True)
            check("the client we sign in as has one of each", [v for k, v in sorted(on_track.items()) if "Sharma" in k],
                  [False, True])

            print("\n=== The client's own tools ===")
            # The client asks in the conversation (B-95); their tools read the Sharma household and no other.
            client = (await s.execute(select(User).where(User.email == "client@astra-india.demo"))).scalar_one()
            mine = await ToolExecutors(s, client.id, UserRole.CLIENT, firm.id).execute("read_my_wealth", {})
            brain = await household_brain(s, sharma.id, firm_id=firm.id)
            check("the client reads the Sharma household", mine["household_id"], str(sharma.id))
            check("in rupees", mine["currency"], "INR")
            check("each goal with its target and likelihood, as an estimate",
                  [(g["target_amount"], g["probability"], g["estimate"]) for g in mine["goals"]],
                  [(g["target_amount"], g["probability"], True) for g in _signals.goal_projections(brain, firm_time.today(firm))])

            print("\n=== Protection works from what is on file ===")
            view = await protection.for_household(s, sharma.id, firm_id=firm.id)
            check("retirement at the firm's 60", view["assumptions"]["retirement_age"], 60)

            print("\n=== Every mandate through the rebalancer ===")
            mandates = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()
            check("seven taxpayers and the Menons' NRO account hold mandates", len(mandates), 8)
            recs = {}
            for m in mandates:
                run = await runtime.run_agent(s, firm=firm, agent_key=AgentKey.DRIFT_REBALANCING,
                                              subject=Subject("mandate", m.id, m.name), trigger="seed_scan", require_human=True)
                await s.flush()
                rec = (await s.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().first()
                if rec:
                    recs[m.constraints["service_track"], m.name] = rec
            check("both tracks raised proposals", {k[0] for k in recs}, {"investment_adviser", "pms_discretionary"})
            for (track, name), rec in recs.items():
                words = " ".join([rec.title, rec.summary or "", rec.rationale or ""] + (rec.evidence.get("limitations") or []))
                foreign = [w for w in FOREIGN if w in words]
                check(f"{name}: all in rupees, and nothing foreign", foreign, [])
                check(f"{name}: whole shares", all(float(o["quantity"]).is_integer() for o in rec.payload["order_set"]), True)
                check(f"{name}: the licence is on it", rec.payload["licence"]["key"], track)

            print("\n=== The two tracks, side by side ===")
            ia = next(r for (t, _), r in recs.items() if t == "investment_adviser")
            pm = next(r for (t, _), r in recs.items() if t == "pms_discretionary")
            adviser = (await s.execute(select(User).where(User.email == "adviser@astra-india.demo"))).scalar_one()
            manager = (await s.execute(select(User).where(User.email == "portfolio@astra-india.demo"))).scalar_one()
            orders = lambda rec: s.execute(select(func.count()).select_from(Order).where(Order.recommendation_id == rec.id))
            await s.execute(Price.__table__.update().values(is_real=True))
            ia = await runtime.decide(s, firm=firm, recommendation=ia, action=HumanAction.APPROVE, actor_id=adviser.id, actor_label=adviser.email)
            pm = await runtime.decide(s, firm=firm, recommendation=pm, action=HumanAction.APPROVE, actor_id=manager.id, actor_label=manager.email)
            check("the adviser's approval places nothing", ((await orders(ia)).scalar_one(), ia.payload["client_consent"]["status"]), (0, "awaiting"))
            check("the portfolio manager's approval trades", (pm.status, (await orders(pm)).scalar_one() > 0), ("executed", True))
            ia = await runtime.client_response(s, firm=firm, recommendation=ia, given=True, channel="simulated",
                                               evidence_ref=None, note=None, actor_label=adviser.email)
            check("a simulated answer places them, and says it was simulated",
                  (ia.status, ia.payload["client_consent"]["simulated"], (await orders(ia)).scalar_one() > 0), ("executed", True, True))
            settled = ia.payload["execution_result"]
            check("nothing failed to settle", (settled["orders_failed"], settled["settlement_breaks"]), (0, 0))

            print("\n=== The tax agent, through the runtime ===")
            run = await runtime.run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, trigger="seed_scan",
                                          subject=Subject("firm", firm.id, firm.name), require_human=True)
            await s.flush()
            memos = (await s.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().all()
            check("a proposal per household with something to say", len(memos) >= 3, True)
            check("each on its household", all(m.subject_type == "household" and m.subject_id for m in memos), True)
            check("and in rupees", all("₹" in m.summary and "$" not in m.summary for m in memos), True)

            asked = [k["prompt"] for k in calls if "tax memo" in k.get("prompt", "")]
            check("the memo names a household once", [p for p in asked if " household." in p.split("\n")[0]], [])
            check("with no second article for the model to add", [p for p in asked if " on The " in p.split("\n")[0]], [])
            check("and does not call a gain a harvest", all("gain(s) that fit the exemption" in p for p in asked), True)
            check("it has room to finish", {k["max_tokens"] for k in calls if "tax memo" in k.get("prompt", "")}, {1200})
            check("and a length that fits it", all("under 250 words" in p for p in asked), True)
            check("each saving has its source", all("from harvesting losses" in p and "from the exemption" in p for p in asked), True)
            check("a loss that saves nothing says so", any("saving ₹0 and carrying ₹" in p for p in asked), True)

            print("\n=== A fill refreshes its household's tax memo ===")
            check("a memo written after the last fill is current", await tax_refresh.stale_memos(s, firm), [])
            memo_of = {m.subject_id: m for m in memos}

            async def household_of(rec):
                mandate = await s.get(Mandate, rec.subject_id)
                owner = (await s.get(Person, mandate.person_id) if mandate.person_id
                         else await s.get(LegalEntity, mandate.entity_id))
                return owner.household_id

            later = traded = None
            for (track, _), rec in recs.items():
                if track == "pms_discretionary" and rec.status == RecommendationStatus.PROPOSED \
                        and await household_of(rec) in memo_of:
                    later, traded = rec, await household_of(rec)
                    break
            check("a discretionary rebalance is left on a household with a memo", later is not None, True)
            memo, later_id = memo_of[traded], later.id
            await runtime.decide(s, firm=firm, recommendation=later, action=HumanAction.APPROVE,
                                 actor_id=manager.id, actor_label=manager.email)
            await s.commit()
            check("its fill makes that memo stale, and no other", [r.id for r in await tax_refresh.stale_memos(s, firm)], [memo.id])
            written = len(calls)
            check("the refresh brings it up to date", await tax_refresh.refresh_after_trades(s, firm),
                  {"refreshed": 1, "closed": 0, "failed": 0})
            await s.refresh(memo)
            rerun = await s.get(AgentRun, __import__("uuid").UUID(memo.payload["refreshed"]["last_run_id"]))
            check("in place, by a run on that household after the trade",
                  (memo.status, rerun.trigger, rerun.subject_id), ("proposed", "orders_settled", traded))
            check("only that household's memo is written again",
                  len([k for k in calls[written:] if "tax memo" in k.get("prompt", "")]), 1)
            check("and nothing is stale until the next fill", await tax_refresh.stale_memos(s, firm), [])

            async def another_fill():
                last = (await s.execute(select(Order).where(Order.recommendation_id == later_id))).scalars().first()
                last.settled_at = __import__("app.core.db", fromlist=["utcnow"]).utcnow()
                await s.commit()

            real = tax_agent.tax_intelligence.for_household

            async def _broken(*a, **k):
                raise RuntimeError("no prices")

            await another_fill()
            tax_agent.tax_intelligence.for_household = _broken
            check("a run that fails is counted", await tax_refresh.refresh_after_trades(s, firm),
                  {"refreshed": 0, "closed": 0, "failed": 1})
            check("and kept as a failed run, so it is not retried every two minutes",
                  await tax_refresh.stale_memos(s, firm), [])

            async def _nothing(*a, **k):
                return None

            await another_fill()
            tax_agent.tax_intelligence.for_household = _nothing
            check("a household left with nothing to raise closes its memo",
                  await tax_refresh.refresh_after_trades(s, firm), {"refreshed": 0, "closed": 1, "failed": 0})
            await s.refresh(memo)
            check("and says why", (memo.status, memo.decision_note),
                  ("expired", "Nothing left to raise once the book had traded."))
            tax_agent.tax_intelligence.for_household = real
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, trigger="seed_scan",
                                    subject=Subject("firm", firm.id, firm.name), require_human=True)
            await s.flush()
            back = (await s.execute(select(Recommendation).where(
                Recommendation.agent_key == AgentKey.TAX_INTELLIGENCE, Recommendation.subject_id == traded,
                Recommendation.status == RecommendationStatus.PROPOSED))).scalars().all()
            check("a closed memo does not stop the next scan raising one", len(back), 1)

            print("\n=== Row 4: funds in their plans, priced from AMFI ===")
            from datetime import date as _date, timedelta as _td

            import httpx

            from app.aurea_core import pan as pan_core, shelf, sip as sip_core
            from app.conduit.marketdata import parse_amfi_navs
            from app.conduit.service import sync_market_data
            from app.models.enums import EntityType, PartyRole, RegistrationType
            from app.models.graph import Account
            from app.models.portfolio import Instrument

            funds = {sym: spec for sym, spec in india_demo.INSTRUMENTS.items() if spec[4].get("amfi_code")}
            check("three schemes, each in a direct and a regular plan",
                  sorted(spec[4]["fund_plan"] for spec in funds.values()), ["direct"] * 3 + ["regular"] * 3)
            from app.core.db import utcnow as _utcnow

            day = _utcnow().date().strftime("%d-%b-%Y")      # the sync's own today, so a local date ahead of UTC is not "future"
            new_layout = "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n\n"
            new_layout += "\n".join(f"{spec[4]['amfi_code']};{spec[4]['isin']};-;{spec[4]['scheme']};x;Growth;{spec[3]};{day}" for spec in funds.values())
            old_layout = "122639;INF879O01027;-;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;89.7397;02-Nov-2026\n100822;-;-;x;N.A.;02-Nov-2026"
            check("the NAV file is read by scheme code, in its new layout", len(parse_amfi_navs(new_layout, {s[4]["amfi_code"] for s in funds.values()})), 6)
            check("and its old one, skipping a NAV that is not given",
                  parse_amfi_navs(old_layout, {"122639", "100822"}), {"122639": (_date(2026, 11, 2), 89.7397)})
            fund_ids = {sym: (await s.execute(select(Instrument.id).where(Instrument.firm_id == firm.id, Instrument.symbol == sym))).scalar_one()
                        for sym in funds}
            check("a fund is seeded with no invented price", (await s.execute(select(func.count()).select_from(Price).where(
                Price.instrument_id.in_(list(fund_ids.values()))))).scalar_one(), 0)
            asked = []

            def _feeds(request):
                asked.append(request.url.host)
                return httpx.Response(200, text=new_layout) if "amfiindia" in request.url.host else httpx.Response(404)

            priced = await sync_market_data(s, firm.id, transport=httpx.MockTransport(_feeds), revalue=False)
            navs = (await s.execute(select(Price).where(Price.instrument_id.in_(list(fund_ids.values()))))).scalars().all()
            check("the sync prices every fund from AMFI, as real", (len(navs), {p.source for p in navs}, all(p.is_real for p in navs)),
                  (6, {"amfi"}, True))
            check("and asks AMFI once, never the listed feed for a fund", (asked.count("portal.amfiindia.com"),
                  [k for k in priced["failed"] if k in funds]), (1, []))

            mandates = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()
            ia_m = next(m for m in mandates if m.constraints["service_track"] == "investment_adviser")
            pms_m = next(m for m in mandates if m.constraints["service_track"] == "pms_discretionary")
            regular = {fund_ids[sym] for sym, spec in funds.items() if spec[4]["fund_plan"] == "regular"}
            for label, m in (("an adviser", ia_m), ("a portfolio manager", pms_m)):
                bars = await shelf.plan_bars(s, firm, m)
                check(f"{label} buys direct plans only", set(bars), regular)
            refused, _ = await shelf.order_refusals(s, firm, ia_m, [{"side": "buy", "instrument_id": str(fund_ids["PPFAS-FLEXI-REGULAR"]),
                                                                  "symbol": "PPFAS-FLEXI-REGULAR"}])
            check("so a regular plan is refused, saying why", "only direct plans are bought" in (refused[0] if refused else ""), True)
            check("a distributor's licence buys the regular plan", licences.LICENCES["mutual_fund_distributor"].fund_plans, ("regular",))

            print("\n=== Row 4: a SIP, bought by amount ===")
            account = (await s.execute(select(Account).where(Account.mandate_id == pms_m.id))).scalars().first()
            account.cash_balance = 30000
            await s.flush()
            terms = dict(instrument="PPFAS-FLEXI-DIRECT", amount=10000, day_of_month=5, starts_on=_date(2030, 1, 1), ends_on=None,
                         evidence_ref=None, simulated=True)

            async def sip_refused(label, contains, **change):
                try:
                    await sip_core.prepare(s, firm, account.id, **{**terms, **change})
                    check(label, "recorded", "refused")
                except sip_core.SipError as exc:
                    check(label, contains in str(exc), True)

            await sip_refused("a SIP is refused in a regular plan", "only direct plans", instrument="PPFAS-FLEXI-REGULAR")
            await sip_refused("and in a listed share", "not one", instrument="INFY.NS")
            await sip_refused("and with no registration on file", "registration is on file", simulated=False)
            await sip_refused("and on a day not every month has", "1 to 28", day_of_month=31)
            row = await sip_core.record(s, firm, account.id, by="adviser@astra-india.demo", **terms)
            check("it is recorded as simulated, with nothing bought yet", (row.simulated, row.history), (True, []))
            nav = float(next(p.close for p in navs if p.instrument_id == fund_ids["PPFAS-FLEXI-DIRECT"]))
            check("nothing before its day", await sip_core.run_due(s, firm, today=_date(2030, 1, 4)), {"bought": 0, "missed": 0})
            check("on its day the instalment is bought", await sip_core.run_due(s, firm, today=_date(2030, 1, 5)), {"bought": 1, "missed": 0})
            first = row.history[-1]
            check("in units for the amount at the real NAV, to three decimals",
                  (first["status"], first["units"], first["reaches_market"]), ("bought", float(int(10000 / nav * 1000) / 1000), False))
            check("once a month", await sip_core.run_due(s, firm, today=_date(2030, 1, 20)), {"bought": 0, "missed": 0})
            account.cash_balance = 500
            await s.flush()
            check("short of cash, the month is missed and says so", await sip_core.run_due(s, firm, today=_date(2030, 2, 5)), {"bought": 0, "missed": 1})
            check("with the shortfall", "short of cash" in row.history[-1]["reason"], True)
            ex = ToolExecutors(s, adviser.id, UserRole.ADVISER, firm.id)
            card = await ex.preview_change("record_sip", {"account_id": str(account.id), "instrument": "UTI-NIFTY50-DIRECT", "amount": 5000,
                                                          "day_of_month": 10, "starts_on": "2030-03-01", "simulated": True})
            shown = dict((r["label"], r["value"]) for r in card["rows"]) if card and "rows" in card else {}
            check("the Confirm card names the fund, the plan and the registration",
                  (shown.get("Plan"), shown.get("Registration")), ("Direct", "Simulated, demonstration firm"))
            read = await ex.execute("read_sips", {})
            check("the SIP is read back, with what it bought", (read["active"], read["sips"][0]["bought"], read["sips"][0]["plan"]), (1, 1, "direct"))
            await sip_core.end(s, firm, row.id, by="adviser@astra-india.demo", reason="the client asked to stop")
            check("ended, nothing more is bought", await sip_core.run_due(s, firm, today=_date(2030, 3, 5)), {"bought": 0, "missed": 0})

            print("\n=== Row 4: PAN, recorded from a document and shown masked ===")
            rajesh_p = (await s.execute(select(Person).where(Person.full_name == "Rajesh Sharma"))).scalar_one()
            huf_e = (await s.execute(select(LegalEntity).where(LegalEntity.entity_type == EntityType.HUF))).scalars().first()

            async def pan_refused(label, contains, **kw):
                try:
                    await pan_core.record(s, firm, by="ops", **kw)
                    check(label, "recorded", "refused")
                except pan_core.PanError as exc:
                    check(label, contains in str(exc), True)

            await pan_refused("a PAN in the wrong form is refused", "five letters", person_id=rajesh_p.id, pan="ABC123", source_document="PAN card")
            await pan_refused("so is one whose holder letter is not a person's", "is an HUF's", person_id=rajesh_p.id, pan="ABCHS1234K", source_document="PAN card")
            done = await pan_core.record(s, firm, by="ops", person_id=rajesh_p.id, pan="abcps 1234 k", source_document="PAN card, copy on file")
            check("it is shown masked, the last four only", done["pan"], "••••••234K")
            meera_p = (await s.execute(select(Person).where(Person.full_name == "Meera Sharma"))).scalar_one()
            await pan_refused("one PAN belongs to one holder", "already on file", person_id=meera_p.id, pan="ABCPS1234K", source_document="PAN card")
            await pan_core.record(s, firm, by="ops", entity_id=huf_e.id, pan="ABCHS1234K", source_document="HUF PAN card")
            position = await tax_intelligence.for_household(s, sharma.id, firm.jurisdiction, firm_id=firm.id)
            pans = {t["name"]: t["pan"] for t in position["taxpayers"]}
            check("the tax position shows whose PAN is on file, masked", (pans["Rajesh Sharma"], pans["Rajesh Sharma HUF"], pans["Meera Sharma"]),
                  ("••••••234K", "••••••234K", None))
            check("and no full PAN reaches a read", "ABCPS1234K" in json.dumps(position), False)

            print("\n=== Row 4: an HUF in onboarding ===")
            check("an HUF is a registration", RegistrationType("huf"), RegistrationType.HUF)
            from app.aurea_core import document_matrix, parties
            check("run by its karta", [r[0] for r in parties.required_roles("huf")], [PartyRole.KARTA])
            check("formed by a declaration, with its own PAN card",
                  [d[0] for d in document_matrix.MATRIX["huf"]["documents"]], ["id", "huf", "pan"])

            print("\n=== Row 5: planning on India's suggested assumptions ===")
            from app.aurea_core import client_file, financial_plan, nominations, planning as planning_core, protection as protection_core
            from app.models.graph import AccountBeneficiary as _Nominee, Account as _Account

            india = planning_core.assumption_set(firm.settings, firm.jurisdiction)
            check("inflation, retirement and the horizon are India's, and say so",
                  (india["inflation"]["cpi"], india["retirement_age"], india["plan_to_age"], india["sources"]["cpi_inflation"]),
                  (0.055, 60, 90, "market"))
            check("so are equity and fixed income returns, the platform's volatility kept",
                  (india["cma"]["equity"][0], india["cma"]["fixed_income"][0], india["source"]), (0.11, 0.07, "market"))
            check("each with its source, as a suggestion", all(b["source"] and b["basis"].startswith("suggested") for b in india["market_basis"]), True)
            check("a firm's own figure replaces the market's", planning_core.assumption_set({"inflation": 0.06}, "IN")["sources"]["cpi_inflation"], "firm")
            check("a market with none keeps the platform's", (planning_core.assumption_set({}, "NZ")["inflation"]["cpi"],
                                                              planning_core.assumption_set({}, "NZ")["source"]), (0.025, "platform"))
            plan = await financial_plan.for_household(s, sharma.id, firm_id=firm.id)
            check("the plan shows the figures it used, and names gratuity as not modelled",
                  (len(plan["assumption_basis"]) > 0, "gratuity" in plan["not_modelled"]), (True, True))
            eps = next(st for st in plan["streams"] if st["kind"] == "eps_pension")
            check("an EPS pension on file counts, and the plan runs to India's horizon",
                  (eps["annual"], eps["start_age"], plan["retirement"]["longevity_age"]), (36000.0, 58, 90))

            print("\n=== Row 5: protection on Indian terms ===")
            cover = await protection_core.for_household(s, sharma.id, firm_id=firm.id)
            rows = {(r["person"], r["kind"], r["provider"]): r for r in cover["policies"]}
            term = rows[("Rajesh Sharma", "life", "Term insurer (demonstration)")]
            rider = rows[("Rajesh Sharma", "tpd", "Term insurer (demonstration)")]
            group = rows[("Rajesh Sharma", "life", "Employer group term (demonstration)")]
            ci = rows[("Meera Sharma", "critical_illness", "Health insurer (demonstration)")]
            check("a spouse nominee on a life policy is beneficially entitled", term["payee"]["means"], "beneficially entitled (Insurance Act s.39(7))")
            check("a brother receives it for the legal heirs", "for the legal heirs" in group["payee"]["means"], True)
            check("an accident-only rider is listed, not counted", (rider["counted"], rider["rider_of"] == term["id"]), (False, True))
            check("nor is critical illness", (ci["counted"], "no need is modelled" in ci["not_counted"]), (False, True))
            rajesh_cover = next(p for p in cover["people"] if p["name"] == "Rajesh Sharma")
            check("the death gap counts the term and the group cover, nothing else",
                  rajesh_cover["death"].get("cover", rajesh_cover["death"].get("covered")), 18000000.0)
            iyer = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == "The Iyer Family"))).scalar_one()
            mwp = next(r for r in (await protection_core.for_household(s, iyer.id, firm_id=firm.id))["policies"] if r["kind"] == "life")
            check("a Married Women's Property Act policy is outside the estate", mwp["payee"]["to"], "Married Women's Property Act trust")
            rajesh_p = (await s.execute(select(Person).where(Person.full_name == "Rajesh Sharma"))).scalar_one()
            for label, change, contains in (("a rider on nothing but a base policy", {"kind": "life", "rider_of": term["id"]}, "rider"),
                                            ("MWP only on the insured's own life", {"kind": "tpd", "mwp": True}, "Married Women")):
                try:
                    await protection_core.prepare_policy(s, firm.id, person_id=rajesh_p.id, provider="X insurer", source_document="Schedule 2026",
                                                         sum_insured=100000, **{**change, "rider_of": __import__("uuid").UUID(change["rider_of"]) if change.get("rider_of") else None})
                    check(label, "recorded", "refused")
                except protection_core.ProtectionError as exc:
                    check(label, contains in str(exc), True)

            print("\n=== Row 5: nominations, and a nominee against the will ===")
            file = await client_file.for_household(s, sharma.id, firm_id=firm.id)
            issues = [(i["account"], i["issue"].split(":")[0], i["name"]) for i in file["designation_issues"]]
            check("a demat nominee the will does not name is flagged",
                  ("Rajesh Sharma demat", "nominee is not named in the will", "Anil Sharma") in issues, True)
            check("a demat with no nomination says so, not that the client opted out",
                  ("Meera Sharma demat", "no nomination on file (an opt-out is not modelled)", None) in issues, True)
            meera_demat = (await s.execute(select(_Account).where(_Account.firm_id == firm.id, _Account.name == "Meera Sharma demat"))).scalar_one()
            for who in ("A", "B", "C"):
                s.add(_Nominee(firm_id=firm.id, account_id=meera_demat.id, beneficiary_name=who, designation_class="primary", percentage=33))
            await s.flush()
            check("a fourth nominee on a demat is refused", "at most 3" in (await nominations.refusal(s, firm, meera_demat) or ""), True)

            print("\n=== Row 6: the disclosure pack ===")
            from datetime import date as _d, timedelta as _td
            from types import SimpleNamespace as _NS
            from app.aurea_core import billing as billing_core, compliance_calendar, consents as consents_core, law_figures, pre_advice
            from app.models.compliance_calendar import ComplianceFiling
            from app.models.consent_registry import ConsentRecord

            check("an Indian single holder needs the charter and a nomination or opt-out",
                  {"in_investor_charter", "in_nomination_choice"} <= set(disclosures.required_for("IN", "individual")), True)
            check("an HUF has no nomination to make", "in_nomination_choice" in disclosures.required_for("IN", "huf"), False)
            check("and New Zealand's set is untouched", disclosures.required_for("NZ", "individual"),
                  ["disclosure_statement", "soa", "privacy_notice"])

            print("\n=== Row 6: nothing advised before the paperwork ===")
            households = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            gate = {n: await pre_advice.status(s, firm, households[n].id) for n in households}
            check("the adviser's households are gated, the portfolio manager's are not",
                  sorted(n for n, g in gate.items() if g["applies"]), ["The Banerjee Household", "The D'Souza Household", "The Menon NRI Household", "The Sharma Family"])
            check("the Sharmas have all five on file, the declaration included because they hold policies",
                  (gate["The Sharma Family"]["met"], [c["state"] for c in gate["The Sharma Family"]["checks"]]), (True, ["pass"] * 5))
            check("the D'Souzas hold nothing outside SEBI's purview, so no declaration is asked",
                  (gate["The D'Souza Household"]["met"], gate["The D'Souza Household"]["checks"][-1]["state"]), (True, "n/a"))
            check("the Banerjees have not consented to their risk profile",
                  gate["The Banerjee Household"]["missing"], ["Risk profile complete and consented"])
            banerjee_mandates = [m.id for m in await pre_advice.adviser_mandates(s, firm, households["The Banerjee Household"].id)]
            banerjee_rec = (await s.execute(select(Recommendation).where(
                Recommendation.agent_key == AgentKey.DRIFT_REBALANCING, Recommendation.subject_id.in_(banerjee_mandates),
                Recommendation.status == RecommendationStatus.PROPOSED))).scalars().first()
            check("a rebalance was raised for the Banerjees", banerjee_rec is not None, True)
            if banerjee_rec is not None:
                adviser = (await s.execute(select(User).where(User.email == "adviser@astra-india.demo"))).scalar_one()
                try:
                    await runtime.decide(s, firm=firm, recommendation=banerjee_rec, action=HumanAction.APPROVE,
                                         actor_id=adviser.id, actor_label=adviser.email)
                    check("approving it is refused", "approved", "refused")
                except pre_advice.PreAdviceError as exc:
                    check("approving it is refused, naming what is missing", "Risk profile complete and consented" in str(exc), True)
                await s.refresh(banerjee_rec)
                check("and nothing was decided", (banerjee_rec.status, banerjee_rec.decided_by), (RecommendationStatus.PROPOSED, None))
                tools = ToolExecutors(s, adviser.id, UserRole.ADVISER, firm.id)
                try:
                    await tools.preview_change("decide_recommendation", {"recommendation_id": str(banerjee_rec.id), "action": "approve"})
                    check("the Confirm card is not offered", "offered", "refused")
                except Exception as exc:
                    check("the Confirm card is not offered either", "before the client's paperwork" in str(exc), True)
            for method, ok in (("portal", False), ("recorded_call", False), ("email", True)):
                try:
                    await consents_core.prepare(s, firm.id, case_id=None, household_id=households["The Banerjee Household"].id, kind="risk_profile",
                                                status="granted", given_by="Subir Banerjee", method=method, evidence="Reply on file, ref 22", given_on=None)
                    check(f"a risk profile consented by {method} is accepted", ok, True)
                except consents_core.ConsentError as exc:
                    check(f"a risk profile consented by {method} is refused", (ok, "registered email" in str(exc) or "evidenced by" in str(exc)), (False, True))
            check("India's kinds are not offered to a firm elsewhere",
                  "risk_profile" in consents_core.kinds_for(_NS(jurisdiction="NZ")), False)
            await consents_core.record(s, firm.id, by="test", case_id=None, household_id=households["The Banerjee Household"].id, kind="risk_profile",
                                       status="granted", given_by="Subir Banerjee", method="email", evidence="Reply on file, ref 22", given_on=None)
            check("once the consent is recorded the gate opens", (await pre_advice.status(s, firm, households["The Banerjee Household"].id))["met"], True)
            dsouza_consent = (await s.execute(select(ConsentRecord).where(ConsentRecord.household_id == households["The D'Souza Household"].id,
                                                                          ConsentRecord.kind == "risk_profile"))).scalar_one()
            dsouza_consent.given_on = dsouza_consent.given_on - _td(days=30)
            await s.flush()
            check("a consent given before the answers it would cover does not count",
                  "predates" in next(c["detail"] for c in (await pre_advice.status(s, firm, households["The D'Souza Household"].id))["checks"]
                                     if c["key"] == "risk_profile"), True)
            dsouza_consent.given_on = dsouza_consent.given_on + _td(days=30)
            await s.flush()

            print("\n=== Row 6: fees per family, against SEBI's cap ===")
            check("the caps carry their circular", all("para 1(iii)" in f["source"] for f in law_figures.cited(["in_ia_fee_fixed_cap", "in_ia_fee_aua_cap"], firm_time.today(firm))), True)
            pv = await billing_core.preview(s, firm.id)
            bills = {}
            for e in pv["arrangements"]:
                bills.setdefault(e["household_name"], []).append(e)
            check("the three adviser families are billed", sorted(n for n in bills if n in gate and gate[n]["applies"]),
                  ["The Banerjee Household", "The D'Souza Household", "The Sharma Family"])
            dsouza_bill = bills["The D'Souza Household"][0]
            check("a fixed fee above the cap is held, at the fixed cap",
                  (dsouza_bill["blocked"], dsouza_bill["fee_cap"]["cap"], dsouza_bill["fee_cap"]["basis"], "sebi_fee_cap" in [c["code"] for c in dsouza_bill["checks"]]),
                  (True, 151000.0, "fixed-fee mode", True))
            sharma_bills = bills["The Sharma Family"]
            check("1% of assets sits within 2.5% of the family's securities, the HUF's included",
                  (any(c["code"] == "sebi_fee_cap" for e in sharma_bills for c in e["checks"]), sharma_bills[0]["fee_cap"]["over"],
                   len(sharma_bills), sharma_bills[0]["fee_cap"]["basis"].startswith("AUA mode")), (False, False, 3, True))
            check("AUA is the securities alone, cash left out", sharma_bills[0]["fee_cap"]["aua"] < sum(e["billable_value"] for e in sharma_bills), True)
            s.add(ConsentRecord(firm_id=firm.id, household_id=households["The Banerjee Household"].id, kind="advisory_agreement", status="withdrawn",
                                given_by="Subir Banerjee", given_on=firm_time.today(firm), method="signed_form", evidence="Termination letter", recorded_by="test"))
            await s.flush()
            withdrawn = [e for e in (await billing_core.preview(s, firm.id))["arrangements"] if e["household_name"] == "The Banerjee Household"]
            check("no fee with no signed agreement standing", all(any(c["code"] == "no_signed_agreement" for c in e["checks"]) and e["blocked"] for e in withdrawn), True)
            overview = await licences.overview(s, firm)
            check("the licences page carries the gate, the caps and the calendar",
                  (len(overview["pre_advice"]), len(overview["fee_caps"]), overview["calendar"]["applies"]), (4, 3, True))

            print("\n=== Row 6: the compliance calendar ===")
            firm.created_at = firm.created_at.replace(year=2026, month=10, day=31)
            cal = await compliance_calendar.overview(s, firm)
            states = {(i["obligation"], i["period"]): i["state"] for i in cal["items"]}
            check("October's complaint data reads filed", states.get(("complaints_monthly", "2026-10")), "filed")
            check("October's portfolio-manager report, due seven working days after the month, reads overdue",
                  (states.get(("pms_monthly_report", "2026-10")),
                   next(i["due_on"] for i in cal["items"] if (i["obligation"], i["period"]) == ("pms_monthly_report", "2026-10"))),
                  ("overdue", "2026-11-10"))
            check("the next of each obligation is listed once, and nothing before the firm came on",
                  (sum(1 for i in cal["items"] if i["obligation"] == "deposit_revision"), any(i["due_on"] < "2026-10-31" for i in cal["items"])), (1, False))
            check("a day the circular does not give is named as assumed",
                  next(i["due_basis"] for i in cal["items"] if i["obligation"] == "saas_undertaking").startswith("assumed"), True)
            check("officers not recorded say so", {"Designated Director", "Chief Information Security Officer"} <= set(cal["officers_missing"]), True)
            await compliance_calendar.record_filing(s, firm, by="compliance@astra-india.demo", obligation="pms_monthly_report", period="2026-10",
                                                    filed_on=None, reference="SEBI SI portal ack 0001")
            check("recording the filing clears it", {(i["obligation"], i["period"]): i["state"] for i in (await compliance_calendar.overview(s, firm))["items"]}
                  .get(("pms_monthly_report", "2026-10")), "filed")
            for label, terms, contains in (("twice", {"period": "2026-10"}, "was recorded filed"),
                                           ("in the future", {"period": "2026-11", "filed_on": firm_time.today(firm) + _td(days=2)}, "future"),
                                           ("for a period it does not have", {"period": "Q9"}, "No monthly report")):
                try:
                    await compliance_calendar.prepare_filing(s, firm, obligation="pms_monthly_report", reference="SEBI SI portal ack 0002",
                                                             **{"filed_on": None, **terms})
                    check(f"a filing {label} is refused", "recorded", "refused")
                except compliance_calendar.CalendarError as exc:
                    check(f"a filing {label} is refused", contains in str(exc), True)
            compliance = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.COMPLIANCE))).scalars().first()
            if compliance is not None:
                tools = ToolExecutors(s, compliance.id, UserRole.COMPLIANCE, firm.id)
                card = await tools.preview_change("record_compliance_filing", {"obligation": "complaints_monthly", "period": "2026-11",
                                                                               "reference": "Website update ref 7"})
                check("the filing's Confirm card names the obligation, its due day and its law",
                      [r["label"] for r in card["rows"]][:3] + ["Rests on" in [r["label"] for r in card["rows"]]], ["Obligation", "Period", "Due", True])
            try:
                compliance_calendar.prepare_officers(firm, {"ciso": "Asha Pillai (demonstration)"}, "")
                check("a registration change with no reason is refused", "recorded", "refused")
            except compliance_calendar.CalendarError:
                check("a registration change with no reason is refused", True, True)

            print("\n=== Row 6: the firm's own compliance persona is its registered officer ===")
            from seed import india_row6
            check("the seed names Neha Kulkarni, and a correction has nothing to do",
                  ((firm.settings or {}).get("sebi_registration", {}).get("compliance_officer"), await india_row6.correct_names(s, firm)),
                  ("Neha Kulkarni (demonstration)", 0))
            firm.settings = {**firm.settings, "sebi_registration": {**firm.settings["sebi_registration"], "compliance_officer": india_row6.WRONG_OFFICER}}
            check("a firm seeded with the old name is corrected", (await india_row6.correct_names(s, firm) >= 1,
                  firm.settings["sebi_registration"]["compliance_officer"]), (True, "Neha Kulkarni (demonstration)"))

            print("\n=== Rows 7 and 8: a non-resident household, a minor, and a Portfolio Manager's client ===")
            from app.aurea_core import guardianship, pms, residency
            from seed import india_row7, india_row8
            menon = households["The Menon NRI Household"]
            gate7 = await pre_advice.status(s, firm, menon.id)
            check("the Menons wait only for Anita's FATCA and CRS self-certification", gate7["missing"], ["FATCA and CRS self-certification"])
            overview7 = await residency.overview(s, firm)
            check("two Menons are on file, both non-resident", (overview7["households"][0]["household"], overview7["households"][0]["non_resident"]),
                  ("The Menon NRI Household", 2))
            minors = {m["name"]: m["state"] for m in (await guardianship.overview(s, firm))["minors"]}
            check("Ira has her father as guardian and Kabir has none", (minors["Ira Menon"], minors["Kabir Menon"]), ("guardian_on_file", "no_guardian"))
            clients = {c["client"]: c for c in (await pms.overview(s, firm))["clients"]}
            check("Karthik's agreement passes every check; the Kapoor Trust has none and is not assessed",
                  (set(k["state"] for k in clients["Karthik Iyer"]["checks"]), clients["Kapoor Family Trust"]["agreement"]), ({"pass"}, None))
            check("rows 7 and 8 add nothing a second time", (await india_row7.add(s, firm), await india_row8.add(s, firm)), (0, 0))

            print("\n=== Running it twice ===")
            check("the slug is fixed, so a second run finds the firm", india_demo.SLUG, firm.slug)

            print("\n=== The backfill ===")
            goal = (await s.execute(select(Goal).where(Goal.firm_id == firm.id,
                                                       Goal.name == "Next generation endowment"))).scalar_one()
            goal.target_amount, goal.assumptions = 500000000, {"years": 20}
            check("it reaches every seeded goal", await india_demo_backfill.reset_goals(s, firm), 6)
            check("and puts the target back", float(goal.target_amount), 28000000.0)
            check("and the assumptions", goal.assumptions["funding_share"], 0.8)
            open_tax = select(func.count()).select_from(Recommendation).where(
                Recommendation.firm_id == firm.id, Recommendation.agent_key == AgentKey.TAX_INTELLIGENCE,
                Recommendation.status == RecommendationStatus.PROPOSED)
            before = (await s.execute(open_tax)).scalar_one()
            await runtime.run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, trigger="seed_scan",
                                    subject=Subject("firm", firm.id, firm.name), require_human=True)
            await s.flush()
            check("its rerun refreshes the tax proposals, it adds none", (await s.execute(open_tax)).scalar_one(), before)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for label in FAIL:
        print(f"  FAILED: {label}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
