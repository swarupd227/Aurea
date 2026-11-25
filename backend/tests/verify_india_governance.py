"""Prove India row 14: India's dates in the Regulatory Countdown, the licence map, the governance register and counsel's review of each law figure.

Covers the policy register (each state, a year after approval unless the document sets another day, a leap day, versions added and never
edited, who records, every refusal), the licence map read out of the licences (held and not held, the agents the ceiling caps, no tier
invented), counsel's review per figure ("not reviewed" by default, confirmed, not confirmed, changed since the review, the figure itself
untouched, who records), the review shown beside each figure in the Licences payload, in a tool result and in the countdown, and India's
dates: the firm's filings, the registration, each policy's review and the rule changes, each with its source and "assumed" where it is;
a household's advance tax, return, KYC review and nominations; and the tools and their cards.

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_governance
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (compliance_calendar, counsel_review, firm_time, governance, india_deadlines, law_figures, licences,
                            regulatory_countdown)
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.aml_risk import AmlRiskRating
from app.models.enums import AgentKey, AutonomyTier, UserRole
from app.models.graph import Household
from app.models.identity import User
from app.models.india_governance import CounselReview, GovernancePolicy
from app.models.onboarding import OnboardingCase
from app.models.tenant import AutonomyPolicy, Firm
from seed import india_demo, india_row14

PASS, FAIL = [], []
G, C = governance, counsel_review


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None, exc=(G.GovernanceError, C.CounselError, ExecutorError)) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
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
            firm = await india_demo.build(s)
            await s.commit()
            today = firm_time.today(firm)
            users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
            sharma = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == "The Sharma Family"))).scalar_one()

            print("\n=== The register, from the seed ===")
            reg = await G.policies(s, firm)
            states = {p["kind"]: p["state"] for p in reg["policies"]}
            check("each policy is in its own state", states, {"grievance": "current", "cyber": "overdue", "business_continuity": "current",
                                                                "outsourcing": "not_recorded", "conflict_of_interest": "due_soon"})
            check("the counts add up", reg["counts"], {"overdue": 1, "due_soon": 1, "current": 2, "not_recorded": 1})
            cyber = next(p for p in reg["policies"] if p["kind"] == "cyber")
            check("an overdue policy shows how many days past, never a countdown", cyber["days"] < 0, True)
            check("a year after approval unless the document sets another day",
                  (next(p for p in reg["policies"] if p["kind"] == "grievance")["review_set_by_document"],
                   next(p for p in reg["policies"] if p["kind"] == "business_continuity")["review_set_by_document"]), (False, True))
            check("a kind with no record does not say the firm has none", "does not mean the firm has no such policy" in reg["not_compliance"], True)
            check("the register never says the firm is compliant", "nothing here says the firm is compliant" in reg["not_compliance"], True)
            cal = await compliance_calendar.overview(s, firm)
            check("the compliance calendar carries the register", cal["governance"]["counts"], reg["counts"])
            lic = await licences.overview(s, firm)
            check("the Licences payload carries the register, the map and counsel's review", (sorted(lic["governance"]), lic["counsel"]["total"] > 10),
                  (["certifications", "map", "policies"], True))

            print("\n=== Recording a policy ===")
            comp = users[UserRole.COMPLIANCE]
            for role in (UserRole.ADVISER, UserRole.BRANCH_LEADER, UserRole.ADMIN, UserRole.OPERATIONS):
                await refused(f"{role.value} does not record a policy: it is compliance's", G.prepare_policy(
                    s, firm, role, kind="outsourcing", version="1.0", owner="Compliance Officer", approved_on=today, document="The approved policy, filed"), "compliance")
            base = dict(kind="outsourcing", version="1.0", owner="Neha Kulkarni, Compliance Officer", approved_on=today - timedelta(days=10),
                        document="The approved outsourcing policy, filed in the policy folder")
            await refused("a kind outside the list is refused", G.prepare_policy(s, firm, UserRole.COMPLIANCE, **{**base, "kind": "dress_code"}), "one of")
            await refused("an approval in the future is refused", G.prepare_policy(s, firm, UserRole.COMPLIANCE, **{**base, "approved_on": today + timedelta(days=1)}), "future")
            await refused("a review before the approval is refused", G.prepare_policy(s, firm, UserRole.COMPLIANCE, **{**base, "next_review_on": today - timedelta(days=20)}), "after the approval")
            await refused("no version, no policy", G.prepare_policy(s, firm, UserRole.COMPLIANCE, **{**base, "version": " "}), "version")
            await refused("no owner, no policy", G.prepare_policy(s, firm, UserRole.COMPLIANCE, **{**base, "owner": "x"}), "owner")
            await refused("no document, no policy", G.prepare_policy(s, firm, UserRole.COMPLIANCE, **{**base, "document": ""}), "filed")
            before = (await s.execute(select(func.count()).select_from(GovernancePolicy))).scalar_one()
            out = await G.record_policy(s, firm, UserRole.COMPLIANCE, by="test", **base)
            check("a recorded policy reads current, a year after approval", (next(p for p in out["policies"] if p["kind"] == "outsourcing")["state"],
                                                                             next(p for p in out["policies"] if p["kind"] == "outsourcing")["next_review_on"]),
                  ("current", (today - timedelta(days=10)).replace(year=today.year + 1).isoformat()))
            await refused("the same version twice is refused", G.prepare_policy(s, firm, UserRole.COMPLIANCE, **base), "already recorded")
            await refused("an earlier approval does not stand over a later one", G.prepare_policy(
                s, firm, UserRole.COMPLIANCE, **{**base, "version": "0.9", "approved_on": today - timedelta(days=40)}), "later approval")
            out = await G.record_policy(s, firm, UserRole.COMPLIANCE, by="test", **{**base, "version": "1.1", "approved_on": today - timedelta(days=2), "next_review_on": today + timedelta(days=200)})
            o = next(p for p in out["policies"] if p["kind"] == "outsourcing")
            check("a new version stands and the earlier one stays on file, added and never edited",
                  (o["version"], [v["version"] for v in o["earlier_versions"]], (await s.execute(select(func.count()).select_from(GovernancePolicy))).scalar_one() - before),
                  ("1.1", ["1.0"], 2))
            check("a review the document sets is kept", (o["review_set_by_document"], o["next_review_on"]), (True, (today + timedelta(days=200)).isoformat()))
            leap = G._add_year(date(2028, 2, 29))
            check("a leap day approval reviews on 28 February", leap, date(2029, 2, 28))
            nz = Firm(id=uuid.uuid4(), name="NZ", slug="nz-g", base_currency="NZD", jurisdiction="NZ")
            s.add(nz)
            await s.flush()
            await refused("a firm outside India has no register", G.prepare_policy(s, nz, UserRole.COMPLIANCE, **base), "Indian licence")
            check("and its read says it does not apply", (await G.policies(s, nz))["applies"], False)

            print("\n=== The licence map ===")
            mp = await G.licence_map(s, firm)
            check("six licences are listed, two held", (len(mp["licences"]), mp["held"]), (6, ["investment_adviser", "pms_discretionary"]))
            ia = next(l for l in mp["licences"] if l["key"] == "investment_adviser")
            ra = next(l for l in mp["licences"] if l["key"] == "research_analyst")
            check("what a track allows is read from the licence: the adviser's client consents to each trade, the analyst places none",
                  (ia["per_trade_consent"], ia["may_execute"], ra["held"], ra["may_execute"]), (True, True, False, False))
            check("mandates and filings count under the licence they sit in", (ia["mandates"] > 0, ia["obligations"] > 0), (True, True))
            check("the ceiling is Tier 2 and no licence reaches Tier 3", (mp["ceiling"], "No licence reaches Tier 3" in mp["note"]), ("tier_2", True))
            s.add(AutonomyPolicy(firm_id=firm.id, agent_key=AgentKey.DRIFT_REBALANCING, mandate_type=None, tier=AutonomyTier.TIER_3,
                                 guardrails={}, rationale="A test: the firm asked for Tier 3"))
            await s.flush()
            mp = await G.licence_map(s, firm)
            drift = next(a for a in mp["agents"] if a["agent_key"] == "drift_rebalancing")
            check("an agent set above the licence is shown capped: configured tier 3, effective tier 2, from the firm's policy",
                  (drift["configured"], drift["effective"], drift["capped"], drift["source"], drift["moves_the_book"]), ("tier_3", "tier_2", True, "firm policy", True))
            check("the capped agents come first and are counted", (mp["agents"][0]["agent_key"], mp["capped"]), ("drift_rebalancing", 1))
            check("no agent has an effective tier above the ceiling", {a["effective"] for a in mp["agents"]} <= {"tier_1", "tier_2"}, True)
            check("the map is not a check against SEBI's register", "not checked against SEBI's register" in mp["note"], True)
            check("a firm with no licence has an empty map", (await G.licence_map(s, nz))["held"], [])

            print("\n=== Counsel's review of each figure ===")
            keys = C.india_keys()
            check("every Indian figure is listed, none other", (all(k.startswith("in_") for k in keys), len(keys) > 20, "us_basic_exclusion" in keys), (True, True, False))
            ov = await C.overview(s, firm)
            by = {f["key"]: f["counsel"]["state"] for f in ov["figures"]}
            check("the three the seed reviewed read so, and every other is not reviewed",
                  (by["in_ia_fee_fixed_cap"], by["in_majority_age"], by["in_complaint_resolve_days"], by["in_res_days_year"]),
                  ("confirmed", "confirmed", "not_confirmed", "not_reviewed"))
            check("the counts add up to the figures", (sum(ov["counts"].values()), ov["counts"]["confirmed"], ov["counts"]["not_confirmed"]), (ov["total"], 2, 1))
            check("'confirmed' is said to be what the reviewer said, not a finding the figure is right", "not a finding" in ov["note"], True)
            fresh = Firm(id=uuid.uuid4(), name="Fresh", slug="fresh-g", base_currency="INR", jurisdiction="IN", settings={"licences": ["investment_adviser"]})
            s.add(fresh)
            await s.flush()
            check("a firm with no review reads every figure not reviewed", {f["counsel"]["state"] for f in (await C.overview(s, fresh))["figures"]}, {"not_reviewed"})
            args = dict(figure_key="in_nro_repatriation", reviewer="Meridian Legal LLP (invented)", reviewed_on=today - timedelta(days=3), outcome="confirmed",
                        document="Counsel's note on NRO remittance, filed")
            for role in (UserRole.ADVISER, UserRole.BRANCH_LEADER, UserRole.ADMIN):
                await refused(f"{role.value} does not record a review: it is compliance's", C.prepare(s, firm, role, **args), "compliance")
            await refused("a figure that is not Indian is refused", C.prepare(s, firm, UserRole.COMPLIANCE, **{**args, "figure_key": "us_basic_exclusion"}), "not an Indian law figure")
            await refused("a review in the future is refused", C.prepare(s, firm, UserRole.COMPLIANCE, **{**args, "reviewed_on": today + timedelta(days=1)}), "future")
            await refused("an outcome outside the list is refused", C.prepare(s, firm, UserRole.COMPLIANCE, **{**args, "outcome": "looks fine"}), "outcome is one of")
            await refused("a correction needs what is wrong", C.prepare(s, firm, UserRole.COMPLIANCE, **{**args, "outcome": "corrected"}), "what counsel found")
            await refused("a review needs its document", C.prepare(s, firm, UserRole.COMPLIANCE, **{**args, "document": ""}), "filed")
            await refused("a reviewer is named", C.prepare(s, firm, UserRole.COMPLIANCE, **{**args, "reviewer": "me"}), "who reviewed")
            await refused("a firm outside India records no review", C.prepare(s, nz, UserRole.COMPLIANCE, **args), "Indian firm")
            figure_before = law_figures.get("in_nro_repatriation").value
            out = await C.record(s, firm, UserRole.COMPLIANCE, by="test", **{**args, "outcome": "corrected", "note": "The limit is stated in US dollars per financial year: confirm against the Master Direction."})
            st = next(f for f in out["figures"] if f["key"] == "in_nro_repatriation")["counsel"]
            check("a correction is recorded and the figure itself is unchanged", (st["state"], st["outcome"], law_figures.get("in_nro_repatriation").value), ("corrected", "corrected", figure_before))
            await refused("the same review twice is refused", C.prepare(s, firm, UserRole.COMPLIANCE, **{**args, "outcome": "corrected", "note": "The limit is stated in US dollars per financial year."}), "already recorded")
            await refused("an earlier review does not stand over a later one", C.prepare(s, firm, UserRole.COMPLIANCE, **{**args, "reviewed_on": today - timedelta(days=30)}), "later review stands")
            out = await C.record(s, firm, UserRole.COMPLIANCE, by="test", **{**args, "reviewed_on": today})
            check("a later review stands, the earlier is kept",
                  (next(f for f in out["figures"] if f["key"] == "in_nro_repatriation")["counsel"]["state"],
                   (await s.execute(select(func.count()).select_from(CounselReview).where(CounselReview.figure_key == "in_nro_repatriation"))).scalar_one()), ("confirmed", 2))
            row = (await s.execute(select(CounselReview).where(CounselReview.figure_key == "in_nro_repatriation").order_by(CounselReview.reviewed_on.desc()))).scalars().first()
            row.figure_as_of = law_figures.get("in_nro_repatriation").as_of - timedelta(days=1)
            await s.flush()
            check("a figure re-read after the review reads changed since", next(f for f in (await C.overview(s, firm))["figures"] if f["key"] == "in_nro_repatriation")["counsel"]["state"], "changed_since")

            print("\n=== Beside the figure, wherever it is shown ===")
            lic = await licences.overview(s, firm)
            found: list = []
            C._figures_in(lic, found)
            check("every Indian figure in the Licences payload carries counsel's status", (len(found) > 5, all("counsel" in f for f in found)), (True, True))
            tool = ToolExecutors(s, comp.id, UserRole.COMPLIANCE, firm.id)
            res = await tool.execute("read_licences", {})
            found = []
            C._figures_in(res, found)
            check("and so does a tool's result", (len(found) > 0, all("counsel" in f for f in found)), (True, True))
            plain = {"label": "no figure here", "key": "something", "list": [{"key": "in_x", "source": "s"}]}
            check("a result with no figure is returned as it was", await C.annotate(s, firm, plain), {"label": "no figure here", "key": "something", "list": [{"key": "in_x", "source": "s"}]})
            loop: dict = {"figure": law_figures.get("in_majority_age").view(today)}
            loop["invoices"] = [loop]
            check("a result that holds itself is read once, not for ever", ("counsel" in (await C.annotate(s, firm, loop))["figure"]), True)
            usfig = law_figures.get("us_basic_exclusion", today).view(today)
            check("a United States figure is left alone", "counsel" not in (await C.annotate(s, firm, {"f": usfig}))["f"], True)
            infig = law_figures.get("in_ia_fee_fixed_cap").view(today)
            check("a firm outside India is left alone", "counsel" not in (await C.annotate(s, nz, {"f": infig}))["f"], True)
            cd = await regulatory_countdown.for_household(s, sharma.id, "IN", firm_id=firm.id)
            found = []
            C._figures_in(cd, found)
            check("the countdown's own figures carry it", (len(found) > 0, all("counsel" in f for f in found)), (True, True))

            print("\n=== India's dates in the countdown: the firm's ===")
            fi = await india_deadlines.firm_items(s, firm)
            kinds = {i["kind"] for i in fi["items"]}
            check("the firm's dates are filings, the registration, policies and rule changes", kinds >= {"filing", "registration", "policy", "rule_change"}, True)
            check("each item names its source", all(i["source"] for i in fi["items"]), True)
            check("overdue first, then due soon", [i["state"] for i in fi["items"]][:1], ["overdue"])
            check("the days follow the order within a state", [i["due_on"] for i in fi["items"] if i["state"] == "overdue"] == sorted(i["due_on"] for i in fi["items"] if i["state"] == "overdue"), True)
            reg_item = next(i for i in fi["items"] if i["kind"] == "registration")
            check("a registration expiry not recorded has no day and says so", (reg_item["state"], reg_item["due_on"], "not recorded" in reg_item["detail"]), ("not_dated", None, True))
            rc = {i["label"]: i for i in fi["items"] if i["kind"] == "rule_change"}
            check("the dated rule changes are in, the data protection day marked assumed",
                  (sorted(rc) == sorted(["Portfolio Managers Regulations 2026 apply", "Common Advertisement Code operative", "Data protection duties start (the day is assumed)"]),
                   rc["Data protection duties start (the day is assumed)"]["assumed"] is not None, rc["Common Advertisement Code operative"]["assumed"]), (True, True, None))
            check("a policy review a year after approval is marked as assumed from that rule, one the document set is not",
                  (next(i for i in fi["items"] if i["label"] == "Grievance redressal policy review")["assumed"],
                   next(i for i in fi["items"] if i["label"] == "Business continuity and disaster recovery policy review")["assumed"]), ("a year after approval", None))
            check("an overdue review reads overdue, never a countdown", next(i for i in fi["items"] if i["label"] == "Cybersecurity and cyber resilience policy review")["state"], "overdue")
            nodate = next(i for i in (await india_deadlines.firm_items(s, fresh))["items"] if i["label"] == "Outsourcing policy review")
            check("a policy with no record has no day to count to, and says it is not in the register", (nodate["state"], nodate["due_on"], "not recorded" in nodate["detail"]), ("not_dated", None, True))
            check("a filing already made is not listed", all(i["state"] != "filed" for i in fi["items"]), True)
            check("it never says the firm is compliant", "does not say the firm is compliant" in fi["not_compliance"], True)
            fa = await india_deadlines.firm_analysis(s, firm)
            check("as a countdown analysis it carries its summary, its law and what it assumed", (fa["type"], "overdue" in fa["summary"], len(fa["law"]), len(fa["assumed"]) > 0), ("in_firm_deadlines", True, 3, True))
            check("a firm outside India has none", (await india_deadlines.firm_items(s, nz))["applies"], False)
            check("india_firm is None for it too", await regulatory_countdown.india_firm(s, nz.id), None)
            firm_cd = await regulatory_countdown.india_firm(s, firm.id)
            check("and present for the Indian firm", firm_cd["analysis"]["type"], "in_firm_deadlines")

            print("\n=== India's dates in the countdown: a household's ===")
            hi = await india_deadlines.household_items(s, firm, sharma.id)
            adv = [i for i in hi["items"] if i["kind"] == "advance_tax"]
            check("the remaining advance tax instalments of the year are listed, each after today, with their source",
                  (1 <= len(adv) <= 4, all(i["due_on"] >= today.isoformat() for i in adv), all("Income-tax Act" in i["source"] for i in adv)), (True, True, True))
            check("whether tax is due at all is not assessed", all("not assessed" in i["detail"] for i in adv), True)
            itr = next(i for i in hi["items"] if i["kind"] == "itr")
            check("the return's day is marked assumed: it is moved by notification", (itr["assumed"] is not None, itr["due_on"][5:]), (True, "07-31"))
            check("the nominations still open carry no day and no day is invented", [(i["state"], i["due_on"]) for i in hi["items"] if i["kind"] == "nomination"], [("not_dated", None)])
            case = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Rajesh Sharma", registration_type="individual", intake={},
                                  materialized={"household_id": str(sharma.id)})
            s.add(case)
            await s.flush()
            s.add(AmlRiskRating(firm_id=firm.id, case_id=case.id, model_version="t", facts={}, factors=[], score=40, model_band="medium", band="medium",
                                paths=[], review_months=24, next_review_on=today - timedelta(days=9), rated_by="test", rated_by_role="compliance"))
            await s.flush()
            hi = await india_deadlines.household_items(s, firm, sharma.id)
            kyc = [i for i in hi["items"] if i["kind"] == "kyc_review"]
            check("a client's periodic KYC review comes from its risk band's cycle, overdue and marked as this build's own",
                  ([i["state"] for i in kyc], "this build's own" in (kyc[0]["assumed"] or "")), (["overdue"], True))
            check("and the firm's list counts it", any(i["kind"] == "kyc_review" and "overdue" in i["label"] for i in (await india_deadlines.firm_items(s, firm))["items"]), True)
            an = await regulatory_countdown.for_household(s, sharma.id, "IN", firm_id=firm.id)
            a = an["analyses"][0]
            check("the household's countdown has India's analysis, critical while something is overdue", (len(an["analyses"]), a["type"], a["urgency"]), (1, "in_client_dates", "critical"))
            check("the Regulatory Countdown agent still raises nothing for India: it proposes on the US and UK only",
                  a["type"] not in ("us_estate_tax_sunset", "uk_iht_pension_inclusion"), True)

            print("\n=== The tools and their cards ===")
            for key in ("record_governance_policy", "record_counsel_review"):
                check(f"{key} stops for a Confirm card", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("read_governance changes nothing", TOOLS["read_governance"].change_state, ToolChangeState.NO)
            card = await tool.preview_change("record_governance_policy", {"kind": "conflict_of_interest", "version": "1.3", "owner": "Neha Kulkarni, Compliance Officer",
                                                                          "approved_on": today.isoformat(), "document": "The approved conflict policy, filed"})
            check("the policy card names the policy, version, owner, review and what it replaces",
                  [r["label"] for r in card["rows"]], ["Policy", "Version", "Owner", "Approved", "Next review", "Replaces", "Evidence", "Note"])
            card = await tool.preview_change("record_counsel_review", {"figure_key": "in_pms_min_ticket", "reviewer": "Meridian Legal LLP (invented)", "reviewed_on": today.isoformat(),
                                                                        "outcome": "confirmed", "document": "Counsel's note on the PM minimum, filed"})
            check("the review card names the figure with its source and says it is unchanged", (card["rows"][0]["label"], any("unchanged" in r["value"] for r in card["rows"])), ("Figure", True))
            n = (await s.execute(select(func.count()).select_from(CounselReview))).scalar_one()
            check("a card writes nothing by being shown", (await s.execute(select(func.count()).select_from(CounselReview))).scalar_one(), n)
            adviser = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
            await refused("an adviser's card is refused: it is compliance's", adviser.preview_change("record_governance_policy", {
                "kind": "grievance", "version": "9", "owner": "Someone Here", "approved_on": today.isoformat(), "document": "The approved policy, filed"}), "compliance")
            await refused("an adviser does not read the register", adviser.execute("read_governance", {}), "compliance")
            read = await tool.execute("read_governance", {"section": "counsel"})
            check("compliance reads counsel's review by section", (sorted(k for k in read if k in ("map", "policies", "counsel")), "not reviewed by counsel" in read["message"]), (["counsel"], True))
            done = await tool.execute("record_governance_policy", {"kind": "grievance", "version": "2.2", "owner": "Neha Kulkarni, Compliance Officer", "approved_on": today.isoformat(),
                                                                    "document": "The approved grievance policy v2.2, filed"})
            check("confirmed, it is recorded and the register moves", (done["message"], next(p for p in done["policies"] if p["kind"] == "grievance")["version"]), ("Recorded.", "2.2"))
            await refused("an unknown section is refused", tool.execute("read_governance", {"section": "everything"}), "map, policies or counsel")
            read = await tool.execute("read_regulatory_countdown", {})
            check("the countdown tool carries the Indian firm's own dates beside the households'", (read["india_firm"]["analysis"]["type"], "India dates" in read["message"]), ("in_firm_deadlines", True))

            print("\n=== The seed, a second time ===")
            check("row 14 adds nothing a second time", await india_row14.add(s, firm), 0)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
