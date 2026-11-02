"""Prove the consent registry keeps each consent with its evidence and lets the latest stand, that electronic delivery
needs e-delivery consent and a fee schedule needs fee-debit authority before activation, that a US account's capacity
decides its disclosures, and that a brochure falls due again a year after it was delivered.

Runs against a throwaway SQLite database.

    python -m tests.verify_consents
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import onboarding as api
from app.aurea_core import consents, disclosures, firm_time, gates
from app.core.db import Base, utcnow
from app.core.tools import TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.consent_registry import ConsentRecord
from app.models.enums import UserRole
from app.models.graph import Household
from app.models.identity import User
from app.models.onboarding import DisclosureDelivery, OnboardingCase
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, consents.ConsentError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def gate(result: dict, key: str) -> str | None:
    return next((g["state"] for g in result["gates"] if g["key"] == key), None)


def pure() -> None:
    print("\n=== The account's capacity decides its disclosures ===")
    check("a US advisory account gets the brochure", disclosures.required_for("US", "individual", "discretionary")[:1], ["form_adv_2a"])
    check("a US brokerage account gets Reg BI, not the brochure",
          disclosures.required_for("US", "individual", "brokerage"), ["form_crs", "reg_bi_disclosure", "privacy_notice"])
    check("New Zealand is unchanged by it", disclosures.required_for("NZ", "individual", "brokerage"), disclosures.required_for("NZ", "individual"))
    items = [{"label": "Form CRS", "delivered": True, "method": "email"}, {"label": "Privacy notice", "delivered": True, "method": "in_person"}]
    status = consents.gate_status([], disclosure_items=items, fee_assigned=True)
    check("email with no consent is named", (status["electronic_without_consent"], status["fee_debit_missing"]), (["Form CRS"], True))


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
            for key, role in (("adv", UserRole.ADVISER), ("comp", UserRole.COMPLIANCE), ("client", UserRole.CLIENT)):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}
            case = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Ada Reyes", registration_type="individual",
                                  engagement_type="discretionary", intake={})
            s.add(case)
            await s.flush()
            s.add(DisclosureDelivery(firm_id=firm.id, case_id=case.id, doc_type="form_crs", delivered_at=utcnow(), method="email",
                                     evidence_ref="msg-1", delivered_by="adv@t"))
            await s.flush()

            print("\n=== The e-delivery gate ===")
            check("email with no consent fails", gate(await api._gates_for(s, firm, case), "e_delivery_consent"), gates.FAIL)
            ask = {"case_id": str(case.id), "kind": "e_delivery", "given_by": "Ada Reyes", "method": "e_signature", "evidence": "DocuSign envelope 7741"}
            await refused("a consent needs its evidence", ex["adv"].preview_change("record_consent", {**ask, "evidence": ""}), "evidence")
            await refused("a consent is one of the kinds", ex["adv"].preview_change("record_consent", {**ask, "kind": "marketing"}), "one of")
            await refused("never dated in the future", ex["adv"].preview_change("record_consent",
                          {**ask, "given_on": (firm_time.today(firm) + timedelta(days=3)).isoformat()}), "future")
            card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("record_consent", ask))["rows"]}
            check("the card names whose, what and the effect", (card["Client"], card["Consent"], card["Effect"]),
                  ("Ada Reyes", "Electronic delivery", "Disclosures may be delivered by email or portal"))
            check("showing it writes nothing", (await s.execute(select(ConsentRecord))).scalars().all(), [])
            check("a client holds no such tool", UserRole.CLIENT in TOOLS["record_consent"].roles_required, False)
            out = await ex["adv"].record_consent(ask)
            check("recorded, and it stands", out["standing"]["e_delivery"]["status"], "granted")
            check("now the gate passes", gate(await api._gates_for(s, firm, case), "e_delivery_consent"), gates.PASS)
            later = {**ask, "status": "withdrawn", "method": "email", "evidence": "Email from Ada, 2 Nov"}
            card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("record_consent", later))["rows"]}
            check("a withdrawal says it no longer stands", card["Effect"], "No longer stands")
            out = await ex["adv"].record_consent(later)
            check("the withdrawal stands and both stay on record", (out["standing"]["e_delivery"]["status"], len(out["records"])), ("withdrawn", 2))
            check("and the gate fails again", gate(await api._gates_for(s, firm, case), "e_delivery_consent"), gates.FAIL)

            print("\n=== The fee-debit gate ===")
            result = gates.evaluate(case, party_status={"complete": True, "missing": [], "unscreened": [], "sanctions": []},
                                    disclosure_status={"items": [], "outstanding": [], "blocks_activation": False},
                                    fee_status={"assigned": True}, consent_status=consents.gate_status([], disclosure_items=[], fee_assigned=True))
            check("a fee schedule with no authority fails", gate(result, "fee_debit_authorised"), gates.FAIL)
            await ex["adv"].record_consent({**ask, "kind": "fee_debit", "evidence": "Advisory agreement section 4, signed"})
            rows = await consents.for_case_rows(s, case)
            result = gates.evaluate(case, party_status={"complete": True, "missing": [], "unscreened": [], "sanctions": []},
                                    disclosure_status={"items": [], "outstanding": [], "blocks_activation": False},
                                    fee_status={"assigned": True}, consent_status=consents.gate_status(rows, disclosure_items=[], fee_assigned=True))
            check("with it on file it passes", gate(result, "fee_debit_authorised"), gates.PASS)

            print("\n=== After opening ===")
            household = Household(id=uuid.uuid4(), firm_id=firm.id, name="Reyes household")
            s.add(household)
            old = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Reyes household", registration_type="individual",
                                 intake={}, activated_at=utcnow() - timedelta(days=400), materialized={"household_id": str(household.id)},
                                 discretion_granted=True, agreement_signed_at=utcnow() - timedelta(days=405), agreement_envelope_id="ENV-9")
            s.add(old)
            await s.flush()
            s.add(DisclosureDelivery(firm_id=firm.id, case_id=old.id, doc_type="form_adv_2a", delivered_at=utcnow() - timedelta(days=400),
                                     method="in_person", delivered_by="adv@t"))
            s.add(DisclosureDelivery(firm_id=firm.id, case_id=old.id, doc_type="privacy_notice", delivered_at=utcnow() - timedelta(days=30),
                                     method="in_person", delivered_by="adv@t"))
            await s.flush()
            reg = await ex["comp"].read_consents({"household_id": str(household.id)})
            check("the brochure is due again, the recent notice is not", [r["doc_type"] for r in reg["redeliveries_due"]], ["form_adv_2a"])
            check("discretion shows as the signed agreement", [(a["kind"], a["source"]) for a in reg["from_agreements"]], [("discretionary_authority", "agreement")])
            check("nothing recorded reads as not on file, not as refused", reg["standing"], None)
            await ex["adv"].record_consent({"household_id": str(household.id), "kind": "householding", "given_by": "Marco Reyes",
                                            "method": "signed_form", "evidence": "Householding form, scanned"})
            reg = await ex["comp"].read_consents({"household_id": str(household.id)})
            check("a household's consent is its own", (list(reg["standing"]), len(reg["records"])), (["householding"], 1))
            await refused("a case or a household, not both", ex["adv"].preview_change("record_consent",
                          {**ask, "household_id": str(household.id)}), "one of them")
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
