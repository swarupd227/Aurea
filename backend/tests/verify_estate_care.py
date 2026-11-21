"""Prove the estate gaps (B-185), how care will be paid (B-186) and asset location by registration (B-187).

B-185: a revocable trust on file with no account registered to a trust reads "may be unfunded"; a household with a minor
and no will on file is named; accounts whose registration or beneficiaries disagree with the file are listed; and all of it
goes to the recorded adviser as one task behind a Confirm card, changing no account. Not on file never means none exists.
B-186: a care-funding decision is the adviser's or paraplanner's, with a reason, added and never edited; none on file
reads "no decision", never self-fund, and the care figure does not move.
B-187: a Roth holds growth apart from tax-deferred, and nothing is said about a move no sheltered account can receive.

Runs against a throwaway SQLite database.

    python -m tests.verify_estate_care
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import asset_location
from app.atlas import runtime
from app.atlas.base import Subject
from app.aurea_core import client_file, firm_time, protection
from app.aurea_core.exits import _RETIREMENT
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.engagement import Task
from app.models.enums import AgentKey, MarketType, RecommendationStatus, UserRole as R
from app.models.governance import Recommendation
from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.protection import CareFundingDecision
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, client_file.ClientFileError, protection.ProtectionError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure_location() -> None:
    print("\n=== B-187: a Roth holds growth apart from tax-deferred ===")
    check("a Roth is tax-free and still an IRA, so sheltered", (asset_location.treatment("roth_ira"), "roth_ira" in _RETIREMENT), ("tax_free", True))
    check("traditional, SEP, SIMPLE and rollover defer tax",
          {asset_location.treatment(r) for r in ("traditional_ira", "sep_ira", "simple_ira", "employer_rollover")}, {"tax_deferred"})
    check("an individual account is neither", asset_location.treatment("individual"), "taxable")
    check("an inherited IRA is sheltered, but takes nothing in a swap", (asset_location.treatment("inherited_ira"), "inherited_ira" in asset_location.RECEIVES), ("sheltered", False))

    eq = [{"instrument": "VTI", "asset_class": "equity", "market_value": 120_000}]
    bond = [{"instrument": "BND", "asset_class": "fixed_income", "market_value": 60_000}]
    run = asset_location._analyze_location

    brain = {"accounts": [{"id": "r", "name": "Roth", "positions": eq}, {"id": "t", "name": "Traditional", "positions": bond}]}
    check("growth in a Roth raises nothing: that is where it belongs", run(brain, {"r": "roth_ira", "t": "traditional_ira"}), [])
    brain = {"accounts": [{"id": "t", "name": "Traditional", "positions": eq}, {"id": "r", "name": "Roth", "positions": bond}]}
    found = {f["issue"]: f for f in run(brain, {"t": "traditional_ira", "r": "roth_ira"})}
    check("growth in a tax-deferred account goes to the Roth", found["growth_in_tax_deferred"]["receiving_accounts"], ["Roth"])
    check("income in the Roth goes to the tax-deferred account", found["income_in_tax_free"]["receiving_accounts"], ["Traditional"])
    check("  each account is named by how it is taxed", (found["growth_in_tax_deferred"]["account_type"], found["income_in_tax_free"]["account_type"]), ("tax_deferred", "tax_free"))

    brain = {"accounts": [{"id": "t", "name": "Traditional", "positions": eq}, {"id": "b", "name": "Brokerage", "positions": bond}]}
    regs = {"t": "traditional_ira", "b": "individual"}
    nz_like = run(brain, regs)
    check("with no Roth, growth goes to the taxable account", [f["receiving_accounts"] for f in nz_like if f["issue"] == "growth_in_tax_deferred"], [["Brokerage"]])
    check("  and step-up is not given as a reason where the market has none", any("steps up" in f["recommendation"] for f in nz_like), False)
    check("  where it has one, it is", any("steps up" in f["recommendation"] for f in run(brain, regs, step_up=True)), True)
    check("  bonds in the taxable account go to the tax-deferred one", [f["receiving_accounts"] for f in nz_like if f["issue"] == "tax_inefficient_in_taxable"], [["Traditional"]])

    print("\n=== B-187: nothing is said where no sheltered account can receive ===")
    before = "tax_inefficient_in_taxable raised with no sheltered account (before B-187)"
    alone = {"accounts": [{"id": "b", "name": "Brokerage", "positions": bond}]}
    check(f"bonds in a taxable account, and no sheltered account: nothing [{before}]", run(alone, {"b": "individual"}), [])
    check("an account with no registration is taxable, and with none sheltered, nothing", run(alone, {"b": None}), [])
    inherited = {"accounts": [{"id": "b", "name": "Brokerage", "positions": bond}, {"id": "i", "name": "Inherited", "positions": eq}]}
    check("an inherited IRA cannot receive, so nothing", run(inherited, {"b": "individual", "i": "inherited_ira"}), [])
    empty = {"accounts": [{"id": "b", "name": "Brokerage", "positions": bond}, {"id": "t", "name": "Empty IRA", "positions": []}]}
    check("an empty IRA holds nothing to swap out, so nothing", run(empty, {"b": "individual", "t": "traditional_ira"}), [])
    roth_only = {"accounts": [{"id": "b", "name": "Brokerage", "positions": bond}, {"id": "r", "name": "Roth", "positions": eq}]}
    check("with only a Roth, bonds go to the Roth", [f["receiving_accounts"] for f in run(roth_only, {"b": "individual", "r": "roth_ira"})], [["Roth"]])
    cash = {"accounts": [{"id": "t", "name": "Traditional", "positions": [{"instrument": "CASH", "asset_class": "cash", "market_value": 90_000}]},
                         {"id": "r", "name": "Roth", "positions": eq}]}
    check("cash in an IRA is not growth equity", run(cash, {"t": "traditional_ira", "r": "roth_ira"}), [])


def catalogue() -> None:
    print("\n=== The tools ===")
    for key, roles in (("raise_estate_review", {R.ADVISER, R.PARAPLANNER, R.OPERATIONS}), ("record_care_funding", {R.ADVISER, R.PARAPLANNER})):
        check(f"{key} stops for a Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
        check(f"  and is held by {sorted(r.value for r in roles)}", TOOLS[key].roles_required, roles)
        check("  a client does not hold it", key in tools_for_role(R.CLIENT), False)
    check("compliance records no care decision", "record_care_funding" in tools_for_role(R.COMPLIANCE), False)
    check("the client file read stays a read", TOOLS["read_client_file"].change_state, ToolChangeState.NO)


async def db_checks() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Oak", slug="oak", base_currency="USD", jurisdiction="US")
            nz = Firm(id=uuid.uuid4(), name="Kauri", slug="kauri", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, nz])
            await s.flush()
            today = firm_time.today(firm)

            def user(f, name, role):
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{uuid.uuid4().hex}@x.test", full_name=name, role=role, hashed_password="x", is_active=True)
                s.add(u)
                return u
            ann, pat, cora, opal, bea = (user(firm, "Ann Adviser", R.ADVISER), user(firm, "Pat Para", R.PARAPLANNER), user(firm, "Cora Compliance", R.COMPLIANCE),
                                         user(firm, "Opal Ops", R.OPERATIONS), user(firm, "Bea Branch", R.BRANCH_LEADER))
            kiri = user(nz, "Kiri Adviser", R.ADVISER)
            park, lone, shell = (Household(id=uuid.uuid4(), firm_id=firm.id, name="Park"), Household(id=uuid.uuid4(), firm_id=firm.id, name="Lone"),
                                 Household(id=uuid.uuid4(), firm_id=firm.id, name="Shell"))
            kauri = Household(id=uuid.uuid4(), firm_id=nz.id, name="Kauri Family")
            s.add_all([park, lone, shell, kauri])
            await s.flush()
            jin = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=park.id, full_name="Jin Park", date_of_birth=date(today.year - 46, 3, 1))
            mia = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=park.id, full_name="Mia Park", date_of_birth=date(today.year - 44, 5, 1))
            kai = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=park.id, full_name="Kai Park", date_of_birth=today - timedelta(days=365 * 9 + 10), is_next_gen=True)
            gus = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=park.id, full_name="Gus Park", is_next_gen=True)
            leo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=lone.id, full_name="Leo Lone", date_of_birth=date(today.year - 60, 1, 1))
            sam = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=shell.id, full_name="Sam Shell", date_of_birth=date(today.year - 70, 1, 1))
            tui = Person(id=uuid.uuid4(), firm_id=nz.id, household_id=kauri.id, full_name="Tui Kauri", date_of_birth=date(today.year - 70, 1, 1))
            s.add_all([jin, mia, kai, gus, leo, sam, tui])
            await s.flush()
            s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ann.id, to_type="household", to_id=park.id))

            def account(person, name, registration, entity=None):
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id if person else None, entity_id=entity.id if entity else None,
                            name=f"{name} mandate", mandate_type="advisory", suitability={}, constraints={})
                s.add(m)
                a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=name, currency="USD", cash_balance=0, registration_type=registration)
                s.add(a)
                return a
            brokerage, ira = account(jin, "Jin Brokerage", "individual"), account(jin, "Jin IRA", "traditional_ira")
            mia_joint = account(mia, "Mia Joint", "joint_jtwros")
            shell_trust = account(sam, "Shell Trust A/C", "trust")
            await s.flush()
            s.add(AccountBeneficiary(firm_id=firm.id, account_id=ira.id, beneficiary_name="Kai Park", percentage=100, designation_class="primary"))
            await s.commit()

            def ex(u):
                return ToolExecutors(s, u.id, u.role, u.firm_id)

            async def card(u, tool, inputs):
                return {r["label"]: r["value"] for r in (await ex(u).preview_change(tool, inputs))["rows"]}

            async def gaps(h=park):
                return (await ex(ann).read_client_file({"household_id": str(h.id)}))["estate_gaps"]

            async def count(model):
                return (await s.execute(select(func.count()).select_from(model))).scalar()

            print("\n=== B-185: a minor and no will on file ===")
            g = await gaps()
            check("no trust on file, none read as unfunded", (g["trusts"], g["may_be_unfunded"]), ([], 0))
            check("the minor is named, with the age on file", g["guardianship"]["minors"], [{"name": "Kai Park", "age": 9}])
            check("  and no will is on file", g["guardianship"]["no_will_on_file"], True)
            check("someone with no date of birth is not taken for a minor, and is named", (g["ages_not_on_file"], "Gus Park" in [m["name"] for m in g["guardianship"]["minors"]]), (["Gus Park"], False))
            check("the age of majority is named as assumed", g["assumed"], ["minor_under_18"])
            check("not on file says it does not mean none exists", "does not mean none exists" in g["not_on_file"], True)
            check("a minor named as an IRA's beneficiary is on the titling list",
                  [(f["account"], f["issue"], f["name"]) for f in g["titling_fixes"]], [("Jin IRA", "beneficiary is a minor", "Kai Park")])
            check("  with its evidence", g["titling_fixes"][0]["evidence"], "date of birth on file, age 9")

            print("\n=== B-185: a revocable trust with nothing in it ===")
            await ex(ann).record_estate_document({"person_id": str(jin.id), "kind": "revocable_trust", "signed_on": (today - timedelta(days=400)).isoformat(),
                                                  "source_document": "Attorney's letter, 2025", "held_by": "Lee & Co"})
            g = await gaps()
            check("a revocable trust with no account registered to a trust may be unfunded", [(t["person"], t["status"]) for t in g["trusts"]], [("Jin Park", "may_be_unfunded")])
            check("Jin's individual account is titled outside it", [f["account"] for f in g["titling_fixes"] if f["issue"] == "titled outside the revocable trust"], ["Jin Brokerage"])
            check("  Mia's joint account is not: the trust is Jin's", any(f["account"] == "Mia Joint" for f in g["titling_fixes"]), False)
            check("  the IRA is not: a retirement account passes by its beneficiaries", any(f["account"] == "Jin IRA" and f["issue"] == "titled outside the revocable trust" for f in g["titling_fixes"]), False)
            await ex(ann).record_estate_document({"person_id": str(mia.id), "kind": "will", "signed_on": (today - timedelta(days=100)).isoformat(), "source_document": "Copy seen, 2026"})
            g = await gaps()
            check("a will on file for an adult: the minor is no longer named as without one", (g["guardianship"]["no_will_on_file"], g["guardianship"]["wills_on_file"]), (False, ["Mia Park"]))

            print("\n=== B-185: raised for the adviser behind a Confirm card ===")
            shown = await card(ann, "raise_estate_review", {"household_id": str(park.id)})
            check("the card names who it goes to", shown["Goes to"], "Ann Adviser")
            check("  what may be unfunded", shown["May be unfunded"], "Jin Park's revocable trust")
            check("  the titling fixes", shown["Titling fixes"], "Jin Brokerage: titled outside the revocable trust; Jin IRA: beneficiary is a minor, Kai Park")
            check("  no minor row once a will is on file", "Minor, no will on file" in shown, False)
            check("  and that it changes nothing", shown["Changes"], "Nothing on any account; nothing is sent")
            check("showing it writes no task", await count(Task), 0)
            await refused("compliance does not raise it", ex(cora).preview_change("raise_estate_review", {"household_id": str(park.id)}), "advisers, paraplanners and operations")
            await refused("another firm's adviser finds no household", ex(kiri).preview_change("raise_estate_review", {"household_id": str(park.id)}), "not found")
            await refused("a household with nothing on file to raise", ex(ann).preview_change("raise_estate_review", {"household_id": str(lone.id)}), "nothing on file", "does not mean none exists")
            done = await ex(opal).execute("raise_estate_review", {"household_id": str(park.id)})
            await s.commit()
            task = (await s.execute(select(Task))).scalars().one()
            check("one task, to the recorded adviser, from the estate review", (task.assigned_to, task.source, task.title), (ann.id, "estate_review", "Estate review: Park"))
            check("  due in thirty days on the firm's calendar", task.due_date, today + timedelta(days=30))
            check("  carrying the titling list", ("Jin Brokerage - titled outside the revocable trust" in task.detail, "Kai Park" in task.detail), (True, True))
            check("  and that not on file does not mean none exists", "does not mean none exists" in task.detail, True)
            check("the result says nothing was sent and names the adviser", ("nothing was sent" in done["message"], done["task"]["assigned_to_name"]), (True, "Ann Adviser"))
            await s.refresh(brokerage)
            check("no account was retitled", brokerage.registration_type, "individual")
            await refused("raising it again while it is open", ex(ann).preview_change("raise_estate_review", {"household_id": str(park.id)}), "already an open task")

            print("\n=== B-185: registration against the documents ===")
            trust_ac = account(jin, "Park Family Trust A/C", "trust")
            await s.commit()
            g = await gaps()
            check("an account registered to a trust: the trust reads funded, naming it", [(t["status"], t["accounts"]) for t in g["trusts"]], [("accounts_registered", ["Park Family Trust A/C"])])
            check("  and a registration with a trust document on file is no fix", any(f["account"] == "Park Family Trust A/C" for f in g["titling_fixes"]), False)
            gs = await gaps(shell)
            check("a trust registration with no trust document or entity on file is listed",
                  [(f["account"], f["issue"]) for f in gs["titling_fixes"]], [("Shell Trust A/C", "registered to a trust with no trust document on file")])
            trust = LegalEntity(id=uuid.uuid4(), firm_id=firm.id, household_id=shell.id, name="Shell Trust", entity_type="trust", governance={}, jurisdiction="US")
            s.add(trust)
            await s.commit()
            check("  a trust entity on file is a record of it", (await gaps(shell))["titling_fixes"], [])
            await refused("  so there is nothing to raise for that household", ex(pat).preview_change("raise_estate_review", {"household_id": str(shell.id)}), "nothing on file")
            # A household with nobody on record as its adviser gets an unassigned task, and says so.
            sams_roth = account(sam, "Sam Roth", "roth_ira")
            await s.flush()
            s.add(AccountBeneficiary(firm_id=firm.id, account_id=sams_roth.id, beneficiary_name="Ex Shell", percentage=60, designation_class="primary"))
            await s.commit()
            shown = await card(pat, "raise_estate_review", {"household_id": str(shell.id)})
            check("designations that do not add up are on the list", shown["Titling fixes"], "Sam Roth: primary designations sum to 60.0%, not 100%")
            check("with no adviser on record it goes unassigned, and says so", shown["Goes to"], "Unassigned: no adviser on record")

            print("\n=== B-186: how care will be paid ===")
            elder = Household(id=uuid.uuid4(), firm_id=firm.id, name="Elder", values={"jurisdiction": "US"})
            s.add(elder)
            await s.flush()
            s.add(Person(id=uuid.uuid4(), firm_id=firm.id, household_id=elder.id, full_name="Ed Elder", date_of_birth=date(today.year - 72, 1, 1)))
            firm.settings = {"protection": {"care": {"annual_cost": 100_000, "years": 3, "source": "Survey 2026", "set_by": "bea"}}}
            await s.commit()
            read = await ex(ann).read_protection({"household_id": str(elder.id)})
            check("none on file reads no decision, never self-fund", (read["care_funding"]["status"], read["care_funding"]["choice"]), ("no_decision", None))
            care_before = read["people"][0]["care"]
            choice = {"household_id": str(elder.id), "choice": "self_fund", "earmark_amount": 250_000, "reason": "Ed would rather keep the portfolio for care than pay premiums."}
            shown = await card(ann, "record_care_funding", choice)
            check("the card names the choice, earmark and reason",
                  (shown["Care paid by"], shown["Earmarked"], shown["Reason"]), ("Self-fund", "$250,000", choice["reason"]))
            check("  and that it supersedes no decision", shown["Supersedes"], "No decision on file")
            check("showing it writes nothing", await count(CareFundingDecision), 0)
            await refused("self-funding with no earmark", ex(ann).preview_change("record_care_funding", {**choice, "earmark_amount": None}), "give the amount")
            await refused("an earmark on care insurance", ex(ann).preview_change("record_care_funding", {**choice, "choice": "care_insurance"}), "earmark goes with")
            await refused("no reason", ex(ann).preview_change("record_care_funding", {**choice, "reason": "ok"}), "reason")
            await refused("an unknown choice", ex(ann).preview_change("record_care_funding", {**choice, "choice": "lottery"}), "choose one of")
            for who, label in ((cora, "compliance"), (bea, "a branch leader"), (opal, "operations")):
                await refused(f"{label} does not record it", ex(who).preview_change("record_care_funding", choice), "adviser or paraplanner")
                await refused(f"  nor by calling it", protection.record_care_funding(s, firm.id, recorded_by="x", role=str(who.role), **{**choice, "household_id": elder.id}), "adviser or paraplanner")
            await refused("another firm's household", ex(kiri).preview_change("record_care_funding", choice), "not in this firm")
            done = await ex(ann).execute("record_care_funding", choice)
            await s.commit()
            check("recorded, by its adviser, on the firm's day", (done["choice"], done["earmark_amount"], done["decided_on"]), ("self_fund", 250_000.0, today.isoformat()))
            read = await ex(ann).read_protection({"household_id": str(elder.id)})
            check("the protection read shows it beside the care need", (read["care_funding"]["choice"], read["care_funding"]["reason"]), ("self_fund", choice["reason"]))
            check("  and the care figure did not move", read["people"][0]["care"], care_before)
            check("  the firm's list carries it too", next(h["care_funding"]["choice"] for h in (await ex(ann).read_protection({}))["households"] if h["household"] == "Elder"), "self_fund")
            later = {"household_id": str(elder.id), "choice": "medicaid_planning", "reason": "Ed's attorney is planning a Medicaid spend-down."}
            check("a new one names what it supersedes", (await card(pat, "record_care_funding", later))["Supersedes"], "Self-fund")
            await ex(pat).execute("record_care_funding", later)
            await s.commit()
            read = await ex(ann).read_protection({"household_id": str(elder.id)})
            check("the latest stands, and one came before it", (read["care_funding"]["choice"], read["care_funding"]["superseded"]), ("medicaid_planning", 1))
            first = (await s.execute(select(CareFundingDecision).order_by(CareFundingDecision.created_at))).scalars().first()
            check("the first is kept as it was, never edited", (await count(CareFundingDecision), first.choice, float(first.earmark_amount)), (2, "self_fund", 250_000.0))
            nz_choice = {"household_id": str(kauri.id), "choice": "medicaid_planning", "reason": "Tui has asked about state care funding."}
            await refused("Medicaid planning for a household not taxed in the US", ex(kiri).preview_change("record_care_funding", nz_choice), "medicaid", "not taxed in the us")
            check("  undecided is anyone's", (await card(kiri, "record_care_funding", {**nz_choice, "choice": "undecided"}))["Care paid by"], "Undecided")

            print("\n=== B-187: the agent, run on a book ===")
            loc = Household(id=uuid.uuid4(), firm_id=firm.id, name="Loc", values={"jurisdiction": "US"})
            s.add(loc)
            await s.flush()
            lo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=loc.id, full_name="Lo Loc", date_of_birth=date(1965, 1, 1))
            s.add(lo)
            await s.flush()
            taxable, roth = account(lo, "Lo Brokerage", "individual"), account(lo, "Lo Roth", "roth_ira")
            bond_i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="BND", name="Bond fund", asset_class="fixed_income", market_type=MarketType.PUBLIC, currency="USD")
            eq_i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="VTI", name="Equity fund", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            s.add_all([bond_i, eq_i])
            await s.flush()
            for inst, acc, qty in ((bond_i, taxable, 600), (eq_i, roth, 1200)):
                s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=today, close=100.0, currency="USD", source="test", is_real=True))
                s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acc.id, instrument_id=inst.id, quantity=qty, market_value=qty * 100.0, cost_basis=qty * 90.0))
            await s.commit()

            async def scan():
                await runtime.run_agent(s, firm=firm, agent_key=AgentKey.ASSET_LOCATION, subject=Subject("household", loc.id, loc.name), trigger="manual")
                await s.commit()
                return (await s.execute(select(Recommendation).where(Recommendation.agent_key == AgentKey.ASSET_LOCATION.value,
                                                                     Recommendation.status == RecommendationStatus.PROPOSED))).scalars().all()
            open_ = await scan()
            check("bonds in the taxable account, a Roth to take them: one proposal", len(open_), 1)
            issues = [(f["issue"], f["account"], f["receiving_accounts"]) for f in open_[0].payload["findings"]]
            check("  naming the Roth, and nothing about the Roth's own growth", issues, [("tax_inefficient_in_taxable", "Lo Brokerage", ["Lo Roth"])])
            check("a second run refreshes it, not a second proposal", len(await scan()), 1)
    finally:
        await db.dispose()


async def main() -> int:
    pure_location()
    catalogue()
    await db_checks()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
