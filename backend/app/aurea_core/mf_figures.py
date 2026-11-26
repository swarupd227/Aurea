"""The mutual fund cut-off times and the large-amount line, as the firm records them from the circular (B-330).

`mf_orders.nav_date_for` works the NAV day an order should get from four figures: the standard cut-off, a liquid or overnight scheme's purchase and
redemption cut-offs, and the amount at or above which a purchase follows the day funds were realised. The L100 India edition gives the rule and no
figure, so the platform carries each in `law_figures` as ASSUMED from SEBI's circulars as understood, unreviewed. This module lets the firm replace an
assumed figure with the circular's own:

  assumed    no row for the figure: the platform's `law_figures` default is read, and every screen says "assumed".
  recorded   compliance or operations records the value with the circular it comes from, the circular's date and a reason. Added and never
             edited; the latest row for a figure stands, and it reads "recorded: <source>". The recording is on the ledger and, because it
             changes what every NAV-day check reads, on the audit log with its reason.

A recorded figure changes only what the NAV-date check reads. It does not change an order, a holding, a unit or a cash balance, and a record
of an order already made is read afresh, so a change moves what is reported as a break and nothing else. It says nothing of whether the firm
reads the circular correctly: that is for counsel.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.india_rem_e import MfFigure
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.COMPLIANCE, UserRole.OPERATIONS})
# figure key -> (label, unit)
FIGURES = {
    "in_mf_cutoff_standard": ("Standard cut-off", "time"),
    "in_mf_cutoff_liquid_purchase": ("Liquid or overnight purchase cut-off", "time"),
    "in_mf_cutoff_liquid_redemption": ("Liquid or overnight redemption cut-off", "time"),
    "in_mf_large_amount": ("Large-purchase line", "inr"),
}


class FigureError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def clock(minutes: float) -> str:
    m = int(minutes)
    return f"{m // 60}:{m % 60:02d}"


def _shown(key: str, value: float) -> str:
    return clock(value) if FIGURES[key][1] == "time" else f"Rs {value:,.0f}"


async def _rows(session: AsyncSession, firm_id: uuid.UUID) -> dict[str, MfFigure]:
    out: dict[str, MfFigure] = {}
    for r in (await session.execute(select(MfFigure).where(MfFigure.firm_id == firm_id).order_by(MfFigure.created_at, MfFigure.id))).scalars().all():
        out[r.figure_key] = r
    return out


async def values(session: AsyncSession, firm: Firm | None) -> dict[str, float]:
    """The figures the firm recorded, by key. A key absent from the result is read from the platform's assumed default."""
    if not applies(firm):
        return {}
    return {k: float(r.value) for k, r in (await _rows(session, firm.id)).items()}


def _value(key: str, raw) -> float:
    unit = FIGURES[key][1]
    if unit == "time":
        text = str(raw).strip()
        if ":" in text:
            try:
                hh, mm = text.split(":")
                minutes = int(hh) * 60 + int(mm)
            except ValueError:
                raise FigureError("Give the cut-off as HH:MM, 24-hour, as the circular states it.")
        else:
            try:
                minutes = int(float(text))
            except ValueError:
                raise FigureError("Give the cut-off as HH:MM, 24-hour, as the circular states it.")
        if not (0 < minutes < 24 * 60):
            raise FigureError("A cut-off is a time within the day.")
        return float(minutes)
    try:
        amount = float(str(raw).replace(",", "").replace("Rs", "").strip())
    except ValueError:
        raise FigureError("Give the line in rupees, as the circular states it.")
    if amount <= 0:
        raise FigureError("The line must be more than nothing.")
    return round(amount, 2)


async def prepare(session: AsyncSession, firm: Firm, role, *, figure_key: str | None = None, value=None, source: str | None = None, as_of=None,
                  reason: str | None = None) -> dict:
    if not applies(firm):
        raise FigureError("The mutual fund cut-off figures are recorded for an Indian firm.")
    if UserRole(role) not in RECORDERS:
        raise FigureError("Compliance or operations records the mutual fund cut-off figures, from the circular.")
    key = (figure_key or "").strip()
    if key not in FIGURES:
        raise FigureError(f"The figure is one of {', '.join(FIGURES)}.")
    if value in (None, ""):
        raise FigureError("Give the figure as the circular states it.")
    new = _value(key, value)
    if len((source or "").strip()) < 5:
        raise FigureError("Name the circular the figure comes from, with its number or date.")
    if len(source.strip()) > 300:
        raise FigureError("Name the circular in 300 characters or fewer.")
    if len((reason or "").strip()) < 5:
        raise FigureError("Give the reason for recording it.")
    today = firm_time.today(firm)
    try:
        day = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
    except ValueError:
        raise FigureError("Give the circular's date as YYYY-MM-DD.")
    if day > today:
        raise FigureError("The circular's date cannot be ahead.")
    before = (await _rows(session, firm.id)).get(key)
    current = float(before.value) if before is not None else float(law_figures.value(key, today))
    if new == current and before is not None:
        raise FigureError(f"Nothing changes: {FIGURES[key][0]} is already recorded as {_shown(key, new)}.")
    return {"key": key, "label": FIGURES[key][0], "value": new, "shown": _shown(key, new), "source": source.strip(), "as_of": day, "reason": reason.strip(),
            "before": before, "was": _shown(key, current), "was_state": "recorded" if before is not None else "assumed"}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, actor=None, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = MfFigure(created_at=utcnow(), firm_id=firm.id, figure_key=p["key"], value=p["value"], source=p["source"], as_of=p["as_of"], reason=p["reason"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="mf_figure_recorded", actor=by, content={
        "figure": p["key"], "value": p["value"], "was": p["was"], "was_state": p["was_state"], "source": p["source"], "as_of": p["as_of"].isoformat(), "reason": p["reason"]})
    if actor is not None:
        from app.aurea_core import platform_admin

        platform_admin.audit(session, firm.id, actor, "mf_figure.recorded", subject=p["label"],
                             detail={"value": p["shown"], "was": p["was"], "source": p["source"], "reason": p["reason"]})
        await session.flush()
    return await overview(session, firm)


async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "figures": []}
    rows = await _rows(session, firm.id)
    cited = {c["key"]: c for c in law_figures.cited(list(FIGURES), today)}
    out = []
    for key, (label, unit) in FIGURES.items():
        r = rows.get(key)
        default = float(law_figures.value(key, today))
        out.append({"key": key, "label": label, "unit": unit, "value": float(r.value) if r else default, "shown": _shown(key, float(r.value) if r else default),
                    "state": "recorded" if r else "assumed", "state_label": f"recorded: {r.source}" if r else "assumed",
                    "source": r.source if r else (cited.get(key) or {}).get("source"), "as_of": r.as_of.isoformat() if r else None,
                    "reason": r.reason if r else None, "recorded_by": r.recorded_by if r else None,
                    "default": _shown(key, default), "default_differs": bool(r) and float(r.value) != default})
    return {"applies": True, "as_of": today.isoformat(), "figures": out, "recorded": sum(1 for f in out if f["state"] == "recorded"),
            "assumed": sum(1 for f in out if f["state"] == "assumed"), "can_record": sorted(r.value for r in RECORDERS),
            "said": "A figure reads assumed until compliance or operations records the circular's own. Recording changes what the NAV-date check reads and nothing else."}
