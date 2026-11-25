"""The conversation's tools for India row 14: the governance register, the licence map and counsel's review of each law figure.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written. ``ExecutorError`` is
imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

INDIA_GOVERNANCE_TOOLS = ("read_governance", "record_governance_policy", "record_counsel_review")
POLICY_TERMS = ("kind", "version", "owner", "approved_on", "approved_by", "next_review_on", "document", "note")
COUNSEL_TERMS = ("figure_key", "reviewer", "reviewed_on", "outcome", "document", "note")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


class IndiaGovernanceExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_governance_map(self) -> dict:
        return {k: getattr(self, k) for k in INDIA_GOVERNANCE_TOOLS}

    async def read_governance(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import counsel_review, governance

        firm = await self._firm()
        if not governance.applies(firm):
            return {"applies": False, "message": "A governance register and a licence map are kept for a firm holding an Indian licence."}
        if self.role not in governance.READERS:
            raise _error(Exception("The governance register is read by compliance, a branch leader, operations and an administrator."))
        section = str(inputs.get("section") or "").strip().lower()
        if section not in ("", "map", "policies", "counsel"):
            raise _error(Exception("A section is map, policies or counsel."))
        out: dict[str, Any] = {"applies": True}
        parts = []
        if section in ("", "map"):
            out["map"] = await governance.licence_map(self.session, firm)
            parts.append(f"{len(out['map']['held'])} licence(s) held, {out['map']['capped']} of {out['map']['total_agents']} agents capped")
        if section in ("", "policies"):
            out["policies"] = await governance.policies(self.session, firm)
            c = out["policies"]["counts"]
            parts.append(f"{c['current']} polic(ies) current, {c['due_soon']} due soon, {c['overdue']} overdue, {c['not_recorded']} not recorded")
        if section in ("", "counsel"):
            out["counsel"] = await counsel_review.overview(self.session, firm)
            n = out["counsel"]["counts"]
            parts.append(f"{n['not_reviewed']} of {out['counsel']['total']} law figures not reviewed by counsel")
        out["message"] = "; ".join(parts) + ". " + governance.NOT_COMPLIANCE
        return out

    async def record_governance_policy(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import governance

        try:
            result = await governance.record_policy(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, POLICY_TERMS))
        except governance.GovernanceError as exc:
            raise _error(exc)
        result["message"] = "Recorded."
        return result

    async def record_counsel_review(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import counsel_review

        try:
            result = await counsel_review.record(self.session, await self._firm(), self.role, by=await self._shelf_user(), **_terms(inputs, COUNSEL_TERMS))
        except counsel_review.CounselError as exc:
            raise _error(exc)
        result["message"] = "Recorded. The figure itself is unchanged: a correction is a code change."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_governance_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import counsel_review, firm_time, governance

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        try:
            if tool_key == "record_governance_policy":
                p = await governance.prepare_policy(self.session, firm, self.role, **_terms(inputs, POLICY_TERMS))
                return [("Policy", p["label"]), ("Version", p["version"]), ("Owner", p["owner"]), ("Approved", spoken(p["approved_on"])),
                        ("Approved by", p["approved_by"]),
                        ("Next review", f"{spoken(p['next_review_on'])}" + ("" if p["review_set"] else " (a year after approval)")),
                        ("Replaces", f"version {p['before'].version}, approved {p['before'].approved_on.isoformat()}" if p["before"] else None),
                        ("Evidence", p["document"]),
                        ("Note", "Records what the firm says it keeps; it says nothing about whether the firm is compliant")]
            if tool_key == "record_counsel_review":
                p = await counsel_review.prepare(self.session, firm, self.role, **_terms(inputs, COUNSEL_TERMS))
                return [("Figure", p["figure"].label), ("Key", p["key"]), ("Its source", p["figure"].source), ("Reviewer", p["reviewer"]),
                        ("Dated", spoken(p["reviewed_on"])), ("Outcome", counsel_review.OUTCOMES[p["outcome"]]), ("Finding", p["note"]),
                        ("Replaces", f"{p['before'].reviewer}, {p['before'].reviewed_on.isoformat()}, {p['before'].outcome}" if p["before"] else None),
                        ("Evidence", p["document"]),
                        ("Note", "The figure itself is unchanged: a correction is a code change with its citation")]
        except (governance.GovernanceError, counsel_review.CounselError) as exc:
            raise _error(exc)
        return []
