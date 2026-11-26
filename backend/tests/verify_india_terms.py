"""Prove Indian onboarding speaks Indian terms (B-385).

The nominations check reads Indian accounts and nominations and never says "all covered" over nothing checked; an Indian
case shows no ACAT, CDD rule, CIP, EIN or US account code; beneficial owners follow the Indian lines and an LLP is a
type; an individual's identity held by the KYC registry is not asked for again; a joint account says how it is operated,
with no default, and an order on an all-sign account says who must sign; and a couple can be one household.

Runs against a throwaway SQLite database; the model is stubbed.

    python -m tests.verify_india_terms
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import custodian_account_opener as opener
from app.agents import onboarding as onboarding_agent
from app.api import onboarding as onboarding_api
from app.atlas.base import Subject
from app.aurea_core import (
    client_file, document_matrix, firm_time, gates as gates_core, onboarding_start, order_guard, parties as parties_core, registration,
    money_in)
from app.core.db import Base, utcnow
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import EntityType, PartyRole, UserRole
from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person
from app.models.identity import User
from app.models.india_family import AssetNomination
from app.models.kyc import KycRecord
from app.models.money_in import BankLink
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.portfolio import Instrument
from app.models.tenant import Firm

PASS, FAIL = [], []
US_WORDS = ("ACAT", "CDD", "CIP", "EIN", "Schwab", "schwab", "25%", "Form W-9", "survivorship", "Tenants in Common")


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def us_words(payload) -> list[str]:
    import re

    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return [w for w in US_WORDS if re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", text)]


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001 - every refusal here carries its words
        text = str(getattr(e, "detail", e))
        ok = all(c.lower() in text.lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {text[:150]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def section(name: str, run) -> None:
    print(f"\n=== {name} ===")
    try:
        await run()
    except Exception as e:  # noqa: BLE001 - a missing piece fails its section and the rest still run
        FAIL.append(name)
        print(f"  FAIL  {name}: {type(e).__name__}: {str(e)[:200]}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            india = Firm(id=uuid.uuid4(), name="India", slug="in", base_currency="INR", jurisdiction="IN", settings={"timezone": "Asia/Kolkata"})
            us = Firm(id=uuid.uuid4(), name="US", slug="us", base_currency="USD", jurisdiction="US", settings={})
            bare = Firm(id=uuid.uuid4(), name="Bare", slug="bare", base_currency="INR", jurisdiction="IN", settings={})
            s.add_all([india, us, bare])
            await s.flush()

            def user(firm, role, name):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{name}@{firm.slug}", full_name=name, role=role, hashed_password="x")
                s.add(u)
                return u

            adviser, ops = user(india, UserRole.ADVISER, "Priya"), user(india, UserRole.OPERATIONS, "Om")
            us_ops = user(us, UserRole.OPERATIONS, "Uma")
            await s.flush()
            today = firm_time.today(india)
            ex = lambda u, f: ToolExecutors(s, u.id, u.role, f.id)  # noqa: E731

            async def household(firm, name, people=(), accounts=True):
                h = Household(firm_id=firm.id, name=name, segment="private_wealth", values={})
                s.add(h)
                await s.flush()
                made = []
                for person_name in people:
                    p = Person(firm_id=firm.id, household_id=h.id, full_name=person_name, preferred_name=person_name.split()[0], kyc={}, profile={})
                    s.add(p)
                    await s.flush()
                    a = None
                    if accounts:
                        m = Mandate(firm_id=firm.id, person_id=p.id, name=f"{person_name} - Core", mandate_type="advisory", suitability={}, constraints={})
                        s.add(m)
                        await s.flush()
                        a = Account(firm_id=firm.id, mandate_id=m.id, name=f"{person_name} demat", currency=firm.base_currency, cash_balance=1_000_000)
                        s.add(a)
                        await s.flush()
                    made.append((p, a))
                return h, made

            async def nominations_page():
                empty = await registration.beneficiary_audit(s, bare.id)
                check("a firm with no accounts is never all covered", (empty["accounts_checked"], empty["clean"], empty["state"]), (0, False, "nothing_checked"))
                h, [(asha, a1), (ravi, a2), (devi, a3)] = await household(india, "The Rao Family", ("Asha Rao", "Ravi Rao", "Devi Rao"))
                huf = LegalEntity(firm_id=india.id, household_id=h.id, name="Ravi Rao HUF", entity_type=EntityType.HUF, governance={}, impact_objectives={}, jurisdiction="IN")
                s.add(huf)
                await s.flush()
                hm = Mandate(firm_id=india.id, entity_id=huf.id, name="HUF - Core", mandate_type="advisory", suitability={}, constraints={})
                s.add(hm)
                await s.flush()
                s.add(Account(firm_id=india.id, mandate_id=hm.id, name="Ravi Rao HUF demat", currency="INR", cash_balance=0))
                s.add(AccountBeneficiary(firm_id=india.id, account_id=a1.id, beneficiary_name="Ravi Rao", designation_class="primary", percentage=100, date_designated=today))
                s.add(AssetNomination(firm_id=india.id, household_id=h.id, person_id=ravi.id, asset_kind="demat", label="Ravi Rao demat", status="opted_out",
                                      declaration_on=today, source_document="Opt-out declaration", recorded_by="Om"))
                await s.flush()
                r = await registration.beneficiary_audit(s, india.id)
                check("every account a person holds is checked, with its nomination or opt-out",
                      (r["accounts_checked"], r["nominated"], r["opted_out"], r["clean"], r["state"]), (3, 1, 1, False, "gaps"))
                check("the one with neither is the gap", [(g["account_name"], g["issue"]) for g in r["gaps"]], [("Devi Rao demat", "no nomination or signed opt-out on file")])
                check("an HUF's account is listed as not checked, never counted as covered",
                      ([n["account_name"] for n in r["not_checked"]], r["accounts_total"]), (["Ravi Rao HUF demat"], 4))
                one = await registration.beneficiary_audit(s, india.id, household_id=h.id)
                check("one household reads the same", (one["accounts_checked"], len(one["gaps"])), (3, 1))
                none_home, _ = await household(india, "No accounts", ("Nobody Yet",), accounts=False)
                z = await registration.beneficiary_audit(s, india.id, household_id=none_home.id)
                check("a household with no account is not all covered", (z["clean"], z["state"]), (False, "nothing_checked"))
                issues = await client_file._designation_issues(s, india.id, h.id, [asha, ravi, devi], [])
                check("the client file says each gap once", [i["issue"] for i in issues if i["account"] == "Devi Rao demat"], ["no nomination or signed opt-out on file"])
                await household(us, "The Fords", ("Sam Ford",))
                u = await registration.beneficiary_audit(s, us.id)
                check("a US book with no account that needs one says nothing was checked", (u["accounts_checked"], u["clean"], u["state"]), (0, False, "nothing_checked"))
                told = await ex(ops, bare).read_beneficiary_audit({})
                check("the conversation says nothing was checked", ("covered" in told["message"].lower(), "no account" in told["message"].lower()), (False, True))

            await section("Nominations are read on Indian accounts", nominations_page)

            async def case(firm, name, rtype, **extra):
                c = OnboardingCase(firm_id=firm.id, prospect_name=name, registration_type=rtype, intake={}, **extra)
                s.add(c)
                await s.flush()
                return c

            def party(c, role, name, **kw):
                p = OnboardingParty(firm_id=c.firm_id, case_id=c.id, role=role, legal_name=name, screening_status="clear", **kw)
                s.add(p)
                return p

            async def indian_words():
                c = await case(india, "Coastal Foods Pvt Ltd", "entity_corp", is_entity=True, entity_type="company")
                gate = await onboarding_api._gates_for(s, india, c)
                check("the activation gates carry no US term", us_words(gate), [])
                listed = await onboarding_api.list_parties(c.id, firm=india, db=s)
                check("the parties page carries no US term", us_words(listed), [])
                tracks = await onboarding_api._tracks_for(s, india, c)
                check("the tracks carry no US term", us_words(tracks), [])
                m = document_matrix.matrix(india)
                check("the matrix lists only Indian account types", [x["registration_type"] for x in m["registrations"] if x["registration_type"] not in onboarding_start.types_for(india)], [])
                check("with no EIN and no US tenancy states", (us_words(m), m.get("states")), ([], None))
                await refused("a PAN is not typed into registration detail", document_matrix.prepare_detail(
                    s, india.id, case_id=c.id, fields={"tax_id": "AAACC1234D"}, documents=None, evidence="PAN card copy"), "pan card")
                templates = await onboarding_api.document_templates(firm=india)
                made = [__import__("app.aurea_core.sample_docs", fromlist=["x"]).generate(t["doc_type"], "Asha Rao", market="IN") for t in templates]
                asked = __import__("app.aurea_core.india_onboarding", fromlist=["x"]).required_identity
                check("the onboarding agent asks an Indian company for no trust deed", await asked(s, india, c, {"trust_deed"}, set()), set())
                check("sample documents are Indian", [w for w in ("New Zealand", "Auckland", "NZ ") if any(w in d for d in made)], [])
                check("no UK pension template for India", "overseas_pension" in [t["doc_type"] for t in templates], False)
                opts = await onboarding_api.case_options(firm=india)
                check("transfers offer Indian rails", [t["value"] for t in opts["transfer_types"]], ["demat", "rtgs_neft", "upi", "cheque"])
                us_opts = await onboarding_api.case_options(firm=us)
                check("a US firm keeps ACAT first", us_opts["transfer_types"][0]["value"], "acat")
                c.custodian_push_status = "active"
                await refused("an ACAT is refused for an Indian case", onboarding_api.create_transfer(
                    c.id, onboarding_api.TransferCreate(transfer_type="acat", direction="in"), user=ops, firm=india, db=s), "off-market demat")
                c.custodian_push_status = None
                c.status, c.cip_status = "approved", "verified"
                await s.flush()
                ctx = SimpleNamespace(session=s, firm=india, config={}, subject=Subject("onboarding_case", c.id, c.prospect_name))

                async def plain(ctx_, *, fallback, **k):
                    return fallback

                opener.narrate = plain
                agent = opener.CustodianAccountOpenerAgent()
                drafts = await agent.think(ctx, await agent.sense(ctx))
                made = drafts[0].payload
                check("the opener names no US custodian or account code", (us_words(drafts[0].payload), us_words(drafts[0].title + drafts[0].summary + drafts[0].rationale),
                      made["custodian_name"], made["custodian_account_id"].startswith("SIM-IN-")), ([], [], "simulated_depository", True))

            await section("An Indian case speaks Indian terms", indian_words)

            async def owners():
                def bo(name, pct, control=False):
                    return SimpleNamespace(id=uuid.uuid4(), role=PartyRole.BENEFICIAL_OWNER, legal_name=name, ownership_pct=pct, is_control_person=control,
                                           screening_status="clear", screening_hits=[])

                def codes(reg, rows, market):
                    return [i["code"] for i in parties_core.completeness_for(reg, rows, market=market)["ownership_issues"]]

                check("India: 12% of a company is a beneficial owner", codes("entity_corp", [bo("A", 12, True)], "IN"), [])
                check("India: 10% is not, the line being more than 10%", codes("entity_corp", [bo("A", 10, True)], "IN"), ["below_threshold"])
                check("India: a partnership reads the same line", codes("entity_partnership", [bo("A", 11, True)], "IN"), [])
                check("India: an LLP reads it too", codes("entity_llp", [bo("A", 9, True)], "IN"), ["below_threshold"])
                check("India: a trust beneficiary at 10% counts", codes("trust", [bo("A", 10)], "IN"), [])
                short = parties_core.completeness_for("trust", [bo("A", 5)], market="IN")
                gate = gates_core.evaluate(OnboardingCase(registration_type="trust", prospect_name="T", intake={}), party_status=short,
                                           disclosure_status={})
                check("India: one short of the line fails the ownership gate, as the parties page says",
                      next(g["state"] for g in gate["gates"] if g["key"] == "ownership_certified"), "fail")
                check("US: 12% is still below 25%", codes("entity_corp", [bo("A", 12, True)], "US"), ["below_threshold"])
                check("US: 25% counts", codes("entity_corp", [bo("A", 25, True)], "US"), [])
                gaps = parties_core.completeness_for("entity_llp", [], market="IN")["role_gaps"]
                check("an LLP needs its owners and the person in control", sorted(g["role"] for g in gaps), ["beneficial_owner", "control_person"])
                check("with the Indian reason and its line", (us_words(gaps), "10%" in gaps[0]["why"] + gaps[1]["why"]), ([], True))
                check("LLP is an Indian type only", ("entity_llp" in onboarding_start.types_for(india), "entity_llp" in onboarding_start.types_for(us)), (True, False))
                need = [k for k, _, _ in document_matrix.requirements("entity_llp", firm=india)["documents"]]
                check("an LLP's documents", need, ["id", "formation", "authority", "pan"])
                check("an Indian company gives its PAN card, not a tax id",
                      ([k for k, _, _ in document_matrix.requirements("entity_corp", firm=india)["documents"]][-1], document_matrix.requirements("entity_corp", firm=india)["fields"]), ("pan", []))
                check("a US company still gives its tax id", document_matrix.requirements("entity_corp", firm=us)["fields"], ["tax_id"])
                card = await onboarding_start.prepare(s, india, prospect_name="Blue Delta LLP", registration_type="entity_llp")
                check("an LLP opens as a partnership entity", (card["label"], onboarding_start.ENTITY_OF[card["type"]]), ("Limited liability partnership (LLP)", "partnership"))

            await section("Beneficial owners follow the market", owners)

            async def registry():
                async def missing(c):
                    return [m["key"] for m in (await document_matrix.check_case(s, c, india))["requirements"]["documents"] if not m["on_file"]]

                def kyc(c, name, status="validated", **kw):
                    kw.setdefault("ckyc_number", "12345678901234")
                    s.add(KycRecord(firm_id=c.firm_id, case_id=c.id, party_name=name, kra_status=status, document="KRA acknowledgement", recorded_by="Om",
                                    beneficial_owners=[], triggers=[], created_at=utcnow(), **kw))

                c = await case(india, "Kiran Shah", "individual")
                check("with nothing on file, the photo ID is asked", await missing(c), ["id"])
                kyc(c, "Kiran Shah")
                await s.flush()
                check("with a validated KYC record, it is not", await missing(c), [])
                got = await document_matrix.check_case(s, c, india)
                line = got["requirements"]["documents"][0]
                check("and the line says where identity is held", ("KYC registry" in line["label"], [i for i in got["issues"] if i["field"] == "id"]), (True, []))
                party(c, PartyRole.OWNER, "Kiran Shah")
                bare_case = await case(india, "Hari Rao", "individual")
                party(bare_case, PartyRole.OWNER, "Hari Rao")
                await s.flush()
                asks = [("photo ID" in json.dumps(await onboarding_api._tracks_for(s, india, x))) for x in (bare_case, c)]
                check("the account track asks only where nothing is held", asks, [True, False])
                ctx = SimpleNamespace(session=s, firm=india, subject=Subject("onboarding_case", c.id, c.prospect_name))
                exceptions = await __import__("app.aurea_core.india_onboarding", fromlist=["x"]).required_identity(
                    s, india, c, {"passport", "drivers_licence"}, set())
                check("the onboarding agent raises no missing passport", exceptions, set())
                registered = await case(india, "Leela Nair", "individual")
                kyc(registered, "Leela Nair", "registered")
                held = await case(india, "Manoj Pillai", "individual")
                kyc(held, "Manoj Pillai", "on_hold")
                lapsed = await case(india, "Nitin Kulkarni", "individual")
                kyc(lapsed, "Nitin Kulkarni", id_proof_valid_to=today - timedelta(days=1))
                other = await case(india, "Omkar Jain", "individual")
                kyc(other, "Someone Else")
                await s.flush()
                for who, c2 in (("registered only, so the next intermediary repeats the KYC", registered), ("on hold", held),
                                ("an expired identity proof", lapsed), ("a record under another name", other)):
                    check(f"still asked: {who}", await missing(c2), ["id"])
                joint = await case(india, "Pooja Menon", "joint_all")
                party(joint, PartyRole.OWNER, "Pooja Menon")
                party(joint, PartyRole.JOINT_OWNER, "Qasim Menon")
                kyc(joint, "Pooja Menon")
                await s.flush()
                check("a joint account asks while one holder has none", await missing(joint), ["id"])
                kyc(joint, "Qasim Menon")
                await s.flush()
                check("and not once every holder is held", await missing(joint), [])
                us_case = await case(us, "Pat Doe", "individual")
                kyc(us_case, "Pat Doe")
                await s.flush()
                check("a US case is asked as before", [m["key"] for m in (await document_matrix.check_case(s, us_case, us))["requirements"]["documents"] if not m["on_file"]], ["id"])

            await section("The registry's identity is not asked for again", registry)

            async def joint_mode():
                types = onboarding_start.types_for(india)
                check("India offers the two modes and neither US tenancy",
                      [t for t in ("joint_all", "joint_anyone", "joint_jtwros", "joint_tic") if t in types], ["joint_all", "joint_anyone"])
                check("a US firm keeps its tenancies", [t for t in ("joint_all", "joint_anyone", "joint_jtwros", "joint_tic") if t in onboarding_start.types_for(us)],
                      ["joint_jtwros", "joint_tic"])
                await refused("a joint account with no mode is not opened: the person says which",
                              onboarding_start.prepare(s, india, prospect_name="Ritu Das", registration_type="joint_jtwros"), "joint_all", "joint_anyone", "neither is assumed")
                await refused("nor is a bare 'joint'", onboarding_start.prepare(s, india, prospect_name="Ritu Das", registration_type="joint"), "joint_all", "joint_anyone")
                p = await onboarding_start.prepare(s, india, prospect_name="Ritu Das", registration_type="joint_all")
                check("the mode is named on the card", p["label"], "Joint (all holders sign)")
                check("the other", (await onboarding_start.prepare(s, india, prospect_name="Ritu Das", registration_type="joint_anyone"))["label"], "Joint (anyone or survivor)")
                roles = sorted(g["role"] for g in parties_core.completeness_for("joint_all", [], market="IN")["role_gaps"])
                check("both modes need two holders", roles, ["joint_owner", "owner"])

                async def passing(*a, **k):
                    return {"blocks_activation": False, "gates": []}

                real_gates = onboarding_agent._activation_gates
                onboarding_agent._activation_gates = passing
                try:
                    made = {}
                    for mode, name, other in (("joint_all", "Sunil Bose", "Tara Bose"), ("joint_anyone", "Uday Sen", "Vani Sen")):
                        c = await onboarding_start.start(s, india, actor=adviser, prospect_name=name, registration_type=mode)
                        party(c, PartyRole.OWNER, name)
                        party(c, PartyRole.JOINT_OWNER, other)
                        await s.flush()
                        ctx = SimpleNamespace(session=s, firm=india, subject=Subject("onboarding_case", c.id, name))
                        out = await onboarding_agent.OnboardingAgent().act(ctx, SimpleNamespace(modified_payload=None, payload={}))
                        made[mode] = await s.get(Account, uuid.UUID(out["account_id"]))
                finally:
                    onboarding_agent._activation_gates = real_gates
                check("activation records the mode on the account", [made[m].registration_type for m in ("joint_all", "joint_anyone")], ["joint_all", "joint_anyone"])
                inst = Instrument(firm_id=india.id, symbol="NIFTYBEES.NS", name="Nifty ETF", asset_class="equity", market_type="public", currency="INR", values_tags={})
                s.add(inst)
                await s.flush()

                async def notes(account):
                    mandate = await s.get(Mandate, account.mandate_id)
                    got = await order_guard.review(s, firm=india, mandate=mandate, account=account, instruments={inst.symbol: inst}, unpriced=[inst.symbol],
                                                   orders=[{"symbol": inst.symbol, "side": "sell", "quantity": 1, "est_value": 0}])
                    return [n for n in got.get("notes") or [] if "sign" in n.lower()]

                said = await notes(made["joint_all"])
                check("an order on an all-sign account says who must sign", (len(said), all(w in said[0] for w in ("All holders must sign", "Sunil Bose", "Tara Bose"))), (1, True))
                check("an anyone-or-survivor account says nothing of the kind", await notes(made["joint_anyone"]), [])
                # A redemption goes to a registered bank of the investor (B-388): the holders' joint bank account, from its evidence.
                await money_in.record_link(s, india.id, by="ops", account_id=made["joint_all"].id, bank="A demonstration bank (invented)", last4="2468",
                                           owner_name="Sunil Bose & Tara Bose", method="voided_cheque", evidence="Cancelled cheque, filed", verified_on=today)
                joint_bank = (await s.execute(select(BankLink).where(BankLink.account_id == made["joint_all"].id))).scalars().one()
                card = {r["label"]: r["value"] for r in (await ex(ops, india).preview_change("request_cash_movement", {
                    "account_id": str(made["joint_all"].id), "kind": "withdrawal", "amount": 1, "bank_link_id": str(joint_bank.id), "reason": "School fees"}))["rows"]}
                check("a redemption's card says it too", "All holders must sign" in str(card.get("Holders")), True)
                await refused("an Indian account is not re-registered as a US tenancy",
                              ex(ops, india).preview_change("set_account_registration", {"account_id": str(made["joint_all"].id), "registration_type": "joint_tic"}), "joint_all")

            await section("A joint account says how it is operated", joint_mode)

            async def one_household():
                h, [(husband, _)] = await household(india, "The Mehta Family", ("Anil Mehta",))
                foreign, _ = await household(us, "Elsewhere", ("X Y",))
                await refused("another firm's household is refused", onboarding_start.prepare(
                    s, india, prospect_name="Bina Mehta", registration_type="individual", household_id=str(foreign.id)), "no such household")
                gone, _ = await household(india, "Left", ("Q R",))
                gone.left_on = today
                await s.flush()
                await refused("a household that has left is refused", onboarding_start.prepare(
                    s, india, prospect_name="Bina Mehta", registration_type="individual", household_id=str(gone.id)), "left")
                ask = {"prospect_name": "Bina Mehta", "registration_type": "individual", "household_id": str(h.id)}
                rows = {r["label"]: r["value"] for r in (await ex(adviser, india).preview_change("start_onboarding", ask))["rows"]}
                check("the card names the household it joins", rows["Household"], "Joins The Mehta Family")
                plain = {r["label"]: r["value"] for r in (await ex(adviser, india).preview_change("start_onboarding", {**ask, "household_id": None}))["rows"]}
                check("without one, a new household", plain["Household"], "A new household")
                smuggled = await onboarding_start.start(s, india, actor=adviser, prospect_name="Chitra Roy", registration_type="individual",
                                                        extra_intake={"household_id": str(foreign.id)})
                check("a household slipped in through intake is dropped", smuggled.intake.get("household_id"), None)
                made = await ex(adviser, india).execute("start_onboarding", ask)
                bina = await s.get(OnboardingCase, uuid.UUID(made["cases"][0]["id"]))
                homes = (await s.execute(select(func.count()).select_from(Household).where(Household.firm_id == india.id))).scalar_one()

                async def passing(*a, **k):
                    return {"blocks_activation": False, "gates": []}

                real_gates = onboarding_agent._activation_gates
                onboarding_agent._activation_gates = passing
                try:
                    agent = onboarding_agent.OnboardingAgent()
                    ctx = SimpleNamespace(session=s, firm=india, subject=Subject("onboarding_case", bina.id, bina.prospect_name))
                    out = await agent.act(ctx, SimpleNamespace(modified_payload=None, payload={}))
                    after = (await s.execute(select(func.count()).select_from(Household).where(Household.firm_id == india.id))).scalar_one()
                    spouse = await s.get(Person, uuid.UUID(out["person_id"]))
                    check("activation makes no second household", (after - homes, out["household_id"], spouse.household_id), (0, str(h.id), h.id))
                    await agent.rollback(ctx, None)
                    check("reversing removes the new person and keeps the household and the spouse",
                          (await s.get(Household, h.id) is not None, await s.get(Person, husband.id) is not None, await s.get(Person, spouse.id)), (True, True, None))
                    again = await onboarding_start.start(s, india, actor=adviser, prospect_name="Anil Mehta", registration_type="individual", household_id=str(h.id))
                    ctx = SimpleNamespace(session=s, firm=india, subject=Subject("onboarding_case", again.id, again.prospect_name))
                    out = await agent.act(ctx, SimpleNamespace(modified_payload=None, payload={}))
                    names = (await s.execute(select(func.count()).select_from(Person).where(Person.household_id == h.id, Person.full_name == "Anil Mehta"))).scalar_one()
                    check("a member's second account makes no second person", (out["person_id"], names), (str(husband.id), 1))
                    await agent.rollback(ctx, None)
                    check("reversing it keeps the member", await s.get(Person, husband.id) is not None, True)
                finally:
                    onboarding_agent._activation_gates = real_gates

            await section("A couple is one household", one_household)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
