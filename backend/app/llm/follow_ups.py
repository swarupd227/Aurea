"""The suggestions that end an Astra reply: the next sentence this person would type.

They used to be three fixed strings ("Continue", "Show details", "Confirm action") that nothing could
answer. Each one here is a complete request that an existing tool answers, so it is only offered if
the caller's role holds that tool, and it follows from what this turn actually found: a household
the turn looked up, proposals it listed, a failed run it surfaced. Names and counts are taken from
the tool results of this turn, never from memory (CLAUDE.md rule 2).
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.tools import tools_for_role
from app.models.enums import UserRole

MAX_SUGGESTIONS = 4

# (tool that answers it, the sentence)
Candidate = tuple[str, str]

WAITING: Candidate = ("read_recommendations", "What proposals are waiting for my decision?")
AGENTS: Candidate = ("read_agents", "Which agents can I run?")
RUNS: Candidate = ("read_agent_history", "Show me the most recent agent runs and whether any failed.")
COMPOSITES: Candidate = ("read_composites", "How are the composites performing?")
GOVERNANCE: Candidate = ("read_ai_governance_summary", "Summarise AI governance: what is overdue for review?")
BENEFICIARIES: Candidate = ("read_beneficiary_audit", "Which accounts are missing beneficiaries?")
SCAN: Candidate = ("run_agent", "Have the next-best-action agent scan the book for opportunities.")


def _the(name: str) -> str:
    return f"the{name[3:]}" if name.lower().startswith("the ") else f"the {name}"


def _household(name: str) -> list[Candidate]:
    return [
        ("read_performance", f"How has {_the(name)} portfolio performed against its benchmark?"),
        ("check_household_wash_sale", f"Check {_the(name)} household for wash-sale conflicts."),
        ("explain_invoice", f"Why was {_the(name)} billed what it was?"),
    ]


def _for_india(candidate: Candidate) -> Candidate:
    """India has no wash-sale rule, so the sentence that follows a household there is its tax."""
    tool, text = candidate
    if tool != "check_household_wash_sale":
        return candidate
    name = text.removeprefix("Check ").removesuffix(" household for wash-sale conflicts.")
    return "read_tax_position", f"What is {name}'s tax position this tax year, taxpayer by taxpayer?"


def _name_of(result: dict[str, Any]) -> str | None:
    brain = result.get("brain") or {}
    household = brain.get("household") if isinstance(brain.get("household"), dict) else {}
    return (result.get("household_name") or household.get("name") or brain.get("name")
            or ((result.get("results") or [{}])[0] or {}).get("name") or None)


def _from_recommendations(inputs: dict, result: dict) -> list[Candidate]:
    recs = result.get("recommendations") or []
    out: list[Candidate] = []
    top = next((r for r in recs if r.get("can_decide") and r.get("status") == "proposed"), None)
    if top:
        out.append(("decide_recommendation", f"Approve the proposal \"{top['title']}\"."))
    if any(r.get("agent_key") == "drift_rebalancing" for r in recs):
        out.append(("read_recommendations", "Which of the open proposals move money on a client's book?"))
    return out + [RUNS]


def _from_agents(inputs: dict, result: dict) -> list[Candidate]:
    startable = {a["agent_key"] for a in result.get("agents") or [] if a.get("can_run")}
    return ([SCAN] if "next_best_action" in startable else []) + [RUNS, WAITING]


def _from_history(inputs: dict, result: dict) -> list[Candidate]:
    failed = (result.get("by_status") or {}).get("failed", 0)
    return ([("read_agent_history", "Show me the agent runs that failed.")] if failed and inputs.get("status") != "failed" else []) + [
        ("read_capacity", "How much capacity have the agents reclaimed, and what is still open?"), WAITING]


def _from_provenance(inputs: dict, result: dict) -> list[Candidate]:
    flags = (result.get("surveillance") or {}).get("open", 0)
    return ([RUNS] if flags else []) + [GOVERNANCE, ("read_compliance_program", "Show me the compliance program and any open conflicts.")]


def _from_onboarding(inputs: dict, result: dict) -> list[Candidate]:
    return [BENEFICIARIES, WAITING]


# What each tool's result leads to. A tool not listed here leads to the role's general next steps.
FOLLOWS: dict[str, Callable[[dict, dict], list[Candidate]]] = {
    "read_household": lambda i, r: _household(_name_of(r)) if _name_of(r) else [],
    "search_households": lambda i, r: _household(_name_of(r)) if _name_of(r) else [],
    # A suggestion names the household as people do; with no name in the result it is not offered
    # rather than offered with an id nobody can read.
    "read_performance": lambda i, r: ([_household(_name_of(r))[1]] if _name_of(r) else []) + [COMPOSITES],
    "check_household_wash_sale": lambda i, r: [_household(_name_of(r))[0]] if _name_of(r) else [],
    "explain_invoice": lambda i, r: _household(_name_of(r))[:2] if _name_of(r) else [],
    "read_billing": lambda i, r: [("read_billing", "Which bills are held, and why?")] if not i.get("household_id") else [],
    "run_billing": lambda i, r: [("read_billing", "Which bills are held, and why?")],
    "read_recommendations": _from_recommendations,
    "read_agents": _from_agents,
    "read_agent_history": _from_history,
    "read_capacity": lambda i, r: [RUNS, WAITING],
    "read_analytics": lambda i, r: [COMPOSITES, ("read_firm_definition_check", "Does the firm definition check come back clean?"), WAITING],
    "read_onboarding_cases": _from_onboarding,
    "read_provenance": _from_provenance,
    "read_composites": lambda i, r: [("read_firm_definition_check", "Does the firm definition check come back clean?")],
    "read_compliance_program": lambda i, r: [GOVERNANCE, WAITING],
    "read_ai_governance_summary": lambda i, r: [("read_compliance_program", "Show me the compliance program and any open conflicts."), RUNS],
    "read_beneficiary_audit": lambda i, r: [WAITING],
    "read_tax_position": lambda i, r: [("read_licences", "Which proposals are waiting for a client's consent?"), WAITING],
    "read_licences": lambda i, r: [WAITING],
}


def derive(role: UserRole | str, used: list[dict[str, Any]], *, india: bool = False) -> list[str]:
    """Up to four sentences, each answerable by a tool `role` holds, shaped by the tools this turn
    used (most recent first) and finished with the role's general next steps."""
    held = tools_for_role(UserRole(role))
    if not held:
        return []
    candidates: list[Candidate] = []
    for call in reversed(used):
        follow = FOLLOWS.get(call["tool_key"])
        if follow is None:
            continue
        try:
            candidates += follow(call.get("inputs") or {}, call.get("result") or {})
        except (KeyError, TypeError, IndexError):
            continue        # a result of an unexpected shape suggests nothing rather than something wrong
    candidates += [WAITING, AGENTS]
    if india:
        candidates = [_for_india(c) for c in candidates]

    # Do not offer what the turn has just done.
    seen: set[str] = set()
    if any(c["tool_key"] == "read_agents" for c in used):
        seen.add(AGENTS[1])
    if any(c["tool_key"] == "read_recommendations" for c in used):
        seen.add(WAITING[1])
    out: list[str] = []
    for tool_key, sentence in candidates:
        if tool_key in held and sentence not in seen:
            seen.add(sentence)
            out.append(sentence)
        if len(out) == MAX_SUGGESTIONS:
            break
    return out
