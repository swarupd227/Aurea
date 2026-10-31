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
from app.agents import drift_rebalancing as drift
from app.agents import tax_intelligence as tax_agent
from app.atlas import runtime
from app.atlas.base import Subject
from app.aurea_core import disclosures, licences, protection, tax_intelligence
from app.compliance import ontology
from app.core.db import Base
from app.core.personas import INDIA_PERSONAS
from app.models.enums import AgentKey, AutonomyTier, HumanAction
from app.models.governance import Recommendation
from app.models.graph import Household, Mandate
from app.models.identity import User
from app.models.portfolio import Price
from app.models.tenant import AgentConfig
from app.models.trading import Order
from app.provenance import policy
from seed import india_demo

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

            async def _fallback(ctx, *, fallback, **k):
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
            check("prices with no feed are marked as invented", invented, len(india_demo.INSTRUMENTS))
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

            print("\n=== Protection works from what is on file ===")
            view = await protection.for_household(s, sharma.id, firm_id=firm.id)
            check("retirement at the firm's 60", view["assumptions"]["retirement_age"], 60)

            print("\n=== Every mandate through the rebalancer ===")
            mandates = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()
            check("seven taxpayers with mandates", len(mandates), 7)
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

            print("\n=== Running it twice ===")
            check("the slug is fixed, so a second run finds the firm", india_demo.SLUG, firm.slug)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for label in FAIL:
        print(f"  FAILED: {label}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
