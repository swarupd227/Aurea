"""A drafted plan summary (B-196): the client's own account of their plan, written from the plan of record alone.

The plan of record (plan_record.py) freezes a household's figures in a baseline. A summary is drafted from that
baseline's frozen figures and nothing else: no figure is worked out again when it is drafted, none is read from the
live file, and no model writes a word of it, so every number a client reads is one a person recorded the plan with.
Its sentences are set out here from those figures, each saying it is an estimate.

A summary is drafted only from a plan that may go to the client: a baseline that passed its review, or one that
needs none under the firm's line. A plan sent back, superseded or still waiting for its review has none.

It reaches the client as a report does (reports.py): the Plan Summary agent drafts it and proposes it, whatever its
tier (`agent_runs.forced_to_proposal`); approving the proposal publishes it and rolling it back withdraws it. Only an
adviser approves it, and the household's recorded adviser when it has one (`decision_rights.PLAN_SUMMARY_AGENTS`,
`named_decider`). Approving checks the plan again: a summary of a baseline that is no longer the current one, or no
longer deliverable, is not published. A rerun rewrites the draft still waiting for a decision. Publishing a summary is
not a delivery: a person records that the plan was delivered, from its evidence (plan_record.record_delivery).
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import markets
from app.models.engagement import ClientReport
from app.models.enums import AgentKey, RecommendationStatus, ReportStatus
from app.models.governance import Recommendation

KIND = "plan_summary"
AGENT = AgentKey.PLAN_SUMMARY.value
READY_STATES = ("reviewed", "delivered", "implementing", "complete")
TIER_WORDS = {"needs": "Needs", "wants": "Wants", "wishes": "Wishes"}
TIER_ONE = {"needs": "a need", "wants": "a want", "wishes": "a wish"}
ESTIMATE = "Every figure here is an estimate from the plan's assumptions, not a promise of what markets will do."


def ready(record: dict | None) -> dict:
    """Whether a summary may be drafted from this plan of record now, and if not, why, in words a person can act on.
    `record` is plan_record.for_household."""
    if record is None:
        return {"ok": False, "why": "That household is not in this firm."}
    baseline = record.get("baseline")
    if not baseline:
        return {"ok": False, "why": f"{record['household']} has no plan baseline: a summary is drafted from a recorded baseline."}
    state = record["state"]
    if record.get("sent_back"):
        return {"ok": False, "why": "This plan's baseline failed review and was sent back: a new baseline is recorded and reviewed first."}
    need = record.get("review_need") or {"needed": True}
    if state in READY_STATES or (state in ("baseline", "scenarios") and not need.get("needed")):
        return {"ok": True, "why": None, "baseline_id": baseline["id"], "version": record["version"]}
    if state == "in_review" or need.get("needed"):
        return {"ok": False, "why": f"Plan v{record['version']} is waiting for its review by a second person; a summary is drafted once it passes."}
    return {"ok": False, "why": f"{record['household']}'s plan is not ready to go to the client."}


def _pct(p: Any) -> str:
    return f"{float(p):.0%}"


def sections(record: dict, ccy: str | None) -> list[dict]:
    """The summary's sections, set out from the baseline's frozen figures and the plan of record alone."""
    baseline = record["baseline"]
    fig = baseline.get("figures") or {}
    money = (lambda x: markets.money(x, fig.get("currency") or ccy))
    delivered = record.get("plan_of_record")
    version = record["version"]
    review = record.get("review")
    about = (f"This summarises plan version {version}, prepared on {baseline['happened_on']}"
             + (f" and checked by a second person on {review['happened_on']}" if review and review.get("outcome") == "passed" else "")
             + (f", and delivered on {delivered['happened_on']}" if delivered and delivered.get("version") == version else "")
             + f". Its figures were taken on {fig.get('as_of')} and do not move with markets since. {ESTIMATE}")
    out = [{"heading": "Your plan", "body": about}]

    r = fig.get("retirement")
    if r:
        parts = []
        if r.get("success_probability") is not None:
            parts.append(f"On the plan's assumptions, your retirement income lasts to age {r.get('longevity_age')} in "
                         f"{_pct(r['success_probability'])} of the market paths it tested, which is "
                         + ("on track." if r.get("on_track") else "below where the plan aims to be."))
        if r.get("retirement_age") is not None:
            parts.append(f"It plans for retirement at {r['retirement_age']}.")
        if r.get("income_need") is not None:
            parts.append(f"The income it plans for is {money(r['income_need'])} a year in today's money.")
        if r.get("sustainable_income") is not None:
            parts.append(f"The income the plan could sustain is {money(r['sustainable_income'])} a year in today's money.")
        if r.get("projected_at_retirement_today") is not None:
            parts.append(f"Your savings are projected to be worth {money(r['projected_at_retirement_today'])} at retirement, in today's money.")
        out.append({"heading": "Retirement", "body": " ".join(parts) or "The plan holds no retirement figures."})

    goals = fig.get("goals") or []
    if goals:
        lines = [f"- {g['name']}: {_pct(g['success_probability'])} likely, "
                 + ("on track" if g.get("on_track") else "needs attention")
                 + (f" ({TIER_ONE[g['tier']]})" if g.get("tier") in TIER_ONE else "")
                 for g in goals if g.get("success_probability") is not None]
        out.append({"heading": "Your goals", "body": "How likely each goal is to be met, on the plan's assumptions:\n\n" + "\n".join(lines)})

    by_tier = (fig.get("tiers") or {}).get("by_tier") or {}
    if by_tier:
        said = [f"{TIER_WORDS.get(k, k.title())}: {'all met' if v else 'not all met'}" for k, v in by_tier.items() if v is not None]
        if said:
            out.append({"heading": "Needs, wants and wishes", "body": "In the plan's central case, " + "; ".join(said) + "."})

    f = fig.get("funded") or {}
    if f.get("ratio") is not None and f.get("assets") is not None and f.get("pv_needs") is not None:
        out.append({"heading": "How well funded the plan is",
                    "body": f"Your assets of {money(f['assets'])} stand against needs worth {money(f['pv_needs'])} today"
                            + (f", valued at {float(f['discount_rate']):.1%} a year" if f.get("discount_rate") is not None else "")
                            + f": a funded ratio of {float(f['ratio']):.2f}, where 1.00 means the needs are covered."})

    if delivered and delivered.get("version") == version and delivered.get("action_items"):
        items = [f"- {i['title']}" + (f", by {i['due_on']}" if i.get("due_on") else "") for i in delivered["action_items"]]
        out.append({"heading": "What happens next", "body": "\n".join(items)})

    source = {"firm": "your adviser's firm", "market": "the market's suggested figures", "platform": "the platform's defaults"}
    out.append({"heading": "The assumptions",
                "body": f"The projections run on assumptions version {fig.get('assumption_version')}, from "
                        f"{source.get(str(fig.get('assumptions_source')), str(fig.get('assumptions_source') or 'the platform'))}. {ESTIMATE}"})
    return out


def data_of(record: dict) -> dict:
    """What a summary report keeps beside its words: which baseline it was drafted from and its frozen figures."""
    fig = record["baseline"].get("figures") or {}
    return {"kind": KIND, "as_of": fig.get("as_of"), "plan_version": record["version"],
            "baseline_id": record["baseline"]["id"], "figures": fig}


def is_summary(report: ClientReport | None) -> bool:
    return report is not None and (report.data or {}).get("kind") == KIND


async def still_current(session: AsyncSession, firm_id: uuid.UUID, report: ClientReport) -> str | None:
    """Why a summary may no longer be published, or None: its baseline must still be the plan's current one, and
    the plan still ready to go to the client."""
    from app.aurea_core import plan_record

    record = await plan_record.for_household(session, report.household_id, firm_id=firm_id, with_today=False)
    now = ready(record)
    if not now["ok"]:
        return now["why"]
    if str(now["baseline_id"]) != str((report.data or {}).get("baseline_id")):
        return (f"plan v{now['version']} has a newer baseline, recorded on {record['baseline']['happened_on']}, than the one "
                "this summary was drafted from; draft the summary again.")
    return None


async def latest(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> dict | None:
    """The household's newest plan summary and its proposal, for the plan of record. None when it has none."""
    rows = (await session.execute(
        select(ClientReport).where(ClientReport.firm_id == firm_id, ClientReport.household_id == household_id)
        .order_by(ClientReport.created_at.desc()))).scalars().all()
    report = next((r for r in rows if is_summary(r)), None)
    if report is None:
        return None
    recs = (await session.execute(
        select(Recommendation).where(Recommendation.firm_id == firm_id, Recommendation.agent_key == AGENT,
                                     Recommendation.subject_id == household_id)
        .order_by(Recommendation.created_at.desc()))).scalars().all()
    rec = next((r for r in recs if str((r.payload or {}).get("report_id")) == str(report.id)), None)
    return {"report_id": str(report.id), "status": str(report.status),
            "with_client": str(report.status) == ReportStatus.CLIENT_READY.value,
            "plan_version": (report.data or {}).get("plan_version"), "baseline_id": (report.data or {}).get("baseline_id"),
            "as_of": (report.data or {}).get("as_of"),
            "published_at": report.published_at.isoformat() if report.published_at else None,
            "recommendation_id": str(rec.id) if rec else None, "proposal_status": str(rec.status) if rec else None,
            "awaiting_decision": rec is not None and str(rec.status) == RecommendationStatus.PROPOSED.value}
