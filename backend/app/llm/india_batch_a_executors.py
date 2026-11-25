"""The conversation's tools for India's batch A items: overseas remittances (B-293), SIP debit mandates (B-295), the Indian shelf's other
classes and a pledge of units (B-296), and the tax habits (B-299).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare``: every check the write makes, nothing written. ``ExecutorError`` is imported
where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

BATCH_A_TOOLS = ("read_lrs", "record_lrs_remittance", "read_sip_mandates", "record_sip_mandate", "read_india_savings", "record_savings_holding",
                 "read_pledges", "record_pledge", "read_tax_habits", "record_tax_gift")
LRS_TERMS = ("person_id", "remitted_on", "amount_usd", "amount_inr", "purpose", "bank", "tcs", "document", "reverses_id", "reason")
MANDATE_TERMS = ("account_id", "reference", "mandate_type", "per_debit_limit", "starts_on", "expires_on", "last4", "status", "document", "cancel", "reason")
SAVINGS_TERMS = ("household_id", "kind", "institution", "value", "value_on", "document", "person_id", "rate", "maturity_on", "account_label")
SAVINGS_DETAILS = ("started_on", "annual_premium", "fund_management_charge", "charges_note", "grams", "series", "tier", "opened_on", "employer", "issuer")
PLEDGE_TERMS = ("account_id", "instrument", "lender", "units", "marked_on", "document", "release_of")
GIFT_TERMS = ("giver_id", "receiver_id", "relationship", "gifted_on", "value", "description", "document", "consideration", "instrument", "quantity")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


def _savings_terms(inputs: dict) -> dict:
    terms = _terms(inputs, SAVINGS_TERMS)
    terms["details"] = {k: inputs[k] for k in SAVINGS_DETAILS if inputs.get(k) not in (None, "")}
    return terms


class IndiaBatchAExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_batch_a_map(self) -> dict:
        return {k: getattr(self, k) for k in BATCH_A_TOOLS}

    # -- Reads -------------------------------------------------------------------------------------------

    async def read_lrs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import lrs
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not lrs.applies(firm):
            return {"applies": False, "people": [], "message": "The Liberalised Remittance Scheme is read for an Indian firm."}
        result = (await lrs.household_view(self.session, firm, _parse_uuid(inputs["household_id"], "household_id")) if inputs.get("household_id")
                  else await lrs.overview(self.session, firm))
        assessed = sum(1 for p in result["people"] if p["state"] == "assessed")
        result["message"] = (f"{assessed} of {len(result['people'])} person(s) have a remittance on file; the rest are not assessed, which does not mean they remitted nothing. "
                             f"{lrs.ESTIMATE} Not modelled: {'; '.join(lrs.NOT_MODELLED)}.")
        return result

    async def read_sip_mandates(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import sip_mandates
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        result = await sip_mandates.overview(self.session, firm, household_id=_parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None)
        if not result["applies"]:
            result["message"] = "SIP mandates are recorded for an Indian firm."
        else:
            result["message"] = (f"{len(result['mandates'])} mandate(s) on file; {result['without_mandate']} active SIP(s) with no mandate on file (warned, not stopped), "
                                 f"{result['failing']} whose debit would fail on its mandate. Nothing here registers a mandate or moves money.")
        return result

    async def read_india_savings(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_savings
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not india_savings.applies(firm):
            return {"applies": False, "holdings": [], "message": "These classes are recorded for an Indian firm."}
        if inputs.get("household_id"):
            result = await india_savings.household_view(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
        else:
            result = await india_savings.overview(self.session, firm)
        result["message"] = ("Each holding is the statement's: its value and its rate. An administered rate is never assumed. Nothing here recommends or compares products. "
                             f"{len(result['holdings'])} holding(s) on file.")
        return result

    async def read_pledges(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import pledges
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        result = await pledges.overview(self.session, firm, household_id=_parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None)
        result["message"] = ("Pledged units cannot be sold by a direct order, the rebalancer or settlement. No pledge on file does not mean no pledge exists."
                             if result["applies"] else "A pledge of units is recorded for an Indian firm.")
        return result

    async def read_tax_habits(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_habits
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not tax_habits.applies(firm):
            return {"applies": False, "message": "These tax habits are read for an Indian firm."}
        try:
            result = await tax_habits.household_view(self.session, firm, _parse_uuid(inputs.get("household_id") or "", "household_id"))
        except tax_habits.HabitError as exc:
            raise _error(exc)
        result["message"] = ("Laid out for a qualified person to check, from what is on file; not assessed where a fact is not. Nothing here advises a sale, a gift or a rebuy, "
                             "and nothing says a rebuy is safe.")
        return result

    # -- Writes ------------------------------------------------------------------------------------------

    async def record_lrs_remittance(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import lrs

        try:
            result = await lrs.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, LRS_TERMS))
        except lrs.LrsError as exc:
            raise _error(exc)
        return {"applies": True, "people": [result], "message": "Recorded. It moves no money and estimates nothing."}

    async def record_sip_mandate(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import sip_mandates

        try:
            result = await sip_mandates.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, MANDATE_TERMS))
        except sip_mandates.MandateError as exc:
            raise _error(exc)
        result["message"] = "Recorded. It registers nothing with a bank and moves no money."
        return result

    async def record_savings_holding(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_savings

        try:
            result = await india_savings.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_savings_terms(inputs))
        except ValueError as exc:     # SavingsError, and held_away's own refusals, read the same way
            raise _error(exc)
        result["message"] = "Recorded as a held-away line, counted once in net worth. The rate and the value are the statement's."
        return result

    async def record_pledge(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import pledges

        try:
            result = await pledges.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, PLEDGE_TERMS))
        except pledges.PledgeError as exc:
            raise _error(exc)
        result["message"] = "Recorded. Pledged units are no longer free to sell; nothing is marked at the depository from here."
        return result

    async def record_tax_gift(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_habits

        try:
            result = await tax_habits.record_gift(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, GIFT_TERMS))
        except tax_habits.HabitError as exc:
            raise _error(exc)
        result["message"] = "Recorded. It moves no asset, and the reading is laid out for a qualified person to check."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_batch_a_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, india_savings, lrs, markets, pledges, sip_mandates, tax_habits

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        money = lambda v: markets.money(v, firm.base_currency or "INR") if v is not None else None  # noqa: E731
        try:
            if tool_key == "record_lrs_remittance":
                p = await lrs.prepare(self.session, firm, self.role, **_terms(inputs, LRS_TERMS))
                rows: list[tuple[str, Any]] = [("Person", p["person"].full_name), ("Recording", "A reversal of an earlier entry" if p["reverses"] else "A remittance abroad"),
                                               ("On", spoken(p["remitted_on"])), ("Dollars counted", f"USD {p['amount_usd']:,.2f}"), ("Rupees debited", money(p["amount_inr"]) if p["amount_inr"] is not None else "Not on the advice"),
                                               ("Purpose", lrs.PURPOSES[p["purpose"]]), ("Bank", p["bank"]), ("Tax collected at source", money(p["tcs"]) if p["tcs"] is not None else "Not on the advice")]
                if p.get("after"):
                    a = p["after"]
                    rows += [("The year after", f"USD {a['remitted_usd']:,.0f} of USD {a['limit_usd']:,.0f}; USD {a['headroom_usd']:,.0f} left"
                              + (" - over the limit" if a["over_limit"] else ""))]
                return [*rows, ("Reason", p["reason"]), ("Evidence", p["document"]), ("Note", "It moves no money and estimates nothing")]
            if tool_key == "record_sip_mandate":
                p = await sip_mandates.prepare(self.session, firm, self.role, **_terms(inputs, MANDATE_TERMS))
                rows = [("Account", p["account"].name), ("Reference", p["reference"]), ("Recording", "Cancellation" if p["kind"] == "cancelled" else sip_mandates.TYPES[p["type"]] + " mandate"),
                        ("Per-debit limit", money(p["limit"])), ("Starts", spoken(p["starts_on"])), ("Expires", spoken(p["expires_on"]) if p["expires_on"] else "No expiry on the registration"),
                        ("Status", sip_mandates.STATUSES.get(p["status"], p["status"])),
                        ("Bank account", f"{p['link'].bank} ending {p['link'].last4}, verified {spoken(p['link'].verified_on)}" if p["link"] else None),
                        ("Replaces", "Nothing on file" if p["before"] is None else f"The registration of {spoken(p['before'].starts_on)}"),
                        ("Over the UPI Autopay line", "Yes: above the figure for mutual-fund debits, which counsel has not read" if p.get("over_upi_line") else None),
                        ("Reason", p["reason"]), ("Evidence", p["document"]), ("Note", "It registers nothing with a bank and moves no money")]
                return rows
            if tool_key == "record_savings_holding":
                p = await india_savings.prepare(self.session, firm, self.role, **_savings_terms(inputs))
                return [("Household", p["household"].name), ("Class", p["label"]), ("Held at", p["institution"]), ("Owner", p["person"].full_name if p["person"] else "Joint"),
                        ("Value", money(p["value"])), ("As at", spoken(p["value_on"])), ("Rate on the statement", f"{p['rate']:g}% a year" if p["rate"] is not None else "Not stated"),
                        ("Matures", spoken(p["maturity_on"]) if p["maturity_on"] else None), ("Replaces", "An earlier statement for the same holding" if p["replacing"] else "Nothing on file"),
                        ("Statement", p["source"]), ("Note", "Counted once in net worth; nothing is estimated or recommended")]
            if tool_key == "record_pledge":
                p = await pledges.prepare(self.session, firm, self.role, **_terms(inputs, PLEDGE_TERMS))
                return [("Account", p["account"].name), ("Recording", "A release" if p["kind"] == "release" else "A pledge to a lender"), ("Instrument", p["instrument"].symbol),
                        ("Lender", p["lender"]), ("Units", f"{p['units']:g}"), ("On", spoken(p["marked_on"])), ("Under lien after", f"{p['under_lien_after']:g} units"),
                        ("Evidence", p["document"]),
                        ("Effect", "Those units can no longer be sold by a direct order, the rebalancer or settlement" if p["kind"] == "pledge" else "The released units are free to sell again"),
                        ("Note", "Nothing is marked at the depository from here")]
            if tool_key == "record_tax_gift":
                p = await tax_habits.prepare_gift(self.session, firm, self.role, **_terms(inputs, GIFT_TERMS))
                return [("From", p["giver"].full_name), ("To", p["receiver"].full_name), ("The receiver is, as stated", tax_habits.RELATIONSHIPS[p["relationship"]]),
                        ("On", spoken(p["gifted_on"])), ("Value", money(p["value"])), ("What", p["description"]),
                        ("Asset", f"{p['instrument'].symbol} x {p['quantity']:g}" if p["instrument"] and p["quantity"] else (p["instrument"].symbol if p["instrument"] else None)),
                        ("Reads as", "From a specified relative: not income in the receiver's hands" if p["relative"] else "Not a specified relative: income in the receiver's hands above the yearly threshold"),
                        ("Clubbing", "Income the asset earns is added back to the giver" if p["clubbed"] else None),
                        ("Evidence", p["document"]), ("Note", "The relationship is as stated, never inferred; laid out for a qualified person to check")]
        except (lrs.LrsError, sip_mandates.MandateError, india_savings.SavingsError, pledges.PledgeError, tax_habits.HabitError) as exc:
            raise _error(exc)
        except ValueError as exc:      # held_away's refusals
            raise _error(exc)
        return []
