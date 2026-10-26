"""Reconciliation to agreement (L200 §5): the third control in the fee module's docstring -
"dual-entry validation; fee-schedule library with maker/checker; reconciliation to agreement
at first bill" - and the only one that was never built.

`billing.preview` works the fee out from the schedule on file. This re-derives it from the
agreement independently and asks whether the two still match, and whether the agreement is
still what was confirmed. It looks for exactly the failures L200 names: a fee schedule
mis-set or changed after sign-off, a bill that disagrees with the schedule it claims to
follow, a duplicate, and a first bill that bears no relation to the balance the fee was agreed
on. Each finding is a `check` on the invoice, tagged `source: "reconcile"` so it can be
replaced rather than stacked when the invoice is re-checked at approval.

A `block` holds the bill; a `warn` is shown with it.
"""
from __future__ import annotations

from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import fees as fees_core
from app.models.billing import FeeArrangement, Invoice
from app.models.onboarding import FeeSchedule

# Agreed-on balance versus the balance actually billed: wider than this is not noise.
FIRST_BILL_BALANCE_TOLERANCE = 0.25
# A householded fee is shared across members, each rounded to the cent.
_CENT = 0.011


def _aware(dt):
    return dt if dt is None or dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _check(code: str, severity: str, detail: str) -> dict:
    return {"code": code, "severity": severity, "detail": detail, "source": "reconcile"}


async def reconcile_invoice(session: AsyncSession, invoice: Invoice) -> list[dict]:
    """Findings for one draft invoice, derived from the agreement as it stands now."""
    checks: list[dict] = []
    arr = await session.get(FeeArrangement, invoice.arrangement_id) if invoice.arrangement_id else None
    if arr is None:
        return [_check("agreement_missing", "block",
                       "The fee arrangement this bill was raised on no longer exists.")]
    if arr.status != "active":
        checks.append(_check("agreement_ended", "block", "The fee arrangement has ended."))
    if not arr.is_confirmed:
        checks.append(_check("agreement_unconfirmed", "block",
                             "The fee arrangement has not been confirmed by a second person."))
    if arr.set_by and arr.confirmed_by and arr.set_by.strip().lower() == arr.confirmed_by.strip().lower():
        checks.append(_check("dual_control_broken", "block",
                             "The same person set and confirmed the fee; maker/checker was not applied."))

    schedule = await session.get(FeeSchedule, arr.fee_schedule_id)
    if schedule is None:
        checks.append(_check("schedule_missing", "block", "The fee schedule no longer exists."))
        return checks
    confirmed_at, edited_at = _aware(arr.confirmed_at), _aware(schedule.updated_at)
    if confirmed_at and edited_at and edited_at > confirmed_at:
        checks.append(_check(
            "schedule_changed_after_confirmation", "warn",
            f"The schedule was edited on {edited_at:%Y-%m-%d}, after the fee was confirmed on "
            f"{confirmed_at:%Y-%m-%d}; the client agreed to the earlier version."))

    clash = (await session.execute(
        select(func.count()).select_from(Invoice).where(
            Invoice.firm_id == invoice.firm_id, Invoice.arrangement_id == arr.id, Invoice.kind == "invoice",
            Invoice.period_start == invoice.period_start, Invoice.id != invoice.id,
            Invoice.status.in_(("issued", "debited"))))).scalar_one()
    if clash:
        checks.append(_check("duplicate_period", "block",
                             f"A bill for {invoice.period_start.isoformat()} to {invoice.period_end.isoformat()} "
                             "has already been issued on this arrangement."))

    # Re-derive the fee from the agreement and compare it with what was drafted.
    calc = invoice.calculation or {}
    value = float(calc.get("billable_value") or 0)
    group = float(calc.get("group_value") or value)
    ppy = int(calc.get("periods_per_year") or fees_core_periods(arr.billing_frequency))
    fraction = float(calc.get("period_fraction") or 0)
    annual = fees_core.compute_annual_fee(schedule, group)
    if annual["annual_fee"] is not None and group > 0 and float(invoice.amount) > 0:
        expected = annual["annual_fee"] * (value / group) / ppy * fraction
        if abs(expected - float(invoice.amount)) > _CENT * (2 if calc.get("householded") else 1):
            checks.append(_check(
                "fee_does_not_match_schedule", "block",
                f"Re-deriving the fee from {schedule.name} gives {expected:,.2f}; the bill says "
                f"{float(invoice.amount):,.2f}."))
        eff = annual["effective_bps"]
        lo, hi = fees_core._SANE_BPS
        if eff is not None and not (lo <= eff <= hi):
            checks.append(_check("rate_outside_expected_range", "warn",
                                 f"The effective rate of {eff:g} bps is outside the expected {lo:g}-{hi:g} bps."))

    prior = (await session.execute(
        select(func.count()).select_from(Invoice).where(
            Invoice.firm_id == invoice.firm_id, Invoice.arrangement_id == arr.id, Invoice.kind == "invoice",
            Invoice.id != invoice.id, Invoice.status.in_(("issued", "debited", "credited"))))).scalar_one()
    if prior == 0 and arr.agreed_billable_aum and value > 0:
        ratio = value / float(arr.agreed_billable_aum)
        if abs(ratio - 1.0) > FIRST_BILL_BALANCE_TOLERANCE:
            checks.append(_check(
                "first_bill_balance_differs", "warn",
                f"This first bill is on {value:,.0f}; the fee was agreed on {float(arr.agreed_billable_aum):,.0f} "
                f"({ratio:.0%} of it). Confirm the client expects this."))
    return checks


def fees_core_periods(frequency: str) -> int:
    from app.aurea_core.billing import PERIODS_PER_YEAR
    return PERIODS_PER_YEAR.get(frequency, 4)


def merge_checks(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Keep the calculation's own checks, replace any earlier reconciliation findings."""
    return [c for c in (existing or []) if c.get("source") != "reconcile"] + list(fresh)
