"""The conversation's tools for India's polish row: reminders raised as tasks, a PMS performance fee carried onto a bill as its own
line, residence and guardian facts read from an onboarding case, and a minor's account converted at majority.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare*``: every check the write makes, nothing written. ``ExecutorError`` is
imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from datetime import date
from typing import Any

INDIA_POLISH_TOOLS = ("read_india_reminders", "raise_india_reminders", "confirm_pms_fee_line", "read_case_facts", "record_case_facts",
                      "record_majority_conversion")
RAISERS = frozenset({"compliance", "branch_leader", "operations", "admin"})
WRITES = ("raise_india_reminders", "confirm_pms_fee_line", "record_case_facts", "record_majority_conversion")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


class IndiaPolishExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_polish_map(self) -> dict:
        return {k: getattr(self, k) for k in INDIA_POLISH_TOOLS}

    async def read_india_reminders(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_reminders

        firm = await self._firm()
        v = await india_reminders.view(self.session, firm)
        if not v["applies"]:
            return v
        v["message"] = (f"{len(v['reminders'])} date(s) overdue or within {india_reminders.WITHIN_DAYS} days; {v['to_raise']} without a task. "
                        "Tasks only: no record changes.")
        return v

    async def raise_india_reminders(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_reminders

        self._polish_may(RAISERS, "Reminders are raised by compliance, a branch leader, operations or an administrator.")
        result = await india_reminders.raise_due(self.session, await self._firm())
        result["message"] = (f"Raised {result['raised']} task(s); {result['already']} already had one." if result["applies"]
                             else "Reminders are raised for a firm holding an Indian licence.")
        return result

    async def confirm_pms_fee_line(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import pms

        if self.role not in pms.RECORDERS:
            raise _error(Exception("A performance fee line is confirmed by the portfolio team, operations or an adviser."))
        try:
            result = await pms.record_fee_line(self.session, await self._firm(), by=await self._shelf_user(), mandate_id=inputs.get("mandate_id"),
                                               period_start=inputs.get("period_start") or None, note=inputs.get("note"))
        except pms.PmsError as exc:
            raise _error(exc)
        result["message"] = "Confirmed. The next draft bill for that period carries it as a separate line; the bill's amount is unchanged."
        return result

    async def read_case_facts(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import case_facts

        try:
            result = await case_facts.read(self.session, await self._firm(), inputs.get("case_id"))
        except case_facts.CaseFactsError as exc:
            raise _error(exc)
        held = [k for k in ("residence", "guardian") if result[k]]
        result["message"] = ("The case holds " + " and ".join(held) + " facts." if held else "The case holds nothing to record.")
        return result

    async def record_case_facts(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import case_facts

        self._polish_may(case_facts.RECORDERS, "A residence or guardian is recorded by an adviser, a paraplanner or operations.")
        try:
            result = await case_facts.record(self.session, await self._firm(), by=await self._shelf_user(), case_id=inputs.get("case_id"),
                                             document=inputs.get("document") or None)
        except case_facts.CaseFactsError as exc:
            raise _error(exc)
        result["message"] = "Recorded: " + " and ".join(result["recorded"]) + "."
        return result

    async def record_majority_conversion(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import guardianship

        self._polish_may(guardianship.CONVERTERS, "A conversion is recorded by an adviser, a paraplanner or operations.")
        try:
            result = await guardianship.record_conversion(self.session, await self._firm(), by=await self._shelf_user(),
                                                          person_id=inputs.get("person_id"), document=inputs.get("document"),
                                                          converted_on=inputs.get("converted_on") or None)
        except guardianship.GuardianError as exc:
            raise _error(exc)
        result["message"] = "Recorded. The guardian's part ends and orders on the account are no longer held for it."
        return result

    def _polish_may(self, roles, why: str) -> None:
        if self.role not in roles:
            raise _error(Exception(why))

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_polish_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import case_facts, firm_time, guardianship, india_reminders, markets, pms

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        try:
            if tool_key == "raise_india_reminders":
                self._polish_may(RAISERS, "Reminders are raised by compliance, a branch leader, operations or an administrator.")
                p = await india_reminders.plan(self.session, firm)
                if not p["applies"]:
                    raise _error(Exception("Reminders are raised for a firm holding an Indian licence."))
                new = [r for r in p["items"] if not r["exists"]]
                if not new:
                    raise _error(Exception("Every date that is near or overdue already has its task: nothing to raise."))
                rows = [("Tasks to raise", str(len(new)))]
                for r in new[:8]:
                    rows.append((r["item"]["label"][:90], f"{r['owner'].full_name if r['owner'] else 'Unassigned'}, due {spoken(date.fromisoformat(r['item']['due_on']))}"))
                rows.append(("Already raised", str(len(p["items"]) - len(new))))
                rows.append(("Note", "Tasks only: no filing, KYC or guardian record changes, and nobody is told"))
                return rows
            if tool_key == "confirm_pms_fee_line":
                if self.role not in pms.RECORDERS:
                    raise _error(Exception("A performance fee line is confirmed by the portfolio team, operations or an adviser."))
                p = await pms.prepare_fee_line(self.session, firm, mandate_id=inputs.get("mandate_id"), period_start=inputs.get("period_start") or None)
                w = p["working"]
                return [("Client", p["mandate"].name), ("Period", f"{spoken(p['period_start'])} to {spoken(p['period_end'])}"),
                        ("Fixed fee on the bill", markets.money(p["fixed_fee"], p["currency"])),
                        ("Performance fee line", markets.money(p["amount"], p["currency"])),
                        ("Return over the period", f"{w['period_return'] * 100:.2f}%" if w.get("period_return") is not None else None),
                        ("Hurdle for the period", f"{w['hurdle_for_period'] * 100:.2f}%" if w.get("hurdle_for_period") is not None else None),
                        ("Basis", "Securities only, gross, monthly-linked: an estimate; the manager's own computation on unit values decides"),
                        ("Fee cap", "Not counted: the invoice's amount stays the fixed fee alone"),
                        ("Debited", "Not here: the line is on the bill and nothing takes it from cash")]
            if tool_key == "record_case_facts":
                self._polish_may(case_facts.RECORDERS, "A residence or guardian is recorded by an adviser, a paraplanner or operations.")
                p = await case_facts.prepare(self.session, firm, case_id=inputs.get("case_id"), document=inputs.get("document") or None)
                rows: list[tuple[str, Any]] = [("Case", p["read"]["case"]), ("Client", p["read"]["person"])]
                if p["residence"]:
                    for c in p["residence"]["changes"]:
                        rows.append((f"Residence: {c['label']}", f"{c['before'] if c['before'] not in (None, []) else 'not on file'} to {c['after']}"))
                    rows.append(("Residence evidence", p["residence"]["document"]))
                if p["guardian"]:
                    g = p["guardian"]
                    rows += [("Guardian", g["guardian_name"]), ("Relationship", guardianship.RELATIONSHIPS[g["relationship"]]),
                             ("Replaces", g["before"]), ("Guardian evidence", g["document"])]
                for note in p["already"]:
                    rows.append(("Already on file", note))
                rows.append(("Note", "Only what the case states: a FEMA status or day count is not read from a registration type"))
                return rows
            if tool_key == "record_majority_conversion":
                self._polish_may(guardianship.CONVERTERS, "A conversion is recorded by an adviser, a paraplanner or operations.")
                p = await guardianship.prepare_conversion(self.session, firm, person_id=inputs.get("person_id"), document=inputs.get("document"),
                                                          converted_on=inputs.get("converted_on") or None)
                s = p["status"]
                return [("Client", p["person"].full_name), ("Reached majority on", s["majority_on"]),
                        ("Guardian whose authority ends", p["guardian"].guardian_name if p["guardian"] else None),
                        ("Converted on", spoken(p["converted_on"])), ("Evidence", p["document"]),
                        ("Note", "Records the step: no holding or cash moves, and the adult's own KYC is recorded separately")]
        except Exception as exc:
            if type(exc).__name__ == "ExecutorError":
                raise
            if isinstance(exc, ValueError):
                raise _error(exc)
            raise
        return []
