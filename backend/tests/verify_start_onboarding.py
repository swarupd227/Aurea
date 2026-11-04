"""Prove a new client starts as an onboarding case, from the conversation or the page alike: a draft recording only
what the person gave, an account type only in the market whose law has it, one open case per name, an LLC that will
activate, and the adviser who opened it as the client's adviser.

Runs against a throwaway SQLite database.

    python -m tests.verify_start_onboarding
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import onboarding as onboarding_api
from app.aurea_core import onboarding_start
from app.core.db import Base
from app.core.tools import tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.identity import AuditEvent, User
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, onboarding_start.StartError, HTTPException) as e:
        text = str(getattr(e, "detail", e))
        ok = all(c.lower() in text.lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {text[:110]}")
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
            nz = Firm(id=uuid.uuid4(), name="NZ", slug="nz", base_currency="NZD", jurisdiction="NZ", settings={})
            us = Firm(id=uuid.uuid4(), name="US", slug="us", base_currency="USD", jurisdiction="US", settings={})
            s.add_all([nz, us])
            await s.flush()

            def user(firm, role, name):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{name}@{firm.slug}", full_name=name, role=role, hashed_password="x")
                s.add(u)
                return u
            sophie, ops = user(nz, UserRole.ADVISER, "Sophie"), user(nz, UserRole.OPERATIONS, "Oli")
            ann, ben = user(us, UserRole.ADVISER, "Ann"), user(us, UserRole.ADVISER, "Ben")
            usops = user(us, UserRole.OPERATIONS, "Uma")
            await s.flush()
            ex = lambda u, f: ToolExecutors(s, u.id, u.role, f.id)  # noqa: E731

            print("\n=== What a case may be opened with ===")
            ask = {"prospect_name": "  Priya   Raman ", "registration_type": "individual", "risk_preference": "growth",
                   "source_of_wealth": "Sale of a dental practice"}
            await refused("a name is needed", ex(sophie, nz).preview_change("start_onboarding", {**ask, "prospect_name": " "}), "name the prospect")
            await refused("an account type that does not exist", ex(sophie, nz).preview_change("start_onboarding", {**ask, "registration_type": "offshore"}), "for this firm")
            await refused("an IRA is US law, refused for a New Zealand firm", ex(sophie, nz).preview_change("start_onboarding",
                          {**ask, "registration_type": "roth_ira"}), "only in US law")
            await refused("an HUF is Indian law, refused for a US firm", ex(ann, us).preview_change("start_onboarding",
                          {**ask, "registration_type": "huf"}), "only in IN law")
            await refused("a risk preference outside the four", ex(sophie, nz).preview_change("start_onboarding", {**ask, "risk_preference": "yolo"}), "one of")
            card = {r["label"]: r["value"] for r in (await ex(sophie, nz).preview_change("start_onboarding", ask))["rows"]}
            check("the card shows what was given, and that nothing happens yet",
                  (card["Prospect"], card["Account type"], card["Stated risk"], card["Mandate"], card["Adviser"], card["Opens as"].startswith("A draft")),
                  ("Priya Raman", "Individual", "Growth", "Not stated", "Sophie", True))

            print("\n=== Opening it ===")
            out = await ex(sophie, nz).execute("start_onboarding", ask)
            case = (await s.execute(select(OnboardingCase).where(OnboardingCase.firm_id == nz.id))).scalar_one()
            check("one draft case, recording only what was said, with its adviser",
                  (case.prospect_name, case.status, sorted(case.intake), case.intake["adviser_id"]),
                  ("Priya Raman", "intake", ["adviser_id", "risk_profile", "source_of_wealth"], str(sophie.id)))
            check("the result is that case on the board, for its card and view", [c["id"] for c in out["cases"]], [str(case.id)])
            await refused("the same name with a case open is refused", ex(ops, nz).preview_change("start_onboarding", {**ask, "prospect_name": "priya raman"}),
                          "already has an onboarding case open")
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "onboarding.case_started"))).scalars().all()
            check("on the audit log", [a.subject for a in audit], ["Priya Raman"])

            print("\n=== The page opens it the same way ===")
            body = onboarding_api.CaseCreate(prospect_name="Harbour Holdings", registration_type="entity_llc",
                                             intake={"source_of_wealth": "Trading profits", "objectives": ["income"]})
            made = await onboarding_api.create_case(body, user=usops, firm=us, db=s)
            llc = await s.get(OnboardingCase, uuid.UUID(made["id"]))
            check("an LLC is an entity of a type activation knows, not 'llc'", (llc.is_entity, llc.entity_type), (True, "company"))
            check("what the page gave beyond the form is kept", llc.intake.get("objectives"), ["income"])
            await refused("the page refuses what the tool refuses", onboarding_api.create_case(
                onboarding_api.CaseCreate(prospect_name="Harbour Holdings", registration_type="entity_llc"), user=usops, firm=us, db=s), "already")

            print("\n=== Who the client's adviser becomes ===")
            check("the adviser who opened it", (await onboarding_start.adviser_for(s, nz, case)).full_name, "Sophie")
            check("with several advisers and none on the case, nobody until a person assigns one",
                  await onboarding_start.adviser_for(s, us, llc), None)
            lone = OnboardingCase(firm_id=nz.id, prospect_name="X", intake={})
            check("a firm's only adviser when none is on the case", (await onboarding_start.adviser_for(s, nz, lone)).full_name, "Sophie")
            check("who may open a case", sorted(str(r) for r in UserRole if "start_onboarding" in tools_for_role(r)),
                  ["adviser", "branch_leader", "operations", "paraplanner"])

            print("\n=== A rule comes from a tool, not from what the model knows (B-152) ===")
            from app.core.tools import TOOLS
            from app.llm.gateway import CLIENT_PROMPT_TEMPLATE, SYSTEM_PROMPT_TEMPLATE
            check("staff: general knowledge of tax or law is not a source", "Knowing it is not a source" in SYSTEM_PROMPT_TEMPLATE, True)
            check("staff: a concern states the rule too", "a 401(k) into a Roth IRA is a taxable\nconversion" in SYSTEM_PROMPT_TEMPLATE, True)
            check("staff: a rule the person states is neither confirmed nor corrected",
                  "neither\nconfirm nor correct it" in SYSTEM_PROMPT_TEMPLATE, True)
            check("client: the same, and their adviser answers it", "what you know in general is not a source" in CLIENT_PROMPT_TEMPLATE, True)
            check("the account type is passed as named and the card refuses it",
                  "call it rather than asking or choosing a type yourself" in TOOLS["start_onboarding"].description, True)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
