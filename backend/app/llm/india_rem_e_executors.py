"""The conversation's tools for India's batch E items: what an agreement carried (B-324), the firm's mutual fund cut-off figures (B-330), counsel's
determination of the family of entities (B-331), and the advertisement approval a promotional report cites (B-332; a message's own is in
``send_client_message``). A client's language or WhatsApp request (B-322) is a button on their page and a task, not a tool.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in ``preview_change``.
Every card is the module's ``prepare``: every check the write makes, nothing written.
"""
from __future__ import annotations

from typing import Any

REM_E_TOOLS = ("read_mf_figures", "record_mf_figure", "read_agreement_contents", "record_agreement_contents", "record_family_determination",
               "cite_report_ad_approval")
FIGURE_TERMS = ("figure_key", "value", "source", "as_of", "reason")
CONTENT_TERMS = ("household_id", "annexure_a", "mitc", "document", "reason")
DETERMINATION_TERMS = ("entity", "determination", "counsel_name", "determined_on", "document")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


def _errors():
    from app.aurea_core import ad_approval, agreement_contents, distribution_separation, mf_figures, reports

    return (agreement_contents.AgreementContentError, mf_figures.FigureError, distribution_separation.SeparationError, ad_approval.AdApprovalError,
            reports.ReportError)


class IndiaRemEExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm(), _user() and _shelf_user()."""

    def _india_rem_e_map(self) -> dict:
        return {k: getattr(self, k) for k in REM_E_TOOLS}

    async def _rem_user(self):
        from app.models.identity import User

        return await self.session.get(User, self.user_id)

    # -- Reads -------------------------------------------------------------------------------------------

    async def read_mf_figures(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import mf_figures

        result = await mf_figures.overview(self.session, await self._firm())
        result["message"] = (f"{result['recorded']} recorded from a circular, {result['assumed']} still assumed. {result['said']}" if result["applies"]
                             else "The mutual fund cut-off figures are for an Indian firm.")
        return result

    async def read_agreement_contents(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import agreement_contents
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        household = await self._own_household(inputs.get("household_id") or "")
        result = await agreement_contents.view(self.session, firm, _parse_uuid(str(household.id), "household_id"))
        result["household"] = household.name
        result["message"] = ("Not recorded: " + " and ".join(result["missing"]) + ". " + result["said"] if result["missing"] else
                             f"Annexure A and the MITC are recorded as carried ({result['source']}). {result['said']}")
        return result

    # -- Writes ------------------------------------------------------------------------------------------

    async def record_mf_figure(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import mf_figures

        try:
            result = await mf_figures.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), actor=await self._rem_user(),
                                             **_terms(inputs, FIGURE_TERMS))
        except mf_figures.FigureError as exc:
            raise _error(exc)
        result["message"] = "Recorded. The NAV-date check now reads it and shows it as recorded; no order, unit or cash changes."
        return result

    async def record_agreement_contents(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import agreement_contents

        try:
            result = await agreement_contents.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, CONTENT_TERMS))
        except agreement_contents.AgreementContentError as exc:
            raise _error(exc)
        result["message"] = "Recorded from the signed document. It does not say the agreement meets any rule."
        return result

    async def record_family_determination(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import distribution_separation as ds

        try:
            result = await ds.record_determination(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, DETERMINATION_TERMS))
        except ds.SeparationError as exc:
            raise _error(exc)
        result["message"] = "Recorded as counsel determined it. It is counsel's, not this platform's, and a flag is never a finding of breach."
        return result

    async def _report_for_citation(self, inputs: dict[str, Any]):
        from app.aurea_core import ad_approval, reports
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not ad_approval.advertising.applies(firm):
            raise _error(ad_approval.AdApprovalError("A report's advertisement approval is cited for an Indian firm."))
        report = await reports.get(self.session, self.firm_id, _parse_uuid(inputs.get("report_id"), "report_id"))
        return firm, report

    async def cite_report_ad_approval(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import ad_approval, reports

        try:
            firm, report = await self._report_for_citation(inputs)
            text = reports.client_text(report)
            ok = await ad_approval.gate(self.session, firm, text, promotional=True, approval_id=inputs.get("ad_approval_id"))
        except _errors() as exc:
            raise _error(exc)
        data = dict(report.data or {})
        data.update(promotional=True, ad_approval_id=str(ok["approval"]["approval_id"]))
        report.data = data
        await self.session.flush()
        return {"report_id": str(report.id), "title": report.title, "approval": ok["approval"], "promotional": True,
                "message": "Recorded. The report may be published only while its text is the approved wording; a changed report needs a new submission."}

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_rem_e_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import ad_approval, agreement_contents, distribution_separation as ds, firm_time, mf_figures, reports

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        yes = lambda v: "Yes" if v else "No"  # noqa: E731
        try:
            if tool_key == "record_mf_figure":
                p = await mf_figures.prepare(self.session, firm, self.role, **_terms(inputs, FIGURE_TERMS))
                return [("Figure", p["label"]), ("Now", p["shown"]), ("Was", f"{p['was']} ({p['was_state']})"), ("Circular", p["source"]), ("Circular dated", spoken(p["as_of"])),
                        ("Reason", p["reason"]),
                        ("Note", "It changes what the NAV-date check reads and nothing else; it is on the audit log with its reason, and whether the circular is read rightly is for counsel")]
            if tool_key == "record_agreement_contents":
                p = await agreement_contents.prepare(self.session, firm, self.role, **_terms(inputs, CONTENT_TERMS))
                b = p["before"]
                return [("Client", p["household"].name), ("Carries Annexure A", yes(p["annexure_a"])), ("Carries the MITC", yes(p["mitc"])),
                        ("Replaces", "Nothing on file" if b is None else f"Annexure A {yes(b.annexure_a)}, MITC {yes(b.mitc)}, which stay on record"),
                        ("Signed document", p["document"]), ("Reason", p["reason"]),
                        ("Note", "As the signed document shows it; the pre-advice gate reads it, and it does not say the agreement meets any rule")]
            if tool_key == "record_family_determination":
                p = await ds.prepare_determination(self.session, firm, self.role, **_terms(inputs, DETERMINATION_TERMS))
                b = p["before"]
                return [("Group entity", p["entity"].name), ("Counsel's determination", ds.DETERMINATIONS[p["determination"]]), ("Counsel", p["counsel"]),
                        ("Determined", spoken(p["determined_on"])),
                        ("Replaces", "Nothing: it reads as awaiting counsel" if b is None else f"{ds.DETERMINATIONS[b.determination]} ({b.counsel_name}, {spoken(b.determined_on)}), which stays on record"),
                        ("Evidence", p["document"]),
                        ("Note", "Counsel's, not the platform's: an entity determined within is flagged, one awaiting counsel is flagged and shown so, one outside is not flagged")]
            if tool_key == "cite_report_ad_approval":
                firm, report = await self._report_for_citation(inputs)
                gate = await ad_approval.gate(self.session, firm, reports.client_text(report), promotional=True, approval_id=inputs.get("ad_approval_id"))
                a = gate["approval"]
                return [("Report", report.title), ("Marked promotional", "Yes"),
                        ("Advertisement approval", f"{a.get('title')}: approved by {a.get('approved_by')} on {a.get('approved_on')}, id {a.get('approval_id')}"),
                        ("Wording", "The report's text is the approved wording"),
                        ("Note", "Publishing the report is still its proposal's approval; this only says which approval stands behind its wording")]
        except _errors() as exc:
            raise _error(exc)
        return []
