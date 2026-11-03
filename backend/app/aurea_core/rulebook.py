"""The firm's rulebook and agent controls, and who may change them.

The rules are the regime's (`compliance/ontology.py`), each with its law, so a new rule is code. What a firm may change is
whether a rule is checked and how severe a breach is: a high breach blocks the proposal and pauses the agent that raised
it. That is compliance's decision, with a reason, on the audit log. The agent controls (`core/foundation.py`) split the
same way: the governance settings (approval everywhere, minimum confidence, turnover cap, default tax budget, research
required, PII redaction) are compliance's, and the platform settings (model fallback, cost cap, tokens, eval gate,
retrieval depth) the administrator's. Nothing here assesses anything; `compliance/engine.py` reads what is set.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.ontology import framework_for
from app.core import foundation
from app.core.db import utcnow
from app.models.compliance import ComplianceCheck
from app.models.enums import UserRole
from app.models.identity import AuditEvent, User
from app.models.tenant import Firm

SEVERITIES = ("high", "medium", "low")
SEVERITY_EFFECT = {"high": "a breach blocks the proposal and pauses the agent that raised it",
                   "medium": "a breach is flagged for review", "low": "a breach is noted"}
IMPACT_DAYS = 90

# setting -> (label, kind, owner)
SETTINGS: dict[str, tuple[str, str, UserRole]] = {
    "require_approval_everywhere": ("Approval needed everywhere, whatever an agent's tier", "bool", UserRole.COMPLIANCE),
    "min_confidence": ("Proposals below this confidence are flagged", "share", UserRole.COMPLIANCE),
    "max_turnover_pct": ("Rebalances above this turnover are flagged (0 is no cap)", "share", UserRole.COMPLIANCE),
    "default_cgt_budget": ("Tax budget where a mandate sets none", "money", UserRole.COMPLIANCE),
    "require_grounding": ("Advice must cite firm research, or it is a high breach", "bool", UserRole.COMPLIANCE),
    "pii_redaction": ("Personal data is masked before any model call", "bool", UserRole.COMPLIANCE),
    "pii_categories": ("What is masked: names, accounts, emails, ids", "list", UserRole.COMPLIANCE),
    "fallback_enabled": ("Fall back to the second model provider", "bool", UserRole.ADMIN),
    "monthly_cost_cap_usd": ("Monthly model cost cap, USD (0 is none)", "number", UserRole.ADMIN),
    "max_tokens_default": ("Most tokens per model call", "number", UserRole.ADMIN),
    "enforce_eval_gate": ("A model or config change needs the evaluation gates green", "bool", UserRole.ADMIN),
    "rag_top_k": ("Research passages read per question", "number", UserRole.ADMIN),
}


class RulebookError(Exception):
    """A change that cannot be made, in words a person can act on."""


def _config(firm: Firm) -> tuple[set[str], dict[str, str]]:
    c = (firm.settings or {}).get("compliance") or {}
    return set(c.get("disabled") or []), dict(c.get("severity") or {})


def owner_word(role: UserRole) -> str:
    return "compliance" if role == UserRole.COMPLIANCE else "an administrator"


async def _fired(session: AsyncSession, firm: Firm) -> dict[str, int]:
    """How often each rule failed in the last 90 days."""
    since = utcnow() - timedelta(days=IMPACT_DAYS)
    out: dict[str, int] = {}
    for results in (await session.execute(select(ComplianceCheck.results).where(
            ComplianceCheck.firm_id == firm.id, ComplianceCheck.created_at >= since))).scalars().all():
        for r in results or []:
            if r.get("status") == "fail":
                out[r.get("rule_id")] = out.get(r.get("rule_id"), 0) + 1
    return out


async def for_firm(session: AsyncSession, firm: Firm) -> dict:
    fw = framework_for(firm)
    disabled, sev = _config(firm)
    fired = await _fired(session, firm)
    pol = foundation.policy(firm)
    stored = (firm.settings or {}).get("foundation") or {}
    return {
        "regime": fw.regime, "name": fw.name, "version": fw.version, "authority": fw.authority,
        "rules": [{"id": r.id, "code": r.code, "citation": r.citation, "title": r.title, "category": r.category,
                   "severity": sev.get(r.id, r.severity), "default_severity": r.severity, "enabled": r.id not in disabled,
                   "applies_to": list(r.applies_to), "fired_90d": fired.get(r.id, 0)} for r in fw.rules],
        "settings": [{"key": k, "label": label, "kind": kind, "value": pol.get(k), "default": foundation.DEFAULTS.get(k),
                      "set_by_firm": k in stored, "owner": owner_word(who)} for k, (label, kind, who) in SETTINGS.items()],
        "impact_days": IMPACT_DAYS,
    }


async def prepare_rule(session: AsyncSession, firm: Firm, *, rule_id: str | None, enabled=None, severity: str | None = None,
                       reason: str | None) -> dict:
    fw = framework_for(firm)
    rule = next((r for r in fw.rules if r.id == (rule_id or "").strip() or r.code == (rule_id or "").strip()), None)
    if rule is None:
        raise RulebookError(f"No rule {rule_id!r} in {fw.name}; read_compliance_rules lists them.")
    reason = (reason or "").strip()
    if not reason:
        raise RulebookError("Give the reason for the change; it goes on the audit log beside the rule.")
    if severity is not None and str(severity).lower() not in SEVERITIES:
        raise RulebookError("Severity is high, medium or low.")
    if enabled is not None and not isinstance(enabled, bool):
        enabled = str(enabled).strip().lower() in ("true", "1", "yes", "on")
    disabled, sev = _config(firm)
    now = {"enabled": rule.id not in disabled, "severity": sev.get(rule.id, rule.severity)}
    after = {"enabled": now["enabled"] if enabled is None else enabled,
             "severity": now["severity"] if severity is None else str(severity).lower()}
    if after == now:
        raise RulebookError("That is already how the rule stands.")
    fired = (await _fired(session, firm)).get(rule.id, 0)
    return {"rule": rule, "framework": fw, "now": now, "after": after, "reason": reason, "fired": fired}


async def set_rule(session: AsyncSession, firm: Firm, *, actor: User, **terms) -> dict:
    p = await prepare_rule(session, firm, **terms)
    rule = p["rule"]
    s = dict(firm.settings or {})
    c = dict(s.get("compliance") or {})
    disabled, sev = set(c.get("disabled") or []), dict(c.get("severity") or {})
    (disabled.discard if p["after"]["enabled"] else disabled.add)(rule.id)
    if p["after"]["severity"] == rule.severity:
        sev.pop(rule.id, None)
    else:
        sev[rule.id] = p["after"]["severity"]
    c["disabled"], c["severity"] = sorted(disabled), sev
    s["compliance"] = c
    firm.settings = s
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="compliance.rule_set",
                           subject=f"{rule.code} · {rule.title}",
                           detail={"rule_id": rule.id, "framework": f"{p['framework'].regime} {p['framework'].version}",
                                   "before": p["now"], "after": p["after"], "reason": p["reason"]}))
    await session.flush()
    return await for_firm(session, firm)


def _value(key: str, raw):
    kind = SETTINGS[key][1]
    if kind == "list":
        allowed = list(foundation.DEFAULTS["pii_categories"])
        items = raw if isinstance(raw, list) else [x.strip() for x in str(raw).split(",") if x.strip()]
        unknown = [x for x in items if x not in allowed]
        if unknown:
            raise RulebookError(f"{', '.join(unknown)} cannot be masked; the categories are {', '.join(allowed)}.")
        return [x for x in allowed if x in items]
    if kind == "bool":
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("true", "1", "yes", "on")
    try:
        v = float(str(raw).replace(",", "").replace("%", "").strip())
    except ValueError:
        raise RulebookError(f"{SETTINGS[key][0]} takes a number.")
    if v < 0:
        raise RulebookError(f"{SETTINGS[key][0]} cannot be negative.")
    if kind == "share":
        v = v / 100 if v > 1 else v
        if v > 1:
            raise RulebookError(f"{SETTINGS[key][0]} is a share, at most 100%.")
    return int(v) if kind == "number" and key != "monthly_cost_cap_usd" else v


def prepare_setting(firm: Firm, *, role: UserRole | str, key: str | None, value, reason: str | None) -> dict:
    key = (key or "").strip()
    if key not in SETTINGS:
        raise RulebookError(f"No agent control {key!r}. They are: {', '.join(SETTINGS)}.")
    label, _kind, who = SETTINGS[key]
    if UserRole(role) != who:
        raise RulebookError(f"{label} is {owner_word(who)}'s to set. Say whose it is; do not offer to reach the same effect "
                            "another way, such as through autonomy policies, because who decides is the control.")
    reason = (reason or "").strip()
    if not reason:
        raise RulebookError("Give the reason for the change; it goes on the audit log.")
    new = _value(key, value)
    now = foundation.policy(firm).get(key)
    if now == new:
        raise RulebookError("That is already the setting.")
    return {"key": key, "label": label, "now": now, "after": new, "reason": reason}


async def set_setting(session: AsyncSession, firm: Firm, *, actor: User, **terms) -> dict:
    p = prepare_setting(firm, role=actor.role, **terms)
    s = dict(firm.settings or {})
    f = dict(s.get("foundation") or {})
    f[p["key"]] = p["after"]
    s["foundation"] = f
    firm.settings = s
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="foundation.setting_set",
                           subject=p["label"], detail={"key": p["key"], "before": p["now"], "after": p["after"], "reason": p["reason"]}))
    await session.flush()
    return await for_firm(session, firm)


def changed_settings(firm: Firm, patch: dict) -> dict:
    """The keys a whole-form save would actually change, so the page is held to the same owners as the tool."""
    pol = foundation.policy(firm)
    return {k: v for k, v in (patch or {}).items() if k in foundation.DEFAULTS and pol.get(k) != v}
