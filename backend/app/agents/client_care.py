"""Client care & retention agent (spec Table 10) — deep. Tier 1/2 — adviser approves outreach.

Detects at-risk and relationship signals — market volatility, life milestones, next-gen heir
engagement gaps — and proposes proactive, personalised outreach. During volatility it grounds
the message by running the household's plan through a stress scenario (a designed journey)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.graph import household_brain, list_households
from app.aurea_core.planning import stress_test
from app.models.client_experience import Message
from app.models.enums import AgentKey, AutonomyTier, MessageAuthor
from app.models.identity import User


def _age(dob: str | None) -> int | None:
    if not dob:
        return None
    try:
        y, m, d = map(int, dob.split("-"))
        today = date.today()
        return today.year - y - ((today.month, today.day) < (m, d))
    except Exception:
        return None


class ClientCareAgent(BaseAgent):
    key = AgentKey.CLIENT_CARE
    name = "Client Care & Retention"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_1
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        if ctx.subject.type in (None, "firm"):
            ids = [h["id"] for h in await list_households(s, ctx.firm.id)]
        else:
            ids = [str(ctx.subject.id)]
        brains = [b for b in [await household_brain(s, hid) for hid in ids] if b]
        scenario = (ctx.config or {}).get("scenario", "covid_2020")
        return {"brains": brains, "scenario": scenario}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts: list[RecommendationDraft] = []
        scenario = sensed["scenario"]
        for brain in sensed["brains"]:
            hh = brain["household"]
            allocation = brain["totals"]["by_asset_class"]
            stress = stress_test(allocation, [scenario]).get(scenario, {})

            # 1. Volatility reassurance (stress-grounded).
            drafts.append(await self._volatility(ctx, hh, scenario, stress))

            # 2. Next-gen heir engagement gap.
            for p in brain.get("persons", []):
                if p.get("is_next_gen"):
                    drafts.append(self._heir(ctx, hh, p))
                    break

            # 3. Pre-retirement milestone.
            for p in brain.get("persons", []):
                age = _age(p.get("date_of_birth"))
                stage = (p.get("profile") or {}).get("life_stage")
                if (age is not None and 60 <= age <= 67) or stage == "pre-retirement":
                    drafts.append(self._milestone(ctx, hh, p, age))
                    break
        return drafts

    async def _volatility(self, ctx, hh, scenario, stress) -> RecommendationDraft:
        impact_pct = stress.get("impact_pct", 0)
        impact_val = stress.get("impact_value", 0)
        fallback = (
            f"Proactive reassurance for {hh['name']}. Under a '{scenario}' stress the portfolio would "
            f"move {impact_pct:.0%} (${impact_val:,.0f}). The message explains what the plan says to do "
            "and reinforces the named adviser relationship — sent only on approval."
        )
        prompt = (
            f"Draft a warm, reassuring outreach message for household '{hh['name']}' during market "
            f"volatility, grounded in a '{scenario}' stress result of {impact_pct:.0%} "
            f"(${impact_val:,.0f}). Explain what the plan says to do; no alarm; reinforce the adviser. "
            "The adviser approves before sending."
        )
        rationale = await narrate(ctx, task="narrative", system=firm_voice(ctx), prompt=prompt, fallback=fallback)
        return RecommendationDraft(
            title=f"Volatility outreach — {hh['name']}",
            summary=f"Stress-grounded reassurance ({scenario}: {impact_pct:.0%}).",
            rationale=rationale, confidence=0.8, priority=2,
            subject=Subject("household", hh["id"], hh["name"]),
            payload={"signal": "volatility", "scenario": scenario, "stress": stress, "channel": "secure_message"},
            evidence={"scenario": scenario})

    def _heir(self, ctx, hh, person) -> RecommendationDraft:
        name = person.get("preferred_name") or person["full_name"]
        return RecommendationDraft(
            title=f"Engage next-gen heir — {name}",
            summary=f"{hh['name']}: a values-led, digital-first onboarding for {name} protects the relationship through the wealth transfer.",
            rationale="Heirs frequently leave a parent's adviser once wealth transfers. Propose an "
                      f"education-led, values-aligned engagement for {name} now — earn the next relationship early.",
            confidence=0.72, priority=3,
            subject=Subject("household", hh["id"], hh["name"]),
            payload={"signal": "heir_engagement", "person_id": person["id"], "channel": "invite"},
            evidence={"source": "relationship_graph"})

    def _milestone(self, ctx, hh, person, age) -> RecommendationDraft:
        name = person.get("preferred_name") or person["full_name"]
        return RecommendationDraft(
            title=f"Retirement milestone — {name}",
            summary=f"{hh['name']}: {name} is approaching retirement — review decumulation and the cash buffer.",
            rationale=f"{name}{f' (age {age})' if age else ''} is in the retirement window where sequencing-"
                      "of-returns risk dominates. Propose a decumulation review and a 1–2 year cash buffer.",
            confidence=0.74, priority=2,
            subject=Subject("household", hh["id"], hh["name"]),
            payload={"signal": "milestone", "person_id": person["id"], "theme": "decumulation"},
            evidence={"source": "life_stage"})

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Deliver the approved outreach into the client's secure Canvas thread, in the adviser's name."""
        s = ctx.session
        household_id = recommendation.subject_id
        if not household_id:
            return {"executed": True, "note": "No household; outreach not delivered."}
        adviser = (
            await s.execute(select(User).where(User.firm_id == ctx.firm.id, User.role == "adviser"))
        ).scalars().first()
        author = adviser.full_name if adviser else f"{ctx.firm.name} Advice Team"
        body = (recommendation.modified_payload or recommendation.payload or {}).get("message") \
            or recommendation.rationale or recommendation.summary
        msg = Message(firm_id=ctx.firm.id, household_id=household_id, author_role=MessageAuthor.ADVISER,
                      author_name=author, body=body, source_recommendation_id=recommendation.id,
                      read_by_adviser=True, read_by_client=False)
        s.add(msg)
        await s.flush()
        return {"executed": True, "message_id": str(msg.id),
                "note": f"Outreach delivered to the client's Canvas inbox as {author}."}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        result = (recommendation.payload or {}).get("execution_result", {})
        mid = result.get("message_id")
        if mid:
            msg = await ctx.session.get(Message, mid)
            if msg:
                await ctx.session.delete(msg)
                await ctx.session.flush()
                return {"reversed": True, "note": "Outreach message withdrawn from the client's inbox."}
        return {"reversed": False, "note": "No delivered message to withdraw."}
