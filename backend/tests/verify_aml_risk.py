"""Prove the risk model scores recorded facts and refuses blanks, that its band sets due diligence and the review
cycle, that an override keeps its reason and never goes below a path's floor, that identity is verified for every
party, that a party is screened when it is recorded, and that the daily screen covers clients already onboarded.

Runs against a throwaway SQLite database.

    python -m tests.verify_aml_risk
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents.adverse_media_pep import AdverseMediaPEPScreenerAgent
from app.agents.cip_identity_verifier import CIPIdentityVerifierAgent
from app.api import onboarding as api
from app.atlas.base import AgentContext, Subject
from app.aurea_core import aml_risk, firm_time
from app.core.db import Base, utcnow
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.aml_risk import AmlRiskRating
from app.models.enums import UserRole
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, aml_risk.AmlRiskError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure() -> None:
    print("\n=== The model ===")
    s = lambda facts, reg="individual", pep=False: aml_risk.score(facts, registration_type=reg, firm_country="US", monitored={"PA"}, pep=pep, adverse=False)  # noqa: E731
    plain = {"residence_country": "US", "wealth_source": "employment", "channel": "face_to_face"}
    check("an employee met in person, at home, is low", (s(plain)["score"], s(plain)["model_band"]), (0.0, "low"))
    check("a call-for-action country is high on its own", s({**plain, "residence_country": "IR"})["model_band"], "high")
    check("the firm's own monitored list counts", s({**plain, "citizenship": "PA"})["score"], 20.0)
    remote_trust = s({**plain, "channel": "remote", "wealth_source": "business_sale", "layered": True}, reg="trust")
    check("a remote layered trust from a business sale", (remote_trust["score"], remote_trust["model_band"]), (40.0, "medium"))
    check("and it has the layered path", [p["path"] for p in remote_trust["paths"]], ["layered_structure"])
    pep = s(plain, pep=True)
    check("a PEP scores 30 but is never below high", (pep["score"], pep["model_band"], pep["floor"]), (30.0, "high", "high"))
    abroad = s({**plain, "residence_country": "GB"})
    check("living abroad adds 10 and asks for a W-8BEN", (abroad["score"], abroad["paths"][0]["requires"][0]), (10.0, "W-8BEN on file"))
    check("blanks are named", s({})["missing"], ["residence_country", "wealth_source", "channel"])


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US", settings={"aml_monitored_countries": ["PA"]})
            s.add(firm)
            await s.flush()
            users = {}
            for key, role in (("ops", UserRole.OPERATIONS), ("comp", UserRole.COMPLIANCE), ("adv", UserRole.ADVISER)):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}
            case = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Lena Ward", registration_type="individual", intake={})
            s.add(case)
            await s.flush()

            print("\n=== Rating a case ===")
            ask = {"case_id": str(case.id)}
            await refused("blanks are not rated low", ex["ops"].preview_change("rate_aml_risk", ask), "record these before rating", "channel")
            facts = {**ask, "residence_country": "US", "wealth_source": "employment", "channel": "remote", "wires_per_year": 36, "third_party_payments": True}
            await refused("an adviser does not rate", ex["adv"].preview_change("rate_aml_risk", facts), "compliance or operations")
            card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("rate_aml_risk", facts))["rows"]}
            check("the card shows the factors and what follows", (card["Model says"], card["Due diligence"], card["Review"]), ("Medium", "EDD, signed off by operations or compliance", "Every 24 months"))
            check("showing it writes nothing", (await s.execute(select(AmlRiskRating))).scalars().all(), [])
            await refused("an override needs its reason", ex["ops"].preview_change("rate_aml_risk", {**facts, "band": "low"}), "override needs the reason")
            await refused("high is compliance's", ex["ops"].preview_change("rate_aml_risk", {**facts, "band": "high", "reason": "Adverse news this week"}), "compliance")
            out = await ex["ops"].rate_aml_risk({**facts, "band": "low", "reason": "Wires are payroll sweeps between her own accounts"})
            check("an override is kept beside the model", (out["rating"]["band"], out["rating"]["model_band"], "payroll" in out["rating"]["override_reason"]), ("low", "medium", True))
            check("and the case reads it", (case.aml_risk_tier, case.edd_status), ("low", "cdd"))
            check("the facts are on file for next time", case.intake["cdd"]["channel"], "remote")
            check("with the next review four years on", out["rating"]["review_months"], 48)
            again = await ex["comp"].rate_aml_risk({**ask, "band": "high", "reason": "Confirmed links to a sanctioned entity in the press"})
            check("the recorded facts carry over", (again["rating"]["band"], case.edd_status), ("high", "edd_pending"))
            check("every rating stays on record", len((await s.execute(select(AmlRiskRating))).scalars().all()), 2)

            print("\n=== A path's floor ===")
            pep_case = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Viktor Sokolov", registration_type="individual", intake={})
            s.add(pep_case)
            await s.flush()
            from app.models.aml_review import ScreeningDecision
            party = OnboardingParty(id=uuid.uuid4(), firm_id=firm.id, case_id=pep_case.id, role="owner", legal_name="Viktor Sokolov", screening_status="review",
                                    screening_hits=[{"matched_name": "Viktor Sokolov", "category": "pep", "score": 1.0}])
            s.add(party)
            await s.flush()
            for kind, decision, by in (("disposition", "true_match", "ops@t"), ("confirmation", "confirmed", "comp@t")):
                s.add(ScreeningDecision(firm_id=firm.id, case_id=pep_case.id, party_id=party.id, hit_key="pep:Viktor Sokolov", category="pep",
                                        kind=kind, decision=decision, rationale="checked", decided_by=by, decided_by_role="compliance"))
            await s.flush()
            pep_facts = {"case_id": str(pep_case.id), "residence_country": "US", "wealth_source": "employment", "channel": "face_to_face"}
            await refused("a PEP is never rated below high", ex["comp"].preview_change("rate_aml_risk", {**pep_facts, "band": "medium", "reason": "Long retired from office"}), "never rated below high")
            await ex["comp"].rate_aml_risk(pep_facts)
            check("rated high, by the model, with senior approval", (pep_case.aml_risk_tier, aml_risk.CONSEQUENCES["high"]["approver"]), ("high", "Compliance, with senior approval"))

            print("\n=== The old rating route goes through the model ===")
            try:
                await api.set_aml_risk_rating(pep_case.id, api.AMLRatingIn(aml_risk_tier="medium", notes="trust me"), user=users["comp"], firm=firm, db=s)
                check("the route refuses what the model refuses", "not refused", "refused")
            except HTTPException as exc:
                check("the route refuses what the model refuses", exc.status_code, 409)

            print("\n=== Reviews come due ===")
            row = (await s.execute(select(AmlRiskRating).where(AmlRiskRating.case_id == pep_case.id))).scalars().first()
            row.next_review_on = firm_time.today(firm) - timedelta(days=1)
            await s.flush()
            due = await aml_risk.reviews_due(s, firm.id, today=firm_time.today(firm))
            check("a rating past its date is due", [d["case"] for d in due], ["Viktor Sokolov"])

            print("\n=== Identity for every party ===")
            jc = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Ward Joint", registration_type="joint_jtwros", intake={})
            s.add(jc)
            await s.flush()
            a = OnboardingParty(id=uuid.uuid4(), firm_id=firm.id, case_id=jc.id, role="owner", legal_name="Lena Ward", dob="1980-01-01", address="1 Main St", id_number="X1", id_type="passport")
            b = OnboardingParty(id=uuid.uuid4(), firm_id=firm.id, case_id=jc.id, role="joint_owner", legal_name="Tom Ward", dob=None, address="1 Main St")
            beneficiary = OnboardingParty(id=uuid.uuid4(), firm_id=firm.id, case_id=jc.id, role="beneficiary", legal_name="Kid Ward")
            s.add_all([a, b, beneficiary])
            await s.flush()
            agent = CIPIdentityVerifierAgent()
            ctx = AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={}, subject=Subject("onboarding_case", jc.id, jc.prospect_name))
            sensed = await agent.sense(ctx)
            check("each owner, not a beneficiary", sorted(x["name"] for x in sensed["subjects"]), ["Lena Ward", "Tom Ward"])
            check("documentary where an ID is on file", {x["name"]: x["method"] for x in sensed["subjects"]}, {"Lena Ward": "documentary", "Tom Ward": "non_documentary"})
            draft = (await agent.think(ctx, sensed))[0]
            rec = type("R", (), {"subject_id": jc.id, "payload": draft.payload, "modified_payload": None})()
            await agent.act(ctx, rec)
            check("each party gets its own result", (a.cip_status is not None, b.cip_status is not None, beneficiary.cip_status), (True, True, None))
            check("the case is verified only if every party is", jc.cip_status == ("verified" if {a.cip_status, b.cip_status} == {"verified"} else jc.cip_status), True)
            check("a missing date of birth is not verified", b.cip_status != "verified", True)

            print("\n=== Screened when recorded, and the book rescreened ===")
            out = await api.add_party(jc.id, api.PartyIn(role="authorised_signer", legal_name="Marcus Delacroix"), user=users["ops"], firm=firm, db=s)
            signer = next(p for p in out["parties"] if p["legal_name"] == "Marcus Delacroix")
            check("a signer added is screened at once", (signer["screening_status"], len(signer["screening_hits"])), ("review", 1))
            jc.status, jc.activated_at = "approved", utcnow() - timedelta(days=40)
            await s.flush()
            sensed = await AdverseMediaPEPScreenerAgent().sense(AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={}, subject=Subject("firm", firm.id, "T")))
            check("the daily screen covers clients already onboarded", "Ward Joint" in [r["case_name"] for r in sensed["results"]], True)
            states = await api.states_overview(s, firm)
            kyc = [e["detail"] for q in [states["queues"]["kyc"]] for e in q["items"] if e["account"] == "Ward Joint"]
            if jc.cip_status != "verified":
                check("unverified 40 days after opening is a KYC exception", any("30 days after opening" in d for d in kyc), True)
            check("a risk review that has come due is a KYC exception", any("risk review due" in e["detail"] for e in states["queues"]["kyc"]["items"]), True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
