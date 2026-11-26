"""A step-up SIP's schedule, as its registration states it (B-394).

A step-up is a term of the client's signed SIP registration: a percentage, how often it applies, and an optional most any
one instalment may be. Without those terms an instalment never rises: nothing here supplies a rate, and nothing reads a
step-up from anywhere but the registration.

The first step falls on the instalment `every` months after the first one, and each later step the same distance on, so a
yearly step-up raises the 13th instalment, the 25th, and so on. The calendar sets the schedule: a missed instalment does
not move it. Each step raises the instalment before it, and none passes the cap.

Pure functions over the terms (`terms`), so the worker that buys, the card that shows and the engines that project all
read one schedule.
"""
from __future__ import annotations

from datetime import date

FREQUENCIES = (6, 12)          # half-yearly or yearly, as registrations are offered
MAX_PERCENT = 100.0
HORIZON_YEARS = 80             # as far as any projection runs


def terms(row) -> dict:
    """A registration's own terms, from the row (`app.models.sip.Sip`)."""
    return {"id": str(row.id), "amount": float(row.amount), "day": int(row.day_of_month), "starts_on": row.starts_on,
            "ends_on": row.ends_on, "step_up_percent": float(row.step_up_percent) if row.step_up_percent is not None else None,
            "step_up_every_months": int(row.step_up_every_months) if row.step_up_every_months is not None else None,
            "step_up_cap": float(row.step_up_cap) if row.step_up_cap is not None else None, "simulated": bool(row.simulated)}


def _day_of(day) -> date | None:
    return date.fromisoformat(day[:10]) if isinstance(day, str) else day


def first_instalment(t: dict) -> date:
    start = _day_of(t["starts_on"])
    on = start.replace(day=int(t["day"]))
    if on >= start:
        return on
    return date(start.year + (start.month == 12), start.month % 12 + 1, int(t["day"]))


def _months_after(first: date, on: date) -> int:
    return (on.year - first.year) * 12 + (on.month - first.month)


def steps_by(t: dict, on: date) -> int:
    """How many times the instalment has been stepped up by the instalment of `on`'s month."""
    every = t.get("step_up_every_months")
    if not t.get("step_up_percent") or not every:
        return 0
    return max(_months_after(first_instalment(t), on), 0) // int(every)


def amount_on(t: dict, on: date) -> float:
    """The instalment of `on`'s month under the registration."""
    amount, n = float(t["amount"]), steps_by(t, on)
    if not n:
        return round(amount, 2)
    rate, cap = float(t["step_up_percent"]) / 100.0, t.get("step_up_cap")
    for _ in range(n):
        amount = amount * (1 + rate)
        if cap is not None and amount >= float(cap):
            return round(float(cap), 2)
    return round(amount, 2)


def next_step(t: dict, after: date) -> dict | None:
    """The first instalment after `after` that is higher than the one before it, within the registration's life; None
    where there is no step-up, the cap is reached or the SIP ends first."""
    every = t.get("step_up_every_months")
    if not t.get("step_up_percent") or not every:
        return None
    first, ends = first_instalment(t), _day_of(t.get("ends_on"))
    n = steps_by(t, after) + 1
    months = n * int(every)
    year, month = first.year + (first.month - 1 + months) // 12, (first.month - 1 + months) % 12 + 1
    on = date(year, month, first.day)
    if ends is not None and on > ends:
        return None
    before = amount_on(t, date(year - (month == 1), (month - 2) % 12 + 1, 1))
    amount = amount_on(t, on)
    return {"on": on.isoformat(), "amount": amount} if amount > before + 1e-9 else None


def yearly(registrations: list[dict], today: date, years: int = HORIZON_YEARS) -> list[float]:
    """What the registrations buy in each year from today (year 1 is the twelve months after today): every instalment
    dated after today, stepped up as registered, until each SIP ends. It reads as if every instalment is bought."""
    out = [0.0] * years
    for t in registrations:
        first, ends = first_instalment(t), _day_of(t.get("ends_on"))
        day = int(t["day"])
        year, month = today.year, today.month
        if date(year, month, day) <= today:
            year, month = year + (month == 12), month % 12 + 1
        for k in range(years * 12):
            on = date(year + (month - 1 + k) // 12, (month - 1 + k) % 12 + 1, day)
            if on < first:
                continue
            if ends is not None and on > ends:
                break
            out[min(k // 12, years - 1)] += amount_on(t, on)
    return [round(v, 2) for v in out]


def label(t: dict, money) -> str | None:
    """The step-up in a few words, for a card or a Confirm row: None without one."""
    if not t.get("step_up_percent"):
        return None
    every = {12: "a year", 6: "every 6 months"}.get(int(t["step_up_every_months"]), f"every {t['step_up_every_months']} months")
    cap = f", never above {money(float(t['step_up_cap']))}" if t.get("step_up_cap") is not None else ""
    return f"{float(t['step_up_percent']):g}% {every}{cap}"
