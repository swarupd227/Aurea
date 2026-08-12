"""Behavioural Finance Layer — client-specific cognitive bias profiling and stress playbook.

Four modules:
  1. Decision profile    — bias scores from recommendation approve / modify / dismiss history
  2. Stress playbook    — portfolio drawdown detection + personalised draft message
  3. Bias signal scan   — recency bias, anchoring, loss-aversion language in transcripts & messages
  4. Adviser coaching   — framing guidance tailored to the client's dominant bias
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain, list_households
from app.models.client_experience import Message
from app.models.engagement import Meeting
from app.models.governance import Recommendation

# ── Constants ─────────────────────────────────────────────────────────────────
LOSS_AVERSION = "loss_aversion"
STATUS_QUO_BIAS = "status_quo_bias"
OVERCONFIDENCE = "overconfidence"
RECENCY_BIAS = "recency_bias"
ANCHORING = "anchoring"

STRESS_THRESHOLD = -0.10  # −10% drawdown triggers the stress playbook

_RECENCY_SIGNALS = [
    "recently", "lately", "everyone is buying", "everyone is selling",
    "market has been", "trending", "hot right now", "can't go wrong",
    "sure thing", "all my friends", "heard that",
]
_ANCHORING_PATTERNS = [
    r"when it gets back to",
    r"back to \$[\d,]+",
    r"at \$[\d,]+",
    r"target of \$[\d,]+",
    r"price of \$[\d,]+",
    r"should be at \$[\d,]+",
    r"used to be worth",
]
_LOSS_AVERSION_SIGNALS = [
    "can't afford to lose", "don't want to lose", "losing money",
    "worried about", "nervous about", "scared", "don't want to sell at a loss",
    "what if it drops", "what if it falls",
]
_REBALANCE_KEYWORDS = ("harvest", "rebalanc", "drift", "trim", "sell", "reduce")


# ── Module 1: Decision profile ────────────────────────────────────────────────

def _score_decisions(recs: list) -> dict:
    """Derive bias scores from recommendation decision history."""
    decided = [
        r for r in recs
        if r.status in ("approved", "modified", "dismissed", "executed", "rolled_back")
    ]
    total = len(decided)
    if not total:
        return {
            "total": 0,
            "approved": 0, "modified": 0, "dismissed": 0,
            "approve_rate": 0.0, "modify_rate": 0.0, "dismiss_rate": 0.0,
            "bias_scores": {LOSS_AVERSION: 0.0, STATUS_QUO_BIAS: 0.0, OVERCONFIDENCE: 0.0},
            "dominant_bias": None,
            "recent_decisions": [],
        }

    approved = sum(1 for r in decided if r.status in ("approved", "executed"))
    modified = sum(1 for r in decided if r.status == "modified")
    dismissed = sum(1 for r in decided if r.status == "dismissed")

    # Loss aversion: high dismiss rate specifically on rebalancing / harvest recs
    rebalance = [r for r in decided if any(kw in (r.title or "").lower() for kw in _REBALANCE_KEYWORDS)]
    loss_aversion = (
        sum(1 for r in rebalance if r.status == "dismissed") / len(rebalance)
    ) if rebalance else 0.0

    status_quo = dismissed / total
    overconfidence = modified / total

    bias_scores = {
        LOSS_AVERSION: round(loss_aversion, 2),
        STATUS_QUO_BIAS: round(status_quo, 2),
        OVERCONFIDENCE: round(overconfidence, 2),
    }
    dominant = max(bias_scores, key=bias_scores.get) if any(bias_scores.values()) else None

    sorted_decided = sorted(
        decided,
        key=lambda r: r.decided_at or date.min,
        reverse=True,
    )

    return {
        "total": total,
        "approved": approved,
        "modified": modified,
        "dismissed": dismissed,
        "approve_rate": round(approved / total, 2),
        "modify_rate": round(modified / total, 2),
        "dismiss_rate": round(dismissed / total, 2),
        "bias_scores": bias_scores,
        "dominant_bias": dominant,
        "recent_decisions": [
            {
                "title": r.title,
                "agent_key": r.agent_key,
                "status": r.status,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                "decision_note": r.decision_note,
            }
            for r in sorted_decided[:6]
        ],
    }


# ── Module 2: Bias signal scan ────────────────────────────────────────────────

def _scan_text(text: str, source: str) -> list[dict]:
    """Return bias signals detected in a piece of text."""
    signals: list[dict] = []
    lo = text.lower()

    for signal in _RECENCY_SIGNALS:
        if signal in lo:
            signals.append({"type": RECENCY_BIAS, "signal": signal, "source": source,
                            "description": "Language consistent with recency bias — weighting recent events too heavily."})
            break  # one per type per text source

    for pattern in _ANCHORING_PATTERNS:
        if re.search(pattern, lo):
            signals.append({"type": ANCHORING, "signal": pattern, "source": source,
                            "description": "Reference to a specific price point — possible anchoring."})
            break

    for signal in _LOSS_AVERSION_SIGNALS:
        if signal in lo:
            signals.append({"type": LOSS_AVERSION, "signal": signal, "source": source,
                            "description": "Language expressing strong loss sensitivity."})
            break

    return signals


# ── Module 3: Stress playbook ──────────────────────────────────────────────────

def _stress_level(brain: dict) -> dict:
    """Compute portfolio drawdown vs cost basis."""
    total_value = brain["totals"].get("total_value", 0)
    total_cost = sum(
        float(pos.get("cost_basis") or 0)
        for acc in brain.get("accounts", [])
        for pos in acc.get("positions", [])
    )
    if not total_cost or not total_value:
        return {
            "total_value": total_value,
            "total_cost": total_cost,
            "unrealised_pnl": 0,
            "drawdown_pct": 0.0,
            "is_stressed": False,
        }

    drawdown = (total_value - total_cost) / total_cost
    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "unrealised_pnl": round(total_value - total_cost, 2),
        "drawdown_pct": round(drawdown * 100, 2),
        "is_stressed": drawdown < STRESS_THRESHOLD,
        "stress_threshold_pct": STRESS_THRESHOLD * 100,
    }


def _draft_stress_message(brain: dict, stress: dict, profile: dict) -> dict | None:
    """Draft a reassurance message when the portfolio is under stress."""
    if not stress.get("is_stressed"):
        return None

    hh_name = brain["household"]["name"]
    drawdown = abs(stress["drawdown_pct"])
    goals = brain.get("goals", [])
    values = (brain["household"].get("values") or {}).get("themes", [])

    longest_goal = max(
        goals, key=lambda g: int((g.get("assumptions") or {}).get("years", 0))
    ) if goals else None
    horizon = f"{(longest_goal.get('assumptions') or {}).get('years', 10)}-year {longest_goal['kind']} plan" if longest_goal else "your long-term plan"
    values_text = f" — anchored to {', '.join(values[:2])}" if values else ""

    dominant = profile.get("dominant_bias")
    if dominant == LOSS_AVERSION:
        opener = "The portfolio was stress-tested for exactly this kind of environment, and the downside is within the parameters we discussed when we set your investment policy."
    elif dominant == OVERCONFIDENCE:
        opener = "Short-term drawdowns are a normal and expected feature of a growth-oriented strategy — the long-term return target remains unchanged."
    else:
        opener = "Your portfolio is responding as expected to current market conditions, and no changes to the strategy are required at this stage."

    body = (
        f"Hi {hh_name.split()[0] if hh_name else 'there'}, I wanted to reach out given recent market movements. "
        f"The portfolio is currently {drawdown:.1f}% below cost. "
        f"{opener} "
        f"Your {horizon}{values_text} has not changed. "
        f"I'd like to schedule a brief call to walk through where we stand and confirm no adjustments are needed. "
        f"In the meantime, please don't hesitate to reach out — that's what I'm here for."
    )

    return {
        "draft_message": body,
        "is_draft": True,
        "trigger": f"{drawdown:.1f}% portfolio drawdown",
        "tone": "reassuring",
        "action_required": "Review and personalise before sending via Messages.",
    }


# ── Module 4: Adviser coaching ────────────────────────────────────────────────

def _coaching_advice(decision_profile: dict, bias_signals: list[dict]) -> list[dict]:
    """Generate framing guidance for the adviser based on the client's bias profile."""
    advice: list[dict] = []
    scores = decision_profile.get("bias_scores", {})

    if scores.get(LOSS_AVERSION, 0) > 0.3:
        sev = "high" if scores[LOSS_AVERSION] > 0.6 else "medium"
        advice.append({
            "bias": "Loss aversion",
            "severity": sev,
            "score": scores[LOSS_AVERSION],
            "framing": "Lead with downside protection: 'This limits your maximum drawdown to…' before mentioning return potential. Use worst-case scenario anchoring to make the recommended path feel safer.",
            "avoid": "Avoid opening with upside projections. Don't use the word 'loss' in the first sentence — use 'drawdown' or 'temporary decline' instead.",
        })

    if scores.get(STATUS_QUO_BIAS, 0) > 0.3:
        sev = "high" if scores[STATUS_QUO_BIAS] > 0.55 else "medium"
        advice.append({
            "bias": "Status quo bias",
            "severity": sev,
            "score": scores[STATUS_QUO_BIAS],
            "framing": "Quantify the explicit cost of inaction. Present doing nothing as the riskier choice. Offer one clear recommendation — not a menu of options. Use a specific deadline ('if we wait another quarter, the CGT window closes').",
            "avoid": "Don't offer multiple alternatives — choice overload deepens inertia. Never make the safe path feel comfortable.",
        })

    if scores.get(OVERCONFIDENCE, 0) > 0.3:
        sev = "high" if scores[OVERCONFIDENCE] > 0.55 else "medium"
        advice.append({
            "bias": "Overconfidence",
            "severity": sev,
            "score": scores[OVERCONFIDENCE],
            "framing": "Open with a historical scenario: 'In 2008/2020 a portfolio like yours fell X% before recovering over Y years.' Use base rates, not outlier outcomes.",
            "avoid": "Don't lead with bull-case projections — the client already over-weights good outcomes.",
        })

    recency_count = sum(1 for s in bias_signals if s["type"] == RECENCY_BIAS)
    if recency_count:
        advice.append({
            "bias": "Recency bias",
            "severity": "medium",
            "score": None,
            "framing": "Reference the IPS objectives set when markets were calm. Use a 5- or 10-year return chart, not a 3-month one. Say: 'This is how the same situation played out historically.'",
            "avoid": "Don't validate recent trend language ('yes, markets have been great lately') — it reinforces the bias.",
        })

    if sum(1 for s in bias_signals if s["type"] == ANCHORING):
        advice.append({
            "bias": "Anchoring",
            "severity": "medium",
            "score": None,
            "framing": "Reframe using forward-looking fundamentals: 'What has changed about the business?' rather than 'When will the price get back to X?' Introduce a new reference point — intrinsic value or peer comparison.",
            "avoid": "Never reference the anchor price yourself. Don't say 'when it gets back to…'.",
        })

    return advice


# ── Public API ────────────────────────────────────────────────────────────────

async def for_household(session: AsyncSession, household_id: uuid.UUID) -> dict | None:
    brain = await household_brain(session, household_id)
    if not brain:
        return None

    person_ids = [uuid.UUID(p["id"]) for p in brain.get("persons", [])]
    mandate_ids = [uuid.UUID(m["id"]) for m in brain.get("mandates", [])]

    # All recommendations touching this household (direct, person-level, mandate-level)
    subject_conditions = [
        (Recommendation.subject_type == "household") & (Recommendation.subject_id == household_id)
    ]
    if person_ids:
        subject_conditions.append(
            (Recommendation.subject_type == "person") & (Recommendation.subject_id.in_(person_ids))
        )
    if mandate_ids:
        subject_conditions.append(
            (Recommendation.subject_type == "mandate") & (Recommendation.subject_id.in_(mandate_ids))
        )

    recs = (
        await session.execute(
            select(Recommendation)
            .where(or_(*subject_conditions))
            .order_by(Recommendation.decided_at.desc().nulls_last())
        )
    ).scalars().all()

    meetings = (
        await session.execute(select(Meeting).where(Meeting.household_id == household_id))
    ).scalars().all()

    messages = (
        await session.execute(select(Message).where(Message.household_id == household_id))
    ).scalars().all()

    # Bias signals from text sources
    raw_signals: list[dict] = []
    for mtg in meetings:
        if mtg.transcript:
            raw_signals.extend(_scan_text(mtg.transcript, f"Meeting: {mtg.title}"))
    for msg in messages:
        if msg.body and msg.author_role == "client":
            raw_signals.extend(_scan_text(msg.body, "Client message"))

    # Deduplicate by (type, source)
    seen: set[tuple] = set()
    bias_signals: list[dict] = []
    for s in raw_signals:
        key = (s["type"], s["source"])
        if key not in seen:
            seen.add(key)
            bias_signals.append(s)

    decision_profile = _score_decisions(recs)
    stress = _stress_level(brain)
    stress_message = _draft_stress_message(brain, stress, decision_profile)
    coaching = _coaching_advice(decision_profile, bias_signals)

    return {
        "household_id": str(household_id),
        "household_name": brain["household"]["name"],
        "decision_profile": decision_profile,
        "bias_signals": bias_signals,
        "stress": stress,
        "stress_draft_message": stress_message,
        "coaching_advice": coaching,
        "summary": {
            "total_recs_analysed": len(recs),
            "decided_recs": decision_profile["total"],
            "dominant_bias": decision_profile.get("dominant_bias"),
            "bias_signals_count": len(bias_signals),
            "coaching_points": len(coaching),
            "is_stressed": stress.get("is_stressed", False),
            "drawdown_pct": stress.get("drawdown_pct", 0),
        },
        "generated_at": date.today().isoformat(),
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Aggregate behavioural snapshots across all households."""
    households = await list_households(session, firm_id)
    results = []
    stressed = []
    biased = []

    for h in households:
        hh_id = uuid.UUID(h["id"])
        result = await for_household(session, hh_id)
        if not result:
            continue
        results.append(result)
        if result["stress"]["is_stressed"]:
            stressed.append({"household_id": h["id"], "household_name": h["name"],
                             "drawdown_pct": result["stress"]["drawdown_pct"]})
        if result["decision_profile"]["dominant_bias"]:
            biased.append({
                "household_id": h["id"], "household_name": h["name"],
                "dominant_bias": result["decision_profile"]["dominant_bias"],
                "bias_scores": result["decision_profile"]["bias_scores"],
                "coaching_points": result["coaching_advice"],
            })

    return {
        "total_households": len(households),
        "stressed": sorted(stressed, key=lambda x: x["drawdown_pct"]),
        "stress_count": len(stressed),
        "bias_profiles": biased,
        "generated_at": date.today().isoformat(),
    }
