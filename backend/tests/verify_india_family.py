"""Prove India row 13: SEBI's family of client, nominations a person recorded, a will's state and the law it follows, an HUF and a gratuity.

Covers the family as a person recorded it (a spouse, a dependent child, a child recorded as not dependent standing alone, a family
across two households), the household standing in with its words where nothing is recorded, the pre-advice gate and the fee cap
reading it, every refusal; nominations for a bank account, EPF and a signed opt-out checked against the caps, the shares, the will
and who has died, and the client file reading them; a will's registration and probate, the personal law never inferred, and the
intestacy order laid out only for a recorded law; an HUF's karta; a gratuity named beside the pot and never added; the tools, their
Confirm cards and who may record.

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_family
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import client_file, fee_cap, financial_plan, firm_time, india_estate, law_figures, nominations, pre_advice, sebi_family
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.graph import Household, LegalEntity, Mandate, Person
from app.models.identity import User
from app.models.india_family import AssetNomination, GratuityRecord, SebiRelation, WillStatus
from app.models.tenant import Firm
from seed import india_demo, india_row13

PASS, FAIL = [], []
E = india_estate


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None, exc=(E.EstateError, sebi_family.FamilyError, ExecutorError)) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def entry(household, mandate, amount: float = 30_000, securities: float = 5_000_000) -> dict:
    return {"household_id": str(household.id), "mandate_id": str(mandate.id), "period_end": "2026-11-20", "amount": amount, "periods_per_year": 4,
            "period_fraction": 1.0, "accounts": [{"securities": securities}], "schedule": {"fee_type": "flat_fee"}, "checks": []}


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
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
            rajesh, meera, aarav = people["Rajesh Sharma"], people["Meera Sharma"], people["Aarav Sharma"]
            elaine, subir, karthik = people["Elaine D'Souza"], people["Subir Banerjee"], people["Karthik Iyer"]
            sharma, iyer, banerjee, dsouza = (homes[n] for n in ("The Sharma Family", "The Iyer Family", "The Banerjee Household", "The D'Souza Household"))

            print("\n=== The definition, with its source ===")
            fig = law_figures.get("in_family_of_client")
            check("SEBI's family is the individual, spouse, dependent children and dependent parents, and says it was not re-read",
                  ("dependent children" in fig.label, "not re-read" in fig.source), (True, True))
            check("the nominee caps are law figures: 3 and 4", (law_figures.value("in_nominee_cap_demat_mf"), law_figures.value("in_nominee_cap_bank")), (3, 4))

            print("\n=== The family as a person recorded it ===")
            check("the seed records the Sharmas", (await s.execute(select(func.count()).select_from(SebiRelation).where(SebiRelation.firm_id == firm.id))).scalar_one() > 0, True)
            v = await sebi_family.household_view(s, firm, sharma.id)
            check("the Sharmas' family is recorded, not the household standing in", (v["basis"], v["stands_in"]), ("recorded", None))
            groups = sorted(sorted(m["name"] for m in f["members"]) for f in v["families"])
            check("Rajesh and Meera are one family and Aarav, recorded as not dependent, is his own", groups, [["Aarav Sharma"], ["Meera Sharma", "Rajesh Sharma"]])
            vi = await sebi_family.household_view(s, firm, iyer.id)
            check("the Iyers have nothing recorded: the household stands in, and says so",
                  (vi["basis"], vi["stands_in"], len(vi["families"])), ("household", sebi_family.STANDS_IN, 1))
            check("each family reads its own members and basis", {f["basis"] for f in vi["families"]}, {"household"})
            ov = await sebi_family.overview(s, firm)
            check("the overview counts recorded families and the households still standing in",
                  (ov["recorded"] >= 2, ov["household_standing_in"] >= 2, ov["figure"][0]["key"]), (True, True, "in_family_of_client"))
            check("dependence is said never to be worked out from an age or an income", "never worked out" in ov["note"], True)

            await refused("a household that is not the firm's is not found", sebi_family.household_view(s, firm, uuid.uuid4()), "not found", exc=(ValueError,))
            await refused("nor does the estate read find it", E.household_view(s, firm, uuid.uuid4()), "not found", exc=(ValueError,))

            print("\n=== Refusals ===")
            adviser = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
            base = {"person_id": str(aarav.id), "of_person_id": str(rajesh.id), "document": "Declaration filed in the client's papers"}
            await refused("a child with no word on dependence is refused: never worked out", sebi_family.prepare(
                s, firm, person_id=aarav.id, of_person_id=rajesh.id, relation="child", dependent=None, document="Declaration filed"), "dependent")
            await refused("a person cannot be in their own family", sebi_family.prepare(
                s, firm, person_id=aarav.id, of_person_id=aarav.id, relation="spouse", dependent=None, document="Declaration filed"), "two people")
            await refused("a relation outside the list is refused", sebi_family.prepare(
                s, firm, person_id=aarav.id, of_person_id=rajesh.id, relation="cousin", dependent=None, document="Declaration filed"), "relation is one of")
            await refused("no document, no relation", sebi_family.prepare(
                s, firm, person_id=aarav.id, of_person_id=rajesh.id, relation="child", dependent=True, document=""), "where it is on file")
            await refused("a parent younger than the child is refused", sebi_family.prepare(
                s, firm, person_id=aarav.id, of_person_id=rajesh.id, relation="parent", dependent=True, document="Declaration filed"), "not older")
            await refused("the same relation twice is refused", sebi_family.prepare(
                s, firm, person_id=aarav.id, of_person_id=rajesh.id, relation="child", dependent=False, document="Declaration filed"), "already recorded")
            other = Firm(id=uuid.uuid4(), name="Other", slug="other", base_currency="INR", jurisdiction="IN", settings={"licences": ["investment_adviser"]})
            s.add(other)
            await s.flush()
            stranger = Person(firm_id=other.id, household_id=None, full_name="A Stranger")
            await refused("a person of another firm is refused", sebi_family.prepare(
                s, firm, person_id=stranger.id if stranger.id else uuid.uuid4(), of_person_id=rajesh.id, relation="spouse", dependent=None, document="Declaration filed"),
                "clients of this firm")
            nz = Firm(id=uuid.uuid4(), name="NZ", slug="nz2", base_currency="NZD", jurisdiction="NZ")
            s.add(nz)
            await s.flush()
            await refused("a firm outside India records no SEBI family", sebi_family.prepare(
                s, nz, person_id=aarav.id, of_person_id=rajesh.id, relation="child", dependent=True, document="Declaration filed"), "Indian firm")

            print("\n=== The pre-advice gate reads it ===")
            pa = await pre_advice.status(s, firm, sharma.id)
            fam_check = next(c for c in pa["checks"] if c["key"] == "family")
            check("the gate names the recorded family and no longer says the household stands in",
                  (pa["stands_in"], pa["family"]["basis"], fam_check["state"], "Rajesh Sharma" in fam_check["detail"]), (None, "recorded", "n/a", True))
            pi = await pre_advice.status(s, firm, dsouza.id)
            check("a household with nothing recorded still says the household stands in", (pi["stands_in"], any(c["key"] == "family" for c in pi["checks"])),
                  (pre_advice.STANDS_IN, False))

            print("\n=== The fee cap reads it ===")
            m_elaine = (await s.execute(select(Mandate).where(Mandate.person_id == elaine.id))).scalars().first()
            m_subir = (await s.execute(select(Mandate).where(Mandate.person_id == subir.id))).scalars().first()
            rows = [entry(dsouza, m_elaine), entry(banerjee, m_subir)]
            await fee_cap.apply(s, firm, rows, today)
            check("two unrelated households are two families, each under the cap",
                  ([bool(r["fee_cap"]["over"]) for r in rows], rows[0]["fee_cap"]["family"]["basis"], rows[0]["fee_cap"]["stands_in"] is not None), ([False, False], "household", True))
            await sebi_family.record(s, firm, by="test", person_id=subir.id, of_person_id=elaine.id, relation="parent", dependent=True,
                                     document="Declaration: Subir depends on Elaine, filed")
            rows = [entry(dsouza, m_elaine), entry(banerjee, m_subir)]
            await fee_cap.apply(s, firm, rows, today)
            check("a dependent parent in another household joins the family: the two bills together are over the cap",
                  ([bool(r["fee_cap"]["over"]) for r in rows], sorted(rows[0]["fee_cap"]["family"]["members"]), rows[0]["fee_cap"]["stands_in"]),
                  ([True, True], ["Elaine D'Souza", "Subir Banerjee"], None))
            check("each bill carries the block", [[c["code"] for c in r["checks"] if c["code"] == "sebi_fee_cap"] for r in rows], [["sebi_fee_cap"], ["sebi_fee_cap"]])
            vb = await pre_advice.status(s, firm, banerjee.id)
            check("the gate for the other household names who is outside it",
                  "outside this household: Elaine D'Souza" in next(c for c in vb["checks"] if c["key"] == "family")["detail"], True)

            print("\n=== Nominations a person recorded ===")
            hv = await E.household_view(s, firm, sharma.id)
            kinds = sorted((n["person"], n["asset_kind"]) for n in hv["nominations"])
            check("Rajesh's bank and EPF nominations are on file", kinds, [("Rajesh Sharma", "bank_account"), ("Rajesh Sharma", "epf")])
            check("an EPF nominee outside the family is flagged and nothing is changed",
                  [(i["name"], "family relationship" in i["issue"]) for i in hv["nomination_issues"] if "family" in i["issue"]], [("Priya Nair", True)])
            check("the nominee a will does not name is flagged on the bank nomination too", any(i["name"] == "Aniket Banerjee" for i in (await E.household_view(s, firm, banerjee.id))["nomination_issues"]), True)
            file = await client_file.for_household(s, dsouza.id, firm_id=firm.id)
            check("Elaine's signed opt-out is not an open nomination", any("Elaine D'Souza" in i["account"] and "no nomination" in i["issue"] for i in file["designation_issues"]), False)
            fs = await client_file.for_household(s, sharma.id, firm_id=firm.id)
            check("Meera's demat with no nomination and no opt-out is still named",
                  any(i["account"] == "Meera Sharma demat" and i["issue"] == "no nomination or signed opt-out on file" for i in fs["designation_issues"]), True)
            check("the client file carries the EPF issue beside the account issues", any(i["name"] == "Priya Nair" for i in fs["designation_issues"]), True)
            check("the household's records are in the client file's payload", fs["india"]["household"], "The Sharma Family")
            await refused("six nominees on a bank account are refused", E.prepare_nomination(
                s, firm, person_id=rajesh.id, asset_kind="bank_account", label="Rajesh SBI", status="nominated",
                nominees="A A:son:20; B B:son:20; C C:son:20; D D:son:20; E E:son:20", document="The bank's form, filed"), "at most 4")
            await refused("four nominees on a demat are refused", E.prepare_nomination(
                s, firm, person_id=rajesh.id, asset_kind="demat", label="Rajesh CDSL", status="nominated",
                nominees="A A:son:25; B B:son:25; C C:son:25; D D:son:25", document="The depository's form, filed"), "at most 3")
            await refused("shares that do not add to 100 are refused", E.prepare_nomination(
                s, firm, person_id=rajesh.id, asset_kind="bank_account", label="Rajesh SBI", status="nominated",
                nominees="A A:son:50; B B:son:30", document="The bank's form, filed"), "100%")
            await refused("an opt-out for a bank account is refused: it is a demat and folio choice", E.prepare_nomination(
                s, firm, person_id=rajesh.id, asset_kind="bank_account", label="Rajesh SBI", status="opted_out", declaration_on=today,
                document="The bank's form, filed"), "demat account or a mutual fund")
            await refused("an opt-out with no signed day is refused", E.prepare_nomination(
                s, firm, person_id=rajesh.id, asset_kind="demat", label="Rajesh CDSL", status="opted_out", document="The depository's form, filed"), "signed the declaration")
            await refused("a nomination dated in the future is refused", E.prepare_nomination(
                s, firm, person_id=rajesh.id, asset_kind="demat", label="Rajesh CDSL", status="opted_out", declaration_on=today + timedelta(days=2),
                document="The depository's form, filed"), "future")
            await refused("the same nomination twice is refused", E.prepare_nomination(
                s, firm, person_id=rajesh.id, asset_kind="bank_account", label="Rajesh Sharma HDFC Bank savings", status="nominated",
                nominees="Meera Sharma:spouse:60; Aarav Sharma:son:40", document="The bank's form, filed"), "already")
            res = nominations.check([{"asset_kind": "bank_account", "label": "X", "status": "nominated", "person_id": "p",
                                      "nominees": [{"name": f"N{i}", "relationship": "son", "share": 20} for i in range(5)]}], today=today)
            check("nominations.check reads the cap from law_figures: five on a bank account is one too many", [i["issue"] for i in res], ["5 nominees, and a bank account takes at most 4"])
            res = nominations.check([{"asset_kind": "demat", "label": "Y", "status": "opted_out", "person_id": "p", "nominees": None, "declaration_on": None}], today=today)
            check("an opt-out with no declaration is flagged, nothing is changed", [i["issue"] for i in res], ["opted out, with no signed declaration dated on file"])
            ghost = Person(firm_id=firm.id, household_id=banerjee.id, full_name="Rina Banerjee", deceased_on=today - timedelta(days=30))
            s.add(ghost)
            await s.flush()
            await E.record_nomination(s, firm, by="test", person_id=subir.id, asset_kind="mutual_fund", label="Subir Banerjee fund folio", status="nominated",
                                      nominees="Rina Banerjee:spouse:100", document="The registrar's nomination form, filed")
            check("a nominee who has died is flagged", any(i["name"] == "Rina Banerjee" and i["issue"] == "nominee has died" for i in
                                                            (await E.household_view(s, firm, banerjee.id))["nomination_issues"]), True)

            print("\n=== A will's state, and the law it follows ===")
            hv = await E.household_view(s, firm, sharma.id)
            raj = next(p for p in hv["people"] if p["name"] == "Rajesh Sharma")
            check("Rajesh's will is registered, with its number, under Hindu law", (raj["status"]["registered"], raj["status"]["personal_law"], "4417" in raj["status"]["registration_ref"]), (True, "hindu", True))
            check("the intestacy order is laid out for Hindu law with its two orders and their sources",
                  (raj["succession"]["state"], [o["title"].split(" (")[0] for o in raj["succession"]["orders"]], all(o["figure"]["source"] for o in raj["succession"]["orders"])),
                  ("laid_out", ["A man dying without a will", "A woman dying without a will"], True))
            check("the family on file is placed from the recorded relations, never inferred",
                  (raj["family_on_file"]["spouse"], raj["family_on_file"]["children"]), (["Meera Sharma"], ["Aarav Sharma"]))
            check("a will on file is said to govern what it disposes of", "governs what it disposes of" in raj["succession"]["message"], True)
            check("for counsel, not advice, no share is worked out", "not advice and not a share" in E.FOR_COUNSEL and "share" not in str(raj["succession"]["orders"][0]["on_file"]).lower(), True)
            mee = next(p for p in hv["people"] if p["name"] == "Meera Sharma")
            check("with no personal law recorded the order is not assessed, and says it is never read from a name",
                  (mee["succession"]["state"], "never read from a name" in mee["succession"]["message"]), ("not_assessed", True))
            check("an open will with no registration recorded is told", any("registered" in f for f in
                  next(p for p in (await E.household_view(s, firm, banerjee.id))["people"] if p["name"] == "Subir Banerjee")["flags"]), True)
            await refused("a will's state needs a will on file", E.prepare_will_status(
                s, firm, person_id=meera.id, registered=True, registration_ref="Sub-Registrar 12", document="A cover note, filed"), "no will on file")
            await refused("a registered will has its number", E.prepare_will_status(
                s, firm, person_id=rajesh.id, registered=True, document="A cover note, filed"), "registration number")
            await refused("probate granted has the court's number", E.prepare_will_status(
                s, firm, person_id=rajesh.id, probate="granted", document="A cover note, filed"), "court")
            await refused("a personal law outside the list is refused", E.prepare_will_status(
                s, firm, person_id=meera.id, personal_law="martian", document="A cover note, filed"), "personal law is one of")
            await E.record_will_status(s, firm, by="test", person_id=meera.id, personal_law="muslim", document="A declaration of personal law, filed")
            mee = next(p for p in (await E.household_view(s, firm, sharma.id))["people"] if p["name"] == "Meera Sharma")
            check("Muslim succession is named as not laid out, and nothing is invented", (mee["succession"]["state"], mee["succession"]["orders"]), ("not_laid_out", []))
            wills_before = (await s.execute(select(func.count()).select_from(WillStatus).where(WillStatus.person_id == rajesh.id))).scalar_one()
            await E.record_will_status(s, firm, by="test", person_id=rajesh.id, executor="Aarav Sharma", document="The executor's letter, filed")
            raj = next(p for p in (await E.household_view(s, firm, sharma.id))["people"] if p["name"] == "Rajesh Sharma")
            check("what a new record does not restate carries over", (raj["status"]["executor"], raj["status"]["registered"], raj["status"]["personal_law"]), ("Aarav Sharma", True, "hindu"))
            check("every record is kept, added and never edited", (await s.execute(select(func.count()).select_from(WillStatus).where(WillStatus.person_id == rajesh.id))).scalar_one(), wills_before + 1)

            print("\n=== An HUF ===")
            hv = await E.household_view(s, firm, sharma.id)
            check("the HUF's karta and coparceners are on file", [(h["karta"], [c["name"] for c in h["coparceners"]], h["flags"]) for h in hv["hufs"]],
                  [("Rajesh Sharma", ["Rajesh Sharma", "Aarav Sharma"], [])])
            rajesh.deceased_on = today - timedelta(days=3)
            await s.flush()
            flags = [f for h in (await E.household_view(s, firm, sharma.id))["hufs"] for f in h["flags"]]
            check("a karta who has died is flagged, and so is the coparcener", sorted(flags), ["coparcener Rajesh Sharma has died", "the karta has died: a new karta is needed"])
            rajesh.deceased_on = None
            await s.flush()
            huf = (await s.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id, LegalEntity.name == "Rajesh Sharma HUF"))).scalar_one()
            trust = (await s.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id, LegalEntity.name == "Kapoor Family Trust"))).scalar_one()
            await refused("a trust is not an HUF", E.prepare_huf(s, firm, entity_id=trust.id, karta_name="Nandini Kapoor", coparceners="Nandini Kapoor:self",
                                                                  document="A deed, filed"), "not an HUF")
            await refused("an HUF with no coparceners is refused", E.prepare_huf(s, firm, entity_id=huf.id, karta_name="Rajesh Sharma", document="A deed, filed"), "coparceners")

            print("\n=== Gratuity: named beside the pot, never added ===")
            plan = await financial_plan.for_household(s, sharma.id, firm_id=firm.id)
            check("the plan names the recorded gratuity beside the pot", (plan["gratuity"]["total"], plan["gratuity"]["statements"][0]["employer"][:7]), (3_800_000.0, "Sharma "))
            check("and no longer lists gratuity as not modelled for this household", "gratuity" in plan["not_modelled"], False)
            plan_iyer = await financial_plan.for_household(s, homes["The Banerjee Household"].id, firm_id=firm.id)
            check("a household with none recorded still says gratuity is not modelled, and names no gratuity", ("gratuity" in plan_iyer["not_modelled"], plan_iyer["gratuity"]), (True, None))
            await refused("a gratuity of nothing is refused", E.prepare_gratuity(s, firm, person_id=rajesh.id, employer="X Ltd", amount=0, document="A statement, filed"), "more than zero")
            await refused("a statement older than the one on file is refused", E.prepare_gratuity(
                s, firm, person_id=rajesh.id, employer="Sharma Industrial Components Pvt Ltd (demonstration)", amount=3_000_000, as_of=today - timedelta(days=400),
                document="A statement, filed"), "later one stands over")
            await E.record_gratuity(s, firm, by="test", person_id=rajesh.id, employer="Sharma Industrial Components Pvt Ltd (demonstration)", amount=4_100_000,
                                    as_of=today, document="A newer statement, filed")
            g = await E.gratuity_of(s, firm.id, sharma.id)
            check("a later statement for the same employer stands, the earlier stays on file", (len(g), g[0]["amount"],
                  (await s.execute(select(func.count()).select_from(GratuityRecord).where(GratuityRecord.person_id == rajesh.id))).scalar_one()), (1, 4_100_000.0, 2))

            print("\n=== The tools, their cards and who may record ===")
            for key in ("record_sebi_family", "record_asset_nomination", "record_will_status", "record_huf", "record_gratuity"):
                check(f"{key} stops for a Confirm card", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            for key in ("read_sebi_family", "read_india_estate"):
                check(f"{key} changes nothing", TOOLS[key].change_state, ToolChangeState.NO)
            card = await adviser.preview_change("record_sebi_family", {"person_id": str(aarav.id), "of_person_id": str(meera.id), "relation": "child", "dependent": True,
                                                                        "document": "Declaration filed for the demonstration"})
            check("the family card names the person, the family, the relation and dependence", [r["label"] for r in card["rows"]][:4], ["Person", "In the family of", "Relation", "Dependent"])
            card = await adviser.preview_change("record_asset_nomination", {"person_id": str(meera.id), "asset_kind": "demat", "label": "Meera Sharma demat", "status": "opted_out",
                                                                             "declaration_on": today.isoformat(), "document": "Signed declaration, filed"})
            check("the nomination card says nothing changes on the account", any("Nothing changes on the account" in str(r["value"]) for r in card["rows"]), True)
            card = await adviser.preview_change("record_gratuity", {"person_id": str(lakshmi := people["Lakshmi Iyer"].id), "employer": "Coastal Software Services Ltd (demonstration)",
                                                                     "amount": 1_300_000, "as_of": today.isoformat(), "document": "A statement, filed"})
            check("the gratuity card is in rupees and names the figure it replaces", (any("13,00,000" in str(r["value"]) for r in card["rows"]),
                                                                                         any(r["label"] == "Replaces" for r in card["rows"])), (True, True))
            before = (await s.execute(select(func.count()).select_from(AssetNomination))).scalar_one()
            await adviser.preview_change("record_asset_nomination", {"person_id": str(meera.id), "asset_kind": "demat", "label": "Meera Sharma demat", "status": "opted_out",
                                                                      "declaration_on": today.isoformat(), "document": "Signed declaration, filed"})
            check("a card writes nothing by being shown", (await s.execute(select(func.count()).select_from(AssetNomination))).scalar_one(), before)
            done = await adviser.execute("record_asset_nomination", {"person_id": str(meera.id), "asset_kind": "demat", "label": "Meera Sharma demat", "status": "opted_out",
                                                                      "declaration_on": today.isoformat(), "document": "Signed declaration, filed"})
            check("confirmed, the opt-out is recorded and Meera's demat is no longer an open nomination",
                  (done["message"].startswith("Recorded"), any(i["account"] == "Meera Sharma demat" and "no nomination" in i["issue"] for i in
                                                                 (await client_file.for_household(s, sharma.id, firm_id=firm.id))["designation_issues"])), (True, False))
            portfolio = ToolExecutors(s, users[UserRole.PORTFOLIO_TEAM].id, UserRole.PORTFOLIO_TEAM, firm.id)
            await refused("a portfolio manager reads and does not record a family", portfolio.execute("record_sebi_family", {
                "person_id": str(aarav.id), "of_person_id": str(meera.id), "relation": "child", "dependent": True, "document": "Declaration filed"}), "adviser, a paraplanner or operations")
            read = await portfolio.execute("read_sebi_family", {})
            check("the family is read by a portfolio manager", (read["applies"], read["recorded"] >= 2), (True, True))
            est = await adviser.execute("read_india_estate", {"household_id": str(sharma.id)})
            check("the estate read says not on file does not mean none exists", "does not mean none exists" in est["message"], True)
            check("and shows each law figure with counsel's status beside it, not reviewed by default",
                  {f["counsel"]["state"] for f in est["figures"]}, {"not_reviewed"})

            print("\n=== The seed, a second time ===")
            check("row 13 adds nothing a second time", await india_row13.add(s, firm), 0)
            licences_view = (await s.execute(select(func.count()).select_from(SebiRelation))).scalar_one()
            check("the relations are added and never edited", licences_view >= 5, True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
