"""Departures and estates: a client leaving, or dying, and what moved out of their accounts.

Reading is for the people who serve a client or supervise that service. An adviser, paraplanner or operations
records that it has happened; only operations records what a custodian moved, because that writes the book. The
agent-facing tools are the same actions behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import exits
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/exits", tags=["exits"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))
RECORDERS = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS)
MOVERS = (UserRole.OPERATIONS,)
CLOSERS = (UserRole.OPERATIONS, UserRole.BRANCH_LEADER)
WITHDRAWERS = (*RECORDERS, UserRole.BRANCH_LEADER)


class DepartureIn(BaseModel):
    household_id: uuid.UUID
    reason: str = ""
    source_document: str = ""
    notice_date: date | None = None
    receiving_institution: str | None = None
    mandate_id: uuid.UUID | None = None


class DeathIn(BaseModel):
    person_id: uuid.UUID
    date_of_death: date | None = None
    source_document: str = ""
    executor: str | None = None


class ExecutorIn(BaseModel):
    executor: str = ""
    source_document: str = ""


class TransferIn(BaseModel):
    account_id: uuid.UUID
    transfer_date: date | None = None
    source_document: str = ""
    to_party: str | None = None
    share_pct: float | None = None
    to_account_id: uuid.UUID | None = None


class WithdrawIn(BaseModel):
    reason: str = ""


def _rights(user: User) -> dict:
    return {"can_record": user.role in RECORDERS, "can_move": user.role in MOVERS, "can_close": user.role in CLOSERS,
            "can_withdraw": user.role in WITHDRAWERS}


def _may(user: User, roles: tuple) -> None:
    if user.role not in roles:
        raise HTTPException(status_code=403, detail="Your role does not record this.")


def _refused(exc: exits.ExitError) -> HTTPException:
    return HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc))


@router.get("")
async def read_exits(household: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                     session: AsyncSession = Depends(get_db)) -> dict:
    result = await exits.for_household(session, household, firm_id=firm.id) if household else await exits.for_firm(session, firm.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Household not found.")
    return {**result, **_rights(user)}


@router.post("/departure")
async def start_departure(body: DepartureIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                          session: AsyncSession = Depends(get_db)) -> dict:
    _may(user, RECORDERS)
    try:
        row = await exits.start_departure(session, body.household_id, firm_id=firm.id, by=user.email, notice_date=body.notice_date, reason=body.reason,
                                          source_document=body.source_document, receiving_institution=body.receiving_institution, mandate_id=body.mandate_id)
    except exits.ExitError as exc:
        raise _refused(exc)
    return await exits.view_of(session, row.id, firm_id=firm.id)


@router.post("/death")
async def record_death(body: DeathIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                       session: AsyncSession = Depends(get_db)) -> dict:
    _may(user, RECORDERS)
    try:
        row = await exits.record_death(session, body.person_id, firm_id=firm.id, by=user.email, date_of_death=body.date_of_death,
                                       source_document=body.source_document, executor=body.executor)
    except exits.ExitError as exc:
        raise _refused(exc)
    return await exits.view_of(session, row.id, firm_id=firm.id)


@router.post("/{exit_id}/executor")
async def record_executor(exit_id: uuid.UUID, body: ExecutorIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                          session: AsyncSession = Depends(get_db)) -> dict:
    _may(user, RECORDERS)
    try:
        await exits.record_executor(session, exit_id, firm_id=firm.id, by=user.email, executor=body.executor, source_document=body.source_document)
    except exits.ExitError as exc:
        raise _refused(exc)
    return await exits.view_of(session, exit_id, firm_id=firm.id)


@router.post("/{exit_id}/transfer")
async def record_transfer(exit_id: uuid.UUID, body: TransferIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                          session: AsyncSession = Depends(get_db)) -> dict:
    _may(user, MOVERS)
    try:
        await exits.record_transfer(session, exit_id, body.account_id, firm_id=firm.id, by=user.email, to_party=body.to_party,
                                    transfer_date=body.transfer_date, source_document=body.source_document, to_account_id=body.to_account_id,
                                    share=body.share_pct / 100 if body.share_pct is not None else None)
    except exits.ExitError as exc:
        raise _refused(exc)
    return await exits.view_of(session, exit_id, firm_id=firm.id)


@router.post("/{exit_id}/close")
async def close(exit_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                session: AsyncSession = Depends(get_db)) -> dict:
    _may(user, CLOSERS)
    try:
        await exits.close(session, exit_id, firm_id=firm.id, by=user.email)
    except exits.ExitError as exc:
        raise _refused(exc)
    return await exits.view_of(session, exit_id, firm_id=firm.id)


@router.post("/{exit_id}/withdraw")
async def withdraw(exit_id: uuid.UUID, body: WithdrawIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                   session: AsyncSession = Depends(get_db)) -> dict:
    _may(user, WITHDRAWERS)
    try:
        await exits.cancel(session, exit_id, firm_id=firm.id, by=user.email, reason=body.reason)
    except exits.ExitError as exc:
        raise _refused(exc)
    return await exits.view_of(session, exit_id, firm_id=firm.id)
