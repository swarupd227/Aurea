"""The compliance engine — runs a recommendation through the resolved regime framework, producing a
cited, versioned assessment; persists it and writes a ledger entry. Pure evaluation is separated from
persistence so it is unit-testable and reusable (eval gates, surveillance, API previews)."""
from __future__ import annotations

from app.compliance import ontology, rules
from app.compliance.rules import CheckContext
from app.core.logging import get_logger

log = get_logger("aurea.compliance")

SEV_RANK = {"low": 0, "medium": 1, "high": 2}


def firm_rule_config(firm) -> dict:
    """Per-firm rule overrides: {'disabled': [...], 'severity': {rule_id: 'high'|...}}."""
    return (getattr(firm, "settings", None) or {}).get("compliance") or {}


def build_context(rec, policy: dict) -> CheckContext:
    payload = rec.payload or {}
    text = " ".join(
        [rec.rationale or ""] + [str(v) for v in payload.values() if isinstance(v, str)]
    ).lower()
    return CheckContext(
        agent_key=str(rec.agent_key), rationale=rec.rationale or "", text=text,
        confidence=float(rec.confidence or 0), payload=payload, evidence=rec.evidence or {},
        citations=rec.citations or [], mandate_type=(payload.get("mandate") or {}).get("type"),
        policy=policy or {},
    )


def evaluate(framework: ontology.Framework, ctx: CheckContext, *,
             disabled: set | None = None, severity_overrides: dict | None = None) -> dict:
    """Run every applicable rule. Returns a structured assessment (pure — no I/O)."""
    disabled = disabled or set()
    severity_overrides = severity_overrides or {}
    results: list[dict] = []
    for rule in framework.rules:
        if rule.id in disabled:
            continue
        if rule.applies_to != ("*",) and ctx.agent_key not in rule.applies_to:
            continue
        fn = rules.REGISTRY.get(rule.eval)
        res = fn(ctx) if fn else rules.RuleResult("na", "No evaluator registered.")
        severity = severity_overrides.get(rule.id) or res.severity or rule.severity
        results.append({
            "rule_id": rule.id, "code": rule.code, "citation": rule.citation, "title": rule.title,
            "category": rule.category, "severity": severity, "status": res.status,
            "finding": res.finding,
        })
    fails = [r for r in results if r["status"] == "fail"]
    blocked = any(r["severity"] == "high" for r in fails)
    status = "blocked" if blocked else ("flags" if fails else "clear")
    return {
        "regime": framework.regime, "framework": framework.name, "version": framework.version,
        "authority": framework.authority, "status": status,
        "evaluated": len(results), "passed": sum(1 for r in results if r["status"] == "pass"),
        "failed": len(fails), "results": results, "fails": fails,
    }


async def assess(session, firm, rec, policy: dict | None = None) -> dict:
    """Evaluate, persist a ComplianceCheck, and write a cited ledger entry. Returns the assessment."""
    from app.core import foundation
    from app.models.compliance import ComplianceCheck
    from app.provenance import ledger

    if policy is None:
        policy = await foundation.for_agent(session, firm, rec.agent_key)
    framework = ontology.framework_for(firm)
    cfg = firm_rule_config(firm)
    assessment = evaluate(
        framework, build_context(rec, policy),
        disabled=set(cfg.get("disabled") or []), severity_overrides=cfg.get("severity") or {},
    )

    session.add(ComplianceCheck(
        firm_id=firm.id, recommendation_id=rec.id, run_id=rec.run_id, agent_key=str(rec.agent_key),
        regime=framework.regime, framework_version=framework.version,
        status=assessment["status"], results=assessment["results"],
    ))
    await ledger.append_entry(
        session, firm_id=firm.id, event_type="compliance", agent_key=str(rec.agent_key),
        run_id=rec.run_id, recommendation_id=rec.id, actor="compliance_engine",
        content={
            "regime": framework.regime, "framework_version": framework.version,
            "status": assessment["status"],
            "checked": [r["code"] for r in assessment["results"]],
            "failed": [{"code": r["code"], "citation": r["citation"], "finding": r["finding"],
                        "severity": r["severity"]} for r in assessment["fails"]],
        },
    )
    return assessment
