"""Prove a screening hit is cleared only by a person's decision, that a true match needs a second reviewer and QA can
reopen a false positive, that a rescreen keeps decisions, that EDD can be signed off against its checklist, and that
the two onboarding dashboards report one cycle time.

Runs against a throwaway SQLite database.

    python -m tests.verify_aml_reviews
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import analytics
from app.aurea_core import aml_review, gates, onboarding_metrics, parties, screening
from app.core.db import Base, utcnow
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.aml_review import ScreeningDecision
from app.models.enums import UserRole
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.tenant import Firm

PASS, FAIL = [], []
NO_DISCLOSURES = {"outstanding": [], "required_count": 0, "delivered_count": 0}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, aml_review.AmlReviewError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def stamp(party, dob=None):
    """What the screener does: rewrite the party's result."""
    result = screening.screen(party.legal_name, dob=dob or party.dob)
    party.screening_status = result.status
    party.screening_hits = result.to_dict()["hits"]
    party.screened_at = utcnow()


async def main() -> int:
    print("\n=== Discriminators ===")
    hit = screening.screen("Viktor Sokolov", dob="1961-03-14").hits[0]
    check("the date of birth is compared", hit.discriminators, {"dob": "match"})
    check("and can differ", screening.screen("Viktor Sokolov", dob="1990-01-01").hits[0].discriminators, {"dob": "differs"})
    check("or be unknown", screening.screen("Viktor Sokolov").hits[0].discriminators, {"dob": "unknown"})

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="USD", jurisdiction="US")
            s.add_all([firm, other])
            await s.flush()
            users = {}
            for key, role in (("ops", UserRole.OPERATIONS), ("comp1", UserRole.COMPLIANCE), ("comp2", UserRole.COMPLIANCE), ("adv", UserRole.ADVISER)):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}
            outsider = ToolExecutors(s, users["comp1"].id, UserRole.COMPLIANCE, other.id)

            case = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Olena Petrenko", registration_type="joint_jtwros", aml_risk_tier="medium")
            s.add(case)
            await s.flush()
            pep = OnboardingParty(id=uuid.uuid4(), firm_id=firm.id, case_id=case.id, role="owner", legal_name="Olena Petrenko", dob="1975-05-09")
            spouse = OnboardingParty(id=uuid.uuid4(), firm_id=firm.id, case_id=case.id, role="joint_owner", legal_name="Imelda Castellanos", dob="1990-02-02")
            s.add_all([pep, spouse])
            await s.flush()
            stamp(pep)
            stamp(spouse)
            await s.flush()

            async def gate(key):
                rows = (await s.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all()
                st = parties.completeness_for(case.registration_type, list(rows), await aml_review.decisions_for(s, [p.id for p in rows]))
                return next(g for g in gates.evaluate(case, party_status=st, disclosure_status=NO_DISCLOSURES)["gates"] if g["key"] == key)

            print("\n=== The screener running is not the same as being cleared ===")
            check("a party with an open hit does not count as screened", (await gate("parties_screened"))["state"], "fail")
            check("and the gate says why", "open" in (await gate("parties_screened"))["detail"], True)
            check("an open sanctions hit stops the account", (await gate("no_sanctions_match"))["state"], "fail")

            def say(party, hit, decision, why="Date of birth differs from the list entry; different person.", kind="disposition"):
                return {"party_id": str(party.id), "hit": hit, "kind": kind, "decision": decision, "rationale": why}

            print("\n=== Deciding a hit ===")
            await refused("an adviser does not decide", ex["adv"].preview_change("record_screening_decision", say(spouse, "Imelda Castellanos", "false_positive")), "compliance or operations")
            await refused("a reason is needed", ex["ops"].preview_change("record_screening_decision", say(spouse, "Imelda Castellanos", "false_positive", why="no")), "reason")
            await refused("a hit that is not there", ex["ops"].preview_change("record_screening_decision", say(spouse, "Someone Else", "false_positive")), "no current hit")
            await refused("another firm's party", outsider.preview_change("record_screening_decision", say(spouse, "Imelda Castellanos", "false_positive")), "not found")
            card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("record_screening_decision", say(spouse, "Imelda Castellanos", "false_positive")))["rows"]}
            check("the card names the hit and its birth date", ("sanctions" in card["Hit"], card["Date of birth"], card["Effect"]), (True, "differs", "Clears this hit; it may be sampled for QA"))
            check("showing it writes nothing", (await s.execute(select(func.count()).select_from(ScreeningDecision))).scalar(), 0)
            out = await ex["ops"].record_screening_decision(say(spouse, "Imelda Castellanos", "false_positive"))
            check("a false positive clears the sanctions gate", (await gate("no_sanctions_match"))["state"], "pass")
            await refused("not twice", ex["ops"].preview_change("record_screening_decision", say(spouse, "Imelda Castellanos", "true_match")), "already decided")

            print("\n=== A true match needs a second person ===")
            await ex["ops"].record_screening_decision(say(pep, "Olena Petrenko", "true_match", "Date of birth matches, same country; she is the listed associate."))
            check("it waits for a second reviewer", next(h["state"] for h in out["hits"] if True) is not None and
                  next(h for h in (await aml_review.for_case(s, case.id, firm_id=firm.id))["hits"] if h["party"] == "Olena Petrenko")["state"], "awaiting_confirmation")
            check("and the party is not yet cleared", (await gate("parties_screened"))["state"], "fail")
            await refused("operations does not confirm", ex["ops"].preview_change("record_screening_decision", say(pep, "Olena Petrenko", "confirmed", "Agreed", kind="confirmation")), "compliance")
            card = {r["label"]: r["value"] for r in (await ex["comp1"].preview_change("record_screening_decision", say(pep, "Olena Petrenko", "confirmed", "Agreed, same person", kind="confirmation")))["rows"]}
            check("a confirmed PEP opens EDD", card["Effect"], "Opens enhanced due diligence")
            await ex["comp1"].record_screening_decision(say(pep, "Olena Petrenko", "confirmed", "Agreed, same person", kind="confirmation"))
            check("confirmed, the party is screened", (await gate("parties_screened"))["state"], "pass")
            check("and EDD is pending", (case.edd_status, (await gate("edd_resolved"))["state"]), ("edd_pending", "fail"))

            print("\n=== A rescreen keeps what people decided ===")
            stamp(pep)
            stamp(spouse)
            await s.flush()
            check("the same hits keep their decisions", ((await gate("parties_screened"))["state"], (await gate("no_sanctions_match"))["state"]), ("pass", "pass"))
            spouse.screening_hits = [*spouse.screening_hits, {"matched_name": "Marcus Delacroix", "category": "adverse_media", "severity": "low", "score": 0.9, "country": "FR", "note": "", "discriminators": {"dob": "unknown"}}]
            spouse.screening_status = "review"
            await s.flush()
            check("a new hit is open until someone decides it", (await gate("parties_screened"))["state"], "fail")
            await ex["ops"].record_screening_decision(say(spouse, "Marcus Delacroix", "false_positive", "Different country and profession on file; not her."))

            print("\n=== QA ===")
            view = await aml_review.for_case(s, case.id, firm_id=firm.id)
            sampled = [h for h in view["hits"] if h["qa_due"]]
            check("the first false positive is sampled", [h["matched_name"] for h in sampled], ["Imelda Castellanos"])
            await refused("not by the person who decided", ex["ops"].preview_change("record_screening_decision", say(spouse, "Imelda Castellanos", "agree", "fine", kind="qa")), "compliance")
            await ex["comp2"].record_screening_decision(say(spouse, "Imelda Castellanos", "overturn", "The list's alias matches her maiden name; reopen.", kind="qa"))
            check("an overturn reopens it", ((await gate("no_sanctions_match"))["state"], (await gate("parties_screened"))["state"]), ("fail", "fail"))
            await ex["comp1"].record_screening_decision(say(spouse, "Imelda Castellanos", "true_match", "Alias confirmed by the passport copy."))
            await ex["comp2"].record_screening_decision(say(spouse, "Imelda Castellanos", "confirmed", "Agreed on the passport.", kind="confirmation"))
            check("a confirmed sanctions match stops the case", ((await gate("no_sanctions_match"))["state"], [e["kind"] for e in case.exceptions]), ("fail", ["sanctions_true_match"]))
            summary = (await aml_review.for_firm(s, firm.id))["summary"]
            check("the false-positive rate reads what people decided", summary["false_positive_pct"], round(1 / 3 * 100, 1))
            check("and the QA overturn rate", summary["qa_overturn_pct"], 100.0)

            print("\n=== EDD sign-off ===")
            edd = {"case_id": str(case.id), "decision": "approve", "wealth_type": "business_sale",
                   "evidence": ["sale_agreement: SPA dated 3 Mar 2024, executed", "proceeds_evidence: Escrow release letter 9 Apr 2024"], "conclusion": "explicable",
                   "rationale": "Proceeds of the 2024 sale of her logistics firm, corroborated."}
            await refused("every checklist item needs a source", ex["comp1"].preview_change("sign_off_edd", edd), "owned and built")
            edd["evidence"].append("ownership_history: Companies register extracts 2009-2024")
            await refused("a source is needed for the conclusion too", ex["comp1"].preview_change("sign_off_edd", {**edd, "conclusion": "not_explicable"}), "explicable")
            await refused("an adviser does not sign off", ex["adv"].preview_change("sign_off_edd", edd), "compliance or operations")
            case.aml_risk_tier = "high"
            await refused("a high-risk file is compliance's", ex["ops"].preview_change("sign_off_edd", edd), "high-risk")
            card = {r["label"]: r["value"] for r in (await ex["comp1"].preview_change("sign_off_edd", edd))["rows"]}
            check("the card names every source", (card["Effect"], "SPA dated" in card["Sources"], card["Wealth"]), ("Completes EDD", True, "business sale"))
            await ex["comp1"].sign_off_edd(edd)
            check("approved, EDD is complete and the gate passes", (case.edd_status, (await gate("edd_resolved"))["state"]), ("edd_complete", "pass"))
            await refused("not twice", ex["comp1"].preview_change("sign_off_edd", edd), "already signed off")
            case.edd_status = "edd_pending"
            await ex["comp1"].sign_off_edd({"case_id": str(case.id), "decision": "decline", "rationale": "Proceeds cannot be traced to the sale."})
            check("declined, the account does not open", (case.edd_status, (await gate("edd_resolved"))["state"]), ("edd_declined", "fail"))
            await ex["comp1"].sign_off_edd({"case_id": str(case.id), "decision": "more_info", "rationale": "New bank records supplied; reopen."})
            check("more information reopens it", case.edd_status, "edd_pending")

            print("\n=== One cycle time ===")
            now = utcnow()
            done = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Done", registration_type="individual", status="approved")
            s.add(done)
            await s.flush()
            done.created_at, done.activated_at, done.updated_at = now - timedelta(days=10), now - timedelta(days=6), now
            await s.flush()
            dash = await analytics.get_onboarding_metrics(firm=firm, db=s)
            core = onboarding_metrics.compute([done], parties_by_case={}, disclosures_by_case={}, transfers_by_case={}, now=now)
            check("analytics measures to activation, as the onboarding metrics do",
                  (dash["cycle_time"]["overall_p50_days"], core["cycle_time_days"]["intake_to_activation"]["p50"]), (4.0, 4.0))
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
