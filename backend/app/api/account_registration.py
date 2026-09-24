"""Account registration type + beneficiaries API (L200-1 §4)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import registration as engine
from app.core.db import get_db
from app.core.decision_rights import STAFF
from app.core.security import UserRole, require_roles
from app.models.enums import RegistrationType
from app.models.graph import Account, AccountBeneficiary
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/accounts", tags=["account-registration"])
StaffDep = Depends(require_roles(*STAFF))
ServicingDep = Depends(require_roles(
    UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE, UserRole.ADMIN,
))

_REGISTRATION_TYPES = {t.value for t in RegistrationType}
_ELECTION_METHODS = {"10_year_rule", "life_expectancy"}


async def _get_account(account_id: uuid.UUID, firm: Firm, db: AsyncSession) -> Account:
    account = (await db.execute(
        select(Account).where(Account.id == account_id, Account.firm_id == firm.id)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    return account


def _beneficiary_out(b: AccountBeneficiary) -> dict:
    return {
        "id": str(b.id), "account_id": str(b.account_id), "beneficiary_name": b.beneficiary_name,
        "relationship_to_owner": b.relationship_to_owner, "designation_class": b.designation_class,
        "percentage": float(b.percentage), "per_stirpes": b.per_stirpes,
        "date_designated": b.date_designated.isoformat() if b.date_designated else None,
        "notes": b.notes,
    }


@router.get("/{account_id}/registration")
async def get_registration(account_id: uuid.UUID, user: User = StaffDep, firm: Firm = Depends(current_firm),
                            db: AsyncSession = Depends(get_db)):
    account = await _get_account(account_id, firm, db)
    beneficiaries = (await db.execute(
        select(AccountBeneficiary).where(AccountBeneficiary.account_id == account.id)
    )).scalars().all()
    rmd = await engine.rmd_for_account(db, account)
    return {
        "account_id": str(account.id), "account_name": account.name,
        "registration_type": account.registration_type,
        "original_owner_death_date": account.original_owner_death_date.isoformat()
            if account.original_owner_death_date else None,
        "rmd_election_method": account.rmd_election_method,
        "rmd": rmd,
        "beneficiaries": [_beneficiary_out(b) for b in beneficiaries],
    }


class RegistrationUpdate(BaseModel):
    registration_type: str | None = None
    original_owner_death_date: date | None = None
    rmd_election_method: str | None = None


@router.patch("/{account_id}/registration")
async def update_registration(account_id: uuid.UUID, body: RegistrationUpdate, user: User = ServicingDep,
                               firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    account = await _get_account(account_id, firm, db)
    if body.registration_type is not None:
        if body.registration_type not in _REGISTRATION_TYPES:
            raise HTTPException(status_code=422, detail=f"registration_type must be one of {sorted(_REGISTRATION_TYPES)}.")
        account.registration_type = body.registration_type
    if body.original_owner_death_date is not None:
        account.original_owner_death_date = body.original_owner_death_date
    if body.rmd_election_method is not None:
        if body.rmd_election_method not in _ELECTION_METHODS:
            raise HTTPException(status_code=422, detail=f"rmd_election_method must be one of {sorted(_ELECTION_METHODS)}.")
        account.rmd_election_method = body.rmd_election_method
    await db.flush()
    return {"account_id": str(account.id), "registration_type": account.registration_type}


class BeneficiaryIn(BaseModel):
    beneficiary_name: str
    relationship_to_owner: str | None = None
    designation_class: str = "primary"
    percentage: float
    per_stirpes: bool = False
    notes: str | None = None


@router.post("/{account_id}/beneficiaries")
async def add_beneficiary(account_id: uuid.UUID, body: BeneficiaryIn, user: User = ServicingDep,
                           firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    account = await _get_account(account_id, firm, db)
    if body.designation_class not in ("primary", "contingent"):
        raise HTTPException(status_code=422, detail="designation_class must be 'primary' or 'contingent'.")
    from app.core.db import utcnow
    row = AccountBeneficiary(
        firm_id=firm.id, account_id=account.id, date_designated=utcnow().date(), **body.model_dump()
    )
    db.add(row)
    await db.flush()
    return _beneficiary_out(row)


class BeneficiaryUpdate(BaseModel):
    beneficiary_name: str | None = None
    relationship_to_owner: str | None = None
    designation_class: str | None = None
    percentage: float | None = None
    per_stirpes: bool | None = None
    notes: str | None = None


@router.patch("/{account_id}/beneficiaries/{beneficiary_id}")
async def update_beneficiary(account_id: uuid.UUID, beneficiary_id: uuid.UUID, body: BeneficiaryUpdate,
                              user: User = ServicingDep, firm: Firm = Depends(current_firm),
                              db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(AccountBeneficiary).where(
            AccountBeneficiary.id == beneficiary_id, AccountBeneficiary.account_id == account_id,
            AccountBeneficiary.firm_id == firm.id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Beneficiary not found.")
    for field in ("beneficiary_name", "relationship_to_owner", "designation_class", "percentage",
                  "per_stirpes", "notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    await db.flush()
    return _beneficiary_out(row)


@router.delete("/{account_id}/beneficiaries/{beneficiary_id}", status_code=204)
async def delete_beneficiary(account_id: uuid.UUID, beneficiary_id: uuid.UUID, user: User = ServicingDep,
                              firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(AccountBeneficiary).where(
            AccountBeneficiary.id == beneficiary_id, AccountBeneficiary.account_id == account_id,
            AccountBeneficiary.firm_id == firm.id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Beneficiary not found.")
    await db.delete(row)
    await db.flush()


registration_audit_router = APIRouter(prefix="/api/registration", tags=["account-registration"])


@registration_audit_router.get("/beneficiary-audit")
async def get_beneficiary_audit(household_id: uuid.UUID | None = None, user: User = StaffDep,
                                 firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await engine.beneficiary_audit(db, firm.id, household_id=household_id)
