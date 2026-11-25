"""Prove India batch C: a mutual fund order's rail, cut-off rule and NAV day (B-294), advice kept apart from distribution with the direct-against-regular
comparison (B-297), and each adviser's NISM certificate with the advertisement pre-approval (B-298).

Covers each assumed figure in the counsel tracker as not reviewed and saying verify; the NAV day the cut-off rule gives (in time, on the cut-off minute, after
it, over a weekend, a large amount following the day funds were realised, a liquid scheme, a redemption, a missing fact as "not assessed"); a record against
the rule, a difference reported as a break and nothing repaired, a correction needing a reason with the earlier row kept; the blotter showing order date and
NAV date apart; the group, a trail and the flag for compliance (never a finding), "not assessed" with no group, the comparison from recorded ratios and "not
assessed" where one is missing; each adviser's certificate state, the countdown, the reminder (once), and a warning (never a block) for the adviser who would
approve; the advertisement submission, its hash, the decision by someone other than the submitter, a citation read against the approved wording; every refusal;
and the tools, their cards and routes.

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_batch_c
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (ad_approval, adviser_certification, advisers, blotter, counsel_review, distribution_separation as ds, firm_time, india_deadlines,
                            india_reminders, law_figures, mf_orders)
from app.core.db import Base
from app.core.security import UserRole
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.india_batch_c import AdDecision, AdSubmission, AdviserCertification, GroupTrail, MfOrderRecord
from app.models.portfolio import Holding, Instrument, Price
from app.models.engagement import Task
from app.models.tenant import Firm
from app.models.trading import Order
from seed import india_batch_c, india_demo

PASS, FAIL = [], []
ERRORS = (mf_orders.MfOrderError, ds.SeparationError, adviser_certification.CertificationError, ad_approval.AdApprovalError, ExecutorError)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except ERRORS as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


NEW_KEYS = ("in_mf_cutoff_standard", "in_mf_cutoff_liquid_purchase", "in_mf_cutoff_liquid_redemption", "in_mf_large_amount", "in_ia_group_commission_rule",
            "in_nism_validity_years", "in_ad_preapproval_rule")
WED = date(2026, 11, 25)       # a Wednesday


def rule(**kw):
    base = dict(side="buy", rule="standard", order_on=WED, order_time="10:30", amount=10_000.0, funds_realised_on=None, today=WED)
    base.update(kw)
    return mf_orders.nav_date_for(**base)


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
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            inst = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
            karthik = people["Karthik Iyer"]
            k_account = (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id).where(Mandate.person_id == karthik.id))).scalars().first()
            ops, adviser, compliance, research = (users[UserRole.OPERATIONS], users[UserRole.ADVISER], users[UserRole.COMPLIANCE], users[UserRole.RESEARCH_CIO])

            print("\n=== The figures: each assumed, and not reviewed ===")
            counsel = await counsel_review.overview(s, firm)
            states = {r["key"]: r["counsel"]["state"] for r in counsel["figures"]}
            check("all seven are in the counsel tracker, not reviewed", [states.get(k) for k in NEW_KEYS], ["not_reviewed"] * len(NEW_KEYS))
            check("the cut-off figures say they are assumed and to verify", all("ASSUMED" in law_figures.get(k).source and "verify" in law_figures.get(k).source.lower()
                                                                            for k in NEW_KEYS[:4]), True)
            check("the cut-offs are 3:00 pm, 1:30 pm and 3:00 pm and the line Rs 2 lakh",
                  [law_figures.value(k) for k in NEW_KEYS[:4]], [900, 810, 900, 200_000])

            print("\n=== B-294: the NAV day the cut-off rule gives ===")
            check("in time on a business day: that day", rule()["nav_date"], WED)
            check("on the cut-off minute: still that day", rule(order_time="15:00")["nav_date"], WED)
            check("a minute after: the next business day", rule(order_time="15:01")["nav_date"], WED + timedelta(days=1))
            check("after the cut-off on a Friday: Monday", rule(order_on=date(2026, 11, 27), order_time="16:00")["nav_date"], date(2026, 11, 30))
            check("on a Saturday: Monday", rule(order_on=date(2026, 11, 28), order_time="10:00")["nav_date"], date(2026, 11, 30))
            check("a large purchase follows the day funds were realised", rule(amount=500_000.0, funds_realised_on=WED + timedelta(days=1))["nav_date"], WED + timedelta(days=1))
            check("a large purchase realised the same day: that day", rule(amount=500_000.0, funds_realised_on=WED)["nav_date"], WED)
            check("a large purchase after the cut-off keeps the later day", rule(amount=500_000.0, order_time="16:00", funds_realised_on=WED)["nav_date"], WED + timedelta(days=1))
            big = rule(amount=500_000.0)
            check("a large purchase with no realisation day is not assessed, and says what it needs", (big["state"], "funds were realised" in big["needs"]), ("not_assessed", True))
            check("a purchase one rupee under the line is read on its cut-off alone", rule(amount=199_999.0)["nav_date"], WED)
            check("a buy with no amount is not assessed", rule(amount=None)["state"], "not_assessed")
            check("a liquid purchase at 1:30 pm with funds in: that day", rule(rule="liquid_overnight", order_time="13:30", funds_realised_on=WED)["nav_date"], WED)
            check("a liquid purchase a minute after: the next day", rule(rule="liquid_overnight", order_time="13:31", funds_realised_on=WED)["nav_date"], WED + timedelta(days=1))
            check("a liquid purchase with no realisation day is not assessed", rule(rule="liquid_overnight", order_time="10:00")["state"], "not_assessed")
            check("a redemption has no realisation step and needs no amount", rule(side="sell", amount=None, order_time="14:59")["nav_date"], WED)
            check("a liquid redemption's cut-off is 3:00 pm", rule(side="sell", rule="liquid_overnight", order_time="14:30", amount=None)["nav_date"], WED)

            print("\n=== B-294: what the seed recorded, and the break ===")
            ov = await mf_orders.overview(s, firm)
            check("four orders recorded: one break, one not assessed, two matching", (len(ov["orders"]), ov["breaks"], ov["not_assessed"],
                                                                                    sum(1 for r in ov["orders"] if r["state"] == "matches")), (4, 1, 1, 2))
            brk = next(r for r in ov["orders"] if r["state"] == "differs")
            check("the break is a business day later than the rule, with its likely cause", (brk["gap_days"] >= 1, "later day" in brk["likely_cause"]), (True, True))
            check("the registrar and rail are named and the folio listed", ({r["rta_label"] for r in ov["orders"]}, len(ov["folios"]), ov["folios"][0]["rta"]), ({"CAMS"}, 1, "CAMS"))
            check("holidays are named as not modelled", any("holidays" in x for x in ov["not_modelled"]), True)
            bl = await blotter.for_firm(s, firm.id)
            mf = [t for t in bl["trades"] if t["mf"]]
            check("the blotter shows four with order date and NAV date apart", (len(mf), all("order_on" in t["mf"] and "nav_date" in t["mf"] for t in mf), bl["summary"]["nav_breaks"]), (4, True, 1))
            d_row = next(t["mf"] for t in mf if t["mf"]["nav_state"] == "differs")
            check("the differing row's NAV date is not its order date", d_row["nav_date"] != d_row["order_on"], True)
            check("the firm's own paper rows hold nothing", await mf_orders.for_blotter(s, Firm(jurisdiction="NZ", settings={}), [brk and __import__("uuid").uuid4()]), {})
            holding_before = (await s.execute(select(func.count()).select_from(Holding))).scalar_one()

            order = (await s.execute(select(Order).where(Order.id == __import__("uuid").UUID(brk["order_id"])))).scalar_one()
            n0 = (await s.execute(select(func.count()).select_from(MfOrderRecord))).scalar_one()
            common = dict(order_id=order.id, rail="bse_star_mf", rta="cams", cutoff_rule="standard", order_on=today - timedelta(days=3), order_time="10:00",
                          nav_date_reported=today - timedelta(days=3), document="Rail confirmation, filed")
            await refused("an unknown rail is refused", mf_orders.prepare(s, firm, UserRole.OPERATIONS, **{**common, "rail": "telegram"}), "rail is one of")
            await refused("an unknown registrar is refused", mf_orders.prepare(s, firm, UserRole.OPERATIONS, **{**common, "rta": "acme"}), "cams or kfintech")
            await refused("a bad time is refused", mf_orders.prepare(s, firm, UserRole.OPERATIONS, **{**common, "order_time": "25:99"}), "HH:MM")
            await refused("a NAV date before the order is refused", mf_orders.prepare(s, firm, UserRole.OPERATIONS, **{**common, "nav_date_reported": today - timedelta(days=9)}), "before the order")
            await refused("an order dated ahead is refused", mf_orders.prepare(s, firm, UserRole.OPERATIONS, **{**common, "order_on": today + timedelta(days=1)}), "ahead")
            await refused("no confirmation is refused", mf_orders.prepare(s, firm, UserRole.OPERATIONS, **{**common, "document": ""}), "confirmation")
            await refused("compliance does not record one", mf_orders.prepare(s, firm, UserRole.COMPLIANCE, **common), "records how an order")
            await refused("a second record needs a reason", mf_orders.prepare(s, firm, UserRole.OPERATIONS, **common), "says why")
            eq_order = Order(firm_id=firm.id, account_id=k_account.id, instrument_id=inst["INFY.NS"].id, mandate_id=k_account.mandate_id, side="buy", status="filled", quantity=1,
                             filled_quantity=1, avg_fill_price=1, reason="A share, not a fund")
            s.add(eq_order)
            await s.flush()
            await refused("a share is not a mutual fund order", mf_orders.prepare(s, firm, UserRole.OPERATIONS, **{**common, "order_id": eq_order.id}), "not a mutual fund")
            await refused("a firm outside India is refused", mf_orders.prepare(s, Firm(jurisdiction="NZ", settings={}), UserRole.OPERATIONS, **common), "Indian firm")
            check("a refusal writes nothing", (await s.execute(select(func.count()).select_from(MfOrderRecord))).scalar_one(), n0)
            fixed = await mf_orders.record(s, firm, UserRole.OPERATIONS, by="test", **{**common, "order_on": date.fromisoformat(brk["order_on"]), "order_time": brk["order_time"],
                                                                                         "nav_date_reported": date.fromisoformat(brk["expected_nav_date"]), "reason": "The registrar corrected its allotment"})
            check("a correction stands over the break, and the earlier row stays", (fixed["orders"][0]["state"] if fixed["orders"] else None,
                                                                                  (await s.execute(select(func.count()).select_from(MfOrderRecord))).scalar_one()), ("matches", n0 + 1))
            check("no break is left, and a break never wrote a holding", ((await mf_orders.overview(s, firm))["breaks"],
                                                                          (await s.execute(select(func.count()).select_from(Holding))).scalar_one()), (0, holding_before))
            check("the ledger holds the record", (await s.execute(select(func.count()).select_from(MfOrderRecord))).scalar_one() > 0, True)

            print("\n=== B-297: the group, trail and the flag ===")
            sep = await ds.overview(s, firm)
            check("two group entities, one with an ARN as stated", ([e["holds_arn"] for e in sep["entities"]].count(True), len(sep["entities"])), (1, 2))
            chk = sep["check"]
            check("one advised household is flagged for compliance, as a flag", (len(chk["flags"]), chk["flags"][0]["state"], "not a finding" in chk["flags"][0]["said"]), (1, "flag", True))
            check("the flag names the household and the entity and says the rule is counsel's", (chk["flags"][0]["household"] is not None, "counsel" in chk["flags"][0]["said"]), (True, True))
            check("the distributor's back office is named as out of scope", "back office" in chk["out_of_scope"], True)
            flagged = chk["flags"][0]["household_id"]
            hid = __import__("uuid").UUID(flagged)
            await refused("an adviser cannot record trail", ds.prepare_trail(s, firm, UserRole.ADVISER, household_id=hid, entity="Demonstration Distribution Private Limited (invented)",
                                                                           statement_on=today, document="Statement, filed"), "Compliance records")
            await refused("trail for an entity not in the group is refused", ds.prepare_trail(s, firm, UserRole.COMPLIANCE, household_id=hid, entity="Stranger Ltd",
                                                                                             statement_on=today, document="Statement, filed"), "not on file as a group entity")
            await refused("a statement dated ahead is refused", ds.prepare_trail(s, firm, UserRole.COMPLIANCE, household_id=hid, entity="Demonstration Distribution Private Limited (invented)",
                                                                                statement_on=today + timedelta(days=2), document="Statement, filed"), "ahead")
            await refused("an earlier statement does not replace a later one", ds.prepare_trail(s, firm, UserRole.COMPLIANCE, household_id=hid, entity="Demonstration Distribution Private Limited (invented)",
                                                                                               statement_on=today - timedelta(days=100), document="Statement, filed"), "later one stands")
            await refused("an ARN is required where one is held", ds.prepare_entity(s, firm, UserRole.COMPLIANCE, name="Another Ltd", relationship="sister", holds_arn=True, document="Chart, filed"), "Give the ARN")
            await refused("an adviser cannot record an entity", ds.prepare_entity(s, firm, UserRole.ADVISER, name="Another Ltd", relationship="sister", holds_arn=False, document="Chart, filed"), "Compliance or a branch")
            await ds.record_trail(s, firm, UserRole.COMPLIANCE, by="test", household_id=hid, entity="Demonstration Holdings Limited (invented)", statement_on=today, document="Statement, filed")
            chk2 = await ds.check(s, firm)
            check("trail from an entity with no ARN is a fact needed, not a flag", (len(chk2["flags"]), len(chk2["facts_needed"])), (1, 1))
            after = await ds.record_trail(s, firm, UserRole.COMPLIANCE, by="test", household_id=hid, entity="Demonstration Distribution Private Limited (invented)",
                                          statement_on=today, ceased=True, reason="The statement shows no trail from the quarter", document="Statement, filed")
            check("a ceasing statement lifts the flag", len(after["flags"]), 0)
            check("with no group entity the check is not assessed, and says none on file is not none",
                  await _no_group_state(), ("not_assessed", True))

            print("\n=== B-297: direct against regular, from recorded ratios ===")
            karthik_home = karthik.household_id
            for sym, qty in (("PPFAS-FLEXI-REGULAR", 1000), ("HDFC-CORPBOND-REGULAR", 500)):
                s.add(Holding(firm_id=firm.id, account_id=k_account.id, instrument_id=inst[sym].id, quantity=qty, market_value=0, cost_basis=0))
                s.add(Price(firm_id=firm.id, instrument_id=inst[sym].id, as_of=today, close=80.0, currency="INR", source="test", is_real=True))
            await s.flush()
            cmp = await ds.comparison(s, firm, household_id=karthik_home)
            by = {r["symbol"]: r for r in cmp["holdings"]}
            ppfas = by["PPFAS-FLEXI-REGULAR"]
            check("a regular holding with both ratios: 0.72 points, Rs 576 a year on Rs 80,000", (ppfas["state"], ppfas["gap_points"], ppfas["yearly_difference"]), ("assessed", 0.72, 576.0))
            check("the ratios carry their dates and documents", (ppfas["direct"]["ter"], ppfas["regular"]["ter"], bool(ppfas["direct"]["document"])), (0.63, 1.35, True))
            check("a scheme with only its direct ratio is not assessed, and none is estimated", (by["HDFC-CORPBOND-REGULAR"]["state"], "regular plan's expense ratio" in by["HDFC-CORPBOND-REGULAR"]["needs"]),
                  ("not_assessed", True))
            check("it recommends no plan", "recommends no plan" in cmp["said"], True)
            await refused("a ratio for no scheme on file is refused", ds.prepare_ter(s, firm, UserRole.RESEARCH_CIO, scheme="Nonesuch Fund", plan="direct", ter_pct=0.5, as_of=today, document="Doc, filed"),
                          "No fund on file")
            await refused("a ratio over 5% is refused", ds.prepare_ter(s, firm, UserRole.RESEARCH_CIO, scheme="Parag Parikh Flexi Cap Fund", plan="direct", ter_pct=7, as_of=today, document="Doc, filed"), "not above 5")
            await refused("an adviser does not record a ratio", ds.prepare_ter(s, firm, UserRole.ADVISER, scheme="Parag Parikh Flexi Cap Fund", plan="direct", ter_pct=0.5, as_of=today, document="Doc, filed"), "Research")
            await refused("an earlier document does not replace a later one", ds.prepare_ter(s, firm, UserRole.RESEARCH_CIO, scheme="Parag Parikh Flexi Cap Fund", plan="direct", ter_pct=0.5,
                                                                                             as_of=today - timedelta(days=200), document="Doc, filed"), "later document")

            print("\n=== B-298: each adviser's certificate ===")
            cert = await adviser_certification.overview(s, firm)
            me = cert["people"][0]
            check("the adviser's X-A is valid and X-B is due within 90 days: due soon overall", ([c["state"] for c in me["certificates"]], me["state"]), (["valid", "due_soon"], "due_soon"))
            check("the required kinds follow the licences held", cert["required"], ["NISM Series X-A", "NISM Series X-B"])
            check("it says it does not say a person is qualified", "does not say" in cert["said"], True)
            extra = User(firm_id=firm.id, email="second.adviser@demo.invalid", full_name="Second Adviser (invented)", hashed_password="x", role=UserRole.ADVISER, title="Adviser")
            third = User(firm_id=firm.id, email="third.adviser@demo.invalid", full_name="Third Adviser (invented)", hashed_password="x", role=UserRole.ADVISER, title="Adviser")
            s.add_all([extra, third])
            await s.flush()
            await adviser_certification.record(s, firm, UserRole.COMPLIANCE, by="test", user_id=extra.id, kind="nism_xa", certificate_number="X-0002", issued_on=today - timedelta(days=1200),
                                               expires_on=today - timedelta(days=20), document="Certificate, filed")
            await adviser_certification.record(s, firm, UserRole.COMPLIANCE, by="test", user_id=extra.id, kind="nism_xb", certificate_number="X-0003", issued_on=today - timedelta(days=200),
                                               expires_on=today + timedelta(days=900), document="Certificate, filed")
            cert = await adviser_certification.overview(s, firm)
            st = {p["name"]: p["state"] for p in cert["people"]}
            check("expired, not on file and due soon are told apart", (st["Second Adviser (invented)"], st["Third Adviser (invented)"]), ("expired", "not_on_file"))
            await refused("an adviser does not record a certificate", adviser_certification.prepare(s, firm, UserRole.ADVISER, user_id=extra.id, kind="nism_xa", certificate_number="X-9", issued_on=today,
                                                                                                      expires_on=today + timedelta(days=9), document="Certificate, filed"), "Compliance records")
            await refused("a certificate that expires before it is issued is refused", adviser_certification.prepare(s, firm, UserRole.COMPLIANCE, user_id=extra.id, kind="nism_xa", certificate_number="X-9",
                                                                                                                     issued_on=today, expires_on=today - timedelta(days=1), document="Certificate, filed"), "expires after")
            await refused("an unknown kind is refused", adviser_certification.prepare(s, firm, UserRole.COMPLIANCE, user_id=extra.id, kind="nism_zz", certificate_number="X-9", issued_on=today,
                                                                                       expires_on=today + timedelta(days=9), document="Certificate, filed"), "one of")
            await refused("an earlier certificate does not replace a later one", adviser_certification.prepare(s, firm, UserRole.COMPLIANCE, user_id=extra.id, kind="nism_xb", certificate_number="X-8",
                                                                                                                 issued_on=today - timedelta(days=400), expires_on=today + timedelta(days=100),
                                                                                                                 document="Certificate, filed"), "later one stands")
            await refused("a client is not an adviser to certify", adviser_certification.prepare(s, firm, UserRole.COMPLIANCE, user_id=__import__("uuid").uuid4(), kind="nism_xa", certificate_number="X-9",
                                                                                                    issued_on=today, expires_on=today + timedelta(days=9), document="Certificate, filed"), "active person")
            items = (await india_deadlines.firm_items(s, firm))["items"]
            certs = [i for i in items if i["kind"] == "certification"]
            check("the countdown carries the certificates that are not valid", (sum(1 for i in certs if i["state"] == "overdue"), sum(1 for i in certs if i["state"] == "due_soon"),
                                                                              sum(1 for i in certs if i["state"] == "not_dated")), (1, 1, 2))
            plan = await india_reminders.plan(s, firm)
            ours = [r for r in plan["items"] if r["item"]["kind"] == "certification"]
            check("a reminder is planned for the expired and the due-soon, not for what is not on file", len(ours), 2)
            r1 = await india_reminders.raise_due(s, firm)
            n_tasks = (await s.execute(select(func.count()).select_from(Task).where(Task.title.like("India: NISM%")))).scalar_one()
            r2 = await india_reminders.raise_due(s, firm)
            check("the tasks are raised once, for compliance, and a second run raises nothing", (n_tasks, r2["raised"]), (2, 0))
            tk = (await s.execute(select(Task).where(Task.title.like("India: NISM%")))).scalars().first()
            check("they go to the compliance user", tk.assigned_to, compliance.id)

            print("\n=== B-298: the warning on a proposal, never a block ===")
            adviser_homes = await advisers.of_households(s, firm.id)
            home_id = next((h for h, u in adviser_homes.items() if u == adviser.id), None)
            check("a household has the demonstration adviser on record", home_id is not None, True)
            ok_now = await adviser_certification.warning_for_household(s, firm, home_id)
            check("a valid X-A and one due soon: no warning", ok_now, None)
            await adviser_certification.record(s, firm, UserRole.COMPLIANCE, by="test", user_id=adviser.id, kind="nism_xa", certificate_number="DEMO-XA-0002", issued_on=today - timedelta(days=100),
                                               expires_on=today - timedelta(days=1), document="Certificate, filed")
            w = await adviser_certification.warning_for_household(s, firm, home_id)
            check("an expired X-A is a warning that names the adviser and says approving is not stopped", (w is not None and "not stopped" in w["warning"], w["adviser"] if w else None),
                  (True, adviser.full_name))
            adv = ToolExecutors(s, adviser.id, UserRole.ADVISER, firm.id)
            fake = SimpleNamespace(id=__import__("uuid").uuid4(), agent_key="drift_rebalancing", subject_type="household", subject_id=home_id)
            check("the card for a book-moving proposal carries the warning", "NISM Series X-A" in (await adv._certification_warning(fake) or ""), True)
            fake_report = SimpleNamespace(id=__import__("uuid").uuid4(), agent_key="research_reporting", subject_type="household", subject_id=home_id)
            check("a proposal that moves no book carries none", await adv._certification_warning(fake_report), None)
            check("a household with no adviser carries none", await adviser_certification.warning_for_household(s, firm, None), None)
            check("a firm outside India reads none", await adviser_certification.warning_for_household(s, Firm(jurisdiction="NZ", settings={}), home_id), None)

            print("\n=== B-298: advertisement pre-approval ===")
            ads = await ad_approval.overview(s, firm)
            check("the seed left one approved, one rejected, one waiting", (ads["approved"], ads["rejected"], ads["pending"]), (1, 1, 1))
            approved = next(r for r in ads["submissions"] if r["state"] == "approved")
            rejected = next(r for r in ads["submissions"] if r["state"] == "rejected")
            pending = next(r for r in ads["submissions"] if r["state"] == "pending")
            check("the rejected copy was found promising a return", rejected["blocks"] >= 1, True)
            sub = await s.get(AdSubmission, __import__("uuid").UUID(approved["id"]))
            check("the approved wording is kept with its hash", (ad_approval.text_hash(sub.body) == sub.text_hash, len(sub.text_hash)), (True, 64))
            check("leading and trailing space does not change the hash", ad_approval.text_hash("  " + sub.body + "\n"), sub.text_hash)
            good = await ad_approval.verify_citation(s, firm, approved["approval_id"], sub.body)
            check("a citation with the approved text reads approved", (good["state"], good["approved_by"] is not None), ("approved", True))
            changed = await ad_approval.verify_citation(s, firm, approved["approval_id"], sub.body + " Now with guaranteed returns.")
            check("changed wording reads changed, needing a new submission", (changed["state"], "new submission" in changed["said"]), ("text_changed", True))
            check("an unknown id is not found", (await ad_approval.verify_citation(s, firm, __import__("uuid").uuid4(), "x"))["state"], "not_found")
            rej_d = (await s.execute(select(AdDecision).where(AdDecision.decision == "rejected"))).scalars().first()
            check("a rejection cannot be cited as an approval", (await ad_approval.verify_citation(s, firm, rej_d.id, "x"))["state"], "rejected")
            await refused("the same wording is not submitted twice", ad_approval.prepare_submit(s, firm, UserRole.ADVISER, title="Again", text=sub.body, channel="email"), "already")
            await refused("a title is required", ad_approval.prepare_submit(s, firm, UserRole.ADVISER, title="", text="Some marketing copy here.", channel="email"), "title")
            await refused("an unknown channel is refused", ad_approval.prepare_submit(s, firm, UserRole.ADVISER, title="A piece", text="Some marketing copy here.", channel="carrier pigeon"), "channel is one of")
            await refused("a client does not submit", ad_approval.prepare_submit(s, firm, UserRole.CLIENT, title="A piece", text="Some marketing copy here.", channel="email"), "Staff submit")
            await refused("an adviser does not decide", ad_approval.prepare_decision(s, firm, adviser, submission_id=pending["id"], decision="approved", reason="Looks fine to me"), "Compliance approves")
            await refused("a decided submission is not decided again", ad_approval.prepare_decision(s, firm, compliance, submission_id=approved["id"], decision="rejected", reason="Changed my mind"), "already decided")
            by_compliance = await ad_approval.submit(s, firm, compliance, title="Compliance's own piece", channel="website", text="A note on how we work with clients, in plain words, with no figures.")
            await refused("the person who submitted a piece does not decide it", ad_approval.prepare_decision(s, firm, compliance, submission_id=by_compliance["submissions"][0]["id"], decision="approved",
                                                                                                                reason="It is fine"), "does not decide it")
            await refused("a decision needs a reason", ad_approval.prepare_decision(s, firm, compliance, submission_id=pending["id"], decision="approved", reason=""), "reason")
            await refused("a decision is approved or rejected", ad_approval.prepare_decision(s, firm, compliance, submission_id=pending["id"], decision="maybe", reason="Not sure about it"), "approved or rejected")
            resub = await ad_approval.submit(s, firm, adviser, title="Returns campaign, reworded", channel="social", supersedes_id=rejected["id"],
                                             text="Our portfolios are built around each client's goals. Investments in the securities market are subject to market risks.")
            check("a changed text is a new submission that follows the rejected one", resub["submissions"][0]["follows"], rejected["id"])
            check("a submission cannot be edited: there is no edit route or tool", ("edit_ad_copy" in TOOLS, any(r.path.endswith("/edit") for r in __import__("app.api.licences", fromlist=["router"]).router.routes)), (False, False))

            print("\n=== The tools and their cards ===")
            writes = ("record_mf_order", "record_group_entity", "record_group_trail", "record_scheme_ter", "record_adviser_certification", "submit_ad_copy", "decide_ad_copy")
            reads = ("read_mf_orders", "read_distribution_separation", "read_adviser_certifications", "read_ad_approvals")
            check("every write pauses for confirmation and every read changes nothing", ([TOOLS[k].change_state for k in writes], [TOOLS[k].change_state for k in reads]),
                  ([ToolChangeState.PAUSE_FOR_CONFIRMATION] * 7, [ToolChangeState.NO] * 4))
            check("no tool is open to a client", all(UserRole.CLIENT not in TOOLS[k].roles_required for k in writes + reads), True)
            ops_ex = ToolExecutors(s, ops.id, UserRole.OPERATIONS, firm.id)
            comp_ex = ToolExecutors(s, compliance.id, UserRole.COMPLIANCE, firm.id)
            res_ex = ToolExecutors(s, research.id, UserRole.RESEARCH_CIO, firm.id)
            card = await ops_ex.preview_change("record_mf_order", {**{k: str(v) for k, v in common.items()}, "order_id": str(order.id), "reason": "Another look at the confirmation",
                                                                     "order_on": brk["order_on"], "order_time": "16:10", "nav_date_reported": brk["order_on"]})
            check("the order card says a difference is a break and nothing is repaired", any("break, nothing repaired" in r["value"] for r in card["rows"]), True)
            card = await comp_ex.preview_change("record_group_entity", {"name": "Third Group Co (invented)", "relationship": "sister", "holds_arn": True, "arn": "ARN-9", "document": "Chart, filed"})
            check("the entity card says the ARN is not checked", any("not checked against AMFI" in r["value"] for r in card["rows"]), True)
            card = await res_ex.preview_change("record_scheme_ter", {"scheme": "UTI Nifty 50 Index Fund", "plan": "direct", "ter_pct": 0.18, "as_of": today.isoformat(), "document": "Doc, filed"})
            check("the ratio card shows the one it replaces", any("replaces" in r["label"].lower() and "%" in r["value"] for r in card["rows"]), True)
            card = await comp_ex.preview_change("record_adviser_certification", {"user_id": str(third.id), "kind": "nism_xa", "certificate_number": "T-1", "issued_on": (today - timedelta(days=10)).isoformat(),
                                                                                    "expires_on": (today + timedelta(days=1000)).isoformat(), "document": "Certificate, filed"})
            check("the certificate card reads the state it gives", any(r["label"] == "Reads as" and r["value"] == "Valid" for r in card["rows"]), True)
            card = await adv.preview_change("submit_ad_copy", {"title": "Guaranteed", "text": "We offer guaranteed returns on every portfolio.", "channel": "email"})
            check("the submission card shows the check and says nothing is published", (any(r["label"] == "Check" and "blocking" in r["value"] for r in card["rows"]),
                                                                                       any("Nothing is published" in r["value"] for r in card["rows"])), (True, True))
            card = await comp_ex.preview_change("decide_ad_copy", {"submission_id": pending["id"], "decision": "approved", "reason": "Reads plainly, with its risk words"})
            check("the decision card shows the copy and the check", (any(r["label"] == "Copy" for r in card["rows"]), any(r["label"] == "Check" for r in card["rows"])), (True, True))
            await refused("a card for an adviser's decision is refused", adv.preview_change("decide_ad_copy", {"submission_id": pending["id"], "decision": "approved", "reason": "Reads plainly"}), "Compliance approves")
            wrote = await comp_ex.execute("decide_ad_copy", {"submission_id": pending["id"], "decision": "approved", "reason": "Reads plainly, with its risk words"})
            check("the tool decides it, the page's list moves, and it carries a message", (wrote["approved"], "does not say the firm is compliant" in wrote["message"]), (2, True))
            read = await adv.execute("read_ad_approvals", {"approval_id": str((await s.execute(select(AdDecision).where(AdDecision.decision == "approved"))).scalars().first().id)})
            check("the read tool checks a citation", read["state"], "approved")
            read = await adv.execute("read_mf_orders", {})
            check("the read tool says holidays are not modelled", "holidays" in read["message"], True)
            read = await comp_ex.execute("read_distribution_separation", {})
            check("the separation read says a flag is not a finding and the back office is out", ("not a finding" in read["message"] or "flags and not findings" in read["message"], "back office" in read["message"]), (True, True))
            read = await adv.execute("read_adviser_certifications", {})
            check("the certificates read says not on file is not none held", "not on file does not mean" in read["message"].lower(), True)

            print("\n=== The page's routes and a firm outside India ===")
            from app.api import licences as api

            paths = {r.path for r in api.router.routes}
            check("each read and each write has its route", all(p in paths for p in ("/api/licences/mf-orders", "/api/licences/separation", "/api/licences/group-entities", "/api/licences/group-trail",
                                                                                    "/api/licences/scheme-ter", "/api/licences/certifications", "/api/licences/ad-approvals",
                                                                                    "/api/licences/ad-approvals/{submission_id}/decide")), True)
            from app.aurea_core import licences as licence_core

            extras = await licence_core._india_pack(s, firm)
            check("the Licences page carries them", (all(k in extras for k in ("mf_orders", "separation", "ad_approvals")), "certifications" in extras["governance"]), (True, True))
            nz = Firm(jurisdiction="NZ", settings={})
            check("none of them applies outside India", (mf_orders.applies(nz), ds.applies(nz), adviser_certification.applies(nz), ad_approval.applies(nz)), (False,) * 4)

            print("\n=== The seed, a second time ===")
            check("the batch C seed adds nothing a second time", await india_batch_c.add(s, firm), 0)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def _no_group_state():
    """A firm with no group entity on file: the check is not assessed."""
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            firm = await india_demo.build(s)
            await s.execute(__import__("sqlalchemy").delete(__import__("app.models.india_batch_c", fromlist=["GroupEntity"]).GroupEntity))
            await s.flush()
            c = await ds.check(s, firm)
            return (c["state"], "does not mean" in c["said"])
    finally:
        await db.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
