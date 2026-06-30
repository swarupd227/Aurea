"""Layer 2.5 — Risk, conduct & data analytics (Analytics Companion §2.5).

The 'can we defend this' layer: conduct surveillance, AML anomaly, data quality, agent
performance and audit/explainability — all from the governed brain and the decision ledger."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import SurveillanceFlag
from app.models.onboarding import OnboardingCase
from app.models.portfolio import Holding, Price
from app.provenance.evaluation import latest_evaluations
from app.provenance.ledger import ledger_count, verify_chain


async def compute(session: AsyncSession, firm_id: uuid.UUID, brains: list[dict] | None = None) -> dict:
    # ── Conduct surveillance ──
    flags = (await session.execute(select(SurveillanceFlag).where(SurveillanceFlag.firm_id == firm_id))).scalars().all()
    by_sev: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    for f in flags:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        by_cat[f.category] = by_cat.get(f.category, 0) + 1

    # ── AML/CFT ──
    cases = (await session.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm_id))).scalars().all()
    aml = {"clear": 0, "review": 0, "blocked": 0, "pending": 0}
    hits = 0
    for c in cases:
        st = (c.screening or {}).get("status")
        aml[st if st in aml else "pending"] = aml.get(st if st in aml else "pending", 0) + 1
        hits += (c.screening or {}).get("n_hits", 0)

    # ── Data quality ──
    holdings = (await session.execute(select(Holding).where(Holding.firm_id == firm_id))).scalars().all()
    n = len(holdings) or 1
    with_lineage = sum(1 for h in holdings if h.lineage)
    avg_conf = sum(float(h.confidence or 0) for h in holdings) / n
    prices = (await session.execute(select(Price).where(Price.firm_id == firm_id))).scalars().all()
    real = sum(1 for p in prices if p.is_real)
    today_priced = sum(1 for p in prices if p.as_of == date.today())
    completeness = with_lineage / n
    timeliness = today_priced / (len(prices) or 1)
    accuracy = real / (len(prices) or 1)
    dq_score = round((completeness * 0.3 + timeliness * 0.25 + accuracy * 0.2 + avg_conf * 0.25), 3)

    # ── Agent performance (reuse the evaluation harness) ──
    evals = await latest_evaluations(session, firm_id)
    healthy = sum(1 for e in evals if e["grade"] == "healthy")

    # ── Audit & explainability ──
    chain = await verify_chain(session, firm_id)
    entries = await ledger_count(session, firm_id)

    return {
        "conduct": {"open_flags": len(flags), "by_severity": by_sev, "by_category": by_cat},
        "aml": {"by_status": aml, "total_hits": hits},
        "data_quality": {"score": dq_score, "completeness": round(completeness, 3),
                         "timeliness": round(timeliness, 3), "accuracy": round(accuracy, 3),
                         "avg_confidence": round(avg_conf, 3), "holdings": len(holdings)},
        "agent_performance": {"agents_evaluated": len(evals), "healthy": healthy,
                              "avg_quality": round(sum(e["quality_score"] for e in evals) / len(evals), 3) if evals else None,
                              "by_agent": evals},
        "audit": {"ledger_entries": entries, "chain_valid": chain["valid"], "reconstructable": True},
    }
