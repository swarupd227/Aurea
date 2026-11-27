"""The conversation's tools for India's retirement stack (B-395, `aurea_core/retirement_stack.py`): the layout, and
recording a monthly contribution, an annuity rate from a quote, a Unified Pension Scheme membership and the end of any of
them.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card
in ``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written. A write
returns the household's layout as it now stands. ``ExecutorError`` is imported where it is raised, because executors.py
imports this module.
"""
from __future__ import annotations

from typing import Any

RETIREMENT_STACK_TOOLS = ("read_retirement_stack", "record_retirement_contribution", "record_annuity_rate", "record_ups_membership",
                          "end_retirement_record")
CONTRIBUTION_TERMS = ("person_id", "scheme", "payer", "monthly_amount", "statement_date", "document")
RATE_TERMS = ("person_id", "rate", "quote_date", "document", "terms")
UPS_TERMS = ("person_id", "statement_date", "document")
END_TERMS = ("record_id", "reason")


def _terms(inputs: dict, keys) -> dict:
    """Every named term, the missing ones as None, so the module says what is missing in its own words."""
    return {k: (None if inputs.get(k) == "" else inputs.get(k)) for k in keys}


class RetirementStackExecutors:
    """Needs the host's session, firm_id, role, _firm() and _shelf_user()."""

    def _retirement_stack_map(self) -> dict:
        return {k: getattr(self, k) for k in RETIREMENT_STACK_TOOLS}

    async def _stack_layout(self, household_id, **what_if) -> dict[str, Any]:
        from app.aurea_core import retirement_stack
        from app.llm.executors import ExecutorError

        try:
            result = await retirement_stack.layout(self.session, await self._firm(), household_id, **what_if)
        except retirement_stack.StackError as exc:
            raise ExecutorError(str(exc))
        if result is None:
            raise ExecutorError("Household not found or access denied")
        return result

    async def read_retirement_stack(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets
        from app.llm.executors import _parse_uuid

        result = await self._stack_layout(_parse_uuid(inputs.get("household_id") or "", "household_id"),
                                          annuity_rate=inputs.get("annuity_rate_percent"), monthly_withdrawal=inputs.get("monthly_withdrawal"))
        result["message"] = layout_message(result, markets.money_for(await self._firm()))
        return result

    async def _stack_write(self, write, said) -> dict[str, Any]:
        from app.aurea_core import retirement_stack
        from app.llm.executors import ExecutorError

        try:
            row = await write(retirement_stack, await self._firm(), await self._shelf_user())
        except retirement_stack.StackError as exc:
            raise ExecutorError(str(exc))
        result = await self._stack_layout(row.household_id)
        result["record_id"] = str(row.id)
        result["message"] = f"{said(row)} {change_words(result)}".strip()
        return result

    async def record_retirement_contribution(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets

        say = markets.money_for(await self._firm())
        return await self._stack_write(
            lambda m, firm, by: m.record_contribution(self.session, firm, self.role, by=by, **_terms(inputs, CONTRIBUTION_TERMS)),
            lambda r: f"Recorded {say(float(r.monthly_amount))} a month into {r.scheme.upper()}, "
                      f"{'the employer' if r.payer == 'employer' else 'the member'} paying. It moves no money.")

    async def record_annuity_rate(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._stack_write(
            lambda m, firm, by: m.record_annuity_rate(self.session, firm, self.role, by=by, **_terms(inputs, RATE_TERMS)),
            lambda r: f"Recorded the quoted annuity rate of {float(r.annuity_rate):g}% a year. It buys nothing.")

    async def record_ups_membership(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._stack_write(
            lambda m, firm, by: m.record_ups(self.session, firm, self.role, by=by, **_terms(inputs, UPS_TERMS)),
            lambda r: "Recorded as a member of the Unified Pension Scheme. Its pension is not worked out, and their NPS is left out.")

    async def end_retirement_record(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._stack_write(
            lambda m, firm, by: m.end_record(self.session, firm, self.role, by=by, **_terms(inputs, END_TERMS)),
            lambda r: "Ended. It no longer counts and stays on file.")

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _retirement_stack_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, markets, retirement_stack as m
        from app.llm.executors import ExecutorError

        firm = await self._firm()
        say = markets.money_for(firm)
        try:
            if tool_key == "end_retirement_record":
                p = await m.prepare_end(self.session, firm, self.role, **_terms(inputs, END_TERMS))
                r = p["record"]
                return [("Client", p["household"].name), ("Person", p["person"].full_name), ("Ending", record_words(r, say)),
                        ("Stated on", firm_time.spoken(r.stated_on)), ("Reason", p["reason"]),
                        ("Effect", "The retirement projection stops counting it; it stays on file")]
            if tool_key == "record_retirement_contribution":
                p = await m.prepare_contribution(self.session, firm, self.role, **_terms(inputs, CONTRIBUTION_TERMS))
                old = p["replaces"]
                return [("Client", p["household"].name), ("Person", p["person"].full_name), ("Into", m.SCHEME_TEXT[p["scheme"]]),
                        ("Paid by", m.PAYER_TEXT[p["payer"]]), ("A month", say(p["monthly_amount"])), ("A year", say(p["monthly_amount"] * 12)),
                        ("Stated on", firm_time.spoken(p["stated_on"])),
                        ("Replaces", f"{say(float(old.monthly_amount))} a month, stated {firm_time.spoken(old.stated_on)}" if old else "Nothing on file"),
                        ("Document", p["document"]),
                        ("Effect", "The retirement projection counts it, level, until retirement. It moves no money")]
            if tool_key == "record_annuity_rate":
                p = await m.prepare_annuity_rate(self.session, firm, self.role, **_terms(inputs, RATE_TERMS))
                old = p["replaces"]
                return [("Client", p["household"].name), ("Person", p["person"].full_name),
                        ("Annuity rate quoted", f"{p['annuity_rate']:g}% a year of the amount paid for it"), ("Terms", p["attributes"].get("terms")),
                        ("Quoted on", firm_time.spoken(p["stated_on"])),
                        ("Replaces", f"{float(old.annuity_rate):g}%, quoted {firm_time.spoken(old.stated_on)}" if old else "Nothing on file"),
                        ("Document", p["document"]),
                        ("Effect", "The projection reads the NPS annuity at this rate. A quote is not a promise at exit; it buys nothing")]
            p = await m.prepare_ups(self.session, firm, self.role, **_terms(inputs, UPS_TERMS))
            return [("Client", p["household"].name), ("Person", p["person"].full_name), ("Recording", "A member of the Unified Pension Scheme"),
                    ("Stated on", firm_time.spoken(p["stated_on"])), ("Document", p["document"]),
                    ("Effect", "Their NPS leaves the projection; the scheme's pension is not worked out")]
        except m.StackError as exc:
            raise ExecutorError(str(exc))


def record_words(r, say) -> str:
    if r.kind == "contribution":
        return f"{say(float(r.monthly_amount))} a month into {r.scheme.upper()} ({'employer' if r.payer == 'employer' else 'own'})"
    if r.kind == "annuity_rate":
        return f"An annuity rate of {float(r.annuity_rate):g}%"
    return "Unified Pension Scheme membership"


def change_words(result: dict) -> str:
    s = result.get("stack")
    if not s:
        return "Nothing of the retirement stack is counted for this household."
    return (f"Retirement success is {s['with']['success_probability']:.0%} with the stack and "
            f"{s['without']['success_probability']:.0%} without it, an estimate.")


def layout_message(v: dict, say) -> str:
    """The layout in a few sentences, for the answer: figures from the result only."""
    if not v.get("applies"):
        return "The retirement stack is laid out for an Indian household."
    if v["status"] == "nothing_on_file":
        return (f"{v['household']}: no EPF, PPF, NPS, SCSS, EPS pension or contribution is on file, which does not mean there is none. "
                "The retirement projection reads the managed book and recorded income only.")
    s, f = v["stack"], v["funded"]
    parts = [f"{v['household']}: {say(s['value_on_file'])} on file in these accounts, with {say(s['monthly_contributions'])} a month recorded going in "
             "(held elsewhere, and in no managed or billed total).",
             change_words(v)]
    if f.get("ratio") is not None:
        parts.append(f"Funded ratio {f['ratio']:.2f} with the stack, {f['ratio_without_stack']:.2f} on the managed book alone.")
    if v.get("what_if"):
        parts.append("This uses figures stated for a what-if; nothing on file changed.")
    for row in v["nps_exit"]:
        if row["annuity_not_assessed"]:
            parts.append(f"{row['person']}'s NPS annuity income is not assessed: {row['annuity_not_assessed']}.")
        elif row["annuity_a_year"] is not None:
            parts.append(f"{row['person']}'s annuity at {row['annuity_rate']:g}% would be about {say(row['annuity_a_year_today_money'])} a year "
                         "in today's money, before tax.")
    fl, li, w = v["floor"], v["liquidity"], v["withdrawals"]
    parts.append(f"Guaranteed income covers {fl['coverage']:.0%} of needs at retirement, before tax." if fl["status"] == "assessed"
                 else f"The income floor is not assessed: {fl['why']}.")
    parts.append(f"Liquid money covers {li['years']:g} years of essential spending and debt payments." if li["status"] == "assessed"
                 else f"The liquid defence is not assessed: {li['why']}.")
    if w["first_monthly"] is not None:
        parts.append(f"The drawing starts at about {say(w['first_monthly'])} a month in the money of that year"
                     + (f" and runs short at {w['runs_out_at']} on a steady return." if w["runs_out_at"] else " on a steady return."))
    if v["not_in_stack"]:
        parts.append(f"{len(v['not_in_stack'])} pension total(s) on net worth carry no statement line and are not counted here.")
    if s["excluded"]:
        parts.append("Left out: " + "; ".join(f"{e['label']} ({e['why']})" for e in s["excluded"]) + ".")
    parts.append("Every rule used is assumed and not reviewed; tax is not worked out; nothing here is advice.")
    return " ".join(parts)
