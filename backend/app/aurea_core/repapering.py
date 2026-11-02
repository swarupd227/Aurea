"""Bulk repapering: moving many clients to new paper at once (L200-1 §2).

A campaign is one change for a set of households: following a recruited adviser, converting a program, moving
custodian, a change of control, a fee change, or an amendment. What consent it needs depends on the change. Where
the client's silence may be taken as consent after a notice period (a custodian conversion under FINRA Rule 2510,
a change of control or a change in the client's favour where the agreement allows consent by notice), a household
that did not object by the effective date is deemed to consent; one that objected, or whose notice came back, is
not. Where a signature is needed (following an adviser, a new program, a fee increase), only a signed answer counts
and silence is no. The table is the platform's until the firm sets its own, and the view says which.

Every answer is recorded by a person from its evidence. Converting the accounts themselves is the custodian's work,
outside this; the campaign says which households are ready and which are not, and why.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.models.graph import Household
from app.models.repapering import RepaperingAnswer, RepaperingCampaign
from app.models.tenant import Firm
from app.provenance import ledger

CHANGES = {
    "custodian_conversion": {"label": "Custodian conversion", "consent": "negative", "notice_days": 30},
    "change_of_control": {"label": "Change of control", "consent": "negative", "notice_days": 30},
    "favourable_amendment": {"label": "Amendment in the client's favour", "consent": "negative", "notice_days": 30},
    "adviser_recruitment": {"label": "Following a recruited adviser", "consent": "affirmative", "notice_days": None},
    "program_conversion": {"label": "Program conversion", "consent": "affirmative", "notice_days": None},
    "fee_increase": {"label": "Fee increase", "consent": "affirmative", "notice_days": None},
}
ANSWERS = ("consented", "objected", "bounced")


class RepaperingError(ValueError):
    """This cannot be recorded, with the reason."""


SIGNATURE_ALWAYS = {"adviser_recruitment", "program_conversion"}   # nobody is moved to a new firm or program by silence


def policy(firm: Firm) -> dict:
    own = (firm.settings or {}).get("repapering") or {}
    out = {k: {**v, **(own.get(k) or {}), "set_by_firm": k in own} for k, v in CHANGES.items()}
    for k in SIGNATURE_ALWAYS:
        out[k].update({"consent": "affirmative", "notice_days": None})
    return out


def standing(campaign: RepaperingCampaign, steps: list, today: date) -> dict:
    """Where one household stands on the campaign."""
    last = steps[-1] if steps else None
    effective = campaign.sent_on + timedelta(days=campaign.notice_days) if campaign.sent_on and campaign.notice_days else None
    if last and last["answer"] == "objected":
        return {"state": "objected", "ready": False}
    if last and last["answer"] == "bounced":
        return {"state": "notice_bounced", "ready": False}
    if last and last["answer"] == "consented":
        return {"state": "consented", "ready": True}
    if campaign.consent == "negative":
        if not campaign.sent_on:
            return {"state": "not_sent", "ready": False}
        if effective and today >= effective:
            return {"state": "deemed_consent", "ready": True}
        return {"state": "in_notice_period", "ready": False}
    return {"state": "awaiting_signature" if campaign.sent_on else "not_sent", "ready": False}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, campaign_id: uuid.UUID | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    q = select(RepaperingCampaign).where(RepaperingCampaign.firm_id == firm_id)
    if campaign_id is not None:
        q = q.where(RepaperingCampaign.id == campaign_id)
    campaigns = (await session.execute(q)).scalars().all()
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    out = []
    for c in campaigns:
        answers = (await session.execute(select(RepaperingAnswer).where(RepaperingAnswer.campaign_id == c.id))).scalars().all()
        rows = [{"household_id": str(a.household_id), "household": names.get(a.household_id), **standing(c, a.steps or [], today), "steps": a.steps or []}
                for a in answers]
        counts: dict = {}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        effective = c.sent_on + timedelta(days=c.notice_days) if c.sent_on and c.notice_days else None
        out.append({"id": str(c.id), "name": c.name, "change_type": c.change_type, "label": CHANGES.get(c.change_type, {}).get("label", c.change_type),
                    "consent": c.consent, "notice_days": c.notice_days, "document": c.document, "sent_on": c.sent_on.isoformat() if c.sent_on else None,
                    "effective_on": effective.isoformat() if effective else None, "households": sorted(rows, key=lambda r: (r["ready"], r["household"] or "")),
                    "counts": counts, "ready": sum(1 for r in rows if r["ready"]), "total": len(rows)})
    return {"campaigns": out, "policy": policy(firm), "as_of": today.isoformat()}


async def prepare_campaign(session: AsyncSession, firm_id: uuid.UUID, *, name: str | None, change_type: str | None, document: str | None,
                           household_ids: list[uuid.UUID] | None) -> dict:
    firm = await session.get(Firm, firm_id)
    change_type = (change_type or "").strip().lower()
    rules = policy(firm)
    if change_type not in rules:
        raise RepaperingError(f"The change is one of {', '.join(rules)}.")
    if len((name or "").strip()) < 3:
        raise RepaperingError("Name the campaign.")
    if len((document or "").strip()) < 5:
        raise RepaperingError("Name the notice or agreement being sent, as filed.")
    all_households = (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()
    if household_ids:
        chosen = [h for h in all_households if h.id in set(household_ids)]
        if len(chosen) != len(set(household_ids)):
            raise RepaperingError("One or more households are not this firm's.")
    else:
        chosen = list(all_households)
    if not chosen:
        raise RepaperingError("There are no households to repaper.")
    rule = rules[change_type]
    return {"name": name.strip(), "change_type": change_type, "rule": rule, "document": document.strip(), "households": chosen}


async def start(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_campaign(session, firm_id, **terms)
    c = RepaperingCampaign(firm_id=firm_id, name=r["name"], change_type=r["change_type"], consent=r["rule"]["consent"], notice_days=r["rule"]["notice_days"],
                           document=r["document"], created_by=by)
    session.add(c)
    await session.flush()
    for h in r["households"]:
        session.add(RepaperingAnswer(firm_id=firm_id, campaign_id=c.id, household_id=h.id, steps=[]))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="repapering_started", actor=by,
                              content={"campaign_id": str(c.id), "change_type": c.change_type, "consent": c.consent, "households": len(r["households"])})
    return await for_firm(session, firm_id)


async def prepare_step(session: AsyncSession, firm_id: uuid.UUID, *, campaign_id: uuid.UUID, action: str | None, evidence: str | None, on: date | None,
                       household_ids: list[uuid.UUID] | None = None) -> dict:
    c = await session.get(RepaperingCampaign, campaign_id)
    if c is None or c.firm_id != firm_id:
        raise RepaperingError("Campaign not found.")
    today = firm_time.today(await session.get(Firm, firm_id))
    day = on or today
    if day > today:
        raise RepaperingError("Nothing is recorded ahead of the day it happened.")
    if len((evidence or "").strip()) < 5:
        raise RepaperingError("Say where it comes from: the mailing record, the signed agreement, the client's letter, the returned envelope.")
    action = (action or "").strip().lower()
    if action == "sent":
        if c.sent_on:
            raise RepaperingError(f"This campaign was sent on {c.sent_on.isoformat()}.")
        return {"campaign": c, "action": action, "on": day, "evidence": evidence.strip(), "answers": []}
    if action not in ANSWERS:
        raise RepaperingError(f"The action is sent, or an answer: {', '.join(ANSWERS)}.")
    if not c.sent_on:
        raise RepaperingError("Record the campaign sent before any answer.")
    if day < c.sent_on:
        raise RepaperingError("An answer comes after the notice was sent.")
    if not household_ids:
        raise RepaperingError("Name the households this answer is for.")
    rows = (await session.execute(select(RepaperingAnswer).where(RepaperingAnswer.campaign_id == c.id, RepaperingAnswer.household_id.in_(household_ids)))).scalars().all()
    if len(rows) != len(set(household_ids)):
        raise RepaperingError("One or more of those households are not in this campaign.")
    return {"campaign": c, "action": action, "on": day, "evidence": evidence.strip(), "answers": list(rows)}


async def record_step(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_step(session, firm_id, **terms)
    c = r["campaign"]
    if r["action"] == "sent":
        c.sent_on = r["on"]
    for a in r["answers"]:
        a.steps = [*(a.steps or []), {"answer": r["action"], "on": r["on"].isoformat(), "evidence": r["evidence"], "by": by}]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="repapering_step", actor=by,
                              content={"campaign_id": str(c.id), "action": r["action"], "households": len(r["answers"])})
    return await for_firm(session, firm_id)
