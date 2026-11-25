"""An Investment Adviser's fee terms since 2025, each recorded from its document (L100 India edition §4.2, B-292).

`fee_cap.py` reads the family's cap and what it is billed. Four newer rules sit beside it, and each is a fact a person records, never
one worked out:

**Advance fees.** SEBI relaxed fee collection in April 2025 from two quarters to up to one year in advance (`in_ia_advance_fee_months`).
The agreement says how far ahead it collects; that is recorded (1 to 12 months). The billing preview holds any invoice whose period runs
past a year, whatever is recorded, and past the recorded months where there are any. A bill for a family with no such record is checked
against the year alone.

**The mode.** The adviser elects the AUA or the fixed-fee mode for a family and may switch. The election is recorded with the day it
starts; a switch is a new row with the adviser's reason, and the earlier one stays. The family is a household here (the household stands in
for SEBI's family of client, `sebi_family`, as it does for the cap). A switch within the financial year lifts that year's cap to the higher
of the two, as the circular has it; a bill worked in a mode other than the recorded election is flagged for a person to confirm, not blocked.

**The second-opinion fee.** Since October 2025 an AUA-based fee within 2.5% (`in_ia_second_opinion_cap`) may be charged on assets the
client holds under a distribution arrangement. It is recorded as that: the assets, the rate, the fee and the day charged. It counts in
what the family is charged for the year against the cap. The assets stay outside the managed AUA, and a fee over the rate is refused.

**The deposit.** The adviser's refundable deposit with IAASB, and whether it sits in a bank deposit or, since August 2025, in liquid or
overnight funds (`in_ia_deposit_liquid_funds`), is on the firm's registration record (`compliance_calendar.OFFICERS`, the administrator's,
with a reason on the audit log): it is read here and recorded there.

Added and never edited; the latest row of a kind stands. Nothing here says the adviser is compliant, lowers a fee or issues a refund.
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.security import UserRole
from app.core.db import utcnow
from app.models.graph import Household
from app.models.india_gates import AdviserFeeRecord
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE})
KINDS = {"advance": "Advance fee collection", "mode": "Fee mode elected", "second_opinion": "Second-opinion fee"}
MODES = {"aua": "AUA", "fixed": "fixed-fee"}
LAW_KEYS = ["in_ia_advance_fee_months", "in_ia_second_opinion_cap", "in_ia_deposit_liquid_funds"]
DEPOSIT_HELD_IN = ("bank deposit", "liquid fund", "overnight fund", "other")
NOT_COMPLIANCE = "These are the terms a person recorded from the documents; none of this says the adviser complies."


class FeeTermError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def add_months(day: date, months: int) -> date:
    y, m = divmod(day.month - 1 + months, 12)
    year, month = day.year + y, m + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _day(value, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise FeeTermError(f"Give {label} as YYYY-MM-DD.")


# ── What is on file ──────────────────────────────────────────────────────────────────────────────────────────────────

async def _rows(session: AsyncSession, firm_id: uuid.UUID, household_ids: list[uuid.UUID]) -> list[AdviserFeeRecord]:
    if not household_ids:
        return []
    return list((await session.execute(select(AdviserFeeRecord).where(
        AdviserFeeRecord.firm_id == firm_id, AdviserFeeRecord.household_id.in_(household_ids))
        .order_by(AdviserFeeRecord.effective_on, AdviserFeeRecord.created_at, AdviserFeeRecord.id))).scalars().all())


def _terms(rows: list[AdviserFeeRecord], start: date | None = None, end: date | None = None) -> dict:
    advance = [r for r in rows if r.kind == "advance"]
    mode_rows = [r for r in rows if r.kind == "mode"]
    second = [r for r in rows if r.kind == "second_opinion"]
    # A switch is a mode row whose mode differs from the one before it, in the same household.
    switches: list[AdviserFeeRecord] = []
    last: dict = {}
    for r in mode_rows:
        before = last.get(r.household_id)
        if before is not None and before.mode != r.mode:
            switches.append(r)
        last[r.household_id] = r
    in_year = [r for r in switches if start is not None and end is not None and start <= r.effective_on <= end]
    window = [r for r in second if start is None or (start <= r.effective_on <= end)]
    return {"advance": advance[-1] if advance else None, "mode": mode_rows[-1] if mode_rows else None, "mode_rows": mode_rows,
            "switches": switches, "switched_on": max((r.effective_on for r in in_year), default=None),
            "second_opinion": second, "second_opinion_fee": float(sum(float(r.fee or 0) for r in window))}


async def household_terms(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    return _terms(await _rows(session, firm.id, [household_id]))


async def family_terms(session: AsyncSession, firm: Firm, household_ids: list[uuid.UUID], start: date, end: date) -> dict:
    return _terms(await _rows(session, firm.id, household_ids), start, end)


def _row_view(r: AdviserFeeRecord) -> dict:
    return {"kind": r.kind, "effective_on": r.effective_on.isoformat(), "mode": r.mode, "mode_label": MODES.get(r.mode) if r.mode else None,
            "advance_months": r.advance_months, "assets": float(r.assets) if r.assets is not None else None,
            "rate": float(r.rate) if r.rate is not None else None, "fee": float(r.fee) if r.fee is not None else None,
            "reason": r.reason, "document": r.source_document, "recorded_by": r.recorded_by}


def terms_view(terms: dict, start: date | None = None, end: date | None = None) -> dict:
    return {"advance": _row_view(terms["advance"]) if terms["advance"] else None,
            "mode": _row_view(terms["mode"]) if terms["mode"] else None,
            "switches": [_row_view(r) for r in terms["switches"]],
            "switched_on": terms["switched_on"].isoformat() if terms["switched_on"] else None,
            "second_opinion": [_row_view(r) for r in terms["second_opinion"]],
            "second_opinion_fee": round(terms["second_opinion_fee"], 2),
            "window": {"start": start.isoformat(), "end": end.isoformat()} if start and end else None}


def advance_check(entry: dict, terms: dict, today: date) -> str | None:
    """The sentence that holds a bill whose period runs further ahead than it may, or None. A period is as long as its start to its end."""
    try:
        start, end = date.fromisoformat(entry["period_start"]), date.fromisoformat(entry["period_end"])
    except (KeyError, ValueError, TypeError):
        return None
    law_months = int(law_figures.value("in_ia_advance_fee_months", today))
    record = terms.get("advance")
    months = min(record.advance_months, law_months) if record and record.advance_months else law_months
    if end > add_months(start, months) - timedelta(days=1):
        why = (f"the agreement as recorded collects up to {record.advance_months} month(s) ahead" if record and record.advance_months and record.advance_months < law_months
               else f"SEBI allows fees up to {law_months} months in advance")
        return (f"This bill covers {start.isoformat()} to {end.isoformat()}, longer than {months} month(s); {why} "
                f"(SEBI Master Circular for Investment Advisers, as amended April 2025).")
    return None


# ── The deposit, read from the firm's registration record ────────────────────────────────────────────────────────────

def deposit(firm: Firm, today: date | None = None) -> dict:
    """The refundable deposit with IAASB as the firm's registration record holds it; not recorded reads so."""
    recorded = ((firm.settings or {}).get("sebi_registration")) or {}
    day = today or firm_time.today(firm)
    held_in = recorded.get("iaasb_deposit_held_in")
    figure = law_figures.get("in_ia_deposit_liquid_funds", day).view(day)
    return {"recorded": bool(recorded.get("iaasb_deposit") or held_in), "amount": recorded.get("iaasb_deposit"), "held_in": held_in,
            "in_liquid_or_overnight": held_in in ("liquid fund", "overnight fund") if held_in else None,
            "document": recorded.get("iaasb_deposit_document"), "figure": figure,
            "said": None if (recorded.get("iaasb_deposit") or held_in) else "Not recorded: the registration record holds no deposit. That does not mean none is held."}


def check_deposit_terms(details: dict) -> None:
    """The deposit keys of a registration record, checked where the record is prepared."""
    held = details.get("iaasb_deposit_held_in")
    if held is not None and held not in DEPOSIT_HELD_IN:
        raise FeeTermError(f"The deposit is held in one of {', '.join(DEPOSIT_HELD_IN)}.")
    if (details.get("iaasb_deposit") or held) and not details.get("iaasb_deposit_document"):
        raise FeeTermError("Say where the deposit's evidence is filed (iaasb_deposit_document): it is recorded from a document.")


# ── Recording ────────────────────────────────────────────────────────────────────────────────────────────────────────

async def prepare(session: AsyncSession, firm: Firm, role, *, household_id, kind: str | None, effective_on=None, mode: str | None = None,
                  advance_months=None, assets=None, rate=None, fee=None, reason: str | None = None, document: str | None = None) -> dict:
    if not applies(firm):
        raise FeeTermError("An Investment Adviser's fee terms are recorded for an Indian firm.")
    if role not in RECORDERS:
        raise FeeTermError("An adviser, paraplanner, operations or compliance records a fee term, from the document.")
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise FeeTermError(f"A fee term is one of {', '.join(KINDS)}.")
    try:
        hid = household_id if isinstance(household_id, uuid.UUID) else uuid.UUID(str(household_id))
    except ValueError:
        raise FeeTermError("Give the household from the client's page.")
    household = await session.get(Household, hid)
    if household is None or household.firm_id != firm.id:
        raise FeeTermError("Household not found.")
    today = firm_time.today(firm)
    if len((document or "").strip()) < 5:
        raise FeeTermError("Say which document this comes from (the signed agreement, the election or the invoice) and where it is filed.")
    day = _day(effective_on, "the day it starts") if effective_on not in (None, "") else today
    if day > today:
        raise FeeTermError("A fee term cannot be dated in the future: record it from the day it took effect.")
    prior = await _rows(session, firm.id, [hid])
    out = {"household": household, "kind": kind, "effective_on": day, "mode": None, "advance_months": None, "assets": None, "rate": None,
           "fee": None, "reason": (reason or "").strip() or None, "document": document.strip(), "switch": False, "before": None}
    same = [r for r in prior if r.kind == kind]
    out["before"] = same[-1] if same else None
    if kind == "advance":
        try:
            n = int(advance_months)
        except (TypeError, ValueError):
            raise FeeTermError("Give the months of fees the agreement collects in advance, 1 to 12.")
        cap = int(law_figures.value("in_ia_advance_fee_months", today))
        if n < 1:
            raise FeeTermError("Give the months of fees the agreement collects in advance, 1 to 12.")
        if n > cap:
            raise FeeTermError(f"Fees may be collected at most {cap} months in advance, so an agreement that collects {n} cannot be recorded as allowed; "
                               "the invoice is held, and a person changes the agreement.")
        out["advance_months"] = n
        if out["before"] is not None and out["before"].advance_months == n:
            raise FeeTermError("That advance-fee term is already recorded.")
    elif kind == "mode":
        m = (mode or "").strip().lower()
        if m not in MODES:
            raise FeeTermError("The mode is aua or fixed.")
        out["mode"] = m
        if out["before"] is not None:
            if out["before"].mode == m:
                raise FeeTermError("That mode is already the one recorded: a switch is a different mode.")
            if day <= out["before"].effective_on:
                raise FeeTermError(f"A switch must start after the election of {out['before'].effective_on.isoformat()}.")
            if len((reason or "").strip()) < 5:
                raise FeeTermError("Say why the adviser switches mode, from the client's agreement or the adviser's note.")
            out["switch"] = True
    else:
        try:
            a, r, f = float(assets), float(rate), float(fee)
        except (TypeError, ValueError):
            raise FeeTermError("Give the assets held under the distribution arrangement, the rate and the fee charged, as numbers.")
        if a <= 0 or f <= 0 or r <= 0:
            raise FeeTermError("The assets, the rate and the fee must each be more than nothing.")
        cap = float(law_figures.value("in_ia_second_opinion_cap", today))
        if r > cap + 1e-9:
            raise FeeTermError(f"A second-opinion fee is within {cap:.1%} of the assets a year; {r:.2%} is over it, so it cannot be recorded as allowed.")
        if f > a * r + 0.005:
            raise FeeTermError("The fee is more than the rate on those assets.")
        out.update({"assets": a, "rate": r, "fee": f})
    return out


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = AdviserFeeRecord(created_at=utcnow(), firm_id=firm.id, household_id=p["household"].id, kind=p["kind"], effective_on=p["effective_on"], mode=p["mode"],
                           advance_months=p["advance_months"], assets=p["assets"], rate=p["rate"], fee=p["fee"], reason=p["reason"],
                           source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="adviser_fee_term_recorded", actor=by, content={
        "household_id": str(row.household_id), "kind": row.kind, "effective_on": row.effective_on.isoformat(), "mode": row.mode,
        "advance_months": row.advance_months, "assets": float(row.assets) if row.assets is not None else None,
        "fee": float(row.fee) if row.fee is not None else None, "switch": p["switch"], "document": row.source_document})
    return await household_view(session, firm, row.household_id)


async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat()}
    year = markets.tax_year(markets.for_firm(firm), today)
    terms = await family_terms(session, firm, [household_id], year["start"], year["end"])
    household = await session.get(Household, household_id)
    return {"applies": True, "household_id": str(household_id), "household": household.name if household else None, "as_of": today.isoformat(),
            "year": year["label"], **terms_view(terms, year["start"], year["end"]), "deposit": deposit(firm, today),
            "stands_in": "The household stands in for SEBI's family of client.", "figures": law_figures.cited(LAW_KEYS, today),
            "note": NOT_COMPLIANCE}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every household with a fee term on file, and the firm's deposit."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "households": []}
    rows = (await session.execute(select(AdviserFeeRecord).where(AdviserFeeRecord.firm_id == firm.id))).scalars().all()
    ids = sorted({r.household_id for r in rows}, key=str)
    out = [await household_view(session, firm, hid) for hid in ids]
    return {"applies": True, "as_of": today.isoformat(), "households": out, "deposit": deposit(firm, today), "figures": law_figures.cited(LAW_KEYS, today),
            "note": NOT_COMPLIANCE}
