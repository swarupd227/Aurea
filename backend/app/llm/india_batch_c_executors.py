"""The conversation's tools for India's batch C items: a mutual fund order's rail and NAV day (B-294), advice kept apart from distribution (B-297),
and each adviser's certification and the advertisement pre-approval (B-298).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in ``preview_change``.
Every card is the module's ``prepare``: every check the write makes, nothing written. ``ExecutorError`` is imported where it is raised, because
executors.py imports this module.
"""
from __future__ import annotations

from datetime import date
from typing import Any

BATCH_C_TOOLS = ("read_mf_orders", "record_mf_order", "read_distribution_separation", "record_group_entity", "record_group_trail", "record_scheme_ter",
                 "read_adviser_certifications", "record_adviser_certification", "read_ad_approvals", "submit_ad_copy", "decide_ad_copy")
MF_TERMS = ("order_id", "rail", "rta", "cutoff_rule", "order_on", "order_time", "nav_date_reported", "document", "folio", "funds_realised_on", "amount_inr", "reason")
ENTITY_TERMS = ("name", "relationship", "holds_arn", "arn", "document", "ended", "reason")
TRAIL_TERMS = ("household_id", "entity", "statement_on", "document", "amount_inr", "ceased", "reason")
TER_TERMS = ("scheme", "plan", "ter_pct", "as_of", "document")
CERT_TERMS = ("user_id", "kind", "certificate_number", "issued_on", "expires_on", "document", "status")
SUBMIT_TERMS = ("title", "text", "channel", "supersedes_id")
DECIDE_TERMS = ("submission_id", "decision", "reason")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


def _errors():
    from app.aurea_core import ad_approval, adviser_certification, distribution_separation, mf_orders

    return (mf_orders.MfOrderError, distribution_separation.SeparationError, adviser_certification.CertificationError, ad_approval.AdApprovalError)


class IndiaBatchCExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_batch_c_map(self) -> dict:
        return {k: getattr(self, k) for k in BATCH_C_TOOLS}

    async def _user(self):
        from app.models.identity import User

        return await self.session.get(User, self.user_id)

    # -- Reads -------------------------------------------------------------------------------------------

    async def read_mf_orders(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import mf_orders
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        result = await mf_orders.overview(self.session, firm, household_id=_parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None)
        if not result["applies"]:
            result["message"] = "A mutual fund order's rail and NAV day are recorded for an Indian firm."
        else:
            result["message"] = (f"{len(result['orders'])} order(s) recorded, {result['breaks']} whose NAV date differs from the cut-off rule's (reported, nothing repaired), "
                                 f"{result['not_assessed']} not assessed, {result['unrecorded']} mutual fund order(s) with no rail on file. {mf_orders.SAID} "
                                 f"Not modelled: {'; '.join(result['not_modelled'])}.")
        return result

    async def read_distribution_separation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import distribution_separation as ds
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        result = await ds.overview(self.session, firm, household_id=_parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None)
        if result["applies"]:
            c = result["check"]
            result["message"] = (f"{len(result['entities'])} group entit(ies) on file; {len(c['flags'])} flag(s) for compliance, which are flags and not findings of breach, "
                                 f"and {len(c['facts_needed'])} needing a fact first. The comparison is from recorded expense ratios only and recommends no plan. {ds.OUT_OF_SCOPE}")
        else:
            result["message"] = "The firm's group and the direct-against-regular comparison are for an Indian firm."
        return result

    async def read_adviser_certifications(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import adviser_certification

        result = await adviser_certification.overview(self.session, await self._firm())
        if not result["applies"]:
            result["message"] = "Certificates are read for a firm that holds an Indian licence."
        else:
            result["message"] = f"{adviser_certification.NOT_QUALIFIED} Required: {', '.join(result['required']) or 'none under the licences held'}."
        return result

    async def read_ad_approvals(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import ad_approval

        firm = await self._firm()
        if inputs.get("approval_id"):
            result = await ad_approval.verify_citation(self.session, firm, inputs["approval_id"], inputs.get("text"))
            result["message"] = result.get("said") or ""
            return result
        result = await ad_approval.overview(self.session, firm)
        result["message"] = (f"{result.get('pending', 0)} waiting for compliance, {result.get('approved', 0)} approved, {result.get('rejected', 0)} rejected. {result['said']}"
                             if result["applies"] else "Advertisement pre-approval is for a firm that holds an Indian licence.")
        return result

    # -- Writes ------------------------------------------------------------------------------------------

    async def record_mf_order(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import mf_orders

        try:
            result = await mf_orders.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, MF_TERMS))
        except mf_orders.MfOrderError as exc:
            raise _error(exc)
        result["message"] = "Recorded. A difference from the cut-off rule is reported and nothing is repaired; no holding, unit or cash is written."
        return result

    async def record_group_entity(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import distribution_separation as ds

        try:
            result = await ds.record_entity(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, ENTITY_TERMS))
        except ds.SeparationError as exc:
            raise _error(exc)
        result["message"] = "Recorded as the document states it; the ARN is not checked against AMFI."
        return result

    async def record_group_trail(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import distribution_separation as ds

        try:
            result = await ds.record_trail(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, TRAIL_TERMS))
        except ds.SeparationError as exc:
            raise _error(exc)
        result["message"] = "Recorded. A flag for compliance is a flag and not a finding of breach."
        return result

    async def record_scheme_ter(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import distribution_separation as ds

        try:
            result = await ds.record_ter(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, TER_TERMS))
        except ds.SeparationError as exc:
            raise _error(exc)
        result["message"] = "Recorded from the scheme document. The comparison recommends no plan."
        return result

    async def record_adviser_certification(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import adviser_certification

        try:
            result = await adviser_certification.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, CERT_TERMS))
        except adviser_certification.CertificationError as exc:
            raise _error(exc)
        result["message"] = "Recorded. It does not say the person is qualified, and a warning on a proposal never stops an approval."
        return result

    async def submit_ad_copy(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import ad_approval

        try:
            result = await ad_approval.submit(self.session, await self._firm(), await self._user(), **_terms(inputs, SUBMIT_TERMS))
        except ad_approval.AdApprovalError as exc:
            raise _error(exc)
        result["message"] = "Submitted to compliance. Nothing is published or sent, and the text is kept as submitted."
        return result

    async def decide_ad_copy(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import ad_approval

        try:
            result = await ad_approval.decide(self.session, await self._firm(), await self._user(), **_terms(inputs, DECIDE_TERMS))
        except ad_approval.AdApprovalError as exc:
            raise _error(exc)
        result["message"] = "Recorded. An approval is for exactly that wording and does not say the firm is compliant."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_batch_c_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import ad_approval, adviser_certification, distribution_separation as ds, firm_time, markets, mf_orders

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        money = lambda v: markets.money(v, firm.base_currency or "INR") if v is not None else None  # noqa: E731
        try:
            if tool_key == "record_mf_order":
                p = await mf_orders.prepare(self.session, firm, self.role, **_terms(inputs, MF_TERMS))
                r, c = p["record"], p["check"]
                rows: list[tuple[str, Any]] = [
                    ("Order", f"{p['instrument'].symbol}, {c['side']}"), ("Rail", mf_orders.RAILS[r.rail]), ("Registrar", mf_orders.RTAS[r.rta]),
                    ("Folio", r.folio), ("Cut-off rule", mf_orders.RULES[r.cutoff_rule]), ("Order taken", f"{spoken(r.order_on)} at {r.order_time}"),
                    ("Funds realised", spoken(r.funds_realised_on) if r.funds_realised_on else None), ("Amount", money(float(r.amount_inr)) if r.amount_inr is not None else None),
                    ("NAV date reported", spoken(r.nav_date_reported))]
                if c["state"] == "not_assessed":
                    rows.append(("NAV date by the rule", f"Not assessed: needs {c['needs']}"))
                else:
                    rows.append(("NAV date by the rule", spoken(date.fromisoformat(c["expected"]))))
                    rows.append(("Reading", "Matches the rule" if c["state"] == "matches" else f"Differs by {c['gap_days']:+d} day(s): reported as a break, nothing repaired. {c['likely_cause']}"))
                return [*rows, ("Replaces", "Nothing on file" if p["before"] is None else "An earlier record for this order, which stays"), ("Reason", r.reason),
                        ("Evidence", r.source_document), ("Note", "The cut-off times are assumed and not reviewed by counsel; holidays are not modelled. It writes no holding, unit or cash")]
            if tool_key == "record_group_entity":
                p = await ds.prepare_entity(self.session, firm, self.role, **_terms(inputs, ENTITY_TERMS))
                return [("Company", p["name"]), ("Recording", "It has left the group" if p["kind"] == "ended" else "A company in the group"),
                        ("Relationship", ds.RELATIONSHIPS[p["relationship"]]), ("Holds an AMFI ARN", "Yes" if p["holds_arn"] else "No"), ("ARN, as stated", p["arn"]),
                        ("Reason", p["reason"]), ("Evidence", p["document"]), ("Note", "The ARN is not checked against AMFI. Compliance reads the check this feeds; it is not a finding")]
            if tool_key == "record_group_trail":
                p = await ds.prepare_trail(self.session, firm, self.role, **_terms(inputs, TRAIL_TERMS))
                advised = p["household"].id in await ds._advised_households(self.session, firm)
                return [("Household", p["household"].name), ("Group entity", p["entity"].name), ("Holds an ARN", "Yes" if p["entity"].holds_arn else "Not recorded"),
                        ("Recording", "The trail has ceased" if p["kind"] == "ceased" else "The entity earns trail from this household"), ("Statement of", spoken(p["statement_on"])),
                        ("Trail", money(p["amount"]) if p["amount"] is not None else None),
                        ("Household", "Advised by an Investment Adviser: this raises a flag for compliance, not a finding" if advised and p["kind"] == "earns" and p["entity"].holds_arn else None),
                        ("Reason", p["reason"]), ("Evidence", p["document"]), ("Note", "A recorded fact, never worked out from holdings")]
            if tool_key == "record_scheme_ter":
                p = await ds.prepare_ter(self.session, firm, self.role, **_terms(inputs, TER_TERMS))
                return [("Scheme", p["scheme"]), ("Plan", p["plan"].title()), ("Total expense ratio", f"{p['ter']:g}% a year"), ("Document of", spoken(p["as_of"])),
                        ("Replaces", "Nothing on file" if p["before"] is None else f"{float(p['before'].ter_pct):g}% of {spoken(p['before'].as_of)}"),
                        ("Evidence", p["document"]), ("Note", "From the scheme document, never estimated; recommends no plan")]
            if tool_key == "record_adviser_certification":
                p = await adviser_certification.prepare(self.session, firm, self.role, **_terms(inputs, CERT_TERMS))
                return [("Adviser", p["person"].full_name), ("Certificate", adviser_certification.KINDS[p["kind"]]), ("Number", p["number"]), ("Issued", spoken(p["issued_on"])),
                        ("Expires", spoken(p["expires_on"])), ("Status, as reported", p["status"]),
                        ("Reads as", adviser_certification.LABELS[p["state_after"]]),
                        ("Replaces", "Nothing on file" if p["before"] is None else f"Number {p['before'].certificate_number}, expiring {spoken(p['before'].expires_on)}"),
                        ("Evidence", p["document"]), ("Note", "From the certificate; it does not say the person is qualified, and a warning never stops an approval")]
            if tool_key == "submit_ad_copy":
                p = await ad_approval.prepare_submit(self.session, firm, self.role, **_terms(inputs, SUBMIT_TERMS))
                f = p["findings"].get("findings", [])
                rows = [("Title", p["title"]), ("Channel", ad_approval.CHANNELS[p["channel"]]), ("Copy", p["text"][:600] + ("..." if len(p["text"]) > 600 else "")),
                        ("Fingerprint", p["hash"][:12]), ("Follows", p["prior"].title if p["prior"] else None),
                        ("Check", f"{sum(1 for x in f if x['severity'] == 'block')} blocking, {sum(1 for x in f if x['severity'] == 'flag')} flagged"
                                  + (": " + "; ".join(x["label"] for x in f[:4]) if f else ""))]
                return [*rows, ("Note", "Nothing is published or sent. The text is kept as submitted; a change is a new submission")]
            if tool_key == "decide_ad_copy":
                p = await ad_approval.prepare_decision(self.session, firm, await self._user(), **_terms(inputs, DECIDE_TERMS))
                s, f = p["submission"], p["findings"].get("findings", [])
                return [("Piece", s.title), ("Channel", ad_approval.CHANNELS.get(s.channel)), ("Submitted by", s.submitted_by), ("Copy", s.body[:600] + ("..." if len(s.body) > 600 else "")),
                        ("Check", f"{sum(1 for x in f if x['severity'] == 'block')} blocking, {sum(1 for x in f if x['severity'] == 'flag')} flagged"
                                  + (": " + "; ".join(x["label"] for x in f[:4]) if f else "")),
                        ("Decision", "Approve exactly this wording" if p["decision"] == "approved" else "Reject"), ("Reason", p["reason"]),
                        ("Note", "An approval carries an id a message may cite and does not say the firm is compliant; the check's patterns are not exhaustive")]
        except _errors() as exc:
            raise _error(exc)
        return []

