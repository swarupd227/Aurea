"""Analytics overview — headline KPIs across all five layers + the maturity climb.

Computes every layer from a single gather of the client brains, so the figures reconcile by
design (Analytics Companion §1, §5)."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.analytics import advice, client_intelligence, portfolio, practice, risk_data
from app.aurea_core.analytics._common import gather_brains

# The analytics maturity climb (Companion §3, Table 6) — all five stages are live in Aurea.
MATURITY = [
    {"stage": "Descriptive", "question": "What happened?",
     "example": "Performance, holdings and book dashboards in Studio.", "live": True},
    {"stage": "Diagnostic", "question": "Why did it happen?",
     "example": "Attribution and risk decomposition behind a result.", "live": True},
    {"stage": "Predictive", "question": "What is likely to happen?",
     "example": "Drift, attrition and goal-probability forecasts.", "live": True},
    {"stage": "Prescriptive", "question": "What should we do about it?",
     "example": "Next-best-action prompts with explained rationale.", "live": True},
    {"stage": "Agentic", "question": "Do it — with approval.",
     "example": "An agent prepares the trade or outreach for adviser sign-off.", "live": True},
]

LAYER_META = [
    {"key": "client_intelligence", "n": "2.1", "title": "Client & household intelligence",
     "question": "Who do we serve, and how well?"},
    {"key": "portfolio", "n": "2.2", "title": "Portfolio & investment analytics",
     "question": "Is this the right advice?"},
    {"key": "advice", "n": "2.3", "title": "Advice & next-best-action",
     "question": "What should we do now?"},
    {"key": "practice", "n": "2.4", "title": "Practice & business analytics",
     "question": "How is the firm doing?"},
    {"key": "risk_data", "n": "2.5", "title": "Risk, conduct & data analytics",
     "question": "Can we defend this?"},
]


async def compute_all(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    brains = await gather_brains(session, firm_id)
    ci = await client_intelligence.compute(session, firm_id, brains)
    pf = await portfolio.compute(session, firm_id, brains)
    ad = await advice.compute(session, firm_id, brains)
    pr = await practice.compute(session, firm_id, brains)
    rd = await risk_data.compute(session, firm_id, brains)

    goals = pf["goals"]
    headline = {
        "firm_aum": ci["total_portfolio"]["firm_aum"],
        "clients": ci["householding"]["households"],
        "firm_wallet_share": ci["wallet_share"]["firm_wallet_share"],
        "consolidation_opportunity": ci["wallet_share"]["consolidation_opportunity"],
        "total_return": pf["performance"]["total_return"],
        "drift_breaches": pf["drift"]["mandates_breached"],
        "goals_on_track_pct": round(goals["on_track"] / goals["total"], 3) if goals["total"] else None,
        "attrition_high": ad["attrition"]["high"],
        "firm_margin": pr["cost_to_serve"]["firm_margin"],
        "fee_revenue": pr["fee_margin"]["fee_revenue"],
        "data_quality_score": rd["data_quality"]["score"],
        "agents_healthy": rd["agent_performance"]["healthy"],
        "agents_total": rd["agent_performance"]["agents_evaluated"],
        "ledger_valid": rd["audit"]["chain_valid"],
    }
    return {"headline": headline, "maturity": MATURITY, "layers": LAYER_META,
            "client_intelligence": ci, "portfolio": pf, "advice": ad,
            "practice": pr, "risk_data": rd}
