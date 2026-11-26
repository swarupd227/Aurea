"""What a signed advisory agreement carried: Annexure A and the MITC, as a person recorded them or an eSign template declared them (B-324).

SEBI's Investment Advisers rules ask for the client's agreement in the prescribed form (Annexure A) and for the most important terms and
conditions (the MITC) to be given to the client (IA Master Circular para 1(ii)(g), as the pre-advice gate cites it). A signature proves a document
was signed; it does not prove which document. So the gate reads, besides the signature, two recorded flags:

  recorded   a person reads the signed document and records whether it carries Annexure A and whether it carries the MITC, with where the document
             is filed. Added and never edited: a correction is a new row and the latest stands.
  declared   an eSign envelope may declare what its template included (`includes`, in the item's detail). Confirming a signed envelope records those
             flags only from that declaration; an envelope that declares nothing records nothing, and the contents stay "not recorded". Nothing is
             assumed from a signature, a template's name or a firm's usual practice.

No record means "not recorded", which does not mean the agreement lacks them: `pre_advice` fails the agreement check naming what is not recorded, so
a person reads the document and records it. No agreement is generated or merged here, and nothing here says the agreement meets any rule.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import licences, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Household
from app.models.india_rem_e import AgreementContent
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE})
PARTS = {"annexure_a": "Annexure A", "mitc": "MITC"}
CITATION = "IA Master Circular para 1(ii)(g), as the pre-advice gate cites it"


class AgreementContentError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN" and bool(licences.held(firm))


async def standing(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> AgreementContent | None:
    rows = (await session.execute(select(AgreementContent).where(AgreementContent.firm_id == firm_id, AgreementContent.household_id == household_id)
                                  .order_by(AgreementContent.created_at, AgreementContent.id))).scalars().all()
    return rows[-1] if rows else None


def not_recorded(row: AgreementContent | None, *, mitc_elsewhere: bool = False) -> list[str]:
    """The parts of the agreement a person has not recorded as carried. `mitc_elsewhere`: the MITC's own delivery is on file."""
    out = []
    if row is None or not row.annexure_a:
        out.append(PARTS["annexure_a"])
    if (row is None or not row.mitc) and not mitc_elsewhere:
        out.append(PARTS["mitc"])
    return out


def incomplete_text(missing: list[str]) -> str:
    return "agreement incomplete: " + " / ".join(missing) + " not recorded"


def _flag(value, label: str) -> bool:
    if value in (None, ""):
        raise AgreementContentError(f"Say whether the signed agreement carries {label}: yes or no, as the document shows.")
    return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "yes", "1")


async def prepare(session: AsyncSession, firm: Firm, role, *, household_id=None, annexure_a=None, mitc=None, document: str | None = None,
                  reason: str | None = None) -> dict:
    if not applies(firm):
        raise AgreementContentError("What an agreement carried is recorded for a firm that holds an Indian licence.")
    if UserRole(role) not in RECORDERS:
        raise AgreementContentError("An adviser, a paraplanner, operations or compliance records what the signed agreement carries, from the document.")
    try:
        household = await session.get(Household, uuid.UUID(str(household_id)))
    except (ValueError, TypeError):
        raise AgreementContentError("Give the household's id, from the client's page.")
    if household is None or household.firm_id != firm.id:
        raise AgreementContentError("No such household at this firm.")
    a, m = _flag(annexure_a, "Annexure A"), _flag(mitc, "the MITC")
    if len((document or "").strip()) < 5:
        raise AgreementContentError("Say where the signed agreement is filed.")
    if len(document.strip()) > 300:
        raise AgreementContentError("Say where the signed agreement is filed in 300 characters or fewer.")
    before = await standing(session, firm.id, household.id)
    if before is not None and (before.annexure_a, before.mitc) == (a, m):
        raise AgreementContentError(f"Nothing changes: this is already recorded for {household.name}.")
    if before is not None and not (reason or "").strip():
        raise AgreementContentError("A record is already on file: a correction says why, and the earlier one stays.")
    return {"household": household, "annexure_a": a, "mitc": m, "document": document.strip(), "reason": (reason or "").strip() or None, "before": before}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = AgreementContent(created_at=utcnow(), firm_id=firm.id, household_id=p["household"].id, annexure_a=p["annexure_a"], mitc=p["mitc"], source="person",
                           source_document=p["document"], reason=p["reason"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="agreement_content_recorded", actor=by, content={
        "household_id": str(row.household_id), "annexure_a": row.annexure_a, "mitc": row.mitc, "document": row.source_document, "reason": row.reason})
    return await view(session, firm, p["household"].id)


async def from_envelope(session: AsyncSession, firm: Firm, item, *, by: str) -> AgreementContent | None:
    """Record what an eSign envelope's template declared it included, when it declared anything. None where it declared nothing: never assumed."""
    detail = item.detail or {}
    if "includes" not in detail or not isinstance(detail["includes"], list):
        return None
    included = {str(x) for x in detail["includes"]}
    row = AgreementContent(created_at=utcnow(), firm_id=firm.id, household_id=item.household_id, annexure_a="annexure_a" in included, mitc="mitc" in included,
                           source="esign", item_id=item.id, source_document=f"eSign envelope {item.reference}: template includes {', '.join(sorted(included)) or 'neither part'}"[:300],
                           recorded_by=by, source_system=item.source_system)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="agreement_content_recorded", actor=by, content={
        "household_id": str(row.household_id), "annexure_a": row.annexure_a, "mitc": row.mitc, "document": row.source_document, "source": "esign"})
    return row


async def for_firm(session: AsyncSession, firm: Firm) -> dict:
    """What each agreement carried, for every household with a mandate on the adviser's track, by name."""
    from app.aurea_core import pre_advice

    if not applies(firm):
        return {"applies": False, "households": []}
    rows = []
    for h in (await session.execute(select(Household).where(Household.firm_id == firm.id).order_by(Household.name))).scalars().all():
        if await pre_advice.adviser_mandates(session, firm, h.id):
            rows.append({**await view(session, firm, h.id), "household": h.name})
    return {"applies": True, "scope": "firm", "households": rows, "complete": sum(1 for r in rows if not r["missing"]),
            "incomplete": sum(1 for r in rows if r["missing"]), "citation": CITATION,
            "said": "A signature does not say which document was signed. Not recorded does not mean the agreement lacks the part: a person reads the signed document and records it."}


async def view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    row = await standing(session, firm.id, household_id)
    return {"applies": applies(firm), "household_id": str(household_id), "recorded": row is not None,
            "annexure_a": row.annexure_a if row else None, "mitc": row.mitc if row else None, "source": row.source if row else None,
            "document": row.source_document if row else None, "recorded_by": row.recorded_by if row else None,
            "missing": not_recorded(row), "citation": CITATION,
            "said": "A signature does not say which document was signed. Not recorded does not mean the agreement lacks the part: a person reads the signed document and records it."}
