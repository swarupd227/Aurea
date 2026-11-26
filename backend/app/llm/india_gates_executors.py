"""The conversation's tools for the L100 India items B-290 to B-292 and B-300: the ticket-size gates, the accredited-investor status, an
adviser's fee terms and the February rate card.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare``: every check the write makes, nothing written. ``ExecutorError`` is imported
where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

import re
from typing import Any

INDIA_GATES_TOOLS = ("read_ticket_gates", "read_accredited_investors", "record_accredited_investor", "read_adviser_fee_terms",
                     "record_adviser_fee_term", "read_rate_card", "record_figure_watch")
ACCREDITED_TERMS = ("person_id", "state", "certificate_ref", "agency", "valid_from", "valid_to", "criteria", "document", "reason")
FEE_TERMS = ("household_id", "kind", "effective_on", "mode", "advance_months", "assets", "rate", "fee", "reason", "document")
WATCH_TERMS = ("figure_key", "state", "reason")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    out = {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}
    if isinstance(out.get("criteria"), str):
        out["criteria"] = [c for c in re.split(r"[\n;]+", out["criteria"]) if c.strip()]
    return out


class IndiaGatesExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_gates_map(self) -> dict:
        return {k: getattr(self, k) for k in INDIA_GATES_TOOLS}

    # -- Reads -------------------------------------------------------------------------------------------

    async def read_ticket_gates(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import product_gates
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not product_gates.applies(firm):
            return {"applies": False, "categories": [], "message": "Ticket-size gates are kept for an Indian firm."}
        if not inputs.get("household_id"):
            result = await product_gates.for_firm(self.session, firm, limit=None)
            opened = [f"{r['household']}: {', '.join(product_gates.CATEGORIES[k]['label'] for k in r['open']) or 'none'}" for r in result["households"]]
            result["message"] = f"{len(result['households'])} household(s). Open: {'; '.join(opened)}. {product_gates.NOT_ADVICE}"
            return {**result, "fleet": True}
        result = await product_gates.open_to(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
        c = result["counts"]
        result["message"] = (f"{c['open']} categor(ies) open, {c['below']} below the minimum, {c['needs_fact']} needing a fact first. "
                             + (f"Open: {', '.join(result['open_labels'])}. " if result["open_labels"] else "") + product_gates.NOT_ADVICE)
        return result

    async def read_accredited_investors(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import accredited
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not accredited.applies(firm):
            return {"applies": False, "people": [], "message": "Accredited-investor status is recorded for an Indian firm."}
        if inputs.get("household_id"):
            result = await accredited.household_view(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
            result["message"] = accredited.CAVEAT
            return result
        result = await accredited.overview(self.session, firm)
        result["message"] = f"{result['counts'].get('accredited', 0)} person(s) accredited on a recorded certificate. {accredited.CAVEAT}"
        return result

    async def read_adviser_fee_terms(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import adviser_fees
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not adviser_fees.applies(firm):
            return {"applies": False, "message": "An Investment Adviser's fee terms are recorded for an Indian firm."}
        if inputs.get("household_id"):
            result = await adviser_fees.household_view(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
        else:
            result = await adviser_fees.overview(self.session, firm)
        result["message"] = adviser_fees.NOT_COMPLIANCE
        return result

    async def read_rate_card(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import rate_card

        firm = await self._firm()
        if self.role not in (self._gates_readers()):
            raise _error(Exception("The rate card is read by compliance, a branch leader, operations and an administrator."))
        result = await rate_card.overview(self.session, firm)
        if not result["applies"]:
            result["message"] = "The February rate card is kept for an Indian firm."
            return result
        c = result["counts"]
        result["message"] = (f"{c['total']} figure(s) on the {result['year']} rate card ({c['budget']} the Budget's, {c['sebi_circular']} a SEBI circular's); "
                             f"{c['not_reviewed']} not reviewed by counsel, {c['awaiting_budget']} awaiting the Budget. " + rate_card.NOT_A_FINDING)
        return result

    def _gates_readers(self):
        from app.core.security import UserRole

        return {UserRole.COMPLIANCE, UserRole.BRANCH_LEADER, UserRole.OPERATIONS, UserRole.ADMIN}

    # -- Writes ------------------------------------------------------------------------------------------

    async def record_accredited_investor(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import accredited

        try:
            result = await accredited.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, ACCREDITED_TERMS))
        except accredited.AccreditedError as exc:
            raise _error(exc)
        result["message"] = "Recorded. The criteria are laid out as the certificate states them and are not checked against wealth."
        return result

    async def record_adviser_fee_term(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import adviser_fees

        try:
            result = await adviser_fees.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, FEE_TERMS))
        except adviser_fees.FeeTermError as exc:
            raise _error(exc)
        result["message"] = "Recorded. It lowers no fee and says nothing about whether the adviser complies."
        return result

    async def record_figure_watch(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import counsel_review, rate_card

        firm = await self._firm()
        try:
            await counsel_review.record_watch(self.session, firm, self.role, by=await self._shelf_user(), **_terms(inputs, WATCH_TERMS))
        except counsel_review.CounselError as exc:
            raise _error(exc)
        result = await rate_card.overview(self.session, firm)
        result["message"] = "Recorded. The figure itself is unchanged."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_gates_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import accredited, adviser_fees, counsel_review, firm_time, markets

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        money = lambda v: markets.money(v, firm.base_currency or "INR") if v is not None else None  # noqa: E731
        try:
            if tool_key == "record_accredited_investor":
                p = await accredited.prepare(self.session, firm, self.role, **_terms(inputs, ACCREDITED_TERMS))
                return [("Person", p["person"].full_name), ("Recording", "A withdrawal" if p["state"] == "withdrawn" else "An accredited-investor certificate"),
                        ("Certificate", p["certificate_ref"]), ("Agency", p["agency"]),
                        ("Starts", spoken(p["valid_from"])), ("Runs out", spoken(p["valid_to"]) if p["valid_to"] else "No end date on the certificate"),
                        ("Criteria stated", "; ".join(p["criteria"]) or None),
                        ("Replaces", accredited.STATES[p["before_state"]] if p["before"] else "Nothing on file"), ("Reason", p["reason"]),
                        ("Evidence", p["document"]),
                        ("Effects", "The Investment Adviser fee cap no longer reaches this person's own mandates, and the SIF minimum is waived"
                         if p["state"] == "granted" else "The fee cap reaches this person's mandates again, and the SIF minimum applies"),
                        ("Note", accredited.CAVEAT)]
            if tool_key == "record_adviser_fee_term":
                p = await adviser_fees.prepare(self.session, firm, self.role, **_terms(inputs, FEE_TERMS))
                rows: list[tuple[str, Any]] = [("Household", p["household"].name), ("Term", adviser_fees.KINDS[p["kind"]]), ("From", spoken(p["effective_on"]))]
                if p["kind"] == "advance":
                    rows += [("Collected ahead", f"{p['advance_months']} month(s)"),
                             ("Replaces", f"{p['before'].advance_months} month(s)" if p["before"] else None)]
                elif p["kind"] == "mode":
                    rows += [("Mode", adviser_fees.MODES[p["mode"]]), ("Replaces", adviser_fees.MODES[p["before"].mode] if p["before"] else "No mode on file"),
                             ("A switch", "Yes: that financial year's cap is the higher of the two modes" if p["switch"] else None), ("Reason", p["reason"])]
                else:
                    rows += [("Assets under the distribution arrangement", money(p["assets"])), ("Rate", f"{p['rate']:.2%}"), ("Fee", money(p["fee"])),
                             ("Counts", "Toward what the family is charged for the year, against the cap")]
                return [*rows, ("Evidence", p["document"]), ("Note", "It lowers no fee and says nothing about whether the adviser complies")]
            if tool_key == "record_figure_watch":
                p = await counsel_review.prepare_watch(self.session, firm, self.role, **_terms(inputs, WATCH_TERMS))
                return [("Figure", p["figure"].label), ("Key", p["key"]), ("Its source", p["figure"].source), ("Last read from it", p["figure"].as_of.isoformat()),
                        ("Marking", "Awaiting the Budget" if p["state"] == "awaiting_budget" else "Released: no longer awaiting"), ("Reason", p["reason"]),
                        ("Note", "The figure itself is unchanged; the mark ends by itself when a newer value is recorded in the code")]
        except (accredited.AccreditedError, adviser_fees.FeeTermError, counsel_review.CounselError) as exc:
            raise _error(exc)
        return []
