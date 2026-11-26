"""The conversation's tools for India money in and bank rules (B-388): the payer check, a payer exception, a credit by an Indian rail,
an inward remittance, a mandate's first debit and the firm's cooling-off on a changed bank.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare``: every check the write makes, nothing written. ``ExecutorError`` is imported
where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

MONEY_TOOLS = ("check_payer", "record_payer_exception", "record_india_credit", "record_inward_remittance", "record_first_debit", "set_bank_cooling_off")
MONEY_WRITES = MONEY_TOOLS[1:]
EXCEPTION_TERMS = ("account_id", "kind", "payer_name", "bank", "last4", "relationship", "evidence", "withdraw", "reason")
CREDIT_TERMS = ("account_id", "rail", "amount", "bank_link_id", "payer_name", "payer_last4", "reference", "on", "evidence", "deposit_id", "return_reason")
REMITTANCE_TERMS = ("account_id", "bank_link_id", "account_kind", "received_on", "amount_inr", "certificate_reference", "remitter_name", "foreign_amount",
                    "foreign_currency", "document")
DEBIT_TERMS = ("account_id", "reference", "outcome", "debited_on", "amount", "document", "reason")


def _terms(inputs: dict, keys) -> dict:
    terms = {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}
    if "withdraw" in terms:
        terms["withdraw"] = str(terms["withdraw"]).strip().lower() in ("true", "yes", "1", "y")
    return terms


def _errors():
    from app.aurea_core import india_money, sip_mandates

    return (india_money.IndiaMoneyError, sip_mandates.MandateError)


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


class IndiaMoneyExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_money_map(self) -> dict:
        return {k: getattr(self, k) for k in MONEY_TOOLS}

    async def check_payer(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_money

        firm = await self._firm()
        try:
            got = await india_money.verdict(self.session, firm, account_id=inputs.get("account_id"), payer_name=inputs.get("payer_name"),
                                            bank_link_id=inputs.get("bank_link_id"), last4=inputs.get("last4"), amount=inputs.get("amount"), use=inputs.get("use"))
        except india_money.IndiaMoneyError as exc:
            raise _error(exc)
        return {"applies": True, "check": got, "message": f"{'Accepted' if got['ok'] else 'Refused'}: {got['said']} Nothing is recorded and no money moves."}

    async def record_payer_exception(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_money

        try:
            result = await india_money.record_exception(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                        **_terms(inputs, EXCEPTION_TERMS))
        except _errors() as exc:
            raise _error(exc)
        result["message"] = "Recorded. Each payment under it is still checked: who the investor is that day, and any line the exception carries."
        return result

    async def record_india_credit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_money

        try:
            result = await india_money.record_credit(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, CREDIT_TERMS))
        except _errors() as exc:
            raise _error(exc)
        return {**result, "message": "Recorded from the advice. It writes no cash and places no order: the custodian's ledger carries the money."}

    async def record_inward_remittance(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_money

        try:
            result = await india_money.record_remittance(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                         **_terms(inputs, REMITTANCE_TERMS))
        except _errors() as exc:
            raise _error(exc)
        result["message"] = "Recorded with its certificate reference. It writes no cash, and says nothing about whether the money may be repatriated."
        return result

    async def record_first_debit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_money

        try:
            result = await india_money.record_first_debit(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, DEBIT_TERMS))
        except _errors() as exc:
            raise _error(exc)
        raised = result.get("tasks_raised") or []
        result["message"] = ("Recorded. Nothing is retried or sent to the client." +
                             (f" A task is open for {raised[0]['to'] or 'nobody: the household has no adviser'}." if raised else ""))
        return result

    async def set_bank_cooling_off(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_money
        from app.models.identity import User

        actor = await self.session.get(User, self.user_id) if self.user_id else None
        try:
            result = await india_money.set_cooling(self.session, await self._firm(), actor, days=inputs.get("days"), reason=inputs.get("reason"))
        except india_money.IndiaMoneyError as exc:
            raise _error(exc)
        result["message"] = f"Set, with the reason on the audit log. {result['cooling']['label']}."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_money_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, india_money, money_in, sip_mandates

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        money = india_money.rupees
        nothing = "No cash is written and no order is placed"
        try:
            if tool_key == "record_payer_exception":
                p = await india_money.prepare_exception(self.session, firm, self.role, **_terms(inputs, EXCEPTION_TERMS))
                return [("Account", p["account"].name), ("Investor", p["investor"]), ("Recording", "Withdrawal of an exception" if p["withdraw"] else "A payer exception"),
                        ("Exception", india_money.EXCEPTION_KINDS[p["kind"]]), ("Payer", p["payer"]), ("Bank", f"{p['bank']} ending {p['last4']}"),
                        ("Payer is", india_money.RELATIVES.get(p["relationship"], (p["relationship"] or "").capitalize() or None) if p["relationship"] else None),
                        ("Most a payment", f"{money(p['line'])} (assumed, not reviewed by counsel)" if p["line"] else None),
                        ("Reason", p["reason"]), ("Evidence", p["evidence"])]
            if tool_key == "record_india_credit":
                p = await india_money.prepare_credit(self.session, firm, self.role, **_terms(inputs, CREDIT_TERMS))
                if p["kind"] == "return":
                    d, detail = p["deposit"], p["detail"]
                    return [("Account", p["account"].name), ("Deposit", f"{money(float(d.amount))} on {spoken(d.received_on)}"),
                            ("Reference", detail.bank_reference if detail else None), ("Recording", "A return"), ("Returned", spoken(p["on"])), ("Reason", p["reason"]),
                            ("Already available", "Yes: it may have been invested" if p["after_available"] else "No: it was still on hold"),
                            ("Evidence", p["evidence"]), ("Note", nothing)]
                policy = money_in.policy(firm)
                return [("Account", p["account"].name), ("Investor", p["check"]["investor"]), ("Amount", money(p["amount"])), ("By", india_money.RAILS[p["rail"]][1]),
                        ("Reference", p["reference"]), ("Paid by", f"{p['payer']}, account ending {p['last4']}"),
                        ("Registered bank", f"{p['link'].bank} ending {p['link'].last4}, {p['link'].owner_name}" if p["link"] else "Not one of the registered banks"),
                        ("Payer check", p["check"]["label"]), ("Credited", spoken(p["on"])),
                        ("Hold", f"Until {spoken(p['hold_until'])}" if p["hold_until"] else ("None" if policy["deposit_hold_threshold"] is not None else "None: the firm has set no hold")),
                        ("Evidence", p["evidence"]), ("Note", nothing)]
            if tool_key == "record_inward_remittance":
                p = await india_money.prepare_remittance(self.session, firm, self.role, **_terms(inputs, REMITTANCE_TERMS))
                return [("Account", p["account"].name), ("Investor", p["investor"]), ("Credited to", f"{india_money.ACCOUNT_KINDS[p['kind']]} account, {p['link'].bank} ending {p['link'].last4}"),
                        ("Amount", money(p["amount"])), ("Foreign amount", f"{p['currency']} {p['foreign']:,.2f}" if p["foreign"] is not None else "Not on the certificate"),
                        ("Credited", spoken(p["on"])), ("Sent by", p["remitter"]),
                        ("Sender is the investor", {"full": "Yes", "initials": "By initials only", "none": "No"}[p["remitter_match"]]),
                        ("Certificate", p["reference"]), ("Warning", p["warning"]), ("Evidence", p["document"]), ("Note", nothing)]
            if tool_key == "record_first_debit":
                p = await india_money.prepare_first_debit(self.session, firm, self.role, **_terms(inputs, DEBIT_TERMS))
                m = p["mandate"]
                then = await india_money.task_owner_said(self.session, firm, p["account"]) if p["outcome"] == "failed" else None
                return [("Account", p["account"].name), ("Mandate", f"{sip_mandates.TYPES[m.mandate_type]} {p['reference']}"),
                        ("First debit", "Confirmed" if p["outcome"] == "confirmed" else "Failed"), ("Amount", money(p["amount"])), ("On", spoken(p["on"])),
                        ("Reason", p["reason"]), ("Then", then), ("Evidence", p["document"]),
                        ("Note", "Nothing is presented or retried, and nothing is sent to the client")]
            if tool_key == "set_bank_cooling_off":
                p = india_money.prepare_cooling(firm, days=inputs.get("days"), reason=inputs.get("reason"))
                said = lambda n: f"{n} days" if n else "None"  # noqa: E731
                return [("Setting", "Cooling-off before a changed bank may be paid"), ("From", said(p["before"])), ("To", said(p["days"])), ("Reason", p["reason"]),
                        ("Effect", "Payments to a changed bank are refused until it has passed" if p["days"] else "A changed bank is payable at once"),
                        ("Note", "The firm's own control: no legal period is assumed")]
        except _errors() as exc:
            raise _error(exc)
        return []
