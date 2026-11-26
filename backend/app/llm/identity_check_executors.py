"""The conversation's tools for identity and bank checks (B-387): the queue, one attempt through a connected service, and a result a
person checked by hand.

Mixed into ``ToolExecutors`` (app/llm/executors.py). Every Confirm card is the module's ``prepare``: every check the write makes,
nothing asked of a service and nothing written. ``ExecutorError`` is imported where it is raised, because executors.py imports this
module.
"""
from __future__ import annotations

import uuid
from typing import Any

TARGET_TERMS = ("kind", "person_id", "entity_id", "case_id", "party_name", "bank_link_id", "bank", "last4")
RUN_TERMS = (*TARGET_TERMS, "pan")
RESULT_TERMS = (*TARGET_TERMS, "outcome", "document", "name_on_document", "note")


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _attempt_said(result: dict) -> str:
    a = result["attempted"]
    lead = "Simulated service" if a["simulated"] else "Recorded"
    said = f"{lead}: {a['check'].split(':')[0]} for {a['party']}: {a['state_label'].lower()}"
    if a["reason"]:
        said += f" ({a['reason'].lower()})"
    if a["score"] is not None:
        said += f", name match {a['score']} of 100"
    if a["state"] == "retry":
        said += f". Attempt {a['tries']} of 3; or by hand: {a['alternative'].lower()}"
    elif a["state"] in ("review", "queued"):
        said += f". It is in the operations queue to be checked by hand: {a['alternative'].lower()}"
    if a["state"] not in ("passed", "passed_alternative"):
        said += ". It has not passed"
    return said + "."


class IdentityCheckExecutors:
    """Needs the host's session, firm_id, role, _firm() and _shelf_user()."""

    def _identity_check_map(self) -> dict:
        return {"read_identity_checks": self.read_identity_checks, "run_identity_check": self.run_identity_check,
                "record_verification_result": self.record_verification_result}

    async def read_identity_checks(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import identity_checks as c

        try:
            ids = {k: uuid.UUID(str(inputs[k])) for k in ("household_id", "case_id") if inputs.get(k)}
        except ValueError:
            raise _error(Exception("household_id and case_id are ids a read tool returned."))
        try:
            result = await c.overview(self.session, await self._firm(), **ids)
        except c.CheckError as exc:
            raise _error(exc)
        if not result["applies"]:
            return {**result, "message": "These checks are kept for an Indian firm; this firm's market is not India."}
        n = result["counts"]
        result["message"] = (f"{n['queued']} check(s) in the operations queue, {n['retry']} failed with retries left, {n['passed']} passed. "
                             "A check not listed was not run, which is not a pass. "
                             + (result["not_connected"] or ("The connected service is simulated." if result["provider"]["simulated"] else "")))
        return result

    async def run_identity_check(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import identity_checks as c

        try:
            result = await c.run(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, RUN_TERMS))
        except c.CheckError as exc:
            raise _error(exc)
        return {**result, "message": _attempt_said(result)}

    async def record_verification_result(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import identity_checks as c

        try:
            result = await c.record_result(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                           **_terms(inputs, RESULT_TERMS))
        except c.CheckError as exc:
            raise _error(exc)
        return {**result, "message": _attempt_said(result)}

    async def _identity_check_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import identity_checks as c

        firm = await self._firm()
        try:
            if tool_key == "run_identity_check":
                p = await c.prepare_run(self.session, firm, self.role, **_terms(inputs, RUN_TERMS))
                simulated = bool(getattr(p["provider"], "simulated", False))
                return [("Party", p["name"]), ("Check", c.CHECKS[p["kind"]]["label"]),
                        ("Account", f"{p['bank']} ending {p['last4']}" if p["kind"] == "bank" else None),
                        ("PAN", c.pans.mask(p["pan"])), ("Service", p["provider"].name),
                        ("Simulated", "Yes: an invented answer, not a real verification" if simulated else None),
                        ("Attempt", f"{p['attempt']} of {c.MAX_ATTEMPTS}"), ("If it fails", f"Retry, or by hand: {c.CHECKS[p['kind']]['alternative'].lower()}")]
            p = await c.prepare_result(self.session, firm, self.role, **_terms(inputs, RESULT_TERMS))
            return [("Party", p["name"]), ("Check", c.CHECKS[p["kind"]]["label"]),
                    ("Account", f"{p['bank']} ending {p['last4']}" if p["kind"] == "bank" else None), ("Was", p["state"]["label"]),
                    ("Result by hand", "Passed" if p["outcome"] == "passed" else "Failed"), ("Name on the document", p["name_shown"]),
                    ("Name match", f"{p['score']} of 100: {p['reading']}" if p["score"] is not None else None), ("Document", p["document"]),
                    ("Note", p["note"])]
        except c.CheckError as exc:
            raise _error(exc)
