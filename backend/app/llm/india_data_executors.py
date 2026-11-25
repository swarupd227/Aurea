"""The conversation's tools for India rows 9 and 10: the Account Aggregator and e-CAS, the complaints register and the
advertising check.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written. ``ExecutorError``
is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

from app.models.enums import UserRole

INDIA_DATA_TOOLS = ("read_aggregator", "record_aggregator_consent", "revoke_aggregator_consent", "pull_aggregator", "record_ecas",
                    "read_complaints", "record_complaint", "record_complaint_step", "check_advertising")
CONSENT_TERMS = ("household_id", "person_id", "artefact_id", "aggregator", "purpose_code", "fi_types", "fetch_type", "data_from", "data_to",
                 "consent_start", "expires_on", "document")
ECAS_TERMS = ("household_id", "person_id", "kind", "statement_date", "period_from", "document")
COMPLAINT_TERMS = ("complainant", "source", "category", "summary", "received_on", "household_id", "scores_reference", "reference",
                   "document", "about_advice")
STEP_TERMS = ("complaint", "kind", "happened_on", "outcome", "reference", "note")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


class IndiaDataExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_data_map(self) -> dict:
        return {k: getattr(self, k) for k in INDIA_DATA_TOOLS}

    # -- row 9 -------------------------------------------------------------------------------------------

    def _aa_may(self) -> None:
        from app.aurea_core import account_aggregator

        if self.role not in account_aggregator.RECORDERS:
            raise _error(Exception("An Account Aggregator consent or a statement is recorded by an adviser, a paraplanner or operations."))

    async def read_aggregator(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_aggregator

        firm = await self._firm()
        if not account_aggregator.applies(firm):
            return {"applies": False, "consents": [], "statements": [],
                    "message": "Account Aggregator consents and consolidated statements are read for an Indian firm."}
        result = await account_aggregator.overview(self.session, firm)
        c = result["counts"]
        result["message"] = (f"{len(result['consents'])} consent(s): {c['active']} active, {c['expired']} lapsed, {c['revoked']} withdrawn; "
                             f"{len(result['statements'])} statement(s) supplied. Nothing is fetched without an active consent.")
        return result

    async def record_aggregator_consent(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_aggregator

        self._aa_may()
        try:
            result = await account_aggregator.record_consent(self.session, await self._firm(), by=await self._shelf_user(),
                                                             **_terms(inputs, CONSENT_TERMS))
        except account_aggregator.AggregatorError as exc:
            raise _error(exc)
        result["message"] = "Recorded."
        return result

    async def revoke_aggregator_consent(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_aggregator

        self._aa_may()
        try:
            result = await account_aggregator.revoke_consent(self.session, await self._firm(), by=await self._shelf_user(),
                                                             consent_id=inputs.get("consent_id"), reason=inputs.get("reason"),
                                                             document=inputs.get("document"))
        except account_aggregator.AggregatorError as exc:
            raise _error(exc)
        result["message"] = "Withdrawn: nothing is fetched under it from now on."
        return result

    async def pull_aggregator(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_aggregator

        self._aa_may()
        try:
            result = await account_aggregator.pull(self.session, await self._firm(), by=await self._shelf_user())
        except account_aggregator.AggregatorError as exc:
            raise _error(exc)
        result["message"] = (f"{result['added']} line(s) added, {result['superseded']} replaced, {result['ended']} ended, {result['unchanged']} unchanged, "
                             f"under {result['consents_used']} active consent(s).")
        return result

    async def record_ecas(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_aggregator

        self._aa_may()
        try:
            result = await account_aggregator.record_ecas(self.session, await self._firm(), by=await self._shelf_user(),
                                                          lines_text=inputs.get("lines"), **_terms(inputs, ECAS_TERMS))
        except account_aggregator.AggregatorError as exc:
            raise _error(exc)
        held = result.pop("held_away", None) or {}
        result["held_away_value"] = (held.get("summary") or {}).get("value") if isinstance(held, dict) else None
        result["message"] = (f"Recorded {result['added']} new and {result['replaced']} replaced line(s) from the {result['kind_label']}; "
                             f"{len(result['refused'])} refused, {len(result['skipped'])} left to the aggregator.")
        return result

    # -- row 10 ------------------------------------------------------------------------------------------

    async def read_complaints(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import complaints

        firm = await self._firm()
        if not complaints.applies(firm):
            return {"applies": False, "complaints": [], "message": "A complaints register is kept for an Indian firm."}
        if self.role not in complaints.READERS:
            raise _error(Exception("The complaints register is read by compliance, a branch leader, operations and an administrator."))
        result = await complaints.overview(self.session, firm)
        if inputs.get("period"):
            try:
                result["annexure_c"] = await complaints.annexure_c(self.session, firm, str(inputs["period"]))
            except complaints.ComplaintError as exc:
                raise _error(exc)
        c = result["counts"]
        ack = "assumed" if result["clocks"]["ack_assumed"] else "the firm's own"
        result["message"] = (f"{c['total']} complaint(s): {c['overdue']} overdue, {c['at_risk']} at risk, {c['open']} open, "
                             f"{c['resolved']} resolved, {c['withdrawn']} withdrawn; {c['about_advice_open']} about advice still open. "
                             f"The acknowledgement period is {ack}.")
        return result

    async def record_complaint(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import complaints

        try:
            result = await complaints.record_complaint(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                       **_terms(inputs, COMPLAINT_TERMS))
        except complaints.ComplaintError as exc:
            raise _error(exc)
        if self.role not in complaints.READERS:      # who records need not read the register
            return {"applies": True, "recorded": result["recorded"], "complaints": [], "counts": {},
                    "message": f"Recorded as {result['recorded']}. Compliance reads the register."}
        result["message"] = f"Recorded as {result['recorded']}."
        return result

    async def record_complaint_step(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import complaints

        try:
            result = await complaints.record_event(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                   **_terms(inputs, STEP_TERMS))
        except complaints.ComplaintError as exc:
            raise _error(exc)
        if self.role not in complaints.READERS:
            return {"applies": True, "recorded": result["recorded"], "complaints": [], "counts": {}, "message": f"Recorded: {result['recorded']}."}
        result["message"] = f"Recorded: {result['recorded']}."
        return result

    async def check_advertising(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import advertising

        result = advertising.for_firm(await self._firm(), inputs.get("text"), str(inputs.get("kind") or "message"))
        c = result.get("counts") or {}
        result["message"] = (advertising.summary(result) or result.get("note", "")) + ". " + advertising.NOT_EXHAUSTIVE
        result["text_length"] = len(str(inputs.get("text") or ""))
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_data_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import account_aggregator, complaints, firm_time, held_away_feeds, markets

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        try:
            if tool_key == "record_aggregator_consent":
                self._aa_may()
                p = await account_aggregator.prepare_consent(self.session, firm, **_terms(inputs, CONSENT_TERMS))
                return [("Household", p["household"].name), ("Person", p["person"].full_name if p["person"] else "The whole household"),
                        ("Consent artefact", p["artefact_id"]), ("Aggregator", p["aggregator"]),
                        ("Purpose", f"{p['purpose_code']}: {account_aggregator.PURPOSES[p['purpose_code']]}"),
                        ("Covers", ", ".join(account_aggregator.FI_TYPES[k] for k in p["fi_types"])),
                        ("Fetch", account_aggregator.FETCH_TYPES[p["fetch_type"]]),
                        ("Data from", f"{spoken(p['data_from'])} to {spoken(p['data_to'])}"), ("Lapses", spoken(p["expires_on"])),
                        ("Evidence", p["document"]), ("Note", "Nothing is fetched until a pull is asked for, and only under this consent")]
            if tool_key == "revoke_aggregator_consent":
                self._aa_may()
                p = await account_aggregator.prepare_revoke(self.session, firm, consent_id=inputs.get("consent_id"),
                                                            reason=inputs.get("reason"), document=inputs.get("document"))
                c = p["consent"]
                return [("Consent artefact", c.artefact_id), ("Aggregator", c.aggregator), ("Reason", p["reason"]),
                        ("Evidence", p["document"]), ("Note", "Nothing is fetched under it from now on; lines already taken stay on file until the next pull ends them")]
            if tool_key == "pull_aggregator":
                self._aa_may()
                feeds = [f for f in held_away_feeds.for_firm(firm) if getattr(f, "account_aggregator", False)]
                if not feeds:
                    raise account_aggregator.AggregatorError("No Account Aggregator source is connected for this firm.")
                active = await account_aggregator.permitted(self.session, firm)
                return [("Source", ", ".join(f.name for f in feeds)), ("Under", f"{len(active)} active consent(s)"),
                        ("Simulated", "Yes: invented statements, not a real aggregator" if any(f.simulated for f in feeds) else None),
                        ("Note", "A consent that has lapsed or been withdrawn delivers nothing, and its earlier lines end, kept on file")]
            if tool_key == "record_ecas":
                self._aa_may()
                p = await account_aggregator.prepare_ecas(self.session, firm, lines_text=inputs.get("lines"), **_terms(inputs, ECAS_TERMS))
                return [("Household", p["household"].name), ("Statement", account_aggregator.CAS_KINDS[p["kind"]]),
                        ("Dated", spoken(p["statement_date"])), ("Lines read", str(len(p["ready"]))),
                        ("Replaces", f"{sum(1 for r in p['ready'] if r['replaces'])} line(s) on file"),
                        ("Total stated", markets.money(p["total"], firm.base_currency)),
                        ("Refused", "; ".join(f"row {r['row']}: {r['reason']}" for r in p["refused"][:4]) or None),
                        ("Left to the aggregator", str(len(p["skipped"])) if p["skipped"] else None), ("Evidence", p["document"]),
                        ("Note", "Each line is the statement's figure; none is estimated and no PAN is kept")]
            if tool_key == "record_complaint":
                p = await complaints.prepare_complaint(self.session, firm, self.role, **_terms(inputs, COMPLAINT_TERMS))
                return [("Complainant", p["complainant"]), ("Client", p["household"].name if p["household"] else None),
                        ("Came in by", complaints.SOURCES[p["source"]]), ("About", complaints.CATEGORIES[p["category"]]),
                        ("Received", spoken(p["received_on"])), ("Resolve by", f"{spoken(p['due_on'])} ({p['clock']['resolve_days']} days)"),
                        ("SCORES number", p["scores_reference"]), ("Reference", p["reference"]),
                        ("Told to compliance", "Yes: a task, because it is about advice" if p["about_advice"] else None),
                        ("They said", p["summary"][:300])]
            if tool_key == "record_complaint_step":
                p = await complaints.prepare_event(self.session, firm, self.role, **_terms(inputs, STEP_TERMS))
                c = p["complaint"]
                return [("Complaint", f"{c.reference}, {c.complainant}"), ("Step", complaints.EVENTS[p["kind"]]), ("On", spoken(p["happened_on"])),
                        ("Outcome", complaints.OUTCOMES.get(p["outcome"] or "")), ("Reference", p["reference"]),
                        ("Resolved after the limit", "Yes" if p["late"] else None), ("Note", p["note"])]
        except (account_aggregator.AggregatorError, complaints.ComplaintError) as exc:
            raise _error(exc)
        return []

    async def _advertising_rows(self, body: str) -> list[tuple[str, Any]]:
        """What the advertising check found in a message, for its Confirm card; nothing for a firm outside India."""
        from app.aurea_core import advertising

        result = advertising.for_firm(await self._firm(), body, "message")
        counts = result.get("counts") or {}
        line = advertising.summary(result) if any(counts.values()) else None     # a clean message shows nothing extra
        return [("Advertising check", line)] if line else []
