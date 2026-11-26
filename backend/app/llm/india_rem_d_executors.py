"""The conversation's tools for the India remainders (B-303, B-283, B-274, B-280, B-281): a manager's own staff, a PMS client's unit-value
ledger and a verifier's letter, the debit of a confirmed PMS fee line, a minor's guardian papers and the real estate choice.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in ``preview_change``.
Every card is the module's ``prepare``: every check the write makes, nothing written. ``ExecutorError`` is imported where it is raised,
because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

INDIA_REM_D_TOOLS = ("read_manager_staff", "record_manager_affiliation", "read_pms_ledger", "record_pms_unit_value", "record_pms_verification",
                     "debit_pms_fee_line", "read_guardian_papers", "read_real_estate_choice")
AFFILIATION_TERMS = ("person_id", "manager", "affiliation", "state", "effective_on", "document")
UNIT_TERMS = ("mandate_id", "as_of", "nav_per_unit", "units", "document")
VERIFY_TERMS = ("strategy", "verifier", "period_start", "period_end", "letter_date", "scope", "document", "state", "reason")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


class IndiaRemDExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_rem_d_map(self) -> dict:
        return {k: getattr(self, k) for k in INDIA_REM_D_TOOLS}

    # -- Reads -------------------------------------------------------------------------------------------

    async def read_manager_staff(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import manager_staff

        result = await manager_staff.overview(self.session, await self._firm())
        result["message"] = ("No post is recorded for anyone." if result["applies"] and not result["people"] else
                             f"{sum(1 for p in result.get('people', []) if p['state'] == 'in_force')} post(s) in force. {result.get('note', '')}")
        return result

    async def read_pms_ledger(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import pms_ledger

        firm = await self._firm()
        result = await pms_ledger.ledger_view(self.session, firm, mandate_id=inputs.get("mandate_id") or None)
        if not result["applies"]:
            result["message"] = "A unit-value ledger is kept for a firm holding a Portfolio Manager licence."
            return result
        result["lines"] = await pms_ledger.open_lines(self.session, firm)
        c = result["counts"]
        result["message"] = (f"{c['with_values']} client(s) with values per unit on file; {c['verified']} quarter(s) verified and {c['not_verified']} not verified. "
                             + result["note"])
        return result

    async def read_guardian_papers(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import guardianship

        result = await guardianship.papers(self.session, await self._firm())
        if result["applies"]:
            result["message"] = (f"{len(result['waiting'])} minor(s) waiting on the guardian's KYC or the minor's PAN. " if result["waiting"] else
                                 "Every minor's papers are on file. ") + result["note"]
        else:
            result["message"] = "A minor's guardian papers are read for an Indian firm."
        return result

    async def read_real_estate_choice(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import real_estate_view

        return await real_estate_view.read(self.session, await self._firm(), inputs)

    # -- Writes ------------------------------------------------------------------------------------------

    async def record_manager_affiliation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import manager_staff

        try:
            result = await manager_staff.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, AFFILIATION_TERMS))
        except manager_staff.StaffError as exc:
            raise _error(exc)
        result["message"] = "Recorded. It lowers an AIF's minimum for that manager's own products only, and nothing is worked out from it."
        return result

    async def record_pms_unit_value(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import pms_ledger

        try:
            result = await pms_ledger.record_unit_value(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, UNIT_TERMS))
        except pms_ledger.LedgerError as exc:
            raise _error(exc)
        result["message"] = "Recorded from the manager's statement. The high-water mark and the returns are read from it; nothing is interpolated."
        return result

    async def record_pms_verification(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import pms_ledger

        try:
            result = await pms_ledger.record_verification(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, VERIFY_TERMS))
        except pms_ledger.LedgerError as exc:
            raise _error(exc)
        result["message"] = "Recorded from the letter. Nothing here verifies a return; it records that a verifier did."
        return result

    async def debit_pms_fee_line(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import pms_ledger

        try:
            result = await pms_ledger.debit_fee_line(self.session, await self._firm(), self.role, by=await self._shelf_user(), fee_line_id=inputs.get("fee_line_id"))
        except pms_ledger.LedgerError as exc:
            raise _error(exc)
        result["message"] = "Debited from the client's cash once. Nothing was sold and no part was taken."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_rem_d_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, manager_staff, markets, pms_ledger

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        money = lambda v: markets.money(v, firm.base_currency or "INR") if v is not None else None  # noqa: E731
        try:
            if tool_key == "record_manager_affiliation":
                p = await manager_staff.prepare(self.session, firm, self.role, **_terms(inputs, AFFILIATION_TERMS))
                return [("Person", p["person"].full_name), ("Manager", p["manager"]), ("Recording", manager_staff.ROLES[p["role"]] if p["state"] == "in_force" else "The end of the post"),
                        ("From", spoken(p["effective_on"])), ("Replaces", manager_staff.ROLES[p["before"].role] + " · " + manager_staff.STATES[p["before"].state] if p["before"] else "Nothing on file"),
                        ("Evidence", p["document"]), ("Effect", "An AIF of this manager takes the lower minimum from this person; no other product does"
                                                       if p["state"] == "in_force" else "An AIF of this manager takes the standard minimum from this person")]
            if tool_key == "record_pms_unit_value":
                p = await pms_ledger.prepare_unit_value(self.session, firm, self.role, **_terms(inputs, UNIT_TERMS))
                return [("Client", p["mandate"].name), ("Statement date", spoken(p["as_of"])), ("Value per unit", f"{p['nav_per_unit']:,.4f}"),
                        ("Units", f"{p['units']:,.4f}" if p["units"] is not None else "Not stated: a performance fee cannot be worked on the ledger without them"),
                        ("Replaces", f"{p['replaces']:,.4f}" if p["replaces"] is not None else "Nothing on file for that day"), ("Evidence", p["document"]),
                        ("Note", "Read from the statement, never interpolated; the high-water mark and the returns are worked from the values on file")]
            if tool_key == "record_pms_verification":
                p = await pms_ledger.prepare_verification(self.session, firm, self.role, **_terms(inputs, VERIFY_TERMS))
                return [("Strategy", p["strategy"]), ("Verifier", p["verifier"]), ("Recording", "A verification" if p["state"] == "verified" else "A withdrawal"),
                        ("Covers", f"{spoken(p['period_start'])} to {spoken(p['period_end'])}"), ("Letter of", spoken(p["letter_date"])), ("Scope", p["scope"]),
                        ("Reason", p["reason"]), ("Evidence", p["document"]),
                        ("Effect", "A client's report for a period it covers may state the return" if p["state"] == "verified" else "A report for that period reads not verified again")]
            if tool_key == "debit_pms_fee_line":
                p = await pms_ledger.prepare_debit(self.session, firm, self.role, by=await self._shelf_user(), fee_line_id=inputs.get("fee_line_id"))
                return [("Client", p["mandate"].name), ("Period", f"{spoken(p['line'].period_start)} to {spoken(p['line'].period_end)}"), ("Amount", money(p["amount"])),
                        ("Line confirmed by", p["line"].confirmed_by), ("Bill", p["invoice"].invoice_number), ("Cash held", money(p["cash"])), ("Held back", money(p["held_back"])),
                        ("Free after that", money(p["free"])), ("Authority", "Fee debit and e-delivery are on the consent registry"),
                        ("Note", "A second person takes it, from the mandate's own cash alone: nothing is sold and no part is taken")]
        except pms_ledger.LedgerError as exc:
            raise _error(exc)
        except manager_staff.StaffError as exc:
            raise _error(exc)
        return []
