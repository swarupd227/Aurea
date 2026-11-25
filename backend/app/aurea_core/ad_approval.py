"""Advertisement pre-approval: marketing copy goes to compliance before it is used, and what was approved is kept (L100 India edition section 13, B-298).

`advertising.py` reads a text against the rules and reports findings; it never approves. The 2023 advertisement code asks for more than a
check: a firm's marketing is approved by someone accountable before it is used. So:

  submit    a member of staff puts a piece of marketing copy (a title, the text, the channel) to compliance. `advertising.check` is run on it as
            marketing and its findings are kept with the submission, as a count of blocks and flags and in the page's list. The text is kept as
            submitted, with a SHA-256 of it (leading and trailing space ignored). It cannot be edited: a changed text is a new submission, which may
            name the one it follows.
  decide    compliance records an approval or a rejection with a reason, never the person who submitted it, once per submission, added and never
            edited. An approval is for exactly that wording: its id is what a message, report or research item may cite.
  cite      `verify_citation` takes an approval id and the text about to be used: it reads "approved" only if the approval exists, is an approval
            and the text's hash is the approved one. Changed wording reads "the text changed since it was approved". Nothing here sends, publishes
            or withdraws: the person who uses the text is accountable for citing it, and an approval is not a statement that the firm is compliant.

The findings are the patterns of `advertising.py`, which are not exhaustive. Compliance may approve a text that carries a finding, with its reason
on the record, because a person decides; the card shows the findings first. The Code is operative from 8 April 2027 and the firm's own control
applies before that. An approval carries no expiry here, because none was read from the Code: compliance rejects or resubmits what has aged.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advertising, firm_time, law_figures, licences, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.identity import User
from app.models.india_batch_c import AdDecision, AdSubmission
from app.models.tenant import Firm
from app.provenance import ledger

DECIDERS = frozenset({UserRole.COMPLIANCE})
SUBMITTERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO, UserRole.OPERATIONS, UserRole.BRANCH_LEADER,
                        UserRole.COMPLIANCE, UserRole.ADMIN})
CHANNELS = {"website": "Website", "social": "Social media", "email": "Email campaign", "print": "Print", "event": "Event or presentation", "other": "Other"}
MAX_TEXT = 8000
RULE_KEY = "in_ad_preapproval_rule"


class AdApprovalError(ValueError):
    """This cannot be done, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN" and bool(licences.held(firm))


def text_hash(text: str | None) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


async def _subs(session: AsyncSession, firm_id: uuid.UUID) -> list[AdSubmission]:
    return list((await session.execute(select(AdSubmission).where(AdSubmission.firm_id == firm_id).order_by(AdSubmission.created_at, AdSubmission.id))).scalars().all())


async def _decisions(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, AdDecision]:
    return {d.submission_id: d for d in (await session.execute(select(AdDecision).where(AdDecision.firm_id == firm_id).order_by(AdDecision.created_at))).scalars().all()}


async def prepare_submit(session: AsyncSession, firm: Firm, role, *, title: str | None = None, text: str | None = None, channel: str | None = None,
                         supersedes_id=None) -> dict:
    if not applies(firm):
        raise AdApprovalError("Advertisement pre-approval is for a firm that holds an Indian licence.")
    if role not in SUBMITTERS:
        raise AdApprovalError("Staff submit marketing copy for approval.")
    t = " ".join((title or "").split())
    if len(t) < 3:
        raise AdApprovalError("Give the piece a title.")
    body = (text or "").strip()
    if len(body) < 10:
        raise AdApprovalError("Give the copy as it will be used.")
    if len(body) > MAX_TEXT:
        raise AdApprovalError(f"The copy is over {MAX_TEXT:,} characters: submit it in pieces, each approved as it will be used.")
    ch = (channel or "").strip().lower()
    if ch not in CHANNELS:
        raise AdApprovalError(f"The channel is one of {', '.join(CHANNELS)}.")
    h = text_hash(body)
    subs, decisions = await _subs(session, firm.id), await _decisions(session, firm.id)
    for s in subs:
        if s.text_hash == h and (s.id not in decisions or decisions[s.id].decision == "approved"):
            raise AdApprovalError("This wording is already " + ("approved" if s.id in decisions else "waiting for compliance") + f" ({s.title}): cite it and do not submit it again.")
    prior = None
    if supersedes_id not in (None, ""):
        try:
            prior = next((s for s in subs if s.id == (supersedes_id if isinstance(supersedes_id, uuid.UUID) else uuid.UUID(str(supersedes_id)))), None)
        except ValueError:
            prior = None
        if prior is None:
            raise AdApprovalError("The submission this follows is not on file.")
    findings = advertising.check(body, "marketing", today=firm_time.today(firm))
    return {"title": t, "text": body, "channel": ch, "hash": h, "findings": findings, "prior": prior}


async def submit(session: AsyncSession, firm: Firm, user: User, *, by: str | None = None, **terms) -> dict:
    p = await prepare_submit(session, firm, UserRole(user.role), **terms)
    f = p["findings"].get("findings", [])
    row = AdSubmission(created_at=utcnow(), firm_id=firm.id, title=p["title"], channel=p["channel"], body=p["text"], text_hash=p["hash"], submitted_by_id=user.id,
                       submitted_by=by or user.email, findings_blocks=sum(1 for x in f if x["severity"] == "block"), findings_flags=sum(1 for x in f if x["severity"] == "flag"),
                       supersedes_id=p["prior"].id if p["prior"] else None)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="ad_submitted", actor=row.submitted_by, content={
        "submission_id": str(row.id), "title": row.title, "channel": row.channel, "text_hash": row.text_hash, "blocks": row.findings_blocks, "flags": row.findings_flags,
        "supersedes": str(row.supersedes_id) if row.supersedes_id else None})
    return await overview(session, firm, focus=row.id)


async def prepare_decision(session: AsyncSession, firm: Firm, user: User, *, submission_id=None, decision: str | None = None, reason: str | None = None) -> dict:
    if not applies(firm):
        raise AdApprovalError("Advertisement pre-approval is for a firm that holds an Indian licence.")
    if UserRole(user.role) not in DECIDERS:
        raise AdApprovalError("Compliance approves or rejects marketing copy, with a reason.")
    try:
        sid = submission_id if isinstance(submission_id, uuid.UUID) else uuid.UUID(str(submission_id))
    except ValueError:
        raise AdApprovalError("Give the submission from the list.")
    sub = await session.get(AdSubmission, sid)
    if sub is None or sub.firm_id != firm.id:
        raise AdApprovalError("Submission not found.")
    if sub.submitted_by_id == user.id:
        raise AdApprovalError("The person who submitted a piece does not decide it: another person in compliance does.")
    d = (decision or "").strip().lower()
    if d not in ("approved", "rejected"):
        raise AdApprovalError("The decision is approved or rejected.")
    if len((reason or "").strip()) < 5:
        raise AdApprovalError("Give the reason for the decision.")
    if sid in await _decisions(session, firm.id):
        raise AdApprovalError("This submission is already decided: a changed text is a new submission.")
    findings = advertising.check(sub.body, "marketing", today=firm_time.today(firm))
    return {"submission": sub, "decision": d, "reason": reason.strip(), "findings": findings}


async def decide(session: AsyncSession, firm: Firm, user: User, *, by: str | None = None, **terms) -> dict:
    p = await prepare_decision(session, firm, user, **terms)
    sub = p["submission"]
    row = AdDecision(created_at=utcnow(), firm_id=firm.id, submission_id=sub.id, decision=p["decision"], reason=p["reason"], text_hash=sub.text_hash,
                     decided_by_id=user.id, decided_by=by or user.email)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="ad_decided", actor=row.decided_by, content={
        "approval_id": str(row.id), "submission_id": str(sub.id), "decision": row.decision, "reason": row.reason, "text_hash": row.text_hash, "title": sub.title})
    return await overview(session, firm, focus=sub.id)


async def verify_citation(session: AsyncSession, firm: Firm, approval_id, text: str | None = None) -> dict:
    """Whether an approval id, cited for a text, stands: it exists, is an approval, and the text is the approved wording (when a text is given)."""
    if not applies(firm):
        return {"applies": False, "state": "not_applicable"}
    try:
        aid = approval_id if isinstance(approval_id, uuid.UUID) else uuid.UUID(str(approval_id))
    except ValueError:
        return {"applies": True, "state": "not_found", "said": "That is not an approval id."}
    d = await session.get(AdDecision, aid)
    if d is None or d.firm_id != firm.id:
        return {"applies": True, "state": "not_found", "said": "No approval with that id is on file here."}
    sub = await session.get(AdSubmission, d.submission_id)
    if d.decision != "approved":
        return {"applies": True, "state": "rejected", "approval_id": str(d.id), "title": sub.title if sub else None,
                "said": f"That decision is a rejection: {d.reason}"}
    if text is not None and text_hash(text) != d.text_hash:
        return {"applies": True, "state": "text_changed", "approval_id": str(d.id), "title": sub.title if sub else None,
                "said": "The text differs from the wording that was approved: a changed text needs a new submission."}
    return {"applies": True, "state": "approved", "approval_id": str(d.id), "title": sub.title if sub else None, "approved_by": d.decided_by,
            "approved_on": firm_time.today(firm, d.created_at).isoformat(), "text_hash": d.text_hash,
            "said": "This is the wording compliance approved." if text is not None else "An approval is on file for this id; give the text to check it is the approved wording."}


async def overview(session: AsyncSession, firm: Firm, *, focus: uuid.UUID | None = None, limit: int = 40) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "submissions": []}
    subs, decisions = await _subs(session, firm.id), await _decisions(session, firm.id)
    rows = []
    for s in reversed(subs):
        d = decisions.get(s.id)
        state = "pending" if d is None else d.decision
        rows.append({"id": str(s.id), "title": s.title, "channel": s.channel, "channel_label": CHANNELS.get(s.channel), "state": state,
                     "state_label": {"pending": "Waiting for compliance", "approved": "Approved", "rejected": "Rejected"}[state],
                     "submitted_by": s.submitted_by, "submitted_on": firm_time.today(firm, s.created_at).isoformat(), "blocks": s.findings_blocks, "flags": s.findings_flags,
                     "text_hash": s.text_hash[:12], "approval_id": str(d.id) if d and d.decision == "approved" else None,
                     "decided_by": d.decided_by if d else None, "reason": d.reason if d else None, "follows": str(s.supersedes_id) if s.supersedes_id else None,
                     "text": s.body if (focus == s.id or d is None) else None})
    return {"applies": True, "as_of": today.isoformat(), "submissions": rows[:limit], "pending": sum(1 for r in rows if r["state"] == "pending"),
            "approved": sum(1 for r in rows if r["state"] == "approved"), "rejected": sum(1 for r in rows if r["state"] == "rejected"),
            "rule": law_figures.cited([RULE_KEY], today)[0], "channels": CHANNELS,
            "said": "An approval is for exactly the wording submitted and carries its id; a changed text is a new submission. It does not say the firm is compliant."}
