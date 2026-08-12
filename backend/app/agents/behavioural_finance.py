"""Behavioural Finance Agent — client-specific bias profiling and stress playbook.

Analyses decision history, meeting transcripts, and client messages to:
  - Build a cognitive bias profile (loss aversion, status quo bias, overconfidence)
  - Detect recency bias and anchoring language in transcripts
  - Trigger a personalised stress-playbook message when portfolio drawdown exceeds threshold
  - Surface adviser coaching framing tailored to each client's dominant bias
"""
from __future__ import annotations

from app.agents._common import narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import behavioural
from app.aurea_core.graph import list_households
from app.models.enums import AgentKey, AutonomyTier


class BehaviouralFinanceAgent(BaseAgent):
    key = AgentKey.BEHAVIOURAL_FINANCE
    name = "Behavioural Finance"
    lifecycle_stage = "advise_engage"
    default_tier = AutonomyTier.TIER_1
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        import uuid
        households = await list_households(ctx.db, ctx.firm_id)
        results = []
        for h in households:
            result = await behavioural.for_household(ctx.db, uuid.UUID(h["id"]))
            if result:
                results.append(result)
        return {"results": results}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts: list[RecommendationDraft] = []

        for result in sensed["results"]:
            hh_id = result["household_id"]
            hh_name = result["household_name"]
            summary = result["summary"]

            # Only surface if there's something actionable
            has_coaching = summary["coaching_points"] > 0
            is_stressed = summary["is_stressed"]
            has_signals = summary["bias_signals_count"] > 0

            if not has_coaching and not is_stressed and not has_signals:
                continue

            # Build narrative memo
            bias_lines = []
            for c in result["coaching_advice"]:
                bias_lines.append(f"- **{c['bias']}** ({c['severity']}): {c['framing']}")

            stress_line = ""
            if is_stressed:
                stress_line = (
                    f"Portfolio is currently {abs(summary['drawdown_pct']):.1f}% below cost basis — "
                    f"the stress playbook should be activated. "
                )

            signal_line = ""
            if has_signals:
                types = list({s["type"] for s in result["bias_signals"]})
                signal_line = f"Bias signals detected in communications: {', '.join(types).replace('_', ' ')}. "

            memo_prompt = (
                f"You are a senior behavioural finance specialist reviewing the adviser file for {hh_name}. "
                f"Write a concise 2-paragraph adviser briefing note covering:\n"
                f"{stress_line}"
                f"{signal_line}"
                f"{'Coaching advice: ' + chr(10).join(bias_lines) if bias_lines else ''}\n\n"
                f"Paragraph 1: What the behavioural signals tell us about this client's psychology. "
                f"Paragraph 2: Specific framing the adviser should use in the next conversation. "
                f"Be direct and practical. No jargon."
            )
            memo = await narrate(ctx, memo_prompt)

            dominant = summary.get("dominant_bias") or "mixed signals"
            conf = 0.8 if summary["decided_recs"] >= 5 else 0.65

            drafts.append(RecommendationDraft(
                title=f"Behavioural profile — {hh_name}",
                summary=(
                    f"Dominant bias: {dominant.replace('_', ' ')}. "
                    f"{summary['bias_signals_count']} signal(s) in communications. "
                    f"{'⚠ Portfolio stressed. ' if is_stressed else ''}"
                    f"{summary['coaching_points']} coaching pointer(s) for next meeting."
                ),
                rationale=memo,
                confidence=conf,
                priority=1 if is_stressed else 2,
                subject=Subject(type="household", id=hh_id, label=hh_name),
                payload=result,
                evidence={
                    "decided_recs": summary["decided_recs"],
                    "dominant_bias": summary["dominant_bias"],
                    "bias_signals": summary["bias_signals_count"],
                    "drawdown_pct": summary["drawdown_pct"],
                },
            ))

        return drafts
