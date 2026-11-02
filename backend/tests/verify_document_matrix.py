"""Prove there is one document matrix, that any one document of a set satisfies a line, that the state and the custodian
add to it, and that the check before submission finds what a custodian rejects: an unsigned signer, a stale or future
date, a tenancy the state does not allow, shares that do not make 100, and registration detail left blank. Recording
detail is behind a Confirm card, and a package in good order clears the activation gate.

Runs against a throwaway SQLite database.

    python -m tests.verify_document_matrix
"""
from __future__ import annotations

import asyncio
import sys
import types
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import nigo_prevention
from app.api import onboarding as api
from app.aurea_core import document_matrix as dm, firm_time, gates, tracks
from app.core.db import Base
from app.core.tools import TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingDocument, OnboardingParty
from app.models.tenant import Firm

PASS, FAIL = [], []
TODAY = date(2026, 11, 2)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, dm.MatrixError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def case(reg, detail=None, custodian=None):
    return types.SimpleNamespace(registration_type=reg, custodian_name=custodian, intake={"registration": detail or {}})


def party(role, name, pct=None, dob=None):
    return types.SimpleNamespace(role=role, legal_name=name, ownership_pct=pct, dob=dob)


def doc(kind):
    return types.SimpleNamespace(doc_type=kind)


def fields(result):
    return sorted({i["field"] for i in result["issues"]})


def pure() -> None:
    print("\n=== One matrix ===")
    check("the tracks and the agent have no matrix of their own", (hasattr(tracks, "_DOC_MATRIX"), hasattr(nigo_prevention, "_DOC_MATRIX")), (False, False))
    check("a passport alone satisfies the ID line", dm.missing_documents(case("individual"), [doc("passport")]), [])
    check("a trust needs its certification", [m["key"] for m in dm.missing_documents(case("trust"), [doc("passport")])], ["trust"])
    firm = types.SimpleNamespace(settings={"custodian_documents": {"Schwab": ["custodian_application"]}})
    check("the custodian adds its own form", [m["label"] for m in dm.missing_documents(case("individual", custodian="Schwab"), [doc("passport")], firm=firm)],
          ["Custodian account application"])
    check("another custodian adds nothing", dm.missing_documents(case("individual", custodian="Fidelity"), [doc("passport")], firm=firm), [])
    check("a document recorded as received counts", dm.missing_documents(case("trust", {"documents": {"trust_certification": {}}}), [doc("passport")]), [])

    print("\n=== The check before submission ===")
    signed = {"signed_by": ["Ada Reyes"], "signed_on": "2026-10-20"}
    owner = [party("owner", "Ada Reyes")]
    ok = dm.check(case("individual", signed), owner, [doc("passport")], firm=None, today=TODAY)
    check("an individual with ID and a signature is in good order", (ok["in_good_order"], ok["issues"]), (True, []))
    r = dm.check(case("individual"), owner, [doc("passport")], firm=None, today=TODAY)
    check("no signatures recorded is caught", fields(r), ["signed_by"])
    joint = [party("owner", "Ada Reyes"), party("joint_owner", "Ben Reyes")]
    r = dm.check(case("joint_jtwros", signed), joint, [doc("passport")], firm=None, today=TODAY)
    check("a joint owner who did not sign is named", [i["description"] for i in r["issues"]], ["Ben Reyes has not signed."])
    r = dm.check(case("individual", {**signed, "signed_on": "2026-06-01"}), owner, [doc("passport")], firm=None, today=TODAY)
    check("an application signed 154 days ago is stale", [i["description"] for i in r["issues"]], ["Signed 154 days ago; custodians take 90."])
    r = dm.check(case("individual", {**signed, "signed_on": "2026-12-01"}), owner, [doc("passport")], firm=None, today=TODAY)
    check("one dated in the future is refused", fields(r), ["signed_on"])
    both = {"signed_by": ["Ada Reyes", "Ben Reyes"], "signed_on": "2026-10-20"}
    r = dm.check(case("joint_tbe", {**both, "state": "CA", "married": True}), joint, [doc("passport")], firm=None, today=TODAY)
    check("tenancy by the entirety in California is wrong tenancy", [(i["field"], i["root_cause"]) for i in r["issues"]], [("state", "wrong_tenancy")])
    r = dm.check(case("joint_tbe", {**both, "state": "FL", "married": True}), joint, [doc("passport")], firm=None, today=TODAY)
    check("in Florida it is in order", r["in_good_order"], True)
    r = dm.check(case("community_property", {**both, "state": "FL", "married": True}), joint, [doc("passport")], firm=None, today=TODAY)
    check("community property in Florida is wrong", fields(r), ["state"])
    r = dm.check(case("joint_tbe", both), joint, [doc("passport")], firm=None, today=TODAY)
    check("with no state or marriage recorded, both are asked for", fields(r), ["married", "state"])
    tic = [party("owner", "Ada Reyes", 60), party("joint_owner", "Ben Reyes", 30)]
    r = dm.check(case("joint_tic", both), tic, [doc("passport")], firm=None, today=TODAY)
    check("tenants in common at 90% are caught", [i["description"] for i in r["issues"]], ["Owners' shares make 90% of 100%."])
    ira = [party("owner", "Ada Reyes"), party("beneficiary", "Cal Reyes", 50), party("beneficiary", "Dee Reyes", 40)]
    have = [doc("passport"), doc("beneficiary_form")]
    r = dm.check(case("traditional_ira", {**signed, "funding": "rollover"}), ira, have, firm=None, today=TODAY)
    check("IRA beneficiaries at 90% are caught", [(i["field"], i["root_cause"]) for i in r["issues"]], [("beneficiaries", "missing_beneficiary")])
    ira[2].ownership_pct = 50
    r = dm.check(case("traditional_ira", {**signed, "funding": "conversion"}), ira, have, firm=None, today=TODAY)
    check("a conversion into a traditional IRA is refused", fields(r), ["funding"])
    r = dm.check(case("roth_ira", {**signed, "funding": "contribution", "contribution_tax_year": 2025}), ira, have, firm=None, today=TODAY)
    check("a 2025 contribution in November 2026 is too late", fields(r), ["contribution_tax_year"])
    r = dm.check(case("roth_ira", {**signed, "signed_on": "2026-04-01", "funding": "contribution", "contribution_tax_year": 2025}), ira, have, firm=None, today=date(2026, 4, 10))
    check("in April it is not", r["issues"], [])
    trust = [party("trustee", "Ada Reyes")]
    r = dm.check(case("trust", {**signed, "trust_date": "2019-05-01", "tax_id_type": "ein", "tax_id": "123456789"}), trust,
                 [doc("passport"), doc("trust_certification")], firm=None, today=TODAY)
    check("an EIN written wrong is caught", fields(r), ["tax_id"])
    minor = [party("custodian", "Ada Reyes"), party("minor", "Eve Reyes", dob="2004-03-01")]
    r = dm.check(case("custodial_utma", {**signed, "state": "NY", "termination_age": 21}), minor, [doc("passport")], firm=None, today=TODAY)
    check("a minor past the state's age is caught", fields(r), ["minor"])
    r = dm.check(case("custodial_utma", {**signed, "state": "NY", "termination_age": 30}), minor, [doc("passport")], firm=None, today=TODAY)
    check("an age no state sets is refused", "termination_age" in fields(r), True)
    r = dm.check(case("inherited_ira", {**signed, "decedent_date_of_death": "2026-03-01", "year_of_death_rmd": "not_taken"}),
                 owner, [doc("passport"), doc("death_certificate")], firm=None, today=TODAY)
    check("an untaken year-of-death RMD is shown and does not block", ([i["severity"] for i in r["issues"]], r["in_good_order"]), (["medium"], True))
    r = dm.check(case("plan_529", signed), owner, [doc("passport")], firm=None, today=TODAY)
    check("a 529 asks for its successor", fields(r), ["successor"])


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US", settings={})
            s.add(firm)
            await s.flush()
            users = {}
            for key, role in (("adv", UserRole.ADVISER), ("ops", UserRole.OPERATIONS), ("comp", UserRole.COMPLIANCE)):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}
            c = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Ada and Ben Reyes", registration_type="joint_tbe", intake={}, custodian_name="Schwab")
            s.add(c)
            await s.flush()
            s.add_all([OnboardingParty(firm_id=firm.id, case_id=c.id, role="owner", legal_name="Ada Reyes"),
                       OnboardingParty(firm_id=firm.id, case_id=c.id, role="joint_owner", legal_name="Ben Reyes"),
                       OnboardingDocument(firm_id=firm.id, case_id=c.id, doc_type="passport", filename="p.pdf", raw_text="")])
            await s.flush()

            print("\n=== The gate ===")
            g = next(x for x in (await api._gates_for(s, firm, c))["gates"] if x["key"] == "registration_in_good_order")
            check("the package fails the gate with what is missing", (g["state"], "No signatures recorded" in g["detail"]), (gates.FAIL, True))

            print("\n=== The custodian's forms ===")
            await refused("a custodian's forms need their source", ex["ops"].preview_change("set_custodian_documents",
                          {"custodian": "Schwab", "documents": "custodian_application", "source": ""}), "say where")
            check("an adviser does not set them", UserRole.ADVISER in TOOLS["set_custodian_documents"].roles_required, False)
            card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("set_custodian_documents",
                    {"custodian": "Schwab", "documents": "custodian_application", "source": "Schwab new-account guide 2026"}))["rows"]}
            check("the card shows what changes", (card["Forms required"], card["Was"]), ("Custodian account application", "None"))
            await ex["ops"].set_custodian_documents({"custodian": "Schwab", "documents": "custodian_application", "source": "Schwab new-account guide 2026"})

            print("\n=== Recording the detail ===")
            ask = {"case_id": str(c.id), "state": "fl", "married": "yes", "signed_by": "Ada Reyes, Ben Reyes",
                   "signed_on": (firm_time.today(firm) - timedelta(days=3)).isoformat(), "evidence": "Schwab joint application, page 4"}
            await refused("detail needs its source", ex["adv"].preview_change("record_registration_detail", {**ask, "evidence": ""}), "say where")
            await refused("a state is its code", ex["adv"].preview_change("record_registration_detail", {**ask, "state": "Florida"}), "two-letter")
            card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("record_registration_detail", ask))["rows"]}
            check("the card shows the detail and what still blocks", (card["State"], card["Owners married to each other"], card["After this"]),
                  ("FL", "Yes", "1 issue(s) still block submission"))
            check("showing it writes nothing", (c.intake or {}).get("registration"), None)
            out = await ex["adv"].record_registration_detail({**ask, "documents": "custodian_application"})
            check("recorded with the application, the package is in good order", out["check"]["in_good_order"], True)
            check("and it keeps where each fact came from", out["check"]["detail"]["sources"][0]["evidence"], "Schwab joint application, page 4")
            g = next(x for x in (await api._gates_for(s, firm, c))["gates"] if x["key"] == "registration_in_good_order")
            check("the gate passes", g["state"], gates.PASS)
            await refused("an open account is not edited here", _open_and_try(s, ex["adv"], c), "custodian")
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


async def _open_and_try(s, ex, c):
    from app.core.db import utcnow
    c.activated_at = utcnow()
    await s.flush()
    return await ex.preview_change("record_registration_detail", {"case_id": str(c.id), "state": "PA", "evidence": "Amended application"})


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
