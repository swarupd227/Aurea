"""Health cover and a savings policy's values, India (B-397): the Protection page's second section.

Reading is for the people who serve a client or supervise that service; no client route reads it. Recording is for
advisers, paraplanners and operations, and the firm's figure is a branch leader's or an administrator's. Every write goes
through the module that the conversation's tool uses, which checks the role again.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import counsel_review, health_cover, policy_values
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/health-cover", tags=["health-cover"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


class Member(BaseModel):
    person_id: str | None = None
    name: str = ""
    relationship: str = ""


class HealthPolicyIn(BaseModel):
    household_id: uuid.UUID
    kind: str
    provider: str
    sum_insured: float
    members: list[Member] | str
    source_document: str
    deductible: float | None = None
    policy_number: str | None = None
    room_rent_cap: str | None = None
    co_payment_percent: float | None = None
    co_payment_terms: str | None = None
    sub_limits: str | None = None
    waiting_periods: str | None = None
    covered_since: str | None = None
    renewal_on: str | None = None
    annual_premium: float | None = None
    replaces_id: uuid.UUID | None = None


class EndIn(BaseModel):
    reason: str


class FigureIn(BaseModel):
    family_cover: float
    source_document: str
    parent_cover: float | None = None


class StatementIn(BaseModel):
    product: str
    statement_date: str
    premiums_paid: float
    source_document: str
    policy_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None
    provider: str | None = None
    sum_assured: float | None = None
    policy_number: str | None = None
    commenced_on: str | None = None
    annual_premium: float | None = None
    premium_years_left: int | None = None
    surrender_value: float | None = None
    paid_up_value: float | None = None
    fund_value: float | None = None
    maturity_on: str | None = None
    maturity_value: float | None = None
    maturity_basis: str | None = None
    charges_stated: str | None = None


def _rights(user: User) -> dict:
    return {"can_record": str(user.role) in health_cover.RECORDERS, "can_set_figure": str(user.role) in health_cover.FIGURE_SETTERS}


def _refused(exc: Exception) -> HTTPException:
    text = str(exc)
    return HTTPException(status_code=403 if "is recorded by" in text or "is set by" in text else 422, detail=text)


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    values = await policy_values.for_firm(db, firm.id)
    return {"health": await health_cover.for_firm(db, firm.id), "values": await counsel_review.annotate(db, firm, values),
            **_rights(user)}


@router.post("/policies")
async def record_policy(body: HealthPolicyIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    try:
        return await health_cover.record_policy(db, firm.id, recorded_by=user.email, role=str(user.role), **body.model_dump())
    except health_cover.HealthCoverError as exc:
        raise _refused(exc)


@router.post("/policies/{policy_id}/end")
async def end_policy(policy_id: uuid.UUID, body: EndIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                     db: AsyncSession = Depends(get_db)):
    try:
        return await health_cover.end_policy(db, firm.id, role=str(user.role), policy_id=policy_id, reason=body.reason, by=user.email)
    except health_cover.HealthCoverError as exc:
        raise _refused(exc)


@router.post("/figure")
async def set_figure(body: FigureIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    try:
        return await health_cover.set_figure(db, firm.id, role=str(user.role), set_by=user.email, **body.model_dump())
    except health_cover.HealthCoverError as exc:
        raise _refused(exc)


@router.post("/statements")
async def record_statement(body: StatementIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                           db: AsyncSession = Depends(get_db)):
    try:
        return await policy_values.record_statement(db, firm.id, recorded_by=user.email, role=str(user.role), **body.model_dump())
    except policy_values.PolicyValueError as exc:
        raise _refused(exc)
