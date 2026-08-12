"""Estate & Succession Planning Agent — deep family wealth stewardship.

Maps the full family wealth picture (portfolio, trust structures, heir readiness,
concentration) and surfaces succession gaps, NZ Trusts Act 2019 compliance gaps,
and a plain-language estate health memo for the adviser conversation.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier
from app.models.graph import LegalEntity, Mandate


def _age(dob: str | None) -> int | None:
    if not dob:
        return None
    try:
        y, m, d = map(int, dob.split("-"))
        today = date.today()
        return today.year - y - ((today.month, today.day) < (m, d))
    except Exception:
        return None


def _governance_score(gov: dict) -> int:
    """Score a trust's governance completeness 0–100."""
    score = 0
    if gov.get("trustees"):
        score += 25
    if gov.get("settlor"):
        score += 25
    if gov.get("beneficiaries"):
        score += 25
    if gov.get("established") or gov.get("review_date"):
        score += 25
    return score


def _succession_gaps(brain: dict, entities_gov: list[dict]) -> list[dict]:
    """Identify succession gaps from the household brain. Returns list of gap dicts."""
    gaps: list[dict] = []
    totals = brain.get("totals", {})
    total_value = totals.get("total_value", 0) or 0
    by_class = totals.get("by_asset_class", {})
    alternatives = by_class.get("alternatives", 0)
    persons = brain.get("persons", [])
    mandates = brain.get("mandates", [])

    # 1. Private-asset concentration (illiquid succession problem)
    if total_value > 0:
        alt_pct = alternatives / total_value
        if alt_pct >= 0.35:
            gaps.append({
                "type": "concentration_risk",
                "severity": "high" if alt_pct >= 0.5 else "medium",
                "title": "Private-asset concentration",
                "detail": f"{alt_pct:.0%} in illiquid alternatives — succession triggers forced sale risk.",
                "recommendation": "Model a liquidity event; review trust deed distribution powers and loan structures.",
            })

    # 2. Trust governance gaps
    for ent in entities_gov:
        gov = ent.get("governance", {})
        score = _governance_score(gov)
        name = ent.get("name", "Trust")
        if score < 75:
            gaps.append({
                "type": "trust_governance_gap",
                "severity": "high" if score < 50 else "medium",
                "title": f"Incomplete governance — {name}",
                "detail": f"Trust governance score {score}/100. Missing: "
                          + ", ".join(f for f, k in [
                              ("trustees", "trustees"), ("settlor", "settlor"),
                              ("beneficiaries", "beneficiaries"), ("establishment date", "established"),
                          ] if not gov.get(k)),
                "recommendation": "Schedule a trust review; update deed to meet NZ Trusts Act 2019 §23 obligations.",
            })

    # 3. NZ Trusts Act 2019 periodic-review gap (trusts > 3 years old without documented review)
    for ent in entities_gov:
        gov = ent.get("governance", {})
        established_str = gov.get("established")
        if established_str and not gov.get("review_date"):
            try:
                est_year = int(established_str[:4])
                if date.today().year - est_year >= 3:
                    gaps.append({
                        "type": "trust_review_overdue",
                        "severity": "medium",
                        "title": f"Review overdue — {ent.get('name', 'Trust')}",
                        "detail": f"Trust established {established_str}; no periodic review recorded.",
                        "recommendation": "NZ Trusts Act 2019 §23 requires trustees to actively manage. "
                                          "Schedule a three-yearly review with the trust solicitor.",
                    })
            except (ValueError, TypeError):
                pass

    # 4. Next-gen heir without advisory mandate
    heir_persons = [p for p in persons if p.get("is_next_gen")]
    if heir_persons:
        person_ids_with_mandate = {m.get("person_id") for m in mandates if m.get("person_id")}
        for heir in heir_persons:
            if heir["id"] not in person_ids_with_mandate:
                name = heir.get("preferred_name") or heir["full_name"]
                gaps.append({
                    "type": "heir_no_mandate",
                    "severity": "medium",
                    "title": f"No advisory mandate — {name}",
                    "detail": f"{name} is designated next-gen heir but has no active mandate.",
                    "recommendation": "Establish an education-led advisory mandate before the wealth transfer occurs.",
                })

    # 5. No trust structure at all (for private-wealth households without entities)
    entities = brain.get("entities", [])
    if not entities and total_value >= 1_000_000:
        gaps.append({
            "type": "no_trust_structure",
            "severity": "medium",
            "title": "No formal structure",
            "detail": "No trust or legal entity is held against this household.",
            "recommendation": "Explore a family trust for estate-planning efficiency and asset protection.",
        })

    # 6. Retirement age without documented succession plan (person >= 65)
    for p in persons:
        age = _age(p.get("date_of_birth"))
        if age and age >= 65:
            name = p.get("preferred_name") or p["full_name"]
            # Check if there's a legacy goal
            goals = brain.get("goals", [])
            has_legacy_goal = any(g.get("kind") == "legacy" for g in goals)
            if not has_legacy_goal:
                gaps.append({
                    "type": "no_legacy_goal",
                    "severity": "low",
                    "title": f"No legacy goal — {name}",
                    "detail": f"{name} (age {age}) has no documented legacy or bequest goal.",
                    "recommendation": "Add a legacy goal to model wealth-transfer scenarios and tax implications.",
                })
            break

    return gaps


class EstateSucessionAgent(BaseAgent):
    key = AgentKey.ESTATE_SUCCESSION
    name = "Estate & Succession Planning"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_1
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        if ctx.subject.type in (None, "firm"):
            from app.aurea_core.graph import list_households
            ids = [h["id"] for h in await list_households(s, ctx.firm.id)]
        else:
            ids = [str(ctx.subject.id)]

        brains = []
        entities_gov_by_hh: dict[str, list[dict]] = {}

        for hid in ids:
            brain = await household_brain(s, hid)
            if not brain:
                continue
            brains.append(brain)
            # Load full LegalEntity rows (including governance) for this household
            import uuid
            rows = (
                await s.execute(select(LegalEntity).where(LegalEntity.household_id == uuid.UUID(hid)))
            ).scalars().all()
            entities_gov_by_hh[hid] = [
                {
                    "id": str(e.id), "name": e.name, "entity_type": e.entity_type,
                    "governance": e.governance or {}, "jurisdiction": e.jurisdiction,
                }
                for e in rows
            ]

        return {"brains": brains, "entities_gov_by_hh": entities_gov_by_hh}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts: list[RecommendationDraft] = []
        for brain in sensed["brains"]:
            hh = brain["household"]
            entities_gov = sensed["entities_gov_by_hh"].get(hh["id"], [])
            gaps = _succession_gaps(brain, entities_gov)
            if not gaps:
                continue

            totals = brain.get("totals", {})
            total_value = totals.get("total_value", 0) or 0
            by_class = totals.get("by_asset_class", {})

            high_gaps = [g for g in gaps if g["severity"] == "high"]
            risk_level = "High" if high_gaps else ("Moderate" if gaps else "Low")
            confidence = 0.88 if high_gaps else 0.75

            # Build a readable gap summary for the LLM
            gap_text = "\n".join(
                f"- [{g['severity'].upper()}] {g['title']}: {g['detail']}" for g in gaps[:5]
            )
            fallback = (
                f"Estate review for {hh['name']}: {len(gaps)} succession gap(s) identified "
                f"(risk level: {risk_level}). Total estate value NZD {total_value:,.0f}. "
                f"Key gaps: {'; '.join(g['title'] for g in gaps[:3])}. "
                "Adviser review recommended before next client meeting."
            )
            prompt = (
                f"Write a concise estate health memo for adviser use — household '{hh['name']}', "
                f"total estate value approximately NZD {total_value:,.0f}. "
                f"Wealth breakdown: {', '.join(f'{k} {v:,.0f}' for k, v in by_class.items() if v)}. "
                f"Succession gaps identified:\n{gap_text}\n\n"
                "Summarise the estate risk picture in 2–3 sentences, name the top priority action, "
                "and note the NZ regulatory context where relevant (Trusts Act 2019, FMC Act). "
                "Write for the adviser — professional, precise, no hype."
            )
            memo = await narrate(
                ctx, task="estate_memo", system=firm_voice(ctx), prompt=prompt, fallback=fallback, max_tokens=400,
            )

            priority = 1 if high_gaps else 2
            drafts.append(RecommendationDraft(
                title=f"Estate review — {hh['name']} ({risk_level} risk)",
                summary=f"{len(gaps)} succession gap(s): {'; '.join(g['title'] for g in gaps[:3])}.",
                rationale=memo,
                confidence=confidence,
                priority=priority,
                subject=Subject("household", hh["id"], hh["name"]),
                payload={
                    "signal": "estate_succession",
                    "risk_level": risk_level.lower(),
                    "total_estate_value": total_value,
                    "gaps": gaps,
                    "wealth_breakdown": by_class,
                    "entities_gov": entities_gov,
                },
                evidence={"gaps_count": len(gaps), "high_count": len(high_gaps)},
            ))

        return drafts
