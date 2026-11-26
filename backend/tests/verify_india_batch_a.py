"""Prove India batch A: overseas remittances under the Liberalised Remittance Scheme (B-293), a SIP's debit mandate (B-295), the shelf's other
classes and a pledge of units (B-296), and the tax habits (B-299).

Covers each figure as a law figure with its source and "not reviewed" by default; a remittance recorded from the bank's advice (added never edited,
a reversal takes one out), the year's headroom as figures, "not assessed" with none on file, and a direct order for an overseas product refused over
the headroom (naming the figures) or shown with the tax at source as an estimate; a mandate verified from a verified bank account, its state by the
day, a lapsed one or an instalment above its limit named as why a SIP was missed, and a SIP with none warned and still tried; each class's rule,
the statement's rate never assumed, NPS's split on the corpus as stated, a ULIP's lock-in and charge against the cap; a pledge excluded from a
direct sale, from the rebalancer and from settlement, and released; gain harvesting laid out first-in first-out, dividends with the tax deducted
above the threshold, a gift's exemption, clubbing added back to the giver, and every refusal; and the tools and their cards.

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_batch_a
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (counsel_review, firm_time, held_away, india_savings, law_figures, lrs, order_guard, pledges, sip as sip_core, sip_mandates,
                            tax_habits, tax_india, tax_india_full)
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AssetClass, MarketType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.india_batch_a import DebitMandate, LrsRemittance, SecuritiesPledge, TaxGift
from app.models.money_in import BankLink
from app.models.portfolio import Holding, Instrument, Price
from app.models.sip import Sip
from app.models.tenant import Firm
from seed import india_b388, india_batch_a, india_demo, india_goals

PASS, FAIL = [], []
ERRORS = (lrs.LrsError, sip_mandates.MandateError, india_savings.SavingsError, pledges.PledgeError, tax_habits.HabitError, held_away.HeldAwayError, ExecutorError)


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


NEW_KEYS = ("in_lrs_limit_usd", "in_lrs_tcs_threshold", "in_lrs_tcs_rate_other", "in_lrs_tcs_rate_edu_medical", "in_lrs_tcs_rate_edu_loan", "in_upi_autopay_limit_mf",
            "in_ppf_rule", "in_ssy_rule", "in_scss_rule", "in_nsc_rule", "in_nps_rule", "in_epf_rule", "in_gsec_rule", "in_mld_rule", "in_reit_invit_rule", "in_sgb_rule",
            "in_ulip_lock_in_years", "in_ulip_fmc_cap", "in_pledge_rule", "in_dividend_tds_threshold", "in_dividend_tds_rate", "in_clubbing_minor_exemption",
            "in_clubbing_rule", "in_gift_relative_exempt", "in_gift_other_threshold")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            # The demonstration rows of B-388 and B-394 (their own SIPs and mandates, proved in verify_india_money and verify_india_goals) are
            # set aside, so this reads batch A's.
            for model in (Sip, DebitMandate):
                await s.execute(model.__table__.delete().where(model.recorded_by.in_((india_b388.BY, india_goals.BY))))
            await s.commit()
            today = firm_time.today(firm)
            users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            inst = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
            karthik, lakshmi, rajesh, meera, aarav = (people[n] for n in ("Karthik Iyer", "Lakshmi Iyer", "Rajesh Sharma", "Meera Sharma", "Aarav Sharma"))
            mandates = {m.person_id: m for m in (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.person_id.isnot(None)))).scalars().all()}
            k_mandate, r_mandate = mandates[karthik.id], mandates[rajesh.id]
            k_account = (await s.execute(select(Account).where(Account.mandate_id == k_mandate.id))).scalars().first()
            r_account = (await s.execute(select(Account).where(Account.mandate_id == r_mandate.id))).scalars().first()

            print("\n=== The figures: each has its source and reads not reviewed ===")
            counsel = await counsel_review.overview(s, firm)
            states = {r["key"]: r["counsel"]["state"] for r in counsel["figures"]}
            check("all twenty-five are in the counsel tracker, not reviewed", [states.get(k) for k in NEW_KEYS], ["not_reviewed"] * len(NEW_KEYS))
            check("every one says it was not re-read or that it is assumed", all(("re-read" in law_figures.get(k).source or "ASSUMED" in law_figures.get(k).source
                                                                              or "not read" in law_figures.get(k).source or "was not" in law_figures.get(k).source) for k in NEW_KEYS), True)
            check("the limit is USD 250,000 and the tax at source starts above Rs 10 lakh at 20%",
                  (law_figures.value("in_lrs_limit_usd"), law_figures.value("in_lrs_tcs_threshold"), law_figures.value("in_lrs_tcs_rate_other")), (250_000, 1_000_000, 0.20))
            check("the tax-at-source figures say they are assumed and to verify", all("ASSUMED" in law_figures.get(k).source for k in ("in_lrs_tcs_threshold", "in_lrs_tcs_rate_other")), True)

            print("\n=== B-293: remittances, from the bank's advice ===")
            view = await lrs.person_view(s, firm, karthik, today)
            check("Karthik's year: USD 100,000 of 250,000, USD 150,000 left", (view["remitted_usd"], view["headroom_usd"], view["limit_usd"]), (100_000.0, 150_000.0, 250_000))
            check("the rupees counted and the tax collected are the advices' own", (view["remitted_inr"], view["tcs_collected"], view["inr_unknown"]), (8_460_000.0, 1_492_000.0, 0))
            check("he is over the tax threshold, so nothing is left before it", view["before_tcs_inr"], 0.0)
            check("the rate used is the latest advice's", view["rate"]["inr_per_usd"], 85.0)
            lv = await lrs.person_view(s, firm, lakshmi, today)
            check("Lakshmi has none on file: not assessed, which is not nothing remitted", (lv["state"], "does not mean none was made" in lv["said"]), ("not_assessed", True))
            n0 = (await s.execute(select(func.count()).select_from(LrsRemittance))).scalar_one()
            await refused("an advice dated ahead is refused", lrs.prepare(s, firm, UserRole.ADVISER, person_id=karthik.id, remitted_on=today + timedelta(days=1), amount_usd=100,
                                                                         purpose="investment", bank="B1", document="Advice, filed"), "ahead")
            await refused("an unknown purpose is refused", lrs.prepare(s, firm, UserRole.ADVISER, person_id=karthik.id, remitted_on=today, amount_usd=100, purpose="holiday",
                                                                      bank="B1", document="Advice, filed"), "purpose is one of")
            await refused("no amount is refused", lrs.prepare(s, firm, UserRole.ADVISER, person_id=karthik.id, remitted_on=today, purpose="investment", bank="B1",
                                                             document="Advice, filed"), "amount")
            await refused("no document is refused", lrs.prepare(s, firm, UserRole.ADVISER, person_id=karthik.id, remitted_on=today, amount_usd=100, purpose="investment",
                                                               bank="B1", document=""), "advice is filed")
            await refused("compliance does not record one", lrs.prepare(s, firm, UserRole.COMPLIANCE, person_id=karthik.id, remitted_on=today, amount_usd=100,
                                                                       purpose="investment", bank="B1", document="Advice, filed"), "records a remittance")
            await refused("a firm outside India is refused", lrs.prepare(s, Firm(jurisdiction="NZ", settings={}), UserRole.ADVISER, person_id=karthik.id, remitted_on=today,
                                                                         amount_usd=1, purpose="other", bank="B1", document="Advice, filed"), "Indian firm")
            check("a card writes nothing by being refused", (await s.execute(select(func.count()).select_from(LrsRemittance))).scalar_one(), n0)
            await lrs.record(s, firm, UserRole.OPERATIONS, by="ops", person_id=lakshmi.id, remitted_on=today - timedelta(days=3), amount_usd=5_000, purpose="education",
                             bank="A demonstration bank (invented)", document="Advice, filed")
            lv = await lrs.person_view(s, firm, lakshmi, today)
            check("recorded with only the dollars, the rupees are unknown and the tax-threshold reading says so", (lv["state"], lv["remitted_usd"], lv["inr_unknown"], lv["before_tcs_inr"]),
                  ("assessed", 5_000.0, 1, None))
            row = (await s.execute(select(LrsRemittance).where(LrsRemittance.person_id == lakshmi.id))).scalar_one()
            await lrs.record(s, firm, UserRole.OPERATIONS, by="ops", person_id=lakshmi.id, reverses_id=row.id, document="Bank's returned-funds advice", reason="The bank returned the funds")
            lv = await lrs.person_view(s, firm, lakshmi, today)
            check("a reversal takes it out of the year and both rows stay", (lv["remitted_usd"], (await s.execute(select(func.count()).select_from(LrsRemittance).where(
                LrsRemittance.person_id == lakshmi.id))).scalar_one()), (0.0, 2))
            await refused("it cannot be reversed twice", lrs.prepare(s, firm, UserRole.OPERATIONS, person_id=lakshmi.id, reverses_id=row.id, document="Advice, filed", reason="Again, in error"),
                          "already reversed")

            print("\n=== B-293: a direct order for an overseas product ===")
            voo = Instrument(firm_id=firm.id, symbol="VOO-DEMO", name="An overseas index ETF (demonstration)", asset_class=AssetClass.EQUITY, market_type=MarketType.PUBLIC,
                             currency="USD", values_tags={"tax_class": "foreign_shares"})
            s.add(voo)
            await s.flush()
            check("an instrument priced in dollars is overseas, an Indian one is not", (lrs.is_overseas(voo, firm), lrs.is_overseas(inst["NIFTYBEES.NS"], firm)), (True, False))
            buy = lambda qty, px=500.0: {"side": "buy", "symbol": "VOO-DEMO", "instrument_id": str(voo.id), "quantity": qty, "est_price": px, "est_value": qty * px}  # noqa: E731
            ok_r, notes = await lrs.order_review(s, firm, k_mandate, [buy(100)], {"VOO-DEMO": voo})
            check("USD 50,000 is inside the USD 150,000 left: not refused, and says what is left", (ok_r, "USD 100,000 would be left" in notes[0]), ([], True))
            check("the tax at source is laid out as an estimate, on the part above the threshold",
                  ("Tax at source, estimated" in notes[1], "20%" in notes[1], "An estimate for a qualified person" in notes[1]), (True, True, True))
            refusal, _ = await lrs.order_review(s, firm, k_mandate, [buy(320)], {"VOO-DEMO": voo})
            check("USD 160,000 is refused, naming what is asked, what is remitted, the limit and the shortfall",
                  all(x in refusal[0] for x in ("USD 160,000", "USD 100,000", "USD 250,000", "short by USD 10,000")), True)
            check("exactly the headroom is not refused", (await lrs.order_review(s, firm, k_mandate, [buy(300)], {"VOO-DEMO": voo}))[0], [])
            r_ref, r_notes = await lrs.order_review(s, firm, r_mandate, [buy(10_000)], {"VOO-DEMO": voo})
            check("a person with no remittance on file is warned, not refused", (r_ref, "not assessed" in r_notes[0]), ([], True))
            sell = {**buy(5000), "side": "sell"}
            check("a sale is never stopped", await lrs.order_review(s, firm, k_mandate, [sell], {"VOO-DEMO": voo}), ([], []))
            check("an Indian instrument reads nothing here", await lrs.order_review(s, firm, k_mandate, [{"side": "buy", "symbol": "NIFTYBEES.NS", "est_value": 9e9}],
                                                                                   {"NIFTYBEES.NS": inst["NIFTYBEES.NS"]}), ([], []))
            trust_m = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.entity_id.isnot(None)))).scalars().first()
            t_ref, t_notes = await lrs.order_review(s, firm, trust_m, [buy(10)], {"VOO-DEMO": voo})
            check("an entity's mandate is named as not modelled, not read against the individual limit", (t_ref, "not modelled" in t_notes[0]), ([], True))
            await lrs.record(s, firm, UserRole.ADVISER, by="adviser", person_id=rajesh.id, remitted_on=today - timedelta(days=10), amount_usd=10_000, amount_inr=850_000,
                             purpose="investment", bank="A demonstration bank (invented)", tcs=0, document="Advice, filed")
            guard = await order_guard.review(s, firm=firm, mandate=r_mandate, account=r_account, orders=[buy(500)], instruments={"VOO-DEMO": voo}, unpriced=[])
            check("order_guard carries the refusal", any("short by USD 10,000" in r for r in guard["refused"]), True)
            guard = await order_guard.review(s, firm=firm, mandate=r_mandate, account=r_account, orders=[buy(20)], instruments={"VOO-DEMO": voo}, unpriced=[])
            check("and the notes for an order inside it", (any("Overseas" in n for n in guard["notes"]), [r for r in guard["refused"] if "remit" in r]), (True, []))
            check("not modelled is named: GIFT City and overseas mutual fund limits", ("GIFT City" in " ".join(lrs.NOT_MODELLED), "mutual fund" in " ".join(lrs.NOT_MODELLED)), (True, True))

            print("\n=== B-295: a SIP's debit mandate ===")
            sips = (await s.execute(select(Sip).where(Sip.firm_id == firm.id))).scalars().all()
            k_sip = next(x for x in sips if x.account_id == k_account.id)
            c_sip = next(x for x in sips if x.account_id != k_account.id)
            behind = await sip_mandates.for_sip(s, k_sip, today)
            check("Karthik's SIP has a NACH mandate in force that fits the instalment", (behind["state"], behind["fits"], behind["row"].reference), ("active", True, "UMRN-DEMO-IYER-0001"))
            none = await sip_mandates.for_sip(s, c_sip, today)
            check("the trust's SIP has none: mandate not on file, warned", (none["state"], none["label"], "Mandate not on file" in none["warning"]), ("none", "Mandate not on file", True))
            ov = await sip_mandates.overview(s, firm)
            check("the overview counts the SIP without one", (ov["without_mandate"], len(ov["mandates"]), ov["failing"]), (1, 1, 0))
            link_n = (await s.execute(select(func.count()).select_from(BankLink))).scalar_one()
            terms = dict(account_id=k_account.id, reference="UMRN-DEMO-IYER-0002", mandate_type="nach", per_debit_limit=5_000, starts_on=today - timedelta(days=5),
                         expires_on=today + timedelta(days=300), last4="4821", status="active", document="Registration, filed")
            await refused("a bank account not verified is refused, saying to verify it", sip_mandates.prepare(s, firm, UserRole.ADVISER, **{**terms, "last4": "9999"}), "not verified")
            await refused("a mandate that expires before it starts is refused", sip_mandates.prepare(s, firm, UserRole.ADVISER, **{**terms, "expires_on": today - timedelta(days=9)}), "cannot expire")
            await refused("an unknown type is refused", sip_mandates.prepare(s, firm, UserRole.ADVISER, **{**terms, "mandate_type": "cheque"}), "NACH or UPI")
            await refused("no reference is refused", sip_mandates.prepare(s, firm, UserRole.ADVISER, **{**terms, "reference": ""}), "reference")
            await refused("the same registration twice is refused", sip_mandates.prepare(s, firm, UserRole.ADVISER, **{**terms, "reference": "UMRN-DEMO-IYER-0001", "per_debit_limit": 25_000,
                                                                                                                       "starts_on": today - timedelta(days=200), "expires_on": today + timedelta(days=3650)}), "already recorded")
            await refused("a cancellation with no mandate under the reference is refused", sip_mandates.prepare(s, firm, UserRole.ADVISER, **{**terms, "reference": "UMRN-NONE", "cancel": True,
                                                                                                                                                 "reason": "The client cancelled"}), "no mandate")
            bank = (await s.execute(select(BankLink).where(BankLink.last4 == "4821"))).scalar_one()
            bank.third_party = True
            await s.flush()
            await refused("an account in a name outside the household is refused", sip_mandates.prepare(s, firm, UserRole.ADVISER, **terms), "outside the household")
            bank.third_party = False
            await s.flush()
            check("none of that wrote a mandate or a link", ((await s.execute(select(func.count()).select_from(DebitMandate))).scalar_one(), (await s.execute(select(func.count()).select_from(BankLink))).scalar_one()),
                  (1, link_n))
            await sip_mandates.record(s, firm, UserRole.ADVISER, by="adviser", **terms)
            lim = (await s.execute(select(DebitMandate).where(DebitMandate.reference == "UMRN-DEMO-IYER-0002"))).scalar_one()
            check("an instalment above the mandate's limit is named, with the limit", ("limit of 5,000" in sip_mandates.reason_unfit(lim, 10_000, today), sip_mandates.reason_unfit(lim, 4_000, today)), (True, None))
            await sip_mandates.record(s, firm, UserRole.ADVISER, by="adviser", account_id=k_account.id, reference="UMRN-DEMO-IYER-0002", cancel=True, document="Bank's cancellation notice",
                                      reason="The client cancelled it at the bank")
            ov = await sip_mandates.overview(s, firm)
            check("a cancellation is a second row, and the latest stands", ({m["reference"]: m["state"] for m in ov["mandates"]}, (await s.execute(select(func.count()).select_from(DebitMandate).where(
                DebitMandate.reference == "UMRN-DEMO-IYER-0002"))).scalar_one()), ({"UMRN-DEMO-IYER-0001": "active", "UMRN-DEMO-IYER-0002": "cancelled"}, 2))
            await sip_mandates.record(s, firm, UserRole.ADVISER, by="adviser", **{**terms, "reference": "UPI-DEMO-IYER-1", "mandate_type": "upi_autopay", "per_debit_limit": 150_000})
            ov = await sip_mandates.overview(s, firm)
            check("a UPI Autopay mandate above the mutual-fund line is named, not refused", [m["over_upi_line"] for m in ov["mandates"] if m["reference"] == "UPI-DEMO-IYER-1"], [True])
            check("the line is a law figure counsel has not read", ov["line"]["counsel"]["state"] if "counsel" in ov["line"] else "not_reviewed", "not_reviewed")
            await sip_mandates.record(s, firm, UserRole.ADVISER, by="adviser", account_id=k_account.id, reference="UMRN-DEMO-IYER-0001", mandate_type="nach", per_debit_limit=25_000,
                                      starts_on=today - timedelta(days=200), expires_on=today - timedelta(days=1), last4="4821", status="active", document="The bank's note of the expiry")
            await sip_mandates.record(s, firm, UserRole.ADVISER, by="adviser", account_id=k_account.id, reference="UPI-DEMO-IYER-1", cancel=True, document="Bank's cancellation notice",
                                      reason="The client moved to the NACH mandate")
            late = today.replace(day=28)
            await s.execute(Price.__table__.update().values(is_real=True))
            ran = await sip_core.run_due(s, firm, today=late)
            check("both SIPs are tried and missed (no NAV in the demonstration)", (ran["bought"], ran["missed"]), (0, 2))
            await s.refresh(k_sip)
            await s.refresh(c_sip)
            hist_k, hist_c = k_sip.history[-1], c_sip.history[-1]
            check("Karthik's miss names the expired mandate", ("expired" in hist_k["reason"], "UMRN-DEMO-IYER-0001" in hist_k["reason"]), (True, True))
            check("the trust's instalment was still tried, and carries the warning: mandate not on file", (hist_c.get("warning"), "expired" in hist_c["reason"]), ("mandate not on file", False))
            row = [x for x in (await sip_core.for_firm(s, firm))["sips"] if x["id"] == str(c_sip.id)][0]
            check("the SIP list says so on its row", row["mandate"]["label"], "Mandate not on file")

            print("\n=== B-296: the shelf's other classes and the rule each carries ===")
            check("every class has its rule figures in law_figures", all(all(k in law_figures.VERSIONS for k in c["rules"]) for c in india_savings.KINDS.values()), True)
            table = {t["kind"]: t for t in india_savings.rules_table(today)}
            check("twelve classes laid out", len(table), 12)
            check("the administered ones say their rate is the statement's, never assumed", sorted(k for k, t in table.items() if t["rate_note"]), ["epf", "nsc", "ppf", "scss", "ssy"])
            check("NPS's rule says 60% lump sum, 40% annuity, locked in to 60", all(x in table["nps"]["rules"][0]["label"] for x in ("60", "40%", "annuity")), True)
            check("SGB says new issuance is discontinued and to verify", ("discontinued" in table["sgb"]["rules"][0]["label"], "verify" in table["sgb"]["rules"][0]["source"]), (True, True))
            check("a market-linked debenture's gain is short-term whatever the period", (tax_india.bucket("mld", date(2020, 1, 1), today), tax_india.long_term_from("mld", date(2020, 1, 1))),
                  (tax_india.ST_SLAB, None))
            rows = {c["key"]: c for c in tax_india_full.asset_classes(today)}
            check("the Sovereign Gold Bond is a class: 12 months, 12.5% with no exemption, redemption exempt noted", (rows["sgb"]["long_after_months"], rows["sgb"]["long_text"], "exempt" in rows["sgb"]["note"]),
                  (12, "12.5%", True))
            check("a bond sold on the exchange follows other listed: long-term after 12 months", tax_india.bucket("sgb", date(2024, 1, 1), today), tax_india.LT_OTHER)
            sv = await india_savings.household_view(s, firm, homes["The Sharma Family"].id)
            ppf = next(h for h in sv["holdings"] if h["kind"] == "ppf")
            check("Rajesh's PPF is the statement's value and rate", (ppf["value"], ppf["rate"], ppf["administered"], ppf["owner"]), (1_850_000.0, 7.1, True, "Rajesh Sharma"))
            nps = next(h for h in sv["holdings"] if h["kind"] == "nps")
            check("Meera's NPS: locked in to 60 (her birthday), the split is 60/40 of the corpus as stated",
                  (nps["locked_in"], nps["locked_in_to"], nps["exit_split"]["lump_sum"], nps["exit_split"]["annuity"]), (True, str(meera.date_of_birth.replace(year=meera.date_of_birth.year + 60)), 1_440_000.0, 960_000.0))
            check("the split says it is on the corpus as stated, before growth", "before any growth" in nps["exit_split"]["basis"], True)
            check("the Sharma view carries only its own classes' rules", sorted(t["kind"] for t in sv["rules"]), ["nps", "ppf"])
            iv = await india_savings.household_view(s, firm, homes["The D'Souza Household"].id)
            ulip = next(h for h in iv["holdings"] if h["kind"] == "ulip")
            check("the ULIP is locked in for five years from its start, and its charge is at the cap and not over",
                  (ulip["locked_in"], ulip["fmc"]["stated"], ulip["fmc"]["cap"], ulip["fmc"]["over_cap"]), (True, 1.35, 1.35, False))
            check("its economics are laid out as recorded, not compared", "not compared" in ulip["economics"]["basis"], True)
            check("a SGB shows its redemption exempt and that new issues are discontinued", [(h["redeems_exempt"], h["new_issues"]) for h in (await india_savings.household_view(s, firm, homes["The Kapoor Family Trust"].id))["holdings"] if h["kind"] == "sgb"],
                  [(True, "discontinued: verify")])
            await india_savings.record(s, firm, UserRole.ADVISER, by="adviser", household_id=homes["The Iyer Family"].id, person_id=lakshmi.id, kind="nsc", institution="A demonstration post office (invented)",
                                       value=300_000, value_on=today - timedelta(days=3), document="Certificate, filed")
            nsc = next(h for h in (await india_savings.household_view(s, firm, homes["The Iyer Family"].id))["holdings"] if h["kind"] == "nsc")
            check("an administered rate not on the statement is 'not on file', never assumed", (nsc["rate"], "never assumed" in nsc["rate_note"]), (None, True))
            await india_savings.record(s, firm, UserRole.ADVISER, by="adviser", household_id=homes["The D'Souza Household"].id, person_id=people["Elaine D'Souza"].id, kind="ulip", institution="A demonstration insurer (invented)",
                                       value=1_500_000, value_on=today - timedelta(days=2), document="Newer statement, filed",
                                       details={"started_on": (today - timedelta(days=1100)).isoformat(), "fund_management_charge": 1.5})
            iv = await india_savings.household_view(s, firm, homes["The D'Souza Household"].id)
            ulips = [h for h in iv["holdings"] if h["kind"] == "ulip"]
            check("a newer statement replaces the line: one current, the old one ended, and a charge over the cap says so", (len(ulips), ulips[0]["value"], ulips[0]["fmc"]["over_cap"]),
                  (1, 1_500_000.0, True))
            ended = (await s.execute(select(func.count()).select_from(held_away.HeldAwayHolding).where(held_away.HeldAwayHolding.status == "ended"))).scalar_one()
            check("the old line is kept as ended", ended >= 1, True)
            bare = Person(firm_id=firm.id, household_id=homes["The Sharma Family"].id, full_name="Demo No-Birthday", segment=rajesh.segment)
            s.add(bare)
            await s.flush()
            await india_savings.record(s, firm, UserRole.ADVISER, by="adviser", household_id=homes["The Sharma Family"].id, person_id=bare.id, kind="nps", institution="A demonstration manager (invented)",
                                       value=100_000, value_on=today - timedelta(days=2), document="Statement, filed")
            nobirth = next(h for h in (await india_savings.household_view(s, firm, homes["The Sharma Family"].id))["holdings"] if h["owner"] == "Demo No-Birthday")
            check("an NPS owner with no date of birth on file: lock-in not assessed", (nobirth["locked_in"], "Not assessed" in nobirth["lock_in_note"]), (None, True))
            await refused("an unknown class is refused", india_savings.prepare(s, firm, UserRole.ADVISER, household_id=homes["The Sharma Family"].id, kind="gold_coin", institution="X Bank", value=1,
                                                                                value_on=today, document="Statement, filed"), "class is one of")
            await refused("a rate over 30 is refused", india_savings.prepare(s, firm, UserRole.ADVISER, household_id=homes["The Sharma Family"].id, kind="ppf", institution="X Bank", value=1, rate=71,
                                                                              value_on=today, document="Statement, filed"), "between 0 and 30")
            await refused("a statement dated ahead is refused", india_savings.prepare(s, firm, UserRole.ADVISER, household_id=homes["The Sharma Family"].id, kind="ppf", institution="X Bank", value=1,
                                                                                       value_on=today + timedelta(days=2), document="Statement, filed"), "future")
            await refused("no statement is refused", india_savings.prepare(s, firm, UserRole.ADVISER, household_id=homes["The Sharma Family"].id, kind="ppf", institution="X Bank", value=1,
                                                                            value_on=today, document=""), "source")
            await refused("a client's role does not record one", india_savings.prepare(s, firm, UserRole.COMPLIANCE, household_id=homes["The Sharma Family"].id, kind="ppf", institution="X Bank", value=1,
                                                                                        value_on=today, document="Statement, filed"), "records a holding")
            hv = await held_away.for_household(s, homes["The Sharma Family"].id, firm_id=firm.id)
            check("net worth counts a savings line once, as held away", [h.get("name") for h in hv["holdings"]].count("Public Provident Fund"), 1)

            print("\n=== B-296: a pledge keeps units from being sold ===")
            nifty = inst["NIFTYBEES.NS"]
            holding = (await s.execute(select(Holding).where(Holding.account_id == k_account.id, Holding.instrument_id == nifty.id))).scalar_one()
            held = float(holding.quantity)
            check("Karthik's 5,000 units are under lien, from the lender's confirmation", (await pledges.pledged_units(s, firm.id, k_account.id, nifty.id), held), (5_000.0, 20_000.0))
            pv = await pledges.overview(s, firm, household_id=homes["The Iyer Family"].id)
            check("the page reads them as pledged with what is free", [(p["state"], p["under_lien"], p["sellable"]) for p in pv["pledges"]], [("pledged", 5_000.0, 15_000.0)])
            sale = lambda q: {"side": "sell", "symbol": "NIFTYBEES.NS", "instrument_id": str(nifty.id), "quantity": q, "est_price": 262.0, "est_value": q * 262.0}  # noqa: E731
            check("a sale that needs a pledged unit is refused, naming the units", (await pledges.order_refusals(s, firm, k_account, [sale(16_000)]))[0].startswith("5000 of the NIFTYBEES.NS"), True)
            check("a sale of what is free is not stopped by the pledge", await pledges.order_refusals(s, firm, k_account, [sale(15_000)]), [])
            infy = inst["INFY.NS"]
            await pledges.record(s, firm, UserRole.ADVISER, by="adviser", account_id=r_account.id, instrument="INFY.NS", lender="A demonstration lender (invented)", units=300,
                                 marked_on=today - timedelta(days=5), document="Lender's confirmation, filed")
            isale = lambda q: {"side": "sell", "symbol": "INFY.NS", "instrument_id": str(infy.id), "quantity": q, "est_price": 1185.0, "est_value": q * 1185.0}  # noqa: E731
            g = await order_guard.review(s, firm=firm, mandate=r_mandate, account=r_account, orders=[isale(500)], instruments={"INFY.NS": infy}, unpriced=[])
            check("order_guard refuses the direct sale that needs a pledged unit, with the units free", any("300 of the INFY.NS" in r and "400 can be sold" in r for r in g["refused"]), True)
            g = await order_guard.review(s, firm=firm, mandate=r_mandate, account=r_account, orders=[isale(400)], instruments={"INFY.NS": infy}, unpriced=[])
            check("a sale of the 400 that are free is not stopped by it", [r for r in g["refused"] if "pledged" in r], [])
            from app.conduit import execution

            res = await execution.execute_order_set(s, firm=firm, actor="adviser", mandate_id=k_mandate.id, order_set=[{
                "account_id": str(k_account.id), "instrument_id": str(nifty.id), "side": "sell", "quantity": 18_000, "symbol": "NIFTYBEES.NS", "asset_class": "equity",
                "custodian": k_account.custodian, "reason": "A sale past what is free"}])
            check("settlement refuses it last of all: nothing settled, and the reason says pledged", (res["orders_settled"], any("pledged" in str(f.get("reason", "")) or "pledged" in str(f) for f in res.get("failed", []) + res.get("breaks", []))), (0, True))
            await s.refresh(holding)
            check("the holding is as it was", float(holding.quantity), held)
            from app.agents.drift_rebalancing import DriftRebalancingAgent
            from app.atlas.base import AgentContext, Subject

            ctx = AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={}, subject=Subject("mandate", k_mandate.id, k_mandate.name))
            sensed = await DriftRebalancingAgent().sense(ctx)
            pos = next(p for p in sensed["positions"] if p["symbol"] == "NIFTYBEES.NS")
            check("the rebalancer works from the units free: 5,000 pledged, 15,000 in its lots, the value untouched", (pos["pledged"], sum(l["quantity"] for l in pos["lots"])), (5_000.0, 15_000.0))
            await refused("pledging more than is free is refused", pledges.prepare(s, firm, UserRole.ADVISER, account_id=k_account.id, instrument="NIFTYBEES.NS", lender="Another lender", units=16_000,
                                                                                      document="Confirmation, filed"), "free to pledge")
            await refused("an unknown instrument is refused", pledges.prepare(s, firm, UserRole.ADVISER, account_id=k_account.id, instrument="NOPE.NS", lender="Lender", units=1,
                                                                                 document="Confirmation, filed"), "no instrument")
            await refused("no lender is refused", pledges.prepare(s, firm, UserRole.ADVISER, account_id=k_account.id, instrument="NIFTYBEES.NS", lender="", units=1,
                                                                    document="Confirmation, filed"), "lender")
            pledge = (await s.execute(select(SecuritiesPledge).where(SecuritiesPledge.kind == "pledge", SecuritiesPledge.account_id == k_account.id))).scalar_one()
            await refused("a release of more than is under lien is refused", pledges.prepare(s, firm, UserRole.ADVISER, account_id=k_account.id, release_of=pledge.id, units=6_000,
                                                                                               document="Release letter, filed"), "still under")
            await pledges.record(s, firm, UserRole.OPERATIONS, by="ops", account_id=k_account.id, release_of=pledge.id, units=2_000, document="Release letter, filed")
            check("a partial release frees those units, and the pledge and its release both stay", (await pledges.pledged_units(s, firm.id, k_account.id, nifty.id), (await s.execute(
                select(func.count()).select_from(SecuritiesPledge).where(SecuritiesPledge.account_id == k_account.id))).scalar_one()), (3_000.0, 2))
            check("17,000 is now free to sell", await pledges.order_refusals(s, firm, k_account, [sale(17_000)]), [])
            await pledges.record(s, firm, UserRole.OPERATIONS, by="ops", account_id=k_account.id, release_of=pledge.id, units=3_000, document="Release letter, filed")
            check("released in full, nothing is under lien and the view says released", (await pledges.pledged_units(s, firm.id, k_account.id, nifty.id),
                                                                                           [p["state"] for p in (await pledges.overview(s, firm, household_id=homes["The Iyer Family"].id))["pledges"]]), (0.0, ["released"]))
            await pledges.record(s, firm, UserRole.OPERATIONS, by="ops", account_id=k_account.id, instrument="NIFTYBEES.NS", lender="A demonstration lender (invented)", units=5_000,
                                 marked_on=today - timedelta(days=90), document="A fresh confirmation, filed")
            check("a new pledge puts the units back under lien", await pledges.pledged_units(s, firm.id, k_account.id, nifty.id), 5_000.0)

            print("\n=== B-299: tax habits ===")
            sharma_view = await tax_habits.household_view(s, firm, homes["The Sharma Family"].id)
            check("each figure carries its source and reads not reviewed", all(f["key"] in law_figures.VERSIONS for f in sharma_view["figures"]) and
                  all(states.get(f["key"]) == "not_reviewed" for f in sharma_view["figures"]), True)
            gh = {g["person"]: g for g in sharma_view["gain_harvest"]}
            check("gain harvesting is read for the people who hold an account, not for Aarav", sorted(gh), ["Meera Sharma", "Rajesh Sharma"])
            rg = gh["Rajesh Sharma"]
            check("it states the Rs 1.25 lakh exemption, what is used and left, first-in first-out, lots on file only",
                  (rg["exemption"], round(rg["used"] + rg["left"], 2), "First-in first-out" in rg["basis"]), (125_000, 125_000.0, True))
            check("each candidate's cost steps up by its gain, and the total adds up", (all(c["cost_step_up"] == c["gain"] for c in rg["candidates"]),
                                                                                       round(sum(c["gain"] for c in rg["candidates"]), 2) == rg["total_step_up"]), (True, True))
            check("what it would realise never goes past what is left", rg["total_step_up"] <= rg["left"] + 1e-6, True)
            check("it never says a rebuy is safe, and names the substance question", ("nothing here says it is safe" in rg["said"] or not rg["candidates"], "safe" not in tax_habits.__doc__.replace("is safe", "")), (True, True))
            check("harvest_guard is unchanged: a rebuy stays a review item", "rebuy_review" in open("app/aurea_core/tax_india_full.py", encoding="utf-8").read(), True)
            div = {d["person"]: d for d in (await tax_habits.household_view(s, firm, homes["The Iyer Family"].id))["dividends"]}["Karthik Iyer"]
            check("dividends on file: Rs 23,000 from two payers", (div["total"], len(div["payers"])), (23_000.0, 2))
            payers = {p["symbol"]: p for p in div["payers"]}
            check("the payer above Rs 10,000 deducted 10% of what it paid; the one below deducted nothing", (payers["INFY.NS"]["tds"], payers["INFY.NS"]["over_threshold"], payers["TCS.NS"]["tds"]), (1_700.0, True, 0.0))
            check("the tax at his recorded 30% slab and cess is laid out against the tax deducted", (div["tds"], div["tax"], div["balance"]), (1_700.0, 7_176.0, 5_476.0))
            rd = [d for d in sharma_view["dividends"] if d["person"] == "Rajesh Sharma"][0]
            check("Rajesh has no dividend on file: said so, not none paid", (rd["state"], "does not mean none was paid" in rd["said"]), ("none_on_file", True))
            nope = [d for d in sharma_view["dividends"] if d["person"] == "Meera Sharma"][0]
            check("Meera's dividends from the HDFC holding are on file, but she has no recorded slab here", (nope["total"], nope["payers"][0]["over_threshold"]), (11_400.0, True))
            gifts = {g["receiver"]: g for g in sharma_view["gifts"]}
            check("a gift from a husband to a wife: a specified relative, so not income in her hands", gifts["Meera Sharma"]["receiver_tax"]["state"], "exempt")
            club = gifts["Meera Sharma"]["clubbing"]
            check("clubbing: her dividends by the share of her units that were the gift (100 of 600) are added back to him, with the tax effect at his slab",
                  (club["state"], club["income"], club["added_to_giver"], club["tax_effect"]), ("added_back", 1_900.0, 1_900.0, 592.8))
            check("a cash gift to an adult child is exempt and has no clubbing to read", (gifts["Aarav Sharma"]["receiver_tax"]["state"], "clubbing" in gifts["Aarav Sharma"]), ("exempt", False))
            menon = {g["receiver"]: g for g in (await tax_habits.household_view(s, firm, next(h for n, h in homes.items() if "Menon" in n).id))["gifts"]}
            check("a cash gift to a minor child: the rule applies and the income is not on file, so not assessed", (menon["Ira Menon"]["clubbing"]["state"], "not on file" in menon["Ira Menon"]["clubbing"]["said"]),
                  ("not_assessed", True))
            check("the minor's exemption of Rs 1,500 is a law figure", law_figures.value("in_clubbing_minor_exemption"), 1_500)
            g_terms = dict(giver_id=rajesh.id, receiver_id=meera.id, relationship="other", gifted_on=today - timedelta(days=10), value=40_000, description="A cash gift", document="Letter, filed")
            await tax_habits.record_gift(s, firm, UserRole.ADVISER, by="adviser", **g_terms)
            after = (await tax_habits.household_view(s, firm, homes["The Sharma Family"].id))["gifts"]
            other = [g for g in after if g["relationship"] == "other"]
            check("from someone who is not a specified relative, under Rs 50,000 in the year: under the threshold", other[0]["receiver_tax"]["state"], "under_threshold")
            await tax_habits.record_gift(s, firm, UserRole.ADVISER, by="adviser", **{**g_terms, "value": 20_000, "description": "A second cash gift"})
            other = [g for g in (await tax_habits.household_view(s, firm, homes["The Sharma Family"].id))["gifts"] if g["relationship"] == "other"]
            check("the second takes the year's total to Rs 60,000: both read as income in the receiver's hands", ({g["receiver_tax"]["state"] for g in other}, other[0]["receiver_tax"]["year_total"]),
                  ({"taxable"}, 60_000.0))
            await refused("a gift to oneself is refused", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, **{**g_terms, "receiver_id": rajesh.id}), "one person to another")
            await refused("a gift across households is refused", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, **{**g_terms, "receiver_id": karthik.id}), "same household")
            await refused("an unstated relationship is refused, never inferred", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, **{**g_terms, "relationship": "friend"}), "never worked out")
            await refused("a gift dated ahead is refused", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, **{**g_terms, "gifted_on": today + timedelta(days=1)}), "ahead")
            await refused("no document is refused", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, **{**g_terms, "document": ""}), "deed")
            await refused("what was paid at the value is a sale, not a gift", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, **{**g_terms, "consideration": 40_000, "description": "A sale"}), "sale")
            await refused("the same gift twice is refused", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, **g_terms), "already recorded")
            await refused("a child of 22 is not a minor child, from the date of birth on file", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, **{**g_terms, "receiver_id": aarav.id, "relationship": "minor_child",
                                                                                                                                                 "description": "Other"}), "not a minor child")
            await refused("and a child of 11 is not an adult child", tax_habits.prepare_gift(s, firm, UserRole.ADVISER, giver_id=people["Vikram Menon"].id, receiver_id=people["Ira Menon"].id, relationship="adult_child",
                                                                                              gifted_on=today - timedelta(days=2), value=1_000, description="Other", document="Deed, filed"), "minor child")
            await refused("compliance does not record a gift", tax_habits.prepare_gift(s, firm, UserRole.COMPLIANCE, **g_terms), "records a gift")
            check("not modelled is named: a son's wife, a trust, capital gains on the gifted asset", ("son's wife" in tax_habits.NOT_MODELLED[0], "capital gain" in tax_habits.NOT_MODELLED[2]), (True, True))

            print("\n=== The tools and their cards ===")
            for key in ("record_lrs_remittance", "record_sip_mandate", "record_savings_holding", "record_pledge", "record_tax_gift"):
                check(f"{key} stops for a Confirm card", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            for key in ("read_lrs", "read_sip_mandates", "read_india_savings", "read_pledges", "read_tax_habits"):
                check(f"{key} changes nothing", TOOLS[key].change_state, ToolChangeState.NO)
            check("no client holds any of them", all(UserRole.CLIENT not in TOOLS[k].roles_required for k in TOOLS if k.startswith(("read_lrs", "record_lrs", "read_sip_mand", "record_sip_mand", "read_india_sav",
                                                                                                                             "record_savings", "read_pledges", "record_pledge", "read_tax_habits", "record_tax_gift"))), True)
            adv = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
            comp = ToolExecutors(s, users[UserRole.COMPLIANCE].id, UserRole.COMPLIANCE, firm.id)
            before = (await s.execute(select(func.count()).select_from(LrsRemittance))).scalar_one()
            card = await adv.preview_change("record_lrs_remittance", {"person_id": str(lakshmi.id), "remitted_on": (today - timedelta(days=2)).isoformat(), "amount_usd": 20_000, "purpose": "investment",
                                                                      "bank": "A demonstration bank (invented)", "document": "Advice, filed"})
            labels = [r["label"] for r in card["rows"]]
            check("the remittance card names the person, the dollars, the purpose and what the year becomes", all(x in labels for x in ("Person", "Dollars counted", "Purpose", "The year after")), True)
            check("a card writes nothing by being shown", (await s.execute(select(func.count()).select_from(LrsRemittance))).scalar_one(), before)
            await refused("a card for a remittance with no document is refused before it is shown", adv.preview_change("record_lrs_remittance", {"person_id": str(lakshmi.id), "remitted_on": today.isoformat(),
                                                                                                                                                    "amount_usd": 1, "purpose": "other", "bank": "B1"}), "advice")
            await refused("compliance's card for a remittance is refused: not its to record", comp.preview_change("record_lrs_remittance", {"person_id": str(lakshmi.id), "remitted_on": today.isoformat(),
                                                                                                                                              "amount_usd": 1, "purpose": "other", "bank": "B1", "document": "Advice, filed"}), "records")
            done = await adv.execute("record_lrs_remittance", {"person_id": str(lakshmi.id), "remitted_on": (today - timedelta(days=2)).isoformat(), "amount_usd": 20_000, "purpose": "investment",
                                                                "bank": "A demonstration bank (invented)", "document": "Advice, filed"})
            check("confirmed, it is recorded", (done["people"][0]["remitted_usd"], done["message"].startswith("Recorded")), (20_000.0, True))
            read = await comp.execute("read_lrs", {"household_id": str(homes["The Iyer Family"].id)})
            check("compliance reads the household's remittances, and the message says not-assessed is not nothing", ([p["person"] for p in read["people"]], "does not mean" in read["message"]),
                  (["Karthik Iyer", "Lakshmi Iyer"], True))
            card = await adv.preview_change("record_sip_mandate", {"account_id": str(k_account.id), "reference": "UMRN-DEMO-IYER-0003", "mandate_type": "nach", "per_debit_limit": 30_000,
                                                                    "starts_on": today.isoformat(), "last4": "4821", "document": "Registration, filed"})
            check("the mandate card shows the verified bank account and says it registers nothing", ([r["value"] for r in card["rows"] if r["label"] == "Bank account"][0].startswith("A demonstration bank"),
                                                                                                    any("registers nothing" in r["value"] for r in card["rows"])), (True, True))
            await refused("the mandate card refuses an unverified account", adv.preview_change("record_sip_mandate", {"account_id": str(k_account.id), "reference": "UMRN-DEMO-IYER-0003",
                                                                                                                     "mandate_type": "nach", "per_debit_limit": 30_000, "starts_on": today.isoformat(),
                                                                                                                     "last4": "0000", "document": "Registration, filed"}), "not verified")
            read = await adv.execute("read_sip_mandates", {})
            check("the mandates tool says what it counts", ("active SIP(s) with no mandate on file" in read["message"], read["applies"]), (True, True))
            card = await adv.preview_change("record_savings_holding", {"household_id": str(homes["The Banerjee Household"].id), "kind": "nsc", "institution": "A demonstration post office (invented)",
                                                                        "value": 150_000, "value_on": today.isoformat(), "document": "Certificate, filed", "rate": 7.7})
            check("the holding card names the class, the statement's rate and says it is counted once", (any(r["label"] == "Class" and r["value"] == "National Savings Certificate" for r in card["rows"]),
                                                                                                         any("once in net worth" in r["value"] for r in card["rows"])), (True, True))
            done = await adv.execute("record_savings_holding", {"household_id": str(homes["The Banerjee Household"].id), "kind": "nsc", "institution": "A demonstration post office (invented)",
                                                                 "value": 150_000, "value_on": today.isoformat(), "document": "Certificate, filed", "rate": 7.7})
            check("confirmed, it reads back with its rule", ([h["kind"] for h in done["holdings"]].count("nsc"), "nsc" in [t["kind"] for t in done["rules"]]), (1, True))
            read = await adv.execute("read_india_savings", {})
            check("with no household it lays out every class's rule", (len(read["all_rules"]), read["applies"]), (12, True))
            card = await adv.preview_change("record_pledge", {"account_id": str(k_account.id), "instrument": "NIFTYBEES.NS", "lender": "A second lender (invented)", "units": 1_000, "document": "Confirmation, filed"})
            check("the pledge card says the units can no longer be sold and marks nothing at the depository", (any("no longer be sold" in r["value"] for r in card["rows"]),
                                                                                                                any("depository" in r["value"] for r in card["rows"])), (True, True))
            await refused("the pledge card refuses more than is free", adv.preview_change("record_pledge", {"account_id": str(k_account.id), "instrument": "NIFTYBEES.NS", "lender": "Lender",
                                                                                                          "units": 19_000, "document": "Confirmation, filed"}), "free to pledge")
            read = await adv.execute("read_pledges", {})
            check("the pledge tool reads them", (read["applies"], read["pledged"]), (True, 2))
            card = await adv.preview_change("record_tax_gift", {"giver_id": str(rajesh.id), "receiver_id": str(aarav.id), "relationship": "adult_child", "gifted_on": today.isoformat(), "value": 10_000,
                                                                 "description": "Cash", "document": "Deed, filed"})
            check("the gift card reads it as exempt and says the relationship is as stated", (any("not income" in r["value"] for r in card["rows"]), any("never inferred" in r["value"] for r in card["rows"])), (True, True))
            await refused("the gift card refuses a contradicting date of birth", adv.preview_change("record_tax_gift", {"giver_id": str(rajesh.id), "receiver_id": str(aarav.id), "relationship": "minor_child",
                                                                                                                       "gifted_on": today.isoformat(), "value": 10_000, "description": "Cash",
                                                                                                                       "document": "Deed, filed"}), "not a minor child")
            read = await adv.execute("read_tax_habits", {"household_id": str(homes["The Sharma Family"].id)})
            check("the tax habits tool lays out the three, and says it advises nothing", ([k for k in ("gain_harvest", "dividends", "gifts") if k in read], "Nothing here advises" in read["message"]),
                  (["gain_harvest", "dividends", "gifts"], True))

            print("\n=== The page's routes ===")
            from app.api import licences as api

            paths = {r.path for r in api.router.routes}
            check("each read and each write has its route", all(p in paths for p in ("/api/licences/lrs", "/api/licences/sip-mandates", "/api/licences/savings", "/api/licences/pledges",
                                                                                    "/api/licences/tax-habits/{household_id}", "/api/licences/tax-habits/gifts")), True)
            from app.aurea_core import licences as licence_core

            extras = await licence_core._india_pack(s, firm)
            if extras is not None:
                check("the Licences page carries them", all(k in extras for k in ("lrs", "sip_mandates", "savings", "pledges")), True)

            print("\n=== A firm outside India ===")
            nz = Firm(jurisdiction="NZ", settings={})
            check("none of them applies", (lrs.applies(nz), sip_mandates.applies(nz), india_savings.applies(nz), pledges.applies(nz), tax_habits.applies(nz)), (False,) * 5)

            print("\n=== The seed, a second time ===")
            check("the batch A seed adds nothing a second time", await india_batch_a.add(s, firm), 0)
            check("and the tax gifts it wrote are three", (await s.execute(select(func.count()).select_from(TaxGift).where(TaxGift.recorded_by == india_batch_a.BY))).scalar_one(), 3)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
