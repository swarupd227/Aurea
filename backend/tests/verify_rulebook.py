"""Prove a compliance rule is switched or re-graded only by compliance, with a reason, on the audit log, and that the
engine then assesses by it; that each agent control is its owner's, compliance for governance and an administrator for
the platform, from the conversation and from a whole-form save alike; and that the rulebook says how often each rule
failed.

Runs against a throwaway SQLite database.

    python -m tests.verify_rulebook
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import rulebook
from app.compliance import engine
from app.compliance.ontology import framework_for
from app.core.db import Base
from app.core.foundation import policy as foundation_policy
from app.core.tools import tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.compliance import ComplianceCheck
from app.models.enums import UserRole
from app.models.identity import AuditEvent, User
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, rulebook.RulebookError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add(firm)
            await s.flush()
            comp = User(id=uuid.uuid4(), firm_id=firm.id, email="c@t", full_name="Cy", role=UserRole.COMPLIANCE, hashed_password="x")
            admin = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="Al", role=UserRole.ADMIN, hashed_password="x")
            s.add_all([comp, admin])
            fw = framework_for(firm)
            grounding = next(r for r in fw.rules if r.eval == "grounding")
            for _ in range(3):
                s.add(ComplianceCheck(firm_id=firm.id, agent_key="drift_rebalancing", regime=fw.regime, framework_version=fw.version,
                                      status="flags", results=[{"rule_id": grounding.id, "status": "fail"}]))
            await s.flush()
            ex = lambda u: ToolExecutors(s, u.id, u.role, firm.id)  # noqa: E731

            print("\n=== The rulebook ===")
            book = await ex(comp).execute("read_compliance_rules", {})
            row = next(r for r in book["rules"] if r["id"] == grounding.id)
            check("each rule with its law, state and how often it failed", (row["citation"] != "", row["enabled"], row["fired_90d"]), (True, True, 3))
            owners = {x["key"]: x["owner"] for x in book["settings"]}
            check("governance controls are compliance's, platform controls an administrator's",
                  (owners["require_approval_everywhere"], owners["min_confidence"], owners["rag_top_k"]), ("compliance", "compliance", "an administrator"))

            print("\n=== Switching a rule ===")
            ask = {"rule_id": grounding.code, "enabled": "false", "reason": "House research is being rewritten this quarter"}
            await refused("a reason is needed", ex(comp).preview_change("set_compliance_rule", {**ask, "reason": " "}), "reason")
            await refused("a rule that is not in the regime", ex(comp).preview_change("set_compliance_rule", {**ask, "rule_id": "made.up"}), "read_compliance_rules")
            await refused("a severity that does not exist", ex(comp).preview_change("set_compliance_rule", {"rule_id": grounding.id, "severity": "extreme", "reason": "r"}), "high, medium or low")
            card = {r["label"]: r["value"] for r in (await ex(comp).preview_change("set_compliance_rule", ask))["rows"]}
            check("the card names the rule, its law, now and after, and how often it failed",
                  (card["Rule"].startswith(grounding.code), card["Law"] == grounding.citation, card["Now"].startswith("Checked"), card["Becomes"],
                   card["Failed in the last 90 days"]), (True, True, True, "Not checked", "3"))
            await ex(comp).execute("set_compliance_rule", ask)
            check("it is off for this firm", grounding.id in firm.settings["compliance"]["disabled"], True)
            ctx = engine.CheckContext(agent_key="drift_rebalancing", payload={}, evidence={}, confidence=0.9, citations=[], mandate_type=None,
                                      policy={}, rationale="", text="")
            cfg = engine.firm_rule_config(firm)
            out = engine.evaluate(fw, ctx, disabled=set(cfg["disabled"]), severity_overrides=cfg["severity"])
            check("and the engine no longer checks it", any(r["rule_id"] == grounding.id for r in out["results"]), False)
            await refused("switching it off again", ex(comp).preview_change("set_compliance_rule", ask), "already")
            await ex(comp).execute("set_compliance_rule", {"rule_id": grounding.id, "enabled": "true", "severity": "high", "reason": "Research is back; advice must cite it"})
            out = engine.evaluate(fw, ctx, **{"disabled": set(engine.firm_rule_config(firm)["disabled"]),
                                              "severity_overrides": engine.firm_rule_config(firm)["severity"]})
            r = next(x for x in out["results"] if x["rule_id"] == grounding.id)
            check("back on at high, so a proposal with no research is blocked", (r["severity"], r["status"], out["status"]), ("high", "fail", "blocked"))
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "compliance.rule_set"))).scalars().all()
            check("both changes on the audit log, before, after and why",
                  sorted((a.detail["after"]["enabled"], a.detail["after"]["severity"], a.detail["reason"]) for a in audit),
                  [(False, grounding.severity, "House research is being rewritten this quarter"), (True, "high", "Research is back; advice must cite it")])
            check("only compliance holds the rule change", sorted(str(x) for x in UserRole if "set_compliance_rule" in tools_for_role(x)), ["compliance"])

            print("\n=== Agent controls ===")
            card = {r["label"]: r["value"] for r in (await ex(comp).preview_change("set_agent_control",
                    {"key": "min_confidence", "value": "60%", "reason": "Fewer weak proposals"}))["rows"]}
            check("a share given as a percentage, shown now and after", (card["Now"], card["Becomes"]), ("50%", "60%"))
            await refused("an administrator may not set a governance control", ex(admin).preview_change("set_agent_control",
                          {"key": "require_approval_everywhere", "value": "true", "reason": "r"}), "compliance's to set")
            await refused("compliance may not set a platform control", ex(comp).preview_change("set_agent_control",
                          {"key": "rag_top_k", "value": "5", "reason": "r"}), "an administrator's to set")
            await refused("a category nothing masks", ex(comp).preview_change("set_agent_control", {"key": "pii_categories", "value": "names,dob", "reason": "r"}),
                          "cannot be masked")
            await ex(admin).execute("set_agent_control", {"key": "rag_top_k", "value": "5", "reason": "Longer answers need more passages"})
            check("an administrator sets a platform control", foundation_policy(firm)["rag_top_k"], 5)
            form = {**foundation_policy(firm), "max_tokens_default": 1500}
            check("a whole-form save changes only what differs", rulebook.changed_settings(firm, form), {"max_tokens_default": 1500})
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "foundation.setting_set"))).scalars().all()
            check("on the audit log with its reason", [(a.detail["key"], a.detail["reason"]) for a in audit], [("rag_top_k", "Longer answers need more passages")])
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
