"""Fee schedule calculation and validation (L200 §2.1 Track A step 2, §5).

L200's failure mode: "fee schedule mis-set at onboarding -> overbilling -> client
reimbursements, enforcement". Its control: "dual-entry validation; fee-schedule library
with maker/checker; reconciliation to agreement at first bill".

This module provides the first two. The schedule is selected from a firm library rather
than typed, the fee it implies is computed and shown before anyone confirms it, and the
confirmation is a separate act by a different person.
"""
from __future__ import annotations

BILLING_METHODS = {"advance", "arrears"}
BILLING_FREQUENCIES = {"monthly", "quarterly", "annually"}

# A schedule producing a headline rate outside this range is almost certainly a data entry
# error — 500bps is 5%, far above any normal advisory fee.
_SANE_BPS = (1.0, 300.0)


def compute_annual_fee(schedule, aum: float | None) -> dict:
    """The annual fee this schedule implies at this AUM.

    Tiered schedules are computed marginally — each band's rate applies only to the portion
    of assets inside it, which is how breakpoints actually work. Applying the top band's
    rate to the whole balance is a classic overbilling error.
    """
    if aum is None or aum <= 0:
        return {"annual_fee": None, "effective_bps": None,
                "breakdown": [], "note": "No billable AUM recorded."}

    breakdown: list[dict] = []
    fee = 0.0

    if schedule.fee_type == "flat_fee":
        fee = float(schedule.flat_fee or 0)
        breakdown.append({"band": "Flat fee", "amount": fee})
    elif schedule.fee_type == "flat_bps":
        bps = float(schedule.flat_bps or 0)
        fee = aum * bps / 10_000
        breakdown.append({"band": f"{bps:g} bps on all assets", "amount": fee})
    else:  # tiered_bps — marginal
        remaining = aum
        for tier in sorted(schedule.tiers or [], key=lambda t: t.get("min_aum", 0)):
            lo = float(tier.get("min_aum", 0) or 0)
            hi = tier.get("max_aum")
            hi = float(hi) if hi is not None else None
            if aum <= lo:
                break
            band = (min(aum, hi) - lo) if hi is not None else (aum - lo)
            if band <= 0:
                continue
            bps = float(tier.get("bps", 0) or 0)
            amount = band * bps / 10_000
            fee += amount
            upper = f"{hi:,.0f}" if hi is not None else "above"
            breakdown.append({
                "band": f"{bps:g} bps on {lo:,.0f}–{upper}",
                "amount": amount,
            })
            remaining -= band

    minimum = float(schedule.minimum_annual_fee or 0)
    applied_minimum = False
    if minimum and fee < minimum:
        breakdown.append({"band": f"Minimum annual fee applied ({minimum:,.0f})",
                          "amount": minimum - fee})
        fee = minimum
        applied_minimum = True

    return {
        "annual_fee": round(fee, 2),
        "effective_bps": round(fee / aum * 10_000, 2) if aum else None,
        "breakdown": breakdown,
        "applied_minimum": applied_minimum,
        "currency": schedule.currency or "NZD",
    }


def validate(schedule, *, billing_method: str | None, billing_frequency: str | None,
             aum: float | None) -> list[str]:
    """Dual-entry validation — problems a human should see before confirming."""
    problems: list[str] = []

    if billing_method not in BILLING_METHODS:
        problems.append(
            f"Billing method must be one of: {', '.join(sorted(BILLING_METHODS))}."
        )
    if billing_frequency not in BILLING_FREQUENCIES:
        problems.append(
            f"Billing frequency must be one of: {', '.join(sorted(BILLING_FREQUENCIES))}."
        )

    if schedule.fee_type == "tiered_bps":
        tiers = sorted(schedule.tiers or [], key=lambda t: t.get("min_aum", 0))
        if not tiers:
            problems.append("Tiered schedule has no bands defined.")
        # Gaps or overlaps between bands silently mis-bill the assets that fall in them.
        for prev, nxt in zip(tiers, tiers[1:]):
            prev_max = prev.get("max_aum")
            if prev_max is None:
                problems.append("An open-ended band is followed by another band.")
                continue
            if float(nxt.get("min_aum", 0)) != float(prev_max):
                problems.append(
                    f"Band boundary mismatch: one ends at {float(prev_max):,.0f}, "
                    f"the next starts at {float(nxt.get('min_aum', 0)):,.0f}."
                )

    calc = compute_annual_fee(schedule, aum)
    eff = calc.get("effective_bps")
    if eff is not None and not (_SANE_BPS[0] <= eff <= _SANE_BPS[1]):
        problems.append(
            f"Effective rate of {eff:g} bps is outside the expected "
            f"{_SANE_BPS[0]:g}–{_SANE_BPS[1]:g} bps range — check the schedule and AUM."
        )
    if aum is None or aum <= 0:
        problems.append("No billable AUM recorded — the fee cannot be reconciled at first bill.")

    return problems


def status_for(case, schedule) -> dict:
    """The case's fee position, including maker/checker state."""
    if schedule is None:
        return {
            "assigned": False, "confirmed": False,
            "detail": "No fee schedule assigned.",
            "problems": ["A fee schedule must be assigned and confirmed before activation."],
        }

    aum = float(case.billable_aum) if case.billable_aum is not None else None
    calc = compute_annual_fee(schedule, aum)
    problems = validate(
        schedule, billing_method=case.billing_method,
        billing_frequency=case.billing_frequency, aum=aum,
    )
    confirmed = bool(case.fee_confirmed_by and case.fee_confirmed_at)

    return {
        "assigned": True,
        "confirmed": confirmed,
        "schedule": {
            "id": str(schedule.id), "code": schedule.code, "name": schedule.name,
            "fee_type": schedule.fee_type, "tiers": schedule.tiers,
            "flat_bps": float(schedule.flat_bps) if schedule.flat_bps is not None else None,
            "flat_fee": float(schedule.flat_fee) if schedule.flat_fee is not None else None,
            "minimum_annual_fee": (
                float(schedule.minimum_annual_fee)
                if schedule.minimum_annual_fee is not None else None
            ),
            "currency": schedule.currency,
        },
        "billing_method": case.billing_method,
        "billing_frequency": case.billing_frequency,
        "householding": case.householding,
        "billable_aum": aum,
        "calculation": calc,
        "set_by": case.fee_set_by,
        "set_at": case.fee_set_at.isoformat() if case.fee_set_at else None,
        "confirmed_by": case.fee_confirmed_by,
        "confirmed_at": case.fee_confirmed_at.isoformat() if case.fee_confirmed_at else None,
        "problems": problems,
        "detail": (
            f"{schedule.name} — {calc['annual_fee']:,.0f} {calc['currency']}/yr "
            f"({calc['effective_bps']:g} bps effective)"
            if calc["annual_fee"] is not None else f"{schedule.name} — AUM not recorded"
        ),
    }
