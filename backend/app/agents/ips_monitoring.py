"""IPS monitor - reads every mandate with an IPS on file against its terms and puts each breach to a person.

A breach is one proposal per mandate, kept up to date while it stands and closed when it clears. Drift
past a band is left out while a rebalance is open for that mandate, because that proposal is already the
answer to it; a band still breached once a rebalance is dismissed comes back here.

It never trades and never changes an IPS. The only thing approving does is accept the breaches as they
stand, for a limited time, under the approver's name (`aurea_core/ips.py`). Fixing one is a rebalance, a
sale or a new version of the IPS, and all of those are a person's.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import firm_time, ips
from app.models.enums import AgentKey, AutonomyTier, RecommendationStatus
from app.models.governance import Recommendation
from app.models.graph import Mandate
from app.models.identity import User


def _words(f: dict) -> str:
    kind = f["kind"]
    if kind == "allocation":
        return (f"{f['asset_class'].replace('_', ' ')} {f['weight']:.1%} against a range of "
                f"{f['low']:.1%} to {f['high']:.1%}")
    if kind == "position":
        return f"{f['symbol']} {f['weight']:.1%} against a limit of {f['limit']:.1%}"
    if kind == "restricted":
        return f"{f['symbol']} held at {f['weight']:.1%}, and it is restricted"
    if kind == "cash":
        return f"cash {f['weight']:.1%} against a floor of {f['limit']:.1%}"
    return "the risk profile has changed since this IPS was accepted"


class IpsMonitoringAgent(BaseAgent):
    key = AgentKey.IPS_MONITORING
    name = "IPS Monitor"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_2
    scheduled = True
    # One proposal per mandate while a breach stands; a run that no longer finds it closes it.
    expires_unraised = True

    async def sense(self, ctx: AgentContext) -> dict:
        overview = await ips.for_firm(ctx.session, ctx.firm.id)
        rebalancing = {str(subject) for subject in (await ctx.session.execute(
            select(Recommendation.subject_id).where(
                Recommendation.firm_id == ctx.firm.id,
                Recommendation.agent_key == AgentKey.DRIFT_REBALANCING,
                Recommendation.status == RecommendationStatus.PROPOSED))).scalars().all()}
        rows = []
        for row in overview["mandates"]:
            findings = [f for f in row["findings"]
                        if not (f["kind"] == "allocation" and row["mandate_id"] in rebalancing)]
            if findings:
                rows.append({"mandate_id": row["mandate_id"], "mandate": row["mandate"], "household": row["household"],
                             "version": row["ips"]["version"], "findings": findings, "price_source": row["price_source"]})
        return {"rows": rows, "summary": overview["summary"]}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        until = firm_time.today(ctx.firm) + timedelta(days=ips.EXCEPTION_DAYS)
        drafts = []
        for row in sensed.get("rows") or []:
            findings = row["findings"]
            kinds = {f["kind"] for f in findings}
            synthetic = row["price_source"] == "synthetic"
            said = "; ".join(_words(f) for f in findings)
            drafts.append(RecommendationDraft(
                # "term(s)", not a plural that changes with the count: the title is the proposal's
                # identity once its numbers are stripped, and it must not change when one term clears.
                title=f"IPS breach - {row['mandate']}: {len(findings)} term(s) outside",
                summary=said[0].upper() + said[1:] + ".",
                rationale=(
                    f"Measured against version {row['version']} of the IPS accepted for this mandate. Approving "
                    f"accepts these breaches as they stand until {firm_time.spoken(until)}, under your name, and "
                    f"nothing trades. A breach that grows comes back. To fix one instead, dismiss this and "
                    f"rebalance, or record a new version of the IPS."
                    + (" At least one holding is valued on a synthetic price." if synthetic else "")
                ),
                confidence=0.75 if synthetic else 0.9,
                priority=2 if kinds & {"restricted", "profile"} else 3,
                subject=Subject("mandate", uuid.UUID(row["mandate_id"]), row["mandate"]),
                payload={"signal": "ips_breach", "findings": findings, "version": row["version"],
                         "accept_until": until.isoformat(), "household": row["household"]},
                evidence={"findings": len(findings), "kinds": sorted(kinds), "price_source": row["price_source"]},
            ))
        return drafts

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        # Accepting a breach of a client's own terms is a person's decision. At Tier 3 the runtime calls
        # act() with nobody having decided, so the proposal stays open for one.
        if not recommendation.decided_by:
            return {"executed": False,
                    "note": "A breach is accepted only when an adviser or the portfolio team approves; "
                            "this proposal is waiting for that decision."}
        mandate = await ctx.session.get(Mandate, ctx.subject.id)
        if mandate is None:
            return {"executed": False, "note": "Mandate not found."}
        # An exception is evidence, and setting aside a client's terms with no reason is not.
        reason = (recommendation.decision_note or "").strip()
        if len(reason) < 3:
            return {"executed": False, "note": "Nothing was accepted, because accepting a breach needs the reason. "
                                               "The next check raises it again."}
        payload = {**(recommendation.payload or {}), **(recommendation.modified_payload or {})}
        approver = await ctx.session.get(User, recommendation.decided_by)
        result = await ips.accept(
            ctx.session, mandate, firm=ctx.firm, terms=[f["term"] for f in payload.get("findings") or []],
            reason=reason, by=approver.email if approver else "unknown", recommendation_id=recommendation.id)
        return {"executed": bool(result["accepted"]), **result,
                "note": (f"Accepted {len(result['accepted'])} breach(es) until {result['until']}"
                         + (f"; {len(result['moved_on'])} had already moved on" if result["moved_on"] else "") + ".")}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        # The runtime's rollback entry in the ledger names who did it.
        withdrawn = await ips.withdraw(ctx.session, recommendation.id, firm_id=ctx.firm.id, by="rollback")
        return {"reversed": withdrawn > 0, "withdrawn": withdrawn,
                "note": f"{withdrawn} exception(s) withdrawn. They stay on record, and the next check raises the breach again."}
