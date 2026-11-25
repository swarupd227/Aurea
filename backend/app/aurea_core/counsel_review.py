"""Whether counsel has read each Indian law figure against its source (India row 14, B-260).

The figures in `law_figures.py` that serve India (the keys that begin `in_`) were taken from the research report's reading of the
statute, circular or directions, and most say in their own source that the clause was not re-read. Counsel's confirmation is the
thing that turns "as the report read it" into "as the law reads". This keeps that as a record per figure, so it is visible beside
the figure wherever it is shown, instead of one sentence in a backlog row.

Compliance records a review from a document (counsel's opinion or note): who read it, the day, what they found (confirmed,
corrected, or not confirmed), and the figure's own as-of day at the time. A row is added and never edited and the latest for a
figure stands. A figure with no row is "not reviewed", which is the default and the honest state; a figure that was re-read after
the review (its as-of day is later than the one reviewed) reads "changed since review". A corrected or not-confirmed outcome does
not change the figure: that is a code change with its citation, as for every figure, and the status says so until it is made.
Nothing here says a figure is right, or that the firm is compliant: "confirmed" is what the named reviewer said on that day.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.security import UserRole
from app.models.india_governance import CounselReview
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.COMPLIANCE})
OUTCOMES = {"confirmed": "Confirmed", "corrected": "Corrected: the figure is wrong as coded", "not_confirmed": "Not confirmed: counsel could not say"}
STATES = {"not_reviewed": "Not reviewed", "confirmed": "Confirmed by counsel", "corrected": "Counsel corrected it", "not_confirmed": "Counsel could not confirm",
          "changed_since": "Changed since the review"}
FIGURE_KEYS = ("key", "source", "as_of", "unit")


class CounselError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def india_keys() -> list[str]:
    return sorted(k for k in law_figures.VERSIONS if k.startswith("in_"))


async def standing(session: AsyncSession, firm_id: uuid.UUID) -> dict[str, CounselReview]:
    latest: dict[str, CounselReview] = {}
    for r in (await session.execute(select(CounselReview).where(CounselReview.firm_id == firm_id)
                                    .order_by(CounselReview.reviewed_on, CounselReview.created_at, CounselReview.id))).scalars().all():
        latest[r.figure_key] = r
    return latest


def status_of(key: str, review: CounselReview | None, today: date) -> dict:
    fig = law_figures.get(key, today)
    if review is None:
        return {"state": "not_reviewed", "label": STATES["not_reviewed"]}
    state = "changed_since" if fig.as_of > review.figure_as_of else review.outcome
    return {"state": state, "label": STATES[state], "reviewer": review.reviewer, "reviewed_on": review.reviewed_on.isoformat(),
            "outcome": review.outcome, "note": review.note, "document": review.source_document,
            "figure_as_of_reviewed": review.figure_as_of.isoformat()}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "figures": []}
    reviews = await standing(session, firm.id)
    rows = []
    for key in india_keys():
        fig = law_figures.get(key, today)
        rows.append({**fig.view(today), "counsel": status_of(key, reviews.get(key), today)})
    counts = {s: sum(1 for r in rows if r["counsel"]["state"] == s) for s in STATES}
    return {"applies": True, "as_of": today.isoformat(), "figures": rows, "counts": counts, "total": len(rows),
            "note": "Each figure's own source says how it was read. 'Confirmed' is what the named reviewer said on that day, not a finding that the "
                    "figure is right or that the firm is compliant."}


async def prepare(session: AsyncSession, firm: Firm, role, *, figure_key: str | None, reviewer: str | None, reviewed_on, outcome: str | None,
                  document: str | None, note: str | None = None) -> dict:
    if not applies(firm):
        raise CounselError("Counsel's review of a figure is recorded for an Indian firm.")
    if role not in RECORDERS:
        raise CounselError("A counsel review is recorded by compliance, from counsel's written opinion or note.")
    key = (figure_key or "").strip()
    if key not in india_keys():
        raise CounselError("That is not an Indian law figure. read_governance (section counsel) lists them, each with its key.")
    who = " ".join((reviewer or "").split())
    if len(who) < 3:
        raise CounselError("Name who reviewed it: counsel or the firm's adviser on the law, as the opinion names them.")
    today = firm_time.today(firm)
    if isinstance(reviewed_on, date):
        day = reviewed_on
    else:
        try:
            day = date.fromisoformat(str(reviewed_on))
        except ValueError:
            raise CounselError("Give the day of the opinion as YYYY-MM-DD.")
    if day > today:
        raise CounselError("A review cannot be dated in the future.")
    out = (outcome or "").strip().lower()
    if out not in OUTCOMES:
        raise CounselError(f"An outcome is one of {', '.join(OUTCOMES)}.")
    if out != "confirmed" and len((note or "").strip()) < 8:
        raise CounselError("Say what counsel found: what is wrong with the figure or what they could not confirm.")
    if len((document or "").strip()) < 5:
        raise CounselError("Say where the opinion or note is filed.")
    fig = law_figures.get(key, today)
    before = (await standing(session, firm.id)).get(key)
    if before is not None and before.reviewer == who and before.reviewed_on == day and before.outcome == out:
        raise CounselError("That review is already recorded.")
    if before is not None and day < before.reviewed_on:
        raise CounselError(f"A review of {before.reviewed_on.isoformat()} is already on file: a later review stands over an earlier one.")
    return {"figure": fig, "key": key, "reviewer": who, "reviewed_on": day, "outcome": out, "note": (note or "").strip() or None,
            "document": document.strip(), "before": before}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = CounselReview(firm_id=firm.id, figure_key=p["key"], reviewer=p["reviewer"], reviewed_on=p["reviewed_on"], figure_as_of=p["figure"].as_of,
                        outcome=p["outcome"], note=p["note"], source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="counsel_review_recorded", actor=by, content={
        "figure": p["key"], "reviewer": row.reviewer, "reviewed_on": row.reviewed_on.isoformat(), "outcome": row.outcome,
        "figure_as_of": row.figure_as_of.isoformat(), "document": row.source_document})
    return await overview(session, firm)


# ── Beside the figure, wherever it is shown ─────────────────────────────────────────────────────────────────────────

def _figures_in(obj, found: list, seen: set | None = None) -> None:
    """Every Indian law figure in a result. A tool's result may hold itself (a view listed among its own invoices), so each
    container is visited once."""
    seen = set() if seen is None else seen
    if isinstance(obj, (dict, list, tuple)):
        if id(obj) in seen:
            return
        seen.add(id(obj))
    if isinstance(obj, dict):
        if all(k in obj for k in FIGURE_KEYS) and str(obj.get("key", "")).startswith("in_") and obj["key"] in law_figures.VERSIONS:
            found.append(obj)
        for v in obj.values():
            _figures_in(v, found, seen)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _figures_in(v, found, seen)


async def annotate(session: AsyncSession, firm: Firm | None, result):
    """Puts each Indian law figure in a result beside what counsel said about it ("counsel"), in place. A result with no such figure,
    or a firm outside India, is returned as it was, with no query."""
    if not applies(firm):
        return result
    found: list = []
    _figures_in(result, found)
    if not found:
        return result
    reviews = await standing(session, firm.id)
    today = firm_time.today(firm)
    for f in found:
        f["counsel"] = status_of(f["key"], reviews.get(f["key"]), today)
    return result
