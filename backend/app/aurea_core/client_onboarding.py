"""A client's own onboarding state, in plain words (B-392, L200-1 India §7 "client-facing status visibility").

For the accounts opened, or being opened, for one household: the step each has reached, since when, and whose move it is. The step is
worked from the same evidence as the onboarding board (`account_states.state_of`) and then told in fewer words: gathering details and
documents are one step, and everything between the signature and the account being sent to be opened is one step, so a client cannot
tell from it whether anything is being looked into, or why something takes long. Nothing about screening, monitoring, identity
findings, risk bands, exceptions, SLAs, who in the firm holds it, or any free text a person wrote is in it, and a case that did not
proceed is not listed. A document to correct is named only by its kind. A KYC registry status is the client's other reading
(`kyc_registry.for_client`). Reads only.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import account_states, advisers, firm_time, markets
from app.models.onboarding import OnboardingCase, TransferRequest
from app.models.tenant import Firm

# The client's steps, in order, with whose move each is.
STEPS = (("details", "Your details and documents", "You and your adviser"), ("signature", "Your signature", "You"),
         ("with_firm", "With the firm", "The firm"), ("opening", "Account being opened", "The firm"), ("open", "Account open", "Nothing waiting on you"),
         ("funding", "Money or investments on the way", "The firm and the sending firm"), ("received", "Money or investments received", "The firm"),
         ("invested", "Invested", "Done"))
FROM_STATE = {"draft": "details", "data_complete": "details", "documents_generated": "signature", "signed": "with_firm", "in_review": "with_firm",
              "submitted": "opening", "open": "open", "funding_in_progress": "funding", "funded": "received", "invested": "invested"}
# The document defects a client can put right, by their kind (`NIGORootCause`).
CORRECTIONS = {"missing_signature": "A signature is missing", "title_mismatch": "The name on a form does not match", "stale_form": "A form is out of date",
               "wrong_tenancy": "The way the account is held needs correcting", "missing_beneficiary": "A nomination is missing",
               "missing_id": "An identity document is missing"}
ACCOUNT_TYPES = {"individual": "Individual", "joint_all": "Joint, all sign", "joint_anyone": "Joint, anyone or survivor", "huf": "HUF",
                 "trust": "Trust", "entity_corp": "Company", "entity_partnership": "Partnership", "entity_llp": "LLP", "nri_nre": "NRI (NRE)",
                 "nri_nro": "NRI (NRO)", "minor_guardian": "Minor, run by a guardian"}


def applies(firm: Firm | None) -> bool:
    return firm is not None and markets.code(firm.jurisdiction) == "IN"


def _home(case) -> str:
    return str((case.materialized or {}).get("household_id") or (case.intake or {}).get("household_id") or "")


async def for_client(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """One household's accounts and the step each has reached. The caller resolves the household inside the firm."""
    if not applies(firm):
        return {"applies": False, "household_id": str(household_id), "accounts": []}
    from app.api.onboarding import _first_fills

    cases = [c for c in (await session.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id)
                                               .order_by(OnboardingCase.created_at.desc()))).scalars().all()
             if _home(c) == str(household_id) and str(c.status) != "rejected"]
    transfers: dict = {}
    if cases:
        for t in (await session.execute(select(TransferRequest).where(TransferRequest.firm_id == firm.id,
                                                                      TransferRequest.case_id.in_([c.id for c in cases])))).scalars().all():
            transfers.setdefault(t.case_id, []).append(t)
    fills = await _first_fills(session, cases)
    keys = [k for k, _, _ in STEPS]
    accounts = []
    for c in cases:
        # Neither the documents gate nor compliance's gates are read: the client's steps do not tell them apart.
        state = account_states.state_of(c, data_complete=False, gates_blocking=False, transfers=transfers.get(c.id, []), first_fill=fills.get(c.id))
        step = FROM_STATE[state["state"]]
        if str(c.status) == "approved" and keys.index(step) < keys.index("opening"):
            step, state = "opening", {**state, "since": c.activated_at}
        label, whose = next((label, whose) for k, label, whose in STEPS if k == step)
        since = state["since"]
        needs = []
        if step == "signature":
            needs.append("Your signature on the agreement")
        if c.nigo_flag and str(c.status) != "approved" and c.nigo_root_cause in CORRECTIONS:
            needs.append(CORRECTIONS[c.nigo_root_cause])
        opened = c.custodian_push_at if c.custodian_push_status == "active" else None
        accounts.append({"name": c.prospect_name, "account_type": ACCOUNT_TYPES.get(c.registration_type or "", "Account"), "step": step,
                         "step_label": label, "position": keys.index(step) + 1, "of": len(keys), "whose_move": whose,
                         "since": firm_time.today(firm, since).isoformat() if since else None,
                         "opened_on": firm_time.today(firm, opened).isoformat() if opened else None, "needs": needs,
                         "done": step == "invested"})
    accounts.sort(key=lambda a: (a["done"], a["position"], a["name"]))
    who = await advisers.adviser_of(session, firm.id, household_id)
    return {"applies": True, "household_id": str(household_id), "as_of": firm_time.today(firm).isoformat(), "accounts": accounts,
            "steps": [label for _, label, _ in STEPS], "adviser": who.full_name if who else None}
