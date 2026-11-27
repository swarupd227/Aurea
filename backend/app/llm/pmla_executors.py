"""The conversation's tools for the PMLA programme (B-390): a politically exposed person's senior approval, a screening
list change and its delta rescreen, a party's father's name, and a report to FIU-IND.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm
card in ``preview_change``. Every card is the module's ``prepare``: every check the write makes, nothing written.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

PMLA_TOOLS = ("record_pep_approval", "record_screening_list_update", "run_delta_rescreen", "record_party_father_name",
              "record_fiu_report")


def _uuid(inputs: dict, key: str):
    from app.llm.executors import _parse_uuid

    return _parse_uuid(inputs[key], key) if inputs.get(key) else None


def _pep_terms(inputs: dict) -> dict:
    return {"case_id": _uuid(inputs, "case_id"), "holder_id": _uuid(inputs, "client_id"), "decision": inputs.get("decision"),
            "approver": inputs.get("approver"), "reason": inputs.get("reason"), "evidence": inputs.get("evidence"),
            "source_of_wealth": inputs.get("source_of_wealth")}


def _list_terms(inputs: dict) -> dict:
    # A change recorded from the conversation is never marked as demonstration data: only the seed writes that.
    return {"list_key": inputs.get("list_key"), "version": inputs.get("version"), "published_on": inputs.get("published_on"),
            "entries_added": inputs.get("entries"), "removed": inputs.get("removed"), "source_document": inputs.get("source_document")}


class PmlaExecutors:
    """Needs the host's session, firm_id, role, _firm(), _shelf_user() and _exit_day()."""

    def _pmla_map(self) -> dict:
        return {k: getattr(self, k) for k in PMLA_TOOLS}

    def _fiu_terms(self, inputs: dict[str, Any]) -> dict:
        return {"kind": inputs.get("kind"), "period": inputs.get("period"), "reference": inputs.get("reference"),
                "filed_on": self._exit_day(inputs.get("filed_on"), "filed_on"), "transactions": inputs.get("transactions"),
                "note": inputs.get("note")}

    async def record_pep_approval(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import aml_review, pep
        from app.llm.executors import ExecutorError

        try:
            done = await pep.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_pep_terms(inputs))
        except pep.PepError as exc:
            raise ExecutorError(str(exc))
        result = (await aml_review.for_case(self.session, done["case"].id, firm_id=self.firm_id) if done["case"]
                  else await aml_review.for_firm(self.session, self.firm_id))
        result["message"] = (f"Recorded: senior approval {'given' if done['decision'] == 'approve' else 'declined'} by {done['approver']} "
                             f"for {', '.join(done['names'])}.")
        return result

    async def record_screening_list_update(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import aml_review, screening_lists
        from app.llm.executors import ExecutorError

        try:
            done = await screening_lists.record_update(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                       **_list_terms(inputs))
        except screening_lists.ListError as exc:
            raise ExecutorError(str(exc))
        result = await aml_review.for_firm(self.session, self.firm_id)
        result["rescreen"] = done
        result["message"] = _rescreen_said("Recorded the list change. ", done)
        return result

    async def run_delta_rescreen(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import aml_review, screening_lists
        from app.llm.executors import ExecutorError

        firm = await self._firm()
        try:
            await screening_lists.prepare_rescreen(self.session, firm, self.role)
        except screening_lists.ListError as exc:
            raise ExecutorError(str(exc))
        done = await screening_lists.rescreen(self.session, firm, by=await self._shelf_user())
        result = await aml_review.for_firm(self.session, self.firm_id)
        result["rescreen"] = done
        result["message"] = _rescreen_said("", done)
        return result

    async def record_party_father_name(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import aml_review, screening_lists
        from app.llm.executors import ExecutorError

        try:
            done = await screening_lists.record_father(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                       party_id=_uuid(inputs, "party_id"), father_name=inputs.get("father_name"),
                                                       document=inputs.get("document"))
        except screening_lists.ListError as exc:
            raise ExecutorError(str(exc))
        result = await aml_review.for_case(self.session, done["case"].id, firm_id=self.firm_id)
        result["message"] = (f"Recorded the father's name of {done['party'].legal_name} and screened them again. "
                             "Any hit is still a person's to decide.")
        return result

    async def record_fiu_report(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import fiu_reports, txn_monitoring
        from app.llm.executors import ExecutorError

        try:
            done = await fiu_reports.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **self._fiu_terms(inputs))
        except fiu_reports.FiuError as exc:
            raise ExecutorError(str(exc))
        result = await txn_monitoring.for_firm(self.session, self.firm_id)
        result["message"] = (f"Recorded the {done['label']} for {done['period']} as filed on {done['filed_on'].isoformat()}, "
                             f"reference {done['reference']}. Nothing was filed from here.")
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _pmla_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import fiu_reports, firm_time, pep, screening_lists
        from app.llm.executors import ExecutorError

        firm = await self._firm()
        try:
            if tool_key == "record_pep_approval":
                p = await pep.prepare(self.session, firm, self.role, **_pep_terms(inputs))
                entry = p["entry"]
                opens = (("The account may open once every other gate passes" if p["decision"] == "approve" else "The account does not open")
                         if p["case"] and not p["case"].activated_at else "Recorded on the file; the open account is not changed")
                return [("Relationship", entry["case"]), ("Politically exposed", "; ".join(p["basis"])),
                        ("Risk rating", (entry["risk_tier"] or "not rated").capitalize() if p["case"] else None),
                        ("Source of wealth", p["source_of_wealth"]), ("Decision", p["decision"].capitalize()),
                        ("Senior approver", p["approver"]), ("Reason", p["reason"]), ("Filed at", p["evidence"]),
                        ("On", firm_time.spoken(p["today"])), ("Effect", opens)]
            if tool_key == "record_screening_list_update":
                p = await screening_lists.prepare_update(self.session, firm, self.role, **_list_terms(inputs))
                return [("List", p["label"]), ("Change", p["version"]), ("Changed on", firm_time.spoken(p["published_on"])),
                        ("Added", "; ".join(e["name"] + (f" (father {e['father_name']})" if e["father_name"] else "") for e in p["entries"]) or None),
                        ("Removed", "; ".join(p["removed"]) or None), ("Parties rescreened", p["parties"]),
                        ("Possible matches with the added names", "; ".join(p["matches"]) or "None"),
                        ("Filed at", p["source_document"]), ("Effect", "Rescreens the book; every hit waits for a person's decision")]
            if tool_key == "run_delta_rescreen":
                w = await screening_lists.prepare_rescreen(self.session, firm, self.role)
                return [("Parties waiting", w["count"]), ("Oldest change", f"{w['oldest_age_days']} day(s) ago"),
                        ("Who", "; ".join(i["party"] for i in w["items"][:12]) + (f" and {w['count'] - 12} more" if w["count"] > 12 else "")),
                        ("Effect", "Screens them against the lists as recorded; decisions already made are kept")]
            if tool_key == "record_party_father_name":
                p = await screening_lists.prepare_father(self.session, firm, self.role, party_id=_uuid(inputs, "party_id"),
                                                         father_name=inputs.get("father_name"), document=inputs.get("document"))
                return [("Case", p["case"].prospect_name), ("Party", p["party"].legal_name), ("Father's name, was", p["party"].father_name),
                        ("Father's name", p["father_name"]), ("Document", p["document"]),
                        ("Effect", "Screens the party again; a differing father's name clears no hit")]
            p = await fiu_reports.prepare(self.session, firm, self.role, **self._fiu_terms(inputs))
            return [("Report", p["label"]), ("Month", p["period"]), ("Due", firm_time.spoken(p["due_on"]) + " (assumed day, not reviewed)"),
                    ("Filed on", firm_time.spoken(p["filed_on"])), ("Reference", p["reference"]),
                    ("Transactions reported", p["transactions"]), ("Principal Officer", p["principal_officer"]), ("Note", p["note"]),
                    ("Effect", "Records what was filed; files nothing, and cannot be edited")]
        except (pep.PepError, screening_lists.ListError, fiu_reports.FiuError) as exc:
            raise ExecutorError(str(exc))


def _rescreen_said(lead: str, done: dict) -> str:
    if not done["rescreened"]:
        return lead + "No party was waiting to be rescreened."
    found = "; ".join(f"{h['party']}: {h['hit'].replace(':', ' ')}" for h in done["new_hits"][:8])
    return (lead + f"Rescreened {done['rescreened']} part(ies); {len(done['new_hits'])} new possible match(es)"
            + (f": {found}" if found else "") + ". Each waits for a person's decision.")
