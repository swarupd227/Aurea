"""The conversation's tools for goals and their saving (B-394): a goal recorded from what a person said, every goal sized
and funded, and a SIP recorded against the goal it saves towards. Changing a goal is `update_goal` (executors.py), which
reads the same `goal_record` checks, and a step-up is a term of `record_sip`'s registration.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare``: every check the write makes, nothing written.
"""
from __future__ import annotations

from typing import Any

GOAL_TOOLS = ("add_goal", "read_goal_sizing", "link_sip_goal")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


class GoalExecutors:
    """Needs the host's session, firm_id, _firm() and _shelf_user()."""

    def _goal_tools_map(self) -> dict:
        return {k: getattr(self, k) for k in GOAL_TOOLS}

    async def _sizing(self, household_id, **kwargs) -> dict[str, Any]:
        from app.aurea_core import goal_sizing

        try:
            result = await goal_sizing.for_household(self.session, household_id, firm_id=self.firm_id, **kwargs)
        except goal_sizing.SizingError as exc:
            raise _error(exc)
        if result is None:
            raise _error(ValueError("Household not found or access denied"))
        return result

    @staticmethod
    def _sizing_said(result: dict, firm) -> str:
        from app.aurea_core import markets

        say = markets.money_for(firm)
        if not result["goals"]:
            return f"No goal is on file for {result['household']}."
        parts = []
        for r in result["goals"]:
            s, f = r["sizing"], r["funded"] or {}
            line = f"{r['name']}: {say(r['target'])} in {r['years']} year(s), {r['likelihood']:.0%} likely"
            if s["status"] == "assessed":
                line += f", {say(s['level_monthly'])} a month level"
                if s["step_up"]:
                    line += (f" or {say(s['step_up']['first_monthly'])} a month in the first year rising {s['step_up']['percent']:g}% "
                             f"a year to {say(s['step_up']['last_monthly'])}")
                line += (f", against {say(r['saving_monthly'])} a month saved on it" if r["saving_monthly"] is not None
                         else ", with nothing recorded as saved on it")
            else:
                line += ", monthly saving not assessed (" + ("no managed holdings to take a return from" if s["why"] == "no_holdings"
                                                             else "no target on file") + ")"
            against = (f" against the retirement plan's spending ({f['ratio_to_target']:.2f} against the goal's own target, which "
                       "the monthly figure is sized on)" if f.get("ratio_to_target") is not None else "")
            line += (f", funded ratio {f['ratio']:.2f}{against}" if f.get("ratio") is not None
                     else ", funded ratio not assessed (no funding share recorded)" if f.get("why") == "no_funding_share" else "")
            parts.append(line)
        whole = result["household_funded"]["ratio"]
        step = "" if result["step_up_percent"] is not None else " No step-up percentage was stated, so none is compared."
        return ("; ".join(parts) + "." + (f" Household funded ratio {whole:.2f}." if whole is not None else "")
                + " Estimates: monthly figures are arithmetic at the expected compound return of the current mix on the "
                  "assumption set, about an even chance and not a likelihood; no goal counts future contributions in its "
                  "funded ratio; vehicle categories by horizon are a convention, not advice." + step)

    async def read_goal_sizing(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.llm.executors import _parse_uuid

        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        goal_id = str(_parse_uuid(inputs["goal_id"], "goal_id")) if inputs.get("goal_id") else None
        result = await self._sizing(household_id, goal_id=goal_id, step_up_percent=inputs.get("step_up_percent"))
        result["message"] = self._sizing_said(result, await self._firm())
        return result

    async def add_goal(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import goal_record
        from app.llm.executors import _parse_uuid

        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        firm = await self._firm()
        try:
            row = await goal_record.add(self.session, firm, household_id, inputs, by=await self._shelf_user())
        except goal_record.GoalError as exc:
            raise _error(exc)
        result = await self._sizing(household_id, goal_id=str(row.id))
        result["recorded"] = {"goal_id": str(row.id), "name": row.name}
        result["message"] = f"Recorded the goal '{row.name}'. " + self._sizing_said(result, firm)
        return result

    async def link_sip_goal(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import sip
        from app.llm.executors import _parse_uuid

        sip_id = _parse_uuid(inputs.get("sip_id") or "", "sip_id")
        firm = await self._firm()
        try:
            row = await sip.link_goal(self.session, firm, sip_id, goal_id=inputs.get("goal_id"), by=await self._shelf_user())
        except sip.SipError as exc:
            raise _error(exc)
        await self.session.flush()
        result = await sip.for_firm(self.session, firm)
        mine = next((s for s in result["sips"] if s["id"] == str(row.id)), None)
        result["message"] = (f"Recorded: this SIP saves towards {mine['goal']}, and that goal's projections now count its schedule."
                             if mine and mine["goal"] else "Taken off: this SIP is not recorded against any goal.")
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _goal_tools_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, goal_record, markets, sip
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        try:
            if tool_key == "add_goal":
                ready = await goal_record.prepare_add(self.session, firm, _parse_uuid(inputs.get("household_id") or "", "household_id"), inputs)
                return ready["rows"]
            r = await sip.prepare_link(self.session, firm, _parse_uuid(inputs.get("sip_id") or "", "sip_id"), goal_id=inputs.get("goal_id"))
        except (goal_record.GoalError, sip.SipError) as exc:
            raise _error(exc)
        say = markets.money_for(firm)
        return [("Account", r["account"].name), ("Fund", r["instrument"].name if r["instrument"] else None),
                ("Instalment", say(sip.amount_due(r["sip"], firm_time.today(firm)))),
                ("Saves towards", f"{r['before'].name if r['before'] else 'No goal'} becomes {r['goal'].name if r['goal'] else 'no goal'}"),
                ("Effect", "That goal's projections count this registration's instalments, in place of its recorded contribution"
                 if r["goal"] else "The goal's recorded contribution is counted again"),
                ("Note", "The registration itself is unchanged")]
