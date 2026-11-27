"""The conversation's tools for India's planning measures (B-400, `aurea_core/planning_measures.py`): reading them for a
household or the firm, and recording a household's emergency fund.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and the Confirm card
in ``preview_change``. The card is the module's ``prepare_fund``: every check the write makes, nothing written.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

PLANNING_MEASURE_TOOLS = ("read_planning_measures", "record_emergency_fund")
FUND_TERMS = ("household_id", "target_amount", "held_amount", "held_in", "statement_date", "document", "complete_by", "build_months")


def _terms(inputs: dict) -> dict:
    """Every named term, the missing ones as None, so the module says what is missing in its own words."""
    return {k: (None if inputs.get(k) == "" else inputs.get(k)) for k in FUND_TERMS}


def measures_message(result: dict, say) -> str:
    """What the read says in words. The figures are the card's."""
    if not result.get("applies"):
        return result.get("why") or "These measures are not read here."
    if result["scope"] == "firm":
        m = result["measures"]
        e, w, n, s = m["emergency_fund"], m["wills"], m["nominations"], m["sip_against_plan"]
        said = [f"Across {e['of']} households: the emergency fund is complete in {e['met']}, with {e['not_on_file']} holding none on file",
                f"every adult has a current will on file in {w['met']} of {w['of']}",
                f"{n['met']} of {n['of']} demat accounts and fund folios carry a nomination or opt-out",
                (f"SIPs registered are {say(s['registered_monthly'])} a month against {say(s['planned_monthly'])} of planned saving"
                 if s["planned_monthly"] else "no goal has a planned monthly saving on file")]
        return "; ".join(said) + ". Each is read from what is on file."
    words = ", ".join(f"{s['label'].lower()} {s['chip'].lower()}" for s in result["order"])
    first = next((s for s in result["order"] if s["key"] == result["next"]), None)
    return (f"{result['household']}, in order: {words}. "
            + (f"The first step not yet in place is {first['label'].lower()}. " if first else "Every step is in place on file. ")
            + "Read from what is on file; not on file does not mean none exists.")


class PlanningMeasureExecutors:
    """Needs the host's session, firm_id, role, _firm() and _shelf_user()."""

    def _planning_measures_map(self) -> dict:
        return {k: getattr(self, k) for k in PLANNING_MEASURE_TOOLS}

    async def read_planning_measures(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets, planning_measures
        from app.llm.executors import ExecutorError, _parse_uuid

        firm = await self._firm()
        if inputs.get("household_id"):
            result = await planning_measures.for_household(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
            if result is None:
                raise ExecutorError("Household not found or access denied")
        else:
            result = await planning_measures.for_firm(self.session, firm)
        result["message"] = measures_message(result, markets.money_for(firm))
        return result

    async def _planning_measures_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, markets, planning_measures
        from app.llm.executors import ExecutorError

        firm = await self._firm()
        try:
            p = await planning_measures.prepare_fund(self.session, firm, self.role, **_terms(inputs))
        except planning_measures.MeasureError as exc:
            raise ExecutorError(str(exc))
        say, before = markets.money_for(firm), p["before"]
        return [("Household", p["household"].name), ("Target", say(p["target"])), ("Set aside", say(p["held"])), ("Held in", p["held_in"]),
                ("Statement of", firm_time.spoken(p["statement_date"])),
                ("Complete by", firm_time.spoken(p["complete_by"]) if p["complete_by"] else None),
                ("Months of the build", p["build_months"]),
                ("On file now", f"{say(float(before.held_amount))} of {say(float(before.target_amount))}" if before else "Nothing"),
                ("Document", p["document"]), ("Counted", "Nowhere else: it adds no money and moves none")]

    async def record_emergency_fund(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets, planning_measures
        from app.llm.executors import ExecutorError

        firm = await self._firm()
        try:
            row = await planning_measures.record_fund(self.session, firm, self.role, by=await self._shelf_user(), **_terms(inputs))
        except planning_measures.MeasureError as exc:
            raise ExecutorError(str(exc))
        result = await planning_measures.for_household(self.session, firm, row.household_id)
        say = markets.money_for(firm)
        result["message"] = (f"Recorded the emergency fund: {say(float(row.held_amount))} set aside against a {say(float(row.target_amount))} "
                             "target. It moves no money.")
        return result
