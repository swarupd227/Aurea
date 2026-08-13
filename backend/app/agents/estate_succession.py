"""Estate & Succession Planning Agent — deep family wealth stewardship.

Maps the full family wealth picture (portfolio, trust structures, heir readiness,
concentration) and surfaces succession gaps, trust governance gaps, and a plain-language
estate health memo for the adviser conversation. Jurisdiction-aware (NZ / US / UK).
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


def _trust_review_recommendation(jurisdiction: str) -> str:
    """Return jurisdiction-appropriate trust review obligation wording."""
    if jurisdiction == "UK":
        return (
            "Schedule a trust review with the solicitor. UK trusts must be registered with HMRC's Trust "
            "Registration Service (TRS). Trustee Act 2000 duties require active investment management "
            "and regular review of trustee powers."
        )
    elif jurisdiction == "US":
        return (
            "Schedule a trust review with the estate attorney. Revocable living trusts should be "
            "reviewed following major life events, tax law changes (TCJA sunset 2026), or changes in "
            "state of domicile. Ensure pour-over will and beneficiary designations are current."
        )
    else:
        return (
            "Schedule a trust review; update deed to meet NZ Trusts Act 2019 §23 obligations. "
            "Three-yearly review recommended with the trust solicitor."
        )


def _trust_governance_recommendation(jurisdiction: str) -> str:
    if jurisdiction == "UK":
        return "Update trust governance and register with HMRC Trust Registration Service (TRS) — mandatory for UK express trusts."
    elif jurisdiction == "US":
        return "Update trust deed; review pour-over will, health care proxies, and durable power of attorney. Confirm step-up in basis planning."
    else:
        return "Schedule a trust review; update deed to meet NZ Trusts Act 2019 §23 obligations."


def _jurisdiction_specific_gaps(brain: dict, entities_gov: list[dict], jurisdiction: str) -> list[dict]:
    """Add jurisdiction-specific estate planning gaps."""
    gaps: list[dict] = []
    totals = brain.get("totals", {})
    total_value = totals.get("total_value", 0) or 0
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]
    married = len(adults) >= 2

    if jurisdiction == "US":
        # US: check estate vs post-sunset exemption
        _US_EXEMPTION = 7_000_000
        multiplier = 2 if married else 1
        threshold = _US_EXEMPTION * multiplier
        retirement = sum(
            (p.get("profile", {}).get("tax", {}).get("ira_value", 0) or 0)
            + (p.get("profile", {}).get("tax", {}).get("roth_value", 0) or 0)
            for p in adults
        )
        if total_value + retirement > threshold:
            exposure = max(0, total_value + retirement - threshold) * 0.40
            gaps.append({
                "type": "us_estate_tax_exposure",
                "severity": "high",
                "title": "Federal estate tax exposure — reduced exemption",
                "detail": (
                    f"Gross estate (~${total_value + retirement:,.0f}) exceeds the "
                    f"post-sunset exemption (${threshold:,.0f}). "
                    f"Estimated federal estate tax: ~${exposure:,.0f} at 40%."
                ),
                "recommendation": (
                    "Engage estate attorney now: consider SLAT, GRAT, annual gift exclusion maximisation, "
                    "and Roth conversion strategy to reduce the taxable estate."
                ),
            })

        # US: no irrevocable trust for estate-exposed household
        irrevocable_trusts = [e for e in entities_gov if "irrevocable" in (e.get("entity_type") or "").lower()]
        if not irrevocable_trusts and total_value > _US_EXEMPTION:
            gaps.append({
                "type": "no_irrevocable_trust",
                "severity": "medium",
                "title": "No irrevocable trust structure",
                "detail": "No irrevocable trust is recorded for this household.",
                "recommendation": (
                    "For estates above the federal exemption, irrevocable trusts (SLAT, ILIT, GRAT) "
                    "are the primary tools to move assets outside the taxable estate while retaining some access."
                ),
            })

    elif jurisdiction == "UK":
        # UK: LPA status check
        lpa_missing = [
            p.get("preferred_name") or p["full_name"].split()[0]
            for p in adults
            if (p.get("profile", {}).get("tax", {}).get("lpa_status") or "none") == "none"
        ]
        if lpa_missing:
            gaps.append({
                "type": "lpa_missing",
                "severity": "high",
                "title": "No Lasting Power of Attorney registered",
                "detail": (
                    f"{', '.join(lpa_missing)} {'has' if len(lpa_missing) == 1 else 'have'} no LPA registered. "
                    "Without a Property & Finance LPA, family cannot manage assets if capacity is lost — "
                    "critical when pension decisions and estate planning need to continue."
                ),
                "recommendation": (
                    "Register both a Property & Finance LPA and a Health & Welfare LPA with the Office of the "
                    "Public Guardian. Processing typically takes 3–4 months — act before capacity is in question."
                ),
            })

        # UK: IHT threshold check
        _UK_NRB = 325_000
        _UK_RNRB = 175_000
        multiplier = 2 if married else 1
        threshold = (_UK_NRB + _UK_RNRB) * multiplier
        if total_value > threshold:
            iht = max(0, total_value - threshold) * 0.40
            gaps.append({
                "type": "uk_iht_exposure",
                "severity": "high" if iht > 100_000 else "medium",
                "title": "IHT threshold exceeded",
                "detail": (
                    f"Estate value (£{total_value:,.0f}) exceeds the combined NRB/RNRB threshold "
                    f"(£{threshold:,.0f} for {'a couple' if married else 'an individual'}). "
                    f"Estimated IHT: £{iht:,.0f} (before pension inclusion from April 2027)."
                ),
                "recommendation": (
                    "Prioritise PET gifting strategy (start 7-year clock), consider whole-of-life policy "
                    "in trust to cover the IHT liability, and review pension drawdown strategy before April 2027."
                ),
            })

    return gaps


def _succession_gaps(brain: dict, entities_gov: list[dict], jurisdiction: str = "NZ") -> list[dict]:
    """Identify succession gaps from the household brain. Returns list of gap dicts."""
    gaps: list[dict] = []
    totals = brain.get("totals", {})
    total_value = totals.get("total_value", 0) or 0
    by_class = totals.get("by_asset_class", {})
    alternatives = by_class.get("alternatives", 0)
    persons = brain.get("persons", [])
    mandates = brain.get("mandates", [])

    # 1. Private-asset concentration (illiquid succession problem — all jurisdictions)
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
                "recommendation": _trust_governance_recommendation(jurisdiction),
            })

    # 3. Periodic trust review gap (3+ years, no documented review)
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
                        "recommendation": _trust_review_recommendation(jurisdiction),
                    })
            except (ValueError, TypeError):
                pass

    # 4. Next-gen heir without advisory mandate (all jurisdictions)
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

    # 5. No trust structure (all jurisdictions, threshold varies)
    entities = brain.get("entities", [])
    if not entities and total_value >= 1_000_000:
        gaps.append({
            "type": "no_trust_structure",
            "severity": "medium",
            "title": "No formal structure",
            "detail": "No trust or legal entity is held against this household.",
            "recommendation": "Explore a family trust for estate-planning efficiency and asset protection.",
        })

    # 6. No legacy goal for person ≥ 65 (all jurisdictions)
    for p in persons:
        age = _age(p.get("date_of_birth"))
        if age and age >= 65:
            name = p.get("preferred_name") or p["full_name"]
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

    # 7. Jurisdiction-specific gaps
    gaps.extend(_jurisdiction_specific_gaps(brain, entities_gov, jurisdiction))

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
        firm_jurisdiction = ctx.firm.jurisdiction if hasattr(ctx.firm, "jurisdiction") else "NZ"

        for brain in sensed["brains"]:
            hh = brain["household"]
            entities_gov = sensed["entities_gov_by_hh"].get(hh["id"], [])

            # Household-level jurisdiction overrides firm default
            jurisdiction = hh.get("values", {}).get("jurisdiction") or firm_jurisdiction

            gaps = _succession_gaps(brain, entities_gov, jurisdiction)
            if not gaps:
                continue

            totals = brain.get("totals", {})
            total_value = totals.get("total_value", 0) or 0
            by_class = totals.get("by_asset_class", {})

            high_gaps = [g for g in gaps if g["severity"] == "high"]
            risk_level = "High" if high_gaps else ("Moderate" if gaps else "Low")
            confidence = 0.88 if high_gaps else 0.75

            gap_text = "\n".join(
                f"- [{g['severity'].upper()}] {g['title']}: {g['detail']}" for g in gaps[:5]
            )

            # Jurisdiction-aware regulatory context
            reg_context = {
                "US": "relevant US estate tax rules (post-TCJA sunset, Reg BI, step-up in basis, SECURE 2.0 RMDs)",
                "UK": "relevant UK regulatory context (IHT nil-rate bands, Trustee Act 2000, TRS registration, LPA obligations)",
                "NZ": "relevant NZ regulatory context (Trusts Act 2019, FMC Act)",
            }.get(jurisdiction, "relevant regulatory context")

            currency_code = {"US": "USD", "UK": "GBP"}.get(jurisdiction, "NZD")

            fallback = (
                f"Estate review for {hh['name']}: {len(gaps)} succession gap(s) identified "
                f"(risk level: {risk_level}). Total estate value {currency_code} {total_value:,.0f}. "
                f"Key gaps: {'; '.join(g['title'] for g in gaps[:3])}. "
                "Adviser review recommended before next client meeting."
            )
            prompt = (
                f"Write a concise estate health memo for adviser use — household '{hh['name']}', "
                f"jurisdiction: {jurisdiction}, total estate value approximately {currency_code} {total_value:,.0f}. "
                f"Wealth breakdown: {', '.join(f'{k} {v:,.0f}' for k, v in by_class.items() if v)}. "
                f"Succession gaps identified:\n{gap_text}\n\n"
                "Summarise the estate risk picture in 2–3 sentences, name the top priority action, "
                f"and note the {reg_context}. "
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
                    "jurisdiction": jurisdiction,
                    "risk_level": risk_level.lower(),
                    "total_estate_value": total_value,
                    "gaps": gaps,
                    "wealth_breakdown": by_class,
                    "entities_gov": entities_gov,
                },
                evidence={"gaps_count": len(gaps), "high_count": len(high_gaps)},
            ))

        return drafts
