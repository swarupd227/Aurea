"""The conversation's tools for B-301 and B-302: a client's language, WhatsApp, and the DigiLocker and eSign seams.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare``: every check the write makes, nothing written. Sending a message in a
client's language or on WhatsApp is ``send_client_message`` (its own card, in executors.py), which calls ``whatsapp.plan`` here.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

INDIA_BATCH_B_TOOLS = ("read_client_channels", "record_client_language", "record_whatsapp_template", "read_india_stack", "pull_digilocker",
                       "send_for_esign", "record_digilocker_kyc", "record_esigned_agreement")
LANGUAGE_TERMS = ("household_id", "language", "asked_by", "evidence")
TEMPLATE_TERMS = ("name", "language", "body", "provider_ref", "state", "reason")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


class IndiaBatchBExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_batch_b_map(self) -> dict:
        return {k: getattr(self, k) for k in INDIA_BATCH_B_TOOLS}

    # -- Reads -------------------------------------------------------------------------------------------

    async def read_client_channels(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import whatsapp
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        hid = _parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None
        if hid is not None:
            await self._own_household(hid)
        result = await whatsapp.overview(self.session, firm, household_id=hid)
        if not result["applies"]:
            return {**result, "message": "A client's language and WhatsApp are kept for an Indian firm."}
        result["deliveries"] = await whatsapp.deliveries(self.session, self.firm_id, hid, limit=10)
        c = result["counts"]
        result["message"] = (f"{c['with_language']} household(s) with a language recorded, {c['opted_in']} opted in to WhatsApp, {c['window_open']} with the 24-hour "
                             f"window open, {c['templates']} approved template(s). No language on file means English, not that the client reads it. "
                             + ("WhatsApp is served by a simulation, not a real channel. " if (result.get("provider") or {}).get("simulated")
                                else "" if result.get("provider") else "No WhatsApp provider is connected, so nothing can be sent there. ")
                             + (f"Translation: {result['translation']['said']}. " if result["translation"]["said"] else "") + whatsapp.NOT_COMPLIANCE)
        return result

    async def read_india_stack(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_stack
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        hid = _parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None
        if hid is not None:
            await self._own_household(hid)
        result = await india_stack.overview(self.session, firm, household_id=hid)
        if not result["applies"]:
            return {**result, "message": "DigiLocker and eSign are kept for an Indian firm."}
        c = result["counts"]
        result["message"] = (f"{c['documents']} DigiLocker document(s) and {c['envelopes']} eSign envelope(s), {c['open']} waiting for a person to confirm, "
                             f"{c['recorded']} recorded. {result['said']} {india_stack.KRA_CKYC['said']}")
        return result

    # -- Writes ------------------------------------------------------------------------------------------

    async def record_client_language(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import client_language

        try:
            result = await client_language.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, LANGUAGE_TERMS))
        except client_language.LanguageError as exc:
            raise _error(exc)
        result["message"] = "Recorded. The next draft to that client is shown in their language beside the English, and nothing is translated until a person confirms the words."
        return result

    async def record_whatsapp_template(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import whatsapp

        try:
            result = await whatsapp.record_template(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, TEMPLATE_TERMS))
        except whatsapp.WhatsappError as exc:
            raise _error(exc)
        result["message"] = "Recorded. Outside the 24-hour window only an approved template may go to a client on WhatsApp; the latest for a name and language stands."
        return result

    async def pull_digilocker(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_stack

        try:
            result = await india_stack.pull_digilocker(self.session, await self._firm(), self.role, person_id=inputs.get("person_id"), by=await self._shelf_user())
        except india_stack.StackError as exc:
            raise _error(exc)
        result["message"] = (f"{result['added']} document reference(s) came back. Nothing is in the KYC yet: a person confirms each into it (record_digilocker_kyc). "
                             + india_stack.CAVEAT)
        return result

    async def send_for_esign(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_stack

        try:
            result = await india_stack.send_for_esign(self.session, await self._firm(), self.role, person_id=inputs.get("person_id"),
                                                      document_name=inputs.get("document_name"), by=await self._shelf_user())
        except india_stack.StackError as exc:
            raise _error(exc)
        result["message"] = "Sent. No agreement is on file until a person confirms a signed envelope into the consent registry (record_esigned_agreement)."
        return result

    async def record_digilocker_kyc(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_stack

        try:
            result = await india_stack.record_kyc(self.session, await self._firm(), self.role, by=await self._shelf_user(), item_id=inputs.get("item_id"),
                                                  document=inputs.get("document"))
        except india_stack.StackError as exc:
            raise _error(exc)
        result["message"] = "Recorded in the KYC with the mode digilocker. The party's state is worked out from it, and no KRA or CKYC lookup was made."
        return result

    async def record_esigned_agreement(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_stack

        try:
            result = await india_stack.record_agreement(self.session, await self._firm(), self.role, by=await self._shelf_user(), item_id=inputs.get("item_id"))
        except india_stack.StackError as exc:
            raise _error(exc)
        result["message"] = "Recorded as the advisory agreement, signed by e-signature, with the envelope as its evidence. The pre-advice gate reads it."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_batch_b_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import client_language, firm_time, india_stack, whatsapp

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        try:
            if tool_key == "record_client_language":
                p = await client_language.prepare(self.session, firm, self.role, **_terms(inputs, LANGUAGE_TERMS))
                was = p["before"]
                return [("Household", p["household"].name), ("Language", f"{client_language.label(p['language'])} ({client_language.native(p['language'])})"),
                        ("Replaces", client_language.label(was.language) if was else "Nothing on file (English)"), ("Asked by", p["asked_by"]),
                        ("Evidence", p["evidence"]),
                        ("Effect", "Drafts to this client show their language beside the English; each is the person's words once confirmed, and the client's own words are never translated here")]
            if tool_key == "record_whatsapp_template":
                p = await whatsapp.prepare_template(self.session, firm, self.role, **_terms(inputs, TEMPLATE_TERMS))
                before = p["before"]
                return [("Template", p["name"]), ("Language", client_language.label(p["language"])),
                        ("Recording", "A withdrawal" if p["state"] == "withdrawn" else "An approved template"), ("Exact wording", p["body"]),
                        ("Provider approval reference", p["provider_ref"]), ("Replaces", before.body if before else "Nothing on file"), ("Reason", p["reason"]),
                        ("Effect", "Outside the 24-hour window this is the only text that may go to a client on WhatsApp; {name} becomes the household's name"
                         if p["state"] == "approved" else "It can no longer be sent"),
                        ("Note", whatsapp.NOT_COMPLIANCE)]
            if tool_key == "pull_digilocker":
                person, household = await india_stack._party(self.session, firm, self.role, inputs.get("person_id"))
                provider = india_stack.provider_for(firm)
                if provider is None:
                    raise india_stack.StackError(india_stack.NOT_CONNECTED)
                return [("Client", f"{person.full_name}, {household.name}"), ("Asks", "The client's documents in DigiLocker, which the client authorises there"),
                        ("Provider", provider.name + (" (simulated)" if getattr(provider, "simulated", False) else "")),
                        ("Effect", "Document references come back for a person to confirm; nothing is recorded in the KYC by this"),
                        ("Note", "An Aadhaar number is never kept: only its last four, masked. " + india_stack.KRA_CKYC["said"]) ]
            if tool_key == "send_for_esign":
                person, household = await india_stack._party(self.session, firm, self.role, inputs.get("person_id"))
                provider = india_stack.provider_for(firm)
                if provider is None:
                    raise india_stack.StackError(india_stack.NOT_CONNECTED)
                return [("Signer", f"{person.full_name}, {household.name}"), ("Document", india_stack.clean_document(inputs.get("document_name"))),
                        ("Provider", provider.name + (" (simulated)" if getattr(provider, "simulated", False) else "")),
                        ("Effect", "An envelope is sent for the client's eSign; the agreement is on file only when a person confirms a signed envelope")]
            if tool_key == "record_digilocker_kyc":
                p = await india_stack.prepare_kyc(self.session, firm, self.role, item_id=inputs.get("item_id"), document=inputs.get("document"))
                item, k = p["item"], p["kyc"]
                rows: list[tuple[str, Any]] = [("Client", k["name"]), ("Document", f"{p['label']} from {item.issuer or 'the issuer'}, {item.masked_number or 'no number'}"),
                                               ("Fetched", spoken(item.fetched_on)), ("Reference", item.reference),
                                               ("Source", "Simulated DigiLocker (demonstration)" if item.source_system else "DigiLocker")]
                for c in k["changes"]:
                    rows.append((c["label"], f"{'not on file' if c['before'] is None else c['before']} -> {c['after']}"))
                rows.append(("Note", "Recorded from the document reference with the mode digilocker; no KRA or CKYC lookup is made, and an Aadhaar number is never kept. "
                                     + india_stack.CAVEAT))
                return rows
            if tool_key == "record_esigned_agreement":
                p = await india_stack.prepare_agreement(self.session, firm, self.role, item_id=inputs.get("item_id"))
                item, c = p["item"], p["consent"]
                return [("Client", c["whose"]), ("Document", item.document_name), ("Signed by", c["given_by"]), ("Signed on", spoken(c["given_on"])),
                        ("Envelope", item.reference), ("Source", "Simulated eSign (demonstration)" if item.source_system else item.provider),
                        ("Records", "The advisory agreement, by e-signature, with the envelope as evidence"),
                        ("Note", "It does not say the agreement carries Annexure A and the MITC: the person checks the document. " + india_stack.CAVEAT)]
        except (client_language.LanguageError, whatsapp.WhatsappError, india_stack.StackError) as exc:
            raise _error(exc)
        return []
