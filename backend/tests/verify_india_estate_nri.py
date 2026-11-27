"""Prove estate and NRI planning at working depth for India (B-398).

  1. The tools: two reads that change nothing, four changes behind a Confirm card, none held by a client.
  2. The asset-and-wishes register lists what is on file, once, and names every asset with no nomination, non-book assets
     included; a recorded nomination answers for its asset.
  3. A will's witnesses and whether its executor was told are recorded facts; what is open is named.
  4. The yearly will check keeps what was on file, goes due after a year, and reads changed when an asset or the family
     changes; a needed change raises a task and drafts nothing.
  5. Wishes and the private-trust triggers are facts, and nothing is advised.
  6. A tax residency certificate is recorded and read against today.
  7. A move raises redesignation tasks from what is on file and changes no record.
  8. The not-ordinarily-resident outlook follows the law's two conditions and says what days would settle.
  9. Another firm and another market read and change nothing, and the demonstration carries it once.

Runs against a throwaway SQLite database, with the demonstration firm built from invented prices.

    python -m tests.verify_india_estate_nri
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import firm_time, law_figures
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.balance_sheet import BalanceItem
from app.models.client_file import LifeEvent
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Account, Household, Person
from app.models.identity import User
from app.models.tenant import Firm
from seed import india_demo

PASS, FAIL = [], []
R = UserRole
ROOT = Path(__file__).resolve().parents[2]
READS = ("read_estate_register", "read_nri_planning")
CHANGES = ("record_estate_wish", "record_will_check", "record_treaty_certificate", "record_residence_move")


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def section(title: str, fn) -> None:
    """One part's checks. A section that raises (the feature is not there) is one failure, and the rest still run."""
    print(f"\n=== {title} ===")
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 - reported as a failure with what raised
        FAIL.append(title)
        print(f"  FAIL  {title}: {type(exc).__name__}: {str(exc)[:300]}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except ExecutorError as e:
        check(label, contains.lower() in str(e).lower() or str(e), True)
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def labels(card: dict) -> dict:
    return {r["label"]: r["value"] for r in card["rows"]}


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            await s.commit()
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            sharma, banerjee, menon = homes["The Sharma Family"], homes["The Banerjee Household"], homes["The Menon NRI Household"]
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            rajesh, meera, subir = people["Rajesh Sharma"], people["Meera Sharma"], people["Subir Banerjee"]
            vikram, anita = people["Vikram Menon"], people["Anita Menon"]
            today = firm_time.today(firm)
            users = {role: (await s.execute(select(User).where(User.email == f"{who}@astra-india.demo"))).scalar_one() for role, who in (
                (R.ADVISER, "adviser"), (R.PARAPLANNER, "paraplanner"), (R.COMPLIANCE, "compliance"))}

            def ex(role=R.ADVISER) -> ToolExecutors:
                return ToolExecutors(s, users[role].id, role, firm.id)

            async def register(home) -> dict:
                return await ex().read_estate_register({"household_id": str(home.id)})

            def person_row(view, name) -> dict:
                return next(p for p in view["people"] if p["name"] == name)

            async def tools():
                for key in READS:
                    check(f"{key} changes nothing", TOOLS[key].change_state, ToolChangeState.NO)
                for key in CHANGES:
                    check(f"{key} waits for Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
                    check(f"{key} is made by those who record", TOOLS[key].roles_required, {R.ADVISER, R.PARAPLANNER, R.OPERATIONS})
                check("no client holds any of them", [k for k in READS + CHANGES if R.CLIENT in TOOLS[k].roles_required], [])
                renderer = (ROOT / "frontend" / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
                panes = (ROOT / "frontend" / "lib" / "paneViews.ts").read_text(encoding="utf-8")
                check("each has a card and opens beside the conversation",
                      [k for k in READS + CHANGES if f'case "{k}":' not in renderer or f'case "{k}":' not in panes], [])
                await refused("compliance records nothing", ex(R.COMPLIANCE).preview_change(
                    "record_estate_wish", {"person_id": str(rajesh.id), "words": "A wish said", "document": "Meeting note"}), "adviser")

            await section("The tools", tools)

            async def the_register():
                v = await register(sharma)
                kinds = {a["category"] for a in v["assets"]}
                check("accounts, holdings elsewhere and policies are listed", {"account", "held_away", "policy"} <= kinds, True)
                check("each asset once", len({a["key"] for a in v["assets"]}), len(v["assets"]))
                huf = [a for a in v["assets"] if a["entity_owned"] and a["category"] == "account"]
                check("an HUF's account takes no nomination", {a["nomination"]["state"] for a in huf}, {"not_applicable"})
                check("a gratuity is named", any(a["category"] == "gratuity" for a in v["assets"]), True)
                epf = next(a for a in v["assets"] if "Employees' Provident Fund" in a["label"] and a["owner"] == "Rajesh Sharma")
                check("the EPF nomination on file answers for the EPF balance", epf["nomination"]["state"], "nominated")
                s.add(BalanceItem(firm_id=firm.id, household_id=sharma.id, person_id=meera.id, side="asset", kind="cash",
                                  name="Meera Sharma ICICI savings", amount=900000, currency="INR", as_of=today,
                                  source_document="Bank statement (test)", recorded_by="test"))
                s.add(BalanceItem(firm_id=firm.id, household_id=sharma.id, person_id=meera.id, side="asset", kind="property",
                                  name="Flat in Pune", amount=18000000, currency="INR", as_of=today, source_document="Sale deed (test)",
                                  recorded_by="test"))
                await s.flush()
                v = await register(sharma)
                missing = {m["label"]: m for m in v["missing_nominations"]}
                check("a bank deposit with no nomination is flagged as a non-book asset",
                      missing.get("Meera Sharma ICICI savings", {}).get("non_book"), True)
                flat = next(a for a in v["assets"] if a["label"] == "Flat in Pune")
                check("a property passes by the will, not a nomination", (flat["nomination"]["state"], "Flat in Pune" in missing), ("not_applicable", False))
                await ex().record_asset_nomination({"person_id": str(meera.id), "asset_kind": "bank_account", "label": "Meera Sharma ICICI savings",
                                                    "status": "nominated", "nominees": "Rajesh Sharma:husband:100", "document": "Form DA1 (test)"})
                v = await register(sharma)
                bank = next(a for a in v["assets"] if a["label"] == "Meera Sharma ICICI savings")
                check("the recorded nomination answers for it", (bank["nomination"]["state"], bank["nomination"]["nominees"]), ("nominated", ["Rajesh Sharma"]))
                s.add(BalanceItem(firm_id=firm.id, household_id=sharma.id, person_id=meera.id, side="asset", kind="pension",
                                  name="Meera Sharma PPF", amount=700000, currency="INR", as_of=today, source_document="Passbook (test)", recorded_by="test"))
                await s.flush()
                await ex().record_asset_nomination({"person_id": str(meera.id), "asset_kind": "ppf", "label": "Meera Sharma PPF",
                                                    "status": "nominated", "nominees": "Aarav Sharma:son:100", "document": "Form (test)"})
                v = await register(sharma)
                check("a PPF nomination is recorded and read",
                      next(a for a in v["assets"] if a["label"] == "Meera Sharma PPF")["nomination"]["state"], "nominated")
                book = await ex(R.COMPLIANCE).read_estate_register({})
                check("the book names each household's counts", {h["household"] for h in book["households"]} >= {sharma.name, banerjee.name}, True)
                check("and the message carries them", "no nomination or opt-out" in book["message"], True)

            await section("The asset and wishes register", the_register)

            async def wills():
                row = person_row(await register(sharma), "Rajesh Sharma")
                check("the demonstration's will has two witnesses and a told executor",
                      (len(row["witnesses"]), row["executor_told"], row["flags"]), (2, True, []))
                theirs = person_row(await register(banerjee), "Subir Banerjee")
                check("one witness and an untold executor are named",
                      theirs["flags"], ["1 witness recorded, 2 expected", "the executor has not been told"])
                await refused("the maker does not witness", ex().preview_change(
                    "record_will_status", {"person_id": str(subir.id), "witnesses": "Subir Banerjee; P. Mukherjee", "document": "Will page"}), "does not witness")
                ready = {"person_id": str(subir.id), "witnesses": "P. Mukherjee; Rina Banerjee", "executor_told": True,
                         "executor_told_on": today.isoformat(), "document": "Attestation page (test)"}
                card = labels(await ex().preview_change("record_will_status", ready))
                check("the card names the witnesses and that the executor was told",
                      (card["Witnesses"], card["Executor told"].startswith("Yes")), ("P. Mukherjee; Rina Banerjee", True))
                await ex().record_will_status(ready)
                theirs = person_row(await register(banerjee), "Subir Banerjee")
                check("a witness also named to inherit is left for counsel", theirs["flags"],
                      ["a witness is also named to inherit (Rina Banerjee): for counsel"])
                await ex().record_will_status({"person_id": str(subir.id), "executor": "A. Ghosh", "document": "Codicil (test)"})
                theirs = person_row(await register(banerjee), "Subir Banerjee")
                check("a new executor has not been told what the last one was", theirs["executor_told"], None)
                check("the law's figure is assumed", law_figures.get("in_will_witnesses").source.startswith("ASSUMED"), True)

            await section("Witnesses and the executor", wills)

            async def will_check():
                row = person_row(await register(banerjee), "Subir Banerjee")
                check("a will on file never checked says so", row["check"]["state"], "never_checked")
                mine = person_row(await register(sharma), "Rajesh Sharma")["check"]
                check("the demonstration's check found a change to make", (mine["state"], bool(mine["due_on"])), ("needs_change", True))
                nobody = person_row(await register(sharma), "Meera Sharma")
                check("no will on file is said", (nobody["check"]["state"], nobody["will"]), ("no_will", None))
                await refused("no will, no check", ex().record_will_check(
                    {"person_id": str(meera.id), "outcome": "matches", "document": "Note (test)"}), "no will on file")
                await refused("a change needs its findings", ex().record_will_check(
                    {"person_id": str(subir.id), "outcome": "needs_change", "document": "Note (test)"}), "no longer matches")
                ready = {"person_id": str(subir.id), "outcome": "matches", "document": "Yearly check note (test)",
                         "checked_on": (today - timedelta(days=400)).isoformat()}
                card = labels(await ex().preview_change("record_will_check", ready))
                check("the card says what it was checked against", card["Checked against"].endswith("asset(s) on file"), True)
                await ex().record_will_check(ready)
                row = person_row(await register(banerjee), "Subir Banerjee")
                check("a check over a year old is due", row["check"]["state"], "due")
                await ex().record_will_check({**ready, "checked_on": today.isoformat()})
                row = person_row(await register(banerjee), "Subir Banerjee")
                check("checked again", row["check"]["state"], "checked")
                s.add(BalanceItem(firm_id=firm.id, household_id=banerjee.id, person_id=subir.id, side="asset", kind="cash",
                                  name="Subir Banerjee fixed deposit", amount=400000, currency="INR", as_of=today,
                                  source_document="Deposit receipt (test)", recorded_by="test"))
                await s.flush()
                row = person_row(await register(banerjee), "Subir Banerjee")
                check("a new asset reads changed since the check", (row["check"]["state"], row["check"]["changes"]),
                      ("changed_since", ["added: Subir Banerjee fixed deposit"]))
                held = (await s.execute(select(BalanceItem).where(BalanceItem.name == "Subir Banerjee fixed deposit"))).scalar_one()
                held.status = "ended"
                s.add(BalanceItem(firm_id=firm.id, household_id=banerjee.id, person_id=subir.id, side="asset", kind="cash",
                                  name="Subir Banerjee fixed deposit", amount=450000, currency="INR", as_of=today,
                                  source_document="A newer receipt (test)", recorded_by="test"))
                await s.flush()
                row = person_row(await register(banerjee), "Subir Banerjee")
                check("a newer figure for the same asset adds no second change", row["check"]["changes"], ["added: Subir Banerjee fixed deposit"])
                s.add(LifeEvent(firm_id=firm.id, household_id=banerjee.id, person_id=subir.id, kind="marriage",
                                happened_on=today + timedelta(days=0), source_document="Certificate (test)", recorded_by="test"))
                before = (await s.execute(select(func.count()).select_from(Task).where(Task.firm_id == firm.id, Task.source == "will_check"))).scalar_one()
                done = await ex().record_will_check({"person_id": str(subir.id), "outcome": "needs_change", "document": "Second note (test)",
                                                     "findings": "The new fixed deposit is not in the will"})
                after = (await s.execute(select(func.count()).select_from(Task).where(Task.firm_id == firm.id, Task.source == "will_check"))).scalar_one()
                check("a needed change raises one task", after - before, 1)
                check("and says the will is unchanged", "will itself is unchanged" in done["message"], True)

            await section("The yearly will check", will_check)

            async def triggers():
                tasks_before = (await s.execute(select(func.count()).select_from(Task).where(Task.firm_id == firm.id))).scalar_one()
                v = await register(menon)
                check("a minor brings a trust into view", [t["key"] for t in v["trust_triggers"]], ["minor_beneficiary"])
                await ex().record_estate_wish({"person_id": str(vikram.id), "kind": "special_needs_beneficiary",
                                               "words": "Kabir will need support for life", "document": "Meeting note (test)"})
                v = await register(menon)
                check("a recorded special-needs beneficiary is a trigger, with its evidence",
                      [t["key"] for t in v["trust_triggers"]], ["minor_beneficiary", "special_needs_beneficiary"])
                mine = await register(sharma)
                check("the demonstration's business wish is a trigger", "business_succession" in [t["key"] for t in mine["trust_triggers"]], True)
                said = mine["message"].lower()
                check("nothing is recommended", ("recommend" in said, "you should" in said), (False, False))
                after = (await s.execute(select(func.count()).select_from(Task).where(Task.firm_id == firm.id))).scalar_one()
                check("a trigger raises nothing", after - tasks_before, 0)
                await refused("a wish is not recorded twice", ex().record_estate_wish(
                    {"person_id": str(vikram.id), "kind": "special_needs_beneficiary", "words": "Kabir will need support for life",
                     "document": "Meeting note (test)"}), "already")

            await section("Wishes and trust triggers", triggers)

            async def certificates():
                v = await ex().read_nri_planning({"household_id": str(menon.id)})
                by = {p["name"]: p for p in v["people"]}
                check("one certificate covers, one person has none", (by["Vikram Menon"]["trc"], by["Anita Menon"]["trc"]), ("covers", "not_on_file"))
                check("treaty relief is not modelled", "not modelled" in v["message"], True)
                old = {"person_id": str(anita.id), "kind": "trc", "country": "United Arab Emirates", "reference": "T-1",
                       "valid_from": "2024-04-01", "valid_to": "2025-03-31", "document": "Certificate (test)"}
                await refused("a treaty certificate is another country's", ex().record_treaty_certificate({**old, "country": "India"}), "another country")
                await refused("its dates run forward", ex().record_treaty_certificate({**old, "valid_to": "2023-01-01"}), "on or after")
                card = labels(await ex().preview_change("record_treaty_certificate", old))
                check("the card names the document", (card["Document type"], card["Reference"]), ("Tax residency certificate", "T-1"))
                v = await ex().record_treaty_certificate(old)
                check("an old certificate reads expired", {p["name"]: p["trc"] for p in v["people"]}["Anita Menon"], "expired")

            await section("Tax residency certificates", certificates)

            async def moves():
                v = await ex().read_nri_planning({"household_id": str(menon.id)})
                move = next(p for p in v["people"] if p["name"] == "Vikram Menon")["moves"][0]
                states = {i["key"]: i["state"] for i in move["items"]}
                check("the demonstration's return raised tasks from what is on file",
                      (move["direction"], move["planned"], states["nri_accounts"], states["demat_resident"], states["fcnr"]),
                      ("returning", True, "open", "open", "nothing_on_file"))
                task = await s.get(Task, uuid.UUID(next(i["task_id"] for i in move["items"] if i["key"] == "demat_resident")))
                check("a task names the account and says the rule is assumed",
                      ("Vikram Menon NRO demat" in task.detail, "assumed" in task.detail, task.source), (True, True, "residence_move"))
                ready = {"person_id": str(rajesh.id), "direction": "leaving", "moved_on": (today + timedelta(days=30)).isoformat(),
                         "country": "Singapore", "document": "Employment contract (test)"}
                account = (await s.execute(select(Account).where(Account.name == "Rajesh Sharma demat", Account.firm_id == firm.id))).scalars().first()
                card = labels(await ex().preview_change("record_residence_move", ready))
                check("the card lists the tasks it would raise", any(k.startswith("Task ") and "demat" in str(v_) for k, v_ in card.items()), True)
                count = select(func.count()).select_from(Task).where(Task.firm_id == firm.id, Task.source == "residence_move")
                before = (await s.execute(count)).scalar_one()
                made = await ex().record_residence_move(ready)
                mine = next(p for p in made["people"] if p["name"] == "Rajesh Sharma")["moves"][0]
                check("leaving raises a task for each step with something on file",
                      ((await s.execute(count)).scalar_one() - before, mine["tasks"] >= 4), (mine["tasks"], True))
                if account is not None:
                    check("no account is changed", (await s.get(Account, account.id)).registration_type, account.registration_type)
                await refused("the same move is not recorded twice", ex().record_residence_move(ready), "already recorded")
                await refused("nor a move years ahead", ex().record_residence_move(
                    {**ready, "moved_on": (today + timedelta(days=900)).isoformat()}), "a year ahead")

            await section("Leaving and returning", moves)

            async def outlook():
                from app.aurea_core import nri_planning, residency

                day = date(2026, 11, 27)
                facts = {"tax_year": "2026-27", "days_in_india": 41, "days_prior_seven_years": 310, "nonresident_years_of_prior_ten": 10,
                         "days_prior_four_years": 170, "indian_citizen_or_pio": True, "left_for_employment_abroad": True,
                         "india_income": 640000, "taxed_elsewhere": False}
                status = residency.tax_status(facts, day)
                o = nri_planning.rnor_outlook(facts, status, day, date(2027, 4, 26))
                check("a long-absent returnee: certain, certain, then depending on days, then ordinary",
                      [(r["year"], r["state"]) for r in o["years"]],
                      [("2027-28", "rnor"), ("2028-29", "rnor"), ("2029-30", "depends"), ("2030-31", "ordinary")])
                check("the ranges are shown", (o["years"][1]["nonresident_of_ten"], o["years"][2]["nonresident_of_ten"]), ([9, 10], [8, 9]))
                check("and the assumptions", len(o["assumed"]) >= 3, True)
                short = nri_planning.rnor_outlook({**facts, "nonresident_years_of_prior_ten": 2, "days_prior_seven_years": 2000},
                                                  status, day, date(2027, 4, 26))
                check("a short absence is ordinarily resident at once", short["years"][0]["state"], "ordinary")
                unknown = nri_planning.rnor_outlook({**facts, "days_prior_seven_years": None}, status, day, date(2027, 4, 26))
                check("a missing fact is not assessed and named", (unknown["state"], "seven years" in unknown["why"]), ("not_assessed", True))
                check("no return, no outlook", nri_planning.rnor_outlook(facts, status, day, None)["state"], "not_applicable")
                v = await ex().read_nri_planning({"household_id": str(menon.id)})
                mine = next(p for p in v["people"] if p["name"] == "Vikram Menon")["outlook"]
                check("the household view lays it out for the planned return", (mine["state"], mine["years"][0]["state"]), ("laid_out", "rnor"))

            await section("Not ordinarily resident, laid out", outlook)

            async def scope():
                other = Firm(id=uuid.uuid4(), name="US", slug="us", base_currency="USD", jurisdiction="US", settings={})
                s.add(other)
                await s.flush()
                amy = User(id=uuid.uuid4(), firm_id=other.id, email="a@us.test", full_name="Amy", hashed_password="x", role=R.ADVISER)
                s.add(amy)
                await s.flush()
                theirs = ToolExecutors(s, amy.id, R.ADVISER, other.id)
                check("another market reads nothing", (await theirs.read_estate_register({}))["applies"], False)
                await refused("another firm records nothing of this client", theirs.record_estate_wish(
                    {"person_id": str(rajesh.id), "words": "A wish said", "document": "Meeting note"}), "Indian firm")
                second = Firm(id=uuid.uuid4(), name="IN2", slug="in2", base_currency="INR", jurisdiction="IN", settings={})
                s.add(second)
                await s.flush()
                them = ToolExecutors(s, amy.id, R.ADVISER, second.id)
                await refused("nor another Indian firm", them.record_estate_wish(
                    {"person_id": str(rajesh.id), "words": "A wish said", "document": "Meeting note"}), "not a client of this firm")
                await refused("nor reads their register", them.read_estate_register({"household_id": str(sharma.id)}), "not found")
                await refused("nor their NRI records", them.read_nri_planning({"household_id": str(menon.id)}), "not found")

            await section("Firm and market scope", scope)

            async def demonstration():
                from seed import india_estate_nri
                from app.models.estate_nri import EstateWish, ResidenceMove, TreatyCertificate, WillCheck

                async def counts():
                    return [(await s.execute(select(func.count()).select_from(m).where(m.recorded_by == india_estate_nri.BY))).scalar_one()
                            for m in (EstateWish, WillCheck, TreatyCertificate, ResidenceMove)]

                was = await counts()
                check("one of each demonstration record", was, [1, 1, 1, 1])
                check("a second run adds nothing", (await india_estate_nri.add(s, firm), await counts()), (0, was))
                limits = {"kind": 32, "asset_label": 200, "source_document": 300, "recorded_by": 200}
                long = [(w.kind, k) for w in (await s.execute(select(EstateWish))).scalars().all()
                        for k, n in limits.items() if len(getattr(w, k) or "") > n]
                check("seeded strings fit their columns", long, [])

            await section("The demonstration", demonstration)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  failed: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
