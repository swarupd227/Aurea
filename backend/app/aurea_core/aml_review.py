"""Deciding screening hits and signing off EDD (L200-1 §5.2, §5.4, §8).

A screening vendor reports possible matches; a person decides each one. A hit is open until someone dispositions it
true match, false positive or needs info, with a reason. A true match stands only once a second person in compliance
confirms it, because a true match is strict-liability territory. A false positive can be sampled for QA, and an
overturn reopens the hit. A party counts as screened when every hit on it is decided, so "the screener ran" is no
longer enough to open an account. Decisions are kept apart from the vendor's result, so a rescreen does not undo them,
and a new hit after a rescreen is open until someone decides it.

An EDD file is signed off against a checklist for the wealth it explains: each item names the source a reviewer saw,
and approval needs every item and a conclusion that the wealth is explicable. "We know the client well" is not a
source. A high-risk file is approved by compliance; a medium one by compliance or operations. Approving completes EDD,
declining keeps the account from opening, and asking for more leaves it pending. Nothing here is told to the client.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aml_review import CONFIRMATIONS, DISPOSITIONS, QA_RESULTS, EddReview, ScreeningDecision
from app.models.enums import EDDStatus, UserRole
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.provenance import ledger

# What a reviewer must see for each kind of wealth before calling it explicable.
CHECKLISTS: dict[str, list[tuple[str, str]]] = {
    "employment": [("employment_evidence", "Employment letter or contract"), ("income_evidence", "Payslips or tax returns")],
    "business_sale": [("sale_agreement", "Purchase agreement"), ("proceeds_evidence", "Escrow or bank record of the proceeds"),
                      ("ownership_history", "How the business was owned and built")],
    "equity_compensation": [("grant_statements", "Grant statements"), ("vesting_schedule", "Vesting schedule"),
                            ("sale_confirmations", "Sale or exercise confirmations")],
    "inheritance": [("estate_documents", "Will, probate or letters of administration"), ("distribution_evidence", "Record of the distribution received")],
    "investments": [("statement_history", "Statements showing how the money grew")],
    "property_sale": [("settlement_statement", "Settlement statement"), ("title_evidence", "Title or ownership record")],
    "crypto": [("exchange_statements", "Exchange statements"), ("wallet_ownership", "Evidence the wallets are the client's"),
               ("chain_analysis", "Chain-analysis report, where the firm's policy allows")],
}
SETTLED = {"clear", "cleared", "true_match"}
DISPOSERS = {UserRole.COMPLIANCE, UserRole.OPERATIONS}
QA_EVERY = 10   # one false positive in ten is sampled for QA


class AmlReviewError(ValueError):
    """This cannot be recorded, with the reason."""


def hit_key(hit: dict) -> str:
    return f"{hit.get('category')}:{hit.get('matched_name')}"


def hit_state(rows: list) -> dict:
    """Where one hit stands, from its decisions in order. A disposition starts a decision; a confirmation settles a
    true match or rejects it, and a QA overturn reopens a false positive."""
    current, confirmed, qa = None, False, None
    for r in rows:
        if r.kind == "disposition":
            current, confirmed, qa = r, False, None
        elif r.kind == "confirmation" and current is not None and current.decision == "true_match":
            if r.decision == "confirmed":
                confirmed = True
            else:
                current = None
        elif r.kind == "qa" and current is not None and current.decision == "false_positive":
            qa = r
            if r.decision == "overturn":
                current = None
    if current is None:
        return {"state": "open", "decision": None, "qa": qa}
    if current.decision == "false_positive":
        return {"state": "false_positive", "decision": current, "qa": qa}
    if current.decision == "true_match":
        return {"state": "true_match" if confirmed else "awaiting_confirmation", "decision": current, "qa": qa}
    return {"state": "needs_info", "decision": current, "qa": qa}


def party_state(party, rows: list) -> dict:
    """A party's screening, as decided. `rows` are this party's decisions, oldest first."""
    raw = party.screening_status or "not_screened"
    if raw == "not_screened":
        return {"state": "not_screened", "hits": [], "sanctions": False}
    by_hit: dict[str, list] = defaultdict(list)
    for r in rows:
        by_hit[r.hit_key].append(r)
    hits = []
    for h in party.screening_hits or []:
        st = hit_state(by_hit.get(hit_key(h), []))
        hits.append({**h, "key": hit_key(h), "state": st["state"]})
    states = {h["state"] for h in hits}
    if not hits:
        state = "clear"
    elif states & {"open", "needs_info", "awaiting_confirmation"}:
        state = "open"
    elif "true_match" in states:
        state = "true_match"
    else:
        state = "cleared"
    # A sanctions hit not shown to be a false positive stops the account, decided or not.
    sanctions = any(h.get("category") == "sanctions" and h["state"] != "false_positive" for h in hits)
    return {"state": state, "hits": hits, "sanctions": sanctions}


async def decisions_for(session: AsyncSession, party_ids: list) -> dict:
    """Each party's decisions, oldest first."""
    out: dict = defaultdict(list)
    if not party_ids:
        return out
    rows = (await session.execute(select(ScreeningDecision).where(ScreeningDecision.party_id.in_(list(party_ids)))
                                  .order_by(ScreeningDecision.created_at))).scalars().all()
    for r in rows:
        out[r.party_id].append(r)
    return out


async def _party(session: AsyncSession, party_id: uuid.UUID, firm_id: uuid.UUID) -> OnboardingParty:
    party = await session.get(OnboardingParty, party_id)
    if party is None or party.firm_id != firm_id:
        raise AmlReviewError("Party not found.")
    return party


def _find_hit(party, hit: str | None) -> dict:
    wanted = (hit or "").strip().lower()
    for h in party.screening_hits or []:
        if wanted in (hit_key(h).lower(), str(h.get("matched_name", "")).lower()):
            return h
    raise AmlReviewError(f"{party.legal_name} has no current hit called '{hit}'.")


async def prepare_decision(session: AsyncSession, party_id: uuid.UUID, *, firm_id: uuid.UUID, hit: str | None, kind: str | None,
                           decision: str | None, rationale: str | None, by: str, role) -> dict:
    """Everything recording would refuse, refused without writing."""
    party = await _party(session, party_id, firm_id)
    h = _find_hit(party, hit)
    kind = (kind or "disposition").strip().lower()
    decision = (decision or "").strip().lower()
    why = (rationale or "").strip()
    rows = (await decisions_for(session, [party.id]))[party.id]
    st = hit_state([r for r in rows if r.hit_key == hit_key(h)])
    if kind == "disposition":
        if role not in DISPOSERS:
            raise AmlReviewError("Compliance or operations decides a screening hit.")
        if decision not in DISPOSITIONS:
            raise AmlReviewError(f"The decision must be one of {', '.join(DISPOSITIONS)}.")
        if st["state"] in ("false_positive", "true_match", "awaiting_confirmation"):
            raise AmlReviewError(f"This hit is already decided: {st['state'].replace('_', ' ')}.")
        if len(why) < 10:
            raise AmlReviewError("Give the reason: which discriminators were checked, and what they showed.")
    elif kind == "confirmation":
        if role != UserRole.COMPLIANCE:
            raise AmlReviewError("A true match is confirmed by compliance.")
        if st["state"] != "awaiting_confirmation":
            raise AmlReviewError("Only a true match waiting for a second reviewer can be confirmed.")
        if st["decision"].decided_by == by:
            raise AmlReviewError("A second person confirms a true match, not the one who called it.")
        if decision not in CONFIRMATIONS:
            raise AmlReviewError(f"The decision must be one of {', '.join(CONFIRMATIONS)}.")
        if len(why) < 5:
            raise AmlReviewError("Give the reason.")
    elif kind == "qa":
        if role != UserRole.COMPLIANCE:
            raise AmlReviewError("QA of a disposition is done by compliance.")
        if st["state"] != "false_positive" or st["qa"] is not None:
            raise AmlReviewError("Only a false positive not yet reviewed can be sampled for QA.")
        if st["decision"].decided_by == by:
            raise AmlReviewError("QA is done by someone other than the person who decided it.")
        if decision not in QA_RESULTS:
            raise AmlReviewError(f"The decision must be one of {', '.join(QA_RESULTS)}.")
        if decision == "overturn" and len(why) < 10:
            raise AmlReviewError("Give the reason for the overturn.")
    else:
        raise AmlReviewError("The kind must be disposition, confirmation or qa.")
    case = await session.get(OnboardingCase, party.case_id)
    return {"party": party, "case": case, "hit": h, "kind": kind, "decision": decision, "rationale": why or decision, "was": st["state"]}


async def record_decision(session: AsyncSession, party_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, role, **terms) -> dict:
    ready = await prepare_decision(session, party_id, firm_id=firm_id, by=by, role=role, **terms)
    party, case, h = ready["party"], ready["case"], ready["hit"]
    session.add(ScreeningDecision(firm_id=firm_id, case_id=party.case_id, party_id=party.id, hit_key=hit_key(h), category=str(h.get("category")),
                                  kind=ready["kind"], decision=ready["decision"], rationale=ready["rationale"], decided_by=by, decided_by_role=str(role)))
    await session.flush()
    # A confirmed PEP or adverse-media match is EDD; a confirmed sanctions match stops the case, and what is reported
    # to the regulator is compliance's.
    if ready["kind"] == "confirmation" and ready["decision"] == "confirmed":
        if h.get("category") == "sanctions":
            case.exceptions = [*(case.exceptions or []), {"kind": "sanctions_true_match", "party": party.legal_name,
                                                          "detail": "Confirmed sanctions match: the account must not open; blocking and reporting are compliance's."}]
        elif case.edd_status != EDDStatus.EDD_COMPLETE.value:
            case.edd_status = EDDStatus.EDD_PENDING.value
    await ledger.append_entry(session, firm_id=firm_id, event_type="screening_decision", actor=by, content={
        "case_id": str(party.case_id), "party_id": str(party.id), "hit": hit_key(h), "kind": ready["kind"], "decision": ready["decision"],
        "rationale": ready["rationale"]})
    return await for_case(session, party.case_id, firm_id=firm_id)


def _qa_sample(false_positives: list) -> set:
    """Every tenth false positive, by when it was decided, starting with the first."""
    ordered = sorted(false_positives, key=lambda r: r.created_at)
    return {r.id for i, r in enumerate(ordered) if i % QA_EVERY == 0}


async def prepare_edd(session: AsyncSession, case_id: uuid.UUID, *, firm_id: uuid.UUID, wealth_type: str | None, evidence: dict | None,
                      conclusion: str | None, decision: str | None, rationale: str | None, role) -> dict:
    case = await session.get(OnboardingCase, case_id)
    if case is None or case.firm_id != firm_id:
        raise AmlReviewError("Case not found.")
    if case.edd_status == EDDStatus.EDD_COMPLETE.value:
        raise AmlReviewError("EDD on this case is already signed off.")
    if case.edd_status not in (EDDStatus.EDD_PENDING.value, "edd_declined"):
        raise AmlReviewError("This case has no EDD open.")
    tier = (case.aml_risk_tier or "").lower()
    allowed = {UserRole.COMPLIANCE} if tier == "high" else DISPOSERS
    if role not in allowed:
        raise AmlReviewError("A high-risk EDD file is signed off by compliance." if tier == "high" else "Compliance or operations signs off an EDD file.")
    decision = (decision or "").strip().lower()
    if decision not in ("approve", "decline", "more_info"):
        raise AmlReviewError("The decision must be approve, decline or more_info.")
    why = (rationale or "").strip()
    if len(why) < 10:
        raise AmlReviewError("Give the reviewer's conclusion in their own words.")
    kind = (wealth_type or "").strip().lower()
    given = {str(k).strip().lower(): str(v).strip() for k, v in (evidence or {}).items() if str(v).strip()}
    conclusion = (conclusion or "").strip().lower() or ("explicable" if decision == "approve" else "not_explicable")
    missing: list[str] = []
    if decision == "approve":
        if kind not in CHECKLISTS:
            raise AmlReviewError(f"Say what the wealth comes from: one of {', '.join(CHECKLISTS)}.")
        missing = [label for key, label in CHECKLISTS[kind] if len(given.get(key, "")) < 3]
        if missing:
            raise AmlReviewError("Approval needs a source for every item: missing " + "; ".join(missing) + ".")
        if conclusion != "explicable":
            raise AmlReviewError("A file is approved only when the wealth is explicable.")
    return {"case": case, "wealth_type": kind or "unstated", "evidence": given, "conclusion": conclusion, "decision": decision, "rationale": why, "tier": tier or None}


async def record_edd(session: AsyncSession, case_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, role, **terms) -> dict:
    ready = await prepare_edd(session, case_id, firm_id=firm_id, role=role, **terms)
    case = ready["case"]
    session.add(EddReview(firm_id=firm_id, case_id=case.id, wealth_type=ready["wealth_type"], evidence=ready["evidence"], conclusion=ready["conclusion"],
                          decision=ready["decision"], rationale=ready["rationale"], risk_tier=ready["tier"], reviewed_by=by, reviewed_by_role=str(role)))
    case.edd_status = {"approve": EDDStatus.EDD_COMPLETE.value, "decline": "edd_declined", "more_info": EDDStatus.EDD_PENDING.value}[ready["decision"]]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="edd_signed_off", actor=by, content={
        "case_id": str(case.id), "decision": ready["decision"], "wealth_type": ready["wealth_type"], "conclusion": ready["conclusion"],
        "sources": ready["evidence"]})
    return await for_case(session, case.id, firm_id=firm_id)


def _decision_view(r) -> dict | None:
    return {"kind": r.kind, "decision": r.decision, "rationale": r.rationale, "by": r.decided_by, "at": r.created_at.isoformat() if r.created_at else None} if r else None


async def _rows(session: AsyncSession, firm_id: uuid.UUID, case_id: uuid.UUID | None = None) -> dict:
    query = select(OnboardingCase).where(OnboardingCase.firm_id == firm_id)
    if case_id is not None:
        query = query.where(OnboardingCase.id == case_id)
    cases = {c.id: c for c in (await session.execute(query)).scalars().all()}
    parties = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id.in_(list(cases))))).scalars().all() if cases else []
    decided = await decisions_for(session, [p.id for p in parties])
    all_rows = [r for rows in decided.values() for r in rows]
    hits, finals = [], []
    for p in parties:
        rows = decided.get(p.id, [])
        ps = party_state(p, rows)
        for h in ps["hits"]:
            mine = [r for r in rows if r.hit_key == h["key"]]
            st = hit_state(mine)
            if st["decision"] is not None and st["state"] in ("false_positive", "true_match"):
                finals.append(st["decision"])
            case = cases[p.case_id]
            hits.append({"case_id": str(case.id), "case": case.prospect_name, "case_status": str(case.status), "party_id": str(p.id), "party": p.legal_name,
                         "role": p.role, "hit": h["key"], "category": h.get("category"), "matched_name": h.get("matched_name"), "score": h.get("score"),
                         "country": h.get("country"), "note": h.get("note"), "discriminators": h.get("discriminators") or {}, "state": st["state"],
                         "decision": _decision_view(st["decision"]), "qa": _decision_view(st["qa"]),
                         "history": [_decision_view(r) for r in mine]})
    fps = [d for d in finals if d.decision == "false_positive"]
    sampled = _qa_sample(fps)
    for h in hits:
        h["qa_due"] = h["state"] == "false_positive" and h["qa"] is None and any(
            d.id in sampled and d.hit_key == h["hit"] and str(d.party_id) == h["party_id"] for d in fps)
    edd_rows = (await session.execute(select(EddReview).where(EddReview.case_id.in_(list(cases))).order_by(EddReview.created_at))).scalars().all() if cases else []
    reviews_by_case: dict = defaultdict(list)
    for r in edd_rows:
        reviews_by_case[r.case_id].append(r)
    edd = []
    for c in cases.values():
        if c.edd_status in (None, "", "none", "cdd") and not reviews_by_case.get(c.id):
            continue
        last = reviews_by_case[c.id][-1] if reviews_by_case.get(c.id) else None
        proposal = c.proposal or {}
        edd.append({"case_id": str(c.id), "case": c.prospect_name, "risk_tier": c.aml_risk_tier, "edd_status": c.edd_status,
                    "source_of_wealth": (c.intake or {}).get("source_of_wealth"), "memo_gaps": proposal.get("edd_gaps") or [],
                    "has_memo": bool(proposal.get("edd_memo")), "last_review": {
                        "decision": last.decision, "wealth_type": last.wealth_type, "conclusion": last.conclusion, "evidence": last.evidence,
                        "rationale": last.rationale, "by": last.reviewed_by, "at": last.created_at.isoformat() if last.created_at else None} if last else None})
    qa_rows = [r for r in all_rows if r.kind == "qa"]
    return {"hits": hits, "edd": edd, "finals": finals, "qa_rows": qa_rows}


def _summary(data: dict) -> dict:
    hits, finals, qa_rows = data["hits"], data["finals"], data["qa_rows"]
    fps = [d for d in finals if d.decision == "false_positive"]
    return {
        "open": sum(1 for h in hits if h["state"] in ("open", "needs_info")),
        "awaiting_confirmation": sum(1 for h in hits if h["state"] == "awaiting_confirmation"),
        "true_matches": sum(1 for h in hits if h["state"] == "true_match"),
        "qa_due": sum(1 for h in hits if h["qa_due"]),
        "edd_pending": sum(1 for e in data["edd"] if e["edd_status"] == "edd_pending"),
        "edd_declined": sum(1 for e in data["edd"] if e["edd_status"] == "edd_declined"),
        "false_positive_pct": round(len(fps) / len(finals) * 100, 1) if finals else None,
        "qa_overturn_pct": round(sum(1 for r in qa_rows if r.decision == "overturn") / len(qa_rows) * 100, 1) if qa_rows else None,
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    data = await _rows(session, firm_id)
    order = {"awaiting_confirmation": 0, "open": 1, "needs_info": 2, "true_match": 3, "false_positive": 4}
    hits = sorted(data["hits"], key=lambda h: (order.get(h["state"], 9), not h["qa_due"], h["case"]))
    return {"hits": hits, "edd": data["edd"], "checklists": {k: [{"key": a, "label": b} for a, b in v] for k, v in CHECKLISTS.items()},
            "summary": _summary(data), "case_id": None}


async def for_case(session: AsyncSession, case_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    data = await _rows(session, firm_id, case_id)
    if not data["hits"] and not data["edd"] and await session.get(OnboardingCase, case_id) is None:
        raise AmlReviewError("Case not found.")
    case = await session.get(OnboardingCase, case_id)
    if case is None or case.firm_id != firm_id:
        raise AmlReviewError("Case not found.")
    return {"hits": data["hits"], "edd": data["edd"], "checklists": {k: [{"key": a, "label": b} for a, b in v] for k, v in CHECKLISTS.items()},
            "summary": _summary(data), "case_id": str(case_id), "case": case.prospect_name}
