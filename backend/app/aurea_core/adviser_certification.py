"""Each adviser's NISM certificate, and whether it stands today (L100 India edition sections 4.2 and 9.3, B-298).

An Investment Adviser must hold NISM Series X-A and X-B, and a mutual fund distributor V-A. The compliance calendar tracks the firm's filings and
says nothing of a person's certificate, so a lapse showed nowhere. Compliance records each certificate from the certificate itself (the holder,
its number as text, the day it was issued, the day it expires and its status as the certificate or NISM's record reports it), added and never
edited: a renewal is a new row for the same person and kind and the latest stands. The expiry is the certificate's own; nothing here works one out
from an issue day, because the validity period is for NISM to state (`law_figures.in_nism_validity_years` is shown for counsel and not used).

An adviser's state, per kind the firm's licences make them hold (X-A and X-B under the Investment Adviser licence, V-A under the distributor's):

  valid        stands today and expires more than 90 days on
  due soon     stands today and expires within 90 days
  expired      past its expiry, or the certificate's status is lapsed or revoked
  not on file  nothing recorded, which does not mean the person holds none

and a person's own state is the worst across their kinds. A firm with neither licence requires none, and says so.

Where it shows: the governance register on the Licences & SEBI page, the countdown (`india_deadlines`), one task for compliance when a certificate
is due soon or expired (`india_reminders`, once per person, kind and expiry), and a WARNING on a book-moving proposal and its Confirm card when
the household's recorded adviser, who would approve it, holds an expired certificate or none on file. It is a warning only: decision rights and
the pre-advice gate are separate controls, and a certificate on file or missing changes neither. It does not say the person is or is not qualified.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advisers, firm_time, law_figures, licences, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.identity import User
from app.models.india_batch_c import AdviserCertification
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.COMPLIANCE})
KINDS = {"nism_xa": "NISM Series X-A", "nism_xb": "NISM Series X-B", "nism_va": "NISM Series V-A"}
STATUSES = ("valid", "lapsed", "revoked")
DUE_WITHIN = 90
ORDER = {"expired": 0, "not_on_file": 1, "due_soon": 2, "valid": 3, "not_required": 4}
LABELS = {"expired": "Expired", "not_on_file": "Not on file", "due_soon": "Due within 90 days", "valid": "Valid", "not_required": "None required"}
CITATION = "NISM certification norms for investment advisers and distributors, as read in the L100 India edition (Nov 2026) sections 4.2 and 9.3; not re-read"
NOT_QUALIFIED = "What was recorded from the certificates. It does not say a person is or is not qualified, and not on file does not mean none is held."


class CertificationError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN" and bool(licences.held(firm))


def required_kinds(firm: Firm) -> list[str]:
    keys = {l.key for l in licences.held(firm)}
    out = []
    if "investment_adviser" in keys:
        out += ["nism_xa", "nism_xb"]
    if "mutual_fund_distributor" in keys:
        out.append("nism_va")
    return out


def state_of(row: AdviserCertification | None, today: date) -> str:
    if row is None:
        return "not_on_file"
    if row.status != "valid" or row.expires_on < today:
        return "expired"
    return "due_soon" if (row.expires_on - today).days <= DUE_WITHIN else "valid"


async def _rows(session: AsyncSession, firm_id: uuid.UUID) -> list[AdviserCertification]:
    return list((await session.execute(select(AdviserCertification).where(AdviserCertification.firm_id == firm_id)
                                       .order_by(AdviserCertification.issued_on, AdviserCertification.created_at, AdviserCertification.id))).scalars().all())


def _standing(rows: list[AdviserCertification]) -> dict[tuple[uuid.UUID, str], AdviserCertification]:
    out: dict[tuple[uuid.UUID, str], AdviserCertification] = {}
    for r in rows:
        out[(r.user_id, r.kind)] = r
    return out


async def _people(session: AsyncSession, firm_id: uuid.UUID) -> list[User]:
    return list((await session.execute(select(User).where(User.firm_id == firm_id, User.role == UserRole.ADVISER, User.is_active.is_(True))
                                       .order_by(User.full_name))).scalars().all())


def _item(person: User, kind: str, row: AdviserCertification | None, today: date) -> dict:
    s = state_of(row, today)
    return {"kind": kind, "label": KINDS[kind], "state": s, "state_label": LABELS[s], "number": row.certificate_number if row else None,
            "issued_on": row.issued_on.isoformat() if row else None, "expires_on": row.expires_on.isoformat() if row else None,
            "days": (row.expires_on - today).days if row else None, "status": row.status if row else None,
            "document": row.source_document if row else None}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "people": []}
    need = required_kinds(firm)
    standing = _standing(await _rows(session, firm.id))
    people = []
    for u in await _people(session, firm.id):
        items = [_item(u, k, standing.get((u.id, k)), today) for k in need]
        worst = min((i["state"] for i in items), key=lambda s: ORDER[s]) if items else "not_required"
        people.append({"user_id": str(u.id), "name": u.full_name, "state": worst, "state_label": LABELS[worst], "certificates": items,
                       "earlier": [{"kind": r.kind, "number": r.certificate_number, "expires_on": r.expires_on.isoformat()} for r in await _earlier(session, firm.id, u.id, standing)]})
    counts = {s: sum(1 for p in people if p["state"] == s) for s in ORDER}
    return {"applies": True, "as_of": today.isoformat(), "required": [KINDS[k] for k in need], "people": people, "counts": counts, "citation": CITATION,
            "validity": law_figures.cited(["in_nism_validity_years"], today)[0], "said": NOT_QUALIFIED,
            "warning_note": "An expired or missing certificate on the adviser who would approve a book-moving proposal is shown as a warning there. It does not stop the approval: "
                            "decision rights and the pre-advice gate are separate controls."}


async def _earlier(session: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID, standing: dict) -> list[AdviserCertification]:
    keep = {r.id for (uid, _), r in standing.items() if uid == user_id}
    return [r for r in await _rows(session, firm_id) if r.user_id == user_id and r.id not in keep]


async def prepare(session: AsyncSession, firm: Firm, role, *, user_id=None, kind: str | None = None, certificate_number: str | None = None, issued_on=None,
                  expires_on=None, status: str | None = "valid", document: str | None = None) -> dict:
    if not applies(firm):
        raise CertificationError("A NISM certificate is recorded for a firm that holds an Indian licence.")
    if role not in RECORDERS:
        raise CertificationError("Compliance records each adviser's certificate, from the certificate.")
    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except ValueError:
        raise CertificationError("Give the adviser from the firm's people.")
    person = await session.get(User, uid)
    if person is None or person.firm_id != firm.id or not person.is_active:
        raise CertificationError("That is not an active person at this firm.")
    k = (kind or "").strip().lower()
    if k not in KINDS:
        raise CertificationError(f"The certificate is one of {', '.join(KINDS)}.")
    number = " ".join((certificate_number or "").split())
    if len(number) < 3:
        raise CertificationError("Give the certificate's number as it prints it.")
    today = firm_time.today(firm)
    days = []
    for label, v in (("issue day", issued_on), ("expiry day", expires_on)):
        if v in (None, ""):
            raise CertificationError(f"Give the {label} as the certificate shows it, YYYY-MM-DD.")
        try:
            days.append(v if isinstance(v, date) else date.fromisoformat(str(v)))
        except ValueError:
            raise CertificationError(f"Give the {label} as YYYY-MM-DD.")
    issued, expires = days
    if issued > today:
        raise CertificationError("A certificate cannot be issued ahead.")
    if expires <= issued:
        raise CertificationError("The certificate expires after it is issued.")
    st = (status or "valid").strip().lower()
    if st not in STATUSES:
        raise CertificationError(f"The status is one of {', '.join(STATUSES)}, as the certificate or NISM's record reports it.")
    if len((document or "").strip()) < 5:
        raise CertificationError("Say where the certificate is filed.")
    before = _standing(await _rows(session, firm.id)).get((uid, k))
    if before is not None and issued < before.issued_on:
        raise CertificationError(f"The certificate issued {before.issued_on.isoformat()} is already on file: a later one stands over an earlier one.")
    if before is not None and before.certificate_number == number and before.expires_on == expires and before.status == st:
        raise CertificationError("That certificate is already on file as recorded.")
    return {"person": person, "kind": k, "number": number, "issued_on": issued, "expires_on": expires, "status": st, "document": document.strip(), "before": before,
            "state_after": state_of(AdviserCertification(status=st, expires_on=expires), today), "today": today}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = AdviserCertification(created_at=utcnow(), firm_id=firm.id, user_id=p["person"].id, kind=p["kind"], certificate_number=p["number"], issued_on=p["issued_on"],
                               expires_on=p["expires_on"], status=p["status"], source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="adviser_certification_recorded", actor=by, content={
        "user_id": str(row.user_id), "kind": row.kind, "number": row.certificate_number, "issued_on": row.issued_on.isoformat(),
        "expires_on": row.expires_on.isoformat(), "status": row.status, "document": row.source_document})
    return await overview(session, firm)


async def deadline_items(session: AsyncSession, firm: Firm) -> list[tuple[str, str, date | None, str, str | None]]:
    """(kind of item, label, expiry or None, state, detail) for the countdown: one per person and certificate, only where something is due soon, expired
    or not on file."""
    if not applies(firm):
        return []
    today = firm_time.today(firm)
    need = required_kinds(firm)
    standing = _standing(await _rows(session, firm.id))
    out = []
    for u in await _people(session, firm.id):
        for k in need:
            row = standing.get((u.id, k))
            s = state_of(row, today)
            if s == "valid":
                continue
            if row is None:
                out.append((u.full_name, f"{KINDS[k]} for {u.full_name}: not on file", None, "not_on_file", "not on file; recorded by compliance from the certificate"))
            else:
                when = f"expired {row.expires_on.isoformat()}" if s == "expired" else f"expires {row.expires_on.isoformat()}"
                out.append((u.full_name, f"{KINDS[k]} for {u.full_name}: {when}", row.expires_on, s, None))
    return out


async def warning_for_household(session: AsyncSession, firm: Firm, household_id: uuid.UUID | None) -> dict | None:
    """A warning when the household's recorded adviser, who would approve a book-moving proposal, holds an expired certificate or none on file.
    None where the firm requires none, the household has no adviser recorded, or the certificates stand. Never a refusal."""
    if household_id is None or not applies(firm):
        return None
    need = required_kinds(firm)
    if not need:
        return None
    adviser = await advisers.adviser_of(session, firm.id, household_id)
    if adviser is None or not adviser.is_active or UserRole(adviser.role) != UserRole.ADVISER:
        return None
    today = firm_time.today(firm)
    standing = _standing(await _rows(session, firm.id))
    bad = []
    for k in need:
        s = state_of(standing.get((adviser.id, k)), today)
        if s in ("expired", "not_on_file"):
            bad.append(f"{KINDS[k]} {LABELS[s].lower()}")
    if not bad:
        return None
    return {"adviser": adviser.full_name, "problems": bad,
            "warning": f"{adviser.full_name}, the household's recorded adviser, has {'; '.join(bad)}. This is a warning and approving is not stopped: "
                       "decision rights and the pre-advice gate are separate controls."}
