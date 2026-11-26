"""The conversation's tools for the KYC registry's status (B-386): the book view, an answer recorded by a person or returned by a
connected lookup, a remediation step, a contact attempt, and a client's own reading.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare``: every check the write makes, nothing written. ``ExecutorError`` is imported
where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

import uuid
from typing import Any

PARTY_TERMS = ("person_id", "entity_id", "case_id", "party_name")
LOOKUP_TERMS = (*PARTY_TERMS, "registry", "status", "agency", "reference", "answered_on", "deficiencies", "deficiency_note", "holds", "document")
STEP_TERMS = ("lookup_id", "step", "state", "happened_on", "note")
CONTACT_TERMS = (*PARTY_TERMS, "channel", "outcome", "happened_on", "note")


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _said(result: dict) -> str:
    c = result["counts"]
    if result.get("case_id"):
        row = result["cases"][0]
        who = "; ".join(f"{p['name']}: {p['label'].lower()}" for p in row["parties"])
        return f"{row['client']}: KYC registry status {row['label'].lower()} ({who}). " + (f"{row['timeline']}." if row["timeline"] else
                                                                                         "Nothing in the registry status holds a timeline.")
    mix = ", ".join(f"{m['count']} {m['label'].lower()}" for m in result["mix"] if m["count"])
    return (f"{c['parties']} part(ies): {mix or 'none'}. {c['backlog']} open re-KYC item(s), {c['no_contact']} with no contact attempt "
            f"recorded since it opened. {c['cases_not_looked_up']} of {c['cases']} open case(s) not looked up at the registry.")


class KycRegistryExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _kyc_registry_map(self) -> dict:
        return {"read_kyc_registry": self.read_kyc_registry, "record_kyc_lookup": self.record_kyc_lookup,
                "lookup_kyc_registry": self.lookup_kyc_registry, "record_kyc_step": self.record_kyc_step,
                "record_kyc_contact": self.record_kyc_contact, "read_my_kyc_status": self.read_my_kyc_status}

    async def read_kyc_registry(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import kyc_registry

        try:
            ids = {k: uuid.UUID(str(inputs[k])) for k in ("household_id", "case_id") if inputs.get(k)}
        except ValueError:
            raise _error(Exception("household_id and case_id are ids a read tool returned."))
        try:
            result = await kyc_registry.book(self.session, await self._firm(), **ids)
        except kyc_registry.RegistryError as exc:
            raise _error(exc)
        if not result["applies"]:
            return {**result, "message": "The KYC registry is read for an Indian firm; this firm's market is not India."}
        result["message"] = _said(result) + (f" {result['not_connected']}" if result["not_connected"] else
                                             " The connected lookup is simulated." if result["provider"]["simulated"] else "")
        return result

    async def read_my_kyc_status(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import client_view, kyc_registry
        from app.models.identity import User

        try:
            household = await client_view.household_of(self.session, await self.session.get(User, self.user_id))
        except client_view.ClientViewError as exc:
            raise _error(exc)
        result = await kyc_registry.for_client(self.session, await self._firm(), household)
        if not result["applies"]:
            return {**result, "message": "There is no KYC registry status to show for this firm."}
        held = [p["name"] for p in result["people"] if p["status"] in kyc_registry.STOPS]
        result["message"] = ("; ".join(f"{p['name']}: {p['label'].lower()}" for p in result["people"]) or "Nobody is listed") + (
            f". {', '.join(held)} has steps to complete; the adviser records what is sent." if held else ".")
        return result

    async def record_kyc_lookup(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import kyc_registry

        try:
            result = await kyc_registry.record_lookup(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                      **_terms(inputs, LOOKUP_TERMS))
        except kyc_registry.RegistryError as exc:
            raise _error(exc)
        return {**result, "message": self._lookup_said(result)}

    async def lookup_kyc_registry(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import kyc_registry

        try:
            result = await kyc_registry.provider_lookup(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                        **_terms(inputs, PARTY_TERMS))
        except kyc_registry.RegistryError as exc:
            raise _error(exc)
        return {**result, "message": self._lookup_said(result)}

    @staticmethod
    def _lookup_said(result: dict) -> str:
        r, task = result["recorded"], result.get("task_raised")
        out = f"{'Simulated lookup' if r['simulated'] else 'Recorded'}: {r['party']} is {r['label'].lower()} at the registry."
        if r["status"] in ("on_hold", "rejected"):
            out += " Activation and the household's orders are held until a newer answer clears it."
        if task:
            out += f" A task is open for {task['owner'] or 'nobody: no adviser is on record'}."
        return out + " Nothing was sent to the client."

    async def record_kyc_step(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import kyc_registry

        try:
            result = await kyc_registry.record_step(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                    **_terms(inputs, STEP_TERMS))
        except kyc_registry.RegistryError as exc:
            raise _error(exc)
        return {**result, "message": "Recorded. The registry status is unchanged: only a newer answer from the registry changes it."}

    async def record_kyc_contact(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import kyc_registry

        try:
            result = await kyc_registry.record_contact(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                       **_terms(inputs, CONTACT_TERMS))
        except kyc_registry.RegistryError as exc:
            raise _error(exc)
        return {**result, "message": "Recorded the attempt. Nothing was sent to the client."}

    async def _kyc_registry_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, kyc_registry as k

        firm = await self._firm()
        try:
            if tool_key == "record_kyc_lookup":
                p = await k.prepare_lookup(self.session, firm, self.role, **_terms(inputs, LOOKUP_TERMS))
                before = p["before"]
                t = p["task"]
                return [("Party", p["name"]), ("Registry", k.REGISTRIES[p["registry"]]), ("Agency", p["agency"]),
                        ("Status", k.STATUSES[p["status"]]), ("Was", before["label"] if before else k.LABELS[k.NOT_LOOKED_UP]),
                        ("Answered", firm_time.spoken(p["answered_on"])),
                        ("Deficiencies", "; ".join(k.DEFICIENCIES[c][0] for c in p["deficiencies"]) or None),
                        ("Registry's words", p["deficiency_note"]),
                        ("Registry holds", ", ".join(k.HOLDS[h] for h in p["holds"]) or None), ("Reference", p["reference"]),
                        ("Evidence", p["document"]),
                        ("Holds", "Activation and the household's orders" if p["status"] in k.STOPS else None),
                        ("Task", (("Already open" if t["exists"] else f"For {t['owner'].full_name}" if t["owner"] else
                                   "Assigned to nobody: no adviser on record")) if t else None),
                        ("Client", "Nothing is sent")]
            if tool_key == "lookup_kyc_registry":
                p = await k.prepare_provider_lookup(self.session, firm, self.role, **_terms(inputs, PARTY_TERMS))
                simulated = bool(getattr(p["provider"], "simulated", False))
                return [("Party", p["name"]), ("Lookup", p["provider"].name), ("Simulated", "Yes: an invented answer, not a KRA check" if simulated else None),
                        ("Records", "The answer, as the registry status that stands"),
                        ("If on hold or rejected", "Activation and orders are held, and a task is raised"), ("Client", "Nothing is sent")]
            if tool_key == "record_kyc_step":
                p = await k.prepare_step(self.session, firm, self.role, **_terms(inputs, STEP_TERMS))
                return [("Party", p["lookup"].party_name), ("Step", p["step"]["action"]), ("From", p["step"]["state_label"]),
                        ("To", k.STEP_STATES[p["state"]]), ("On", firm_time.spoken(p["on"])), ("Note", p["note"]),
                        ("Registry status", f"Stays {k.STATUSES[p['lookup'].status].lower()} until a newer answer")]
            if tool_key == "record_kyc_contact":
                p = await k.prepare_contact(self.session, firm, self.role, **_terms(inputs, CONTACT_TERMS))
                return [("Party", p["name"]), ("Channel", k.CHANNELS[p["channel"]]), ("Outcome", k.OUTCOMES[p["outcome"]]),
                        ("On", firm_time.spoken(p["on"])), ("Note", p["note"]), ("Records", "An attempt made; nothing is sent")]
        except k.RegistryError as exc:
            raise _error(exc)
        return []
