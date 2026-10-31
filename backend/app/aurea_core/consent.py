"""Per-trade client consent: the gate between an adviser's approval and an order.

An adviser approves what they advise. On a track that needs the client's consent, that approval
places nothing: it raises a consent request bound to the exact orders, and the orders wait. A person
records the client's answer from the evidence, by email, recorded call or signed document, which
keeps clients without state-changing tools (CLAUDE.md rule 8). The record says how the client
answered, and who recorded it.

runtime.client_response is the only thing that moves one on, under the recommendation's lock, so an
answer executes once. drift_rebalancing.act asks `blocked` again as the last line before an order.

A demonstration firm may record a "simulated" answer. It is stamped as simulated everywhere it
appears, and no other firm can record one.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import licences
from app.core import decision_rights
from app.core.db import utcnow
from app.models.consent import CONSENT_CHANNELS, TradeConsent
from app.models.graph import Mandate
from app.models.governance import Recommendation


class ConsentError(Exception):
    """This answer cannot be recorded. Carries a reason fit to show the user."""


def order_lines(order_set: list[dict]) -> list[dict]:
    return [{"side": o.get("side"), "symbol": o.get("symbol"), "instrument_id": str(o.get("instrument_id")),
             "account_id": str(o.get("account_id")), "quantity": o.get("quantity"), "est_value": o.get("est_value")}
            for o in order_set or []]


def digest(order_set: list[dict]) -> str:
    """Instrument, side and size: a consent covers these orders and no others."""
    lines = sorted((str(o.get("instrument_id")), str(o.get("side")), str(o.get("account_id")), float(o.get("quantity") or 0))
                   for o in order_set or [])
    return hashlib.sha256(json.dumps(lines).encode()).hexdigest()


def _final(rec: Recommendation) -> dict:
    return rec.modified_payload or rec.payload or {}


def stamp(rec: Recommendation, state: dict) -> None:
    """Put the consent state on the proposal, in the payload the screen reads."""
    rec.payload = {**(rec.payload or {}), "client_consent": state}
    if rec.modified_payload:
        rec.modified_payload = {**rec.modified_payload, "client_consent": state}


def _state(row: TradeConsent) -> dict:
    return {"status": row.status, "licence": row.licence, "channel": row.channel,
            "evidence_ref": row.evidence_ref, "simulated": row.simulated, "recorded_by": row.recorded_by,
            "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
            "orders": row.orders}


async def _track(session: AsyncSession, firm, rec: Recommendation):
    if not licences.held(firm):
        return None, None
    if not decision_rights.moves_the_book(rec.agent_key) or rec.subject_type != "mandate" or not rec.subject_id:
        return None, None
    mandate = await session.get(Mandate, rec.subject_id)
    return licences.track_for(firm, mandate), mandate


async def hold(session: AsyncSession, *, firm, rec: Recommendation, requested_by: str) -> dict | None:
    """Called at approval. None lets the orders go; anything else means they wait."""
    track, mandate = await _track(session, firm, rec)
    if track is None:
        return None
    if not track.may_execute:
        state = {"status": "not_permitted", "licence": track.key}
        stamp(rec, state)
        rec.payload = {**(rec.payload or {}), "execution_result": {
            "executed": False, "note": f"{track.name} may not execute a trade, so nothing was placed."}}
        return state
    if not track.per_trade_consent:
        return None
    orders = _final(rec).get("order_set") or []
    row = TradeConsent(firm_id=firm.id, recommendation_id=rec.id, mandate_id=mandate.id if mandate else None,
                       licence=track.key, status="awaiting", orders=order_lines(orders),
                       order_digest=digest(orders), requested_by=requested_by)
    session.add(row)
    await session.flush()
    state = _state(row)
    stamp(rec, state)
    return state


async def blocked(session: AsyncSession, *, firm, rec: Recommendation) -> str | None:
    """The last line before an order. Why nothing may be placed, or None."""
    track, _ = await _track(session, firm, rec)
    if track is None or (track.may_execute and not track.per_trade_consent):
        return None
    if not track.may_execute:
        return f"{track.name} may not execute a trade, so nothing was placed."
    row = (await session.execute(
        select(TradeConsent).where(TradeConsent.recommendation_id == rec.id))).scalar_one_or_none()
    if row is None or row.status != "given":
        return f"Nothing was placed: {track.name} needs the client's consent to these trades, and none is on record."
    if row.order_digest != digest(_final(rec).get("order_set") or []):
        return "Nothing was placed: the orders have changed since the client consented to them."
    return None


async def waiting_on_mandate(session: AsyncSession, mandate_id) -> TradeConsent | None:
    return (await session.execute(
        select(TradeConsent).where(TradeConsent.mandate_id == mandate_id, TradeConsent.status == "awaiting")
    )).scalars().first()


def check_answer(firm, *, channel: str, evidence_ref: str | None) -> bool:
    """Validate how the answer was given. Returns whether it is simulated."""
    if channel not in CONSENT_CHANNELS:
        raise ConsentError(f"channel must be one of {', '.join(CONSENT_CHANNELS)}.")
    simulated = channel == "simulated"
    if simulated and not (firm.settings or {}).get("demonstration"):
        raise ConsentError("A simulated answer is for demonstration firms. Record the client's real answer.")
    if not simulated and not (evidence_ref or "").strip():
        raise ConsentError("Say where the client's answer is on file: the email, call recording or document.")
    return simulated


async def record(session: AsyncSession, *, firm, rec: Recommendation, given: bool, channel: str,
                 evidence_ref: str | None, recorded_by: str, note: str | None) -> TradeConsent:
    """Write the answer. The caller holds the recommendation's lock (runtime.client_response)."""
    simulated = check_answer(firm, channel=channel, evidence_ref=evidence_ref)
    row = (await session.execute(
        select(TradeConsent).where(TradeConsent.recommendation_id == rec.id).with_for_update()
    )).scalar_one_or_none()
    if row is None:
        raise ConsentError("This proposal is not waiting for a client's consent.")
    if row.status != "awaiting":
        raise ConsentError(f"The client's answer is already on record: {row.status}.")
    if row.order_digest != digest(_final(rec).get("order_set") or []):
        raise ConsentError("The orders have changed since consent was requested. Run the agent again.")
    row.status = "given" if given else "declined"
    row.channel = channel
    row.evidence_ref = (evidence_ref or "").strip() or None
    row.simulated = simulated
    row.recorded_by = recorded_by
    row.recorded_at = utcnow()
    row.note = note
    await session.flush()
    stamp(rec, _state(row))
    return row


def _view(row: TradeConsent, rec: Recommendation | None) -> dict:
    return {"id": str(row.id), "recommendation_id": str(row.recommendation_id),
            "title": rec.title if rec else None, "subject": rec.subject_label if rec else None,
            "licence": row.licence, "requested_by": row.requested_by,
            "requested_at": row.created_at.isoformat() if row.created_at else None, **_state(row)}


async def _rows(session: AsyncSession, firm, *conditions, limit: int) -> list[dict]:
    rows = (await session.execute(
        select(TradeConsent, Recommendation).join(Recommendation, Recommendation.id == TradeConsent.recommendation_id)
        .where(TradeConsent.firm_id == firm.id, *conditions).order_by(TradeConsent.created_at.desc()).limit(limit)
    )).all()
    return [_view(row, rec) for row, rec in rows]


async def awaiting(session: AsyncSession, firm) -> list[dict]:
    return await _rows(session, firm, TradeConsent.status == "awaiting", limit=50)


async def recent(session: AsyncSession, firm) -> list[dict]:
    return await _rows(session, firm, TradeConsent.status != "awaiting", limit=20)
