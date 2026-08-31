"""Prove that a missing capacity assessment no longer pauses the drift agent.

The defect: capacity_for_loss defaulted to "medium", whose 70% equity ceiling the firm's
own 75% growth model exceeds by construction. So every growth mandate produced a guardrail
breach on every run, within_guardrails failed at HIGH severity, and conduct surveillance
auto-paused drift permanently — over a field nobody had ever filled in.

This exercises the compliance layer directly rather than the whole agent, because that is
where the pause decision is made: only a HIGH flag trips the kill-switch.

    python -m tests.verify_capacity
"""
from __future__ import annotations

import sys

from app.compliance import rules
from app.compliance.ontology import FRAMEWORKS
from app.compliance.rules import CheckContext

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(f"{label}: got {got}, want {want}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def ctx(**evidence) -> CheckContext:
    return CheckContext(agent_key="drift_rebalancing", rationale="", text="",
                        confidence=0.9, payload={}, evidence=evidence, citations=[],
                        mandate_type="advisory", policy={})


def main() -> int:
    print("\n=== an unassessed capacity is a gap, not a breach ===")
    gap = ["Capacity for loss has not been assessed for this mandate, so the equity "
           "ceiling cannot be checked."]
    c = ctx(guardrail_breaches=[], suitability_gaps=gap)

    guard = rules.within_guardrails(c)
    check("within_guardrails passes (nothing to enforce)", guard.status, "pass")

    complete = rules.suitability_complete(c)
    check("suitability_complete fails", complete.status, "fail")
    check("but at LOW severity, so it cannot auto-pause", complete.severity, "low")
    check("and names the gap", "Capacity for loss" in complete.finding, True)

    print("\n=== a real breach is still a real breach ===")
    breach = ctx(guardrail_breaches=["Proposed equity (75%) exceeds risk capacity limit "
                                     "(70%) for capacity_for_loss='medium'"],
                 suitability_gaps=[])
    check("within_guardrails fails", rules.within_guardrails(breach).status, "fail")
    check("suitability_complete passes (the input was there)",
          rules.suitability_complete(breach).status, "pass")

    print("\n=== a complete, compliant assessment is clean ===")
    clean = ctx(guardrail_breaches=[], suitability_gaps=[])
    check("within_guardrails passes", rules.within_guardrails(clean).status, "pass")
    check("suitability_complete passes", rules.suitability_complete(clean).status, "pass")

    print("\n=== only HIGH auto-pauses, so severity is the whole game ===")
    # Mirrors provenance/surveillance: it pauses on any HIGH flag and nothing else.
    for name, framework in FRAMEWORKS.items():
        rule = next((r for r in framework.rules if r.eval == "suitability_complete"), None)
        if rule is None:
            check(f"{name} has a suitability-inputs rule", False, True)
            continue
        check(f"{name}: {rule.code} severity", rule.severity, "low")

    print("\n=== the evaluator is registered, so the rule can actually run ===")
    check("suitability_complete in REGISTRY", "suitability_complete" in rules.REGISTRY, True)
    for name, framework in FRAMEWORKS.items():
        missing = [r.id for r in framework.rules if r.eval not in rules.REGISTRY]
        check(f"{name} has no rule pointing at a missing evaluator", missing, [])

    print("\n=== the seed no longer leaves capacity unassessed ===")
    import re
    from pathlib import Path
    src = Path("seed/run.py").read_text(encoding="utf-8")

    def blocks(opener: str) -> list[str]:
        """Every {...} literal introduced by `opener`, balanced."""
        out = []
        for m in re.finditer(re.escape(opener), src):
            depth, i = 1, m.end()
            while depth and i < len(src):
                depth += {"{": 1, "}": -1}.get(src[i], 0)
                i += 1
            out.append(src[m.end():i])
        return out

    # Mandate suitability and onboarding intake both feed a suitability record. Household
    # `profile=` dicts do not, so they are deliberately excluded.
    for opener in ("suitability={", "intake={"):
        found = blocks(opener)
        missing = [b for b in found if '"risk_profile"' in b
                   and "capacity_for_loss" not in b]
        dupes = [b for b in found if b.count("capacity_for_loss") > 1]
        check(f"every {opener[:-2]} with a risk profile has a capacity",
              len(missing), 0)
        check(f"no {opener[:-2]} declares capacity twice", len(dupes), 0)

    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
