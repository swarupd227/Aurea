"""The conversation's tools for the advice file (B-175): read it, put a recommendation on it, record what became of one.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card
in ``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.models.enums import UserRole

# Who records: those who advise. Who reads: those who advise or supervise, and the portfolio team. Never a client.
ADVICE_RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
ADVICE_READERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE, UserRole.BRANCH_LEADER})
ADVICE_TOOLS = ("read_advice_file", "record_recommendation", "record_advice_outcome")
_RECOMMENDATION_TERMS = ("household_id", "category", "recommendation", "facts", "alternatives", "decision", "evidence_kind", "evidence",
                         "decided_on", "owner", "due_on", "revisit_on", "person_id", "recommended_on", "data_as_of")
_OUTCOME_TERMS = ("recommendation_id", "status", "evidence_kind", "evidence", "happened_on", "revisit_on")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


class AdviceFileExecutors:
    """Needs the host's session, firm_id, user_id, role and _shelf_user()."""

    def _advice_file_map(self) -> dict:
        return {"read_advice_file": self.read_advice_file, "record_recommendation": self.record_recommendation,
                "record_advice_outcome": self.record_advice_outcome}

    def _advice_may(self, roles: frozenset, words: str) -> None:
        # The gateway checks the catalogue's roles; this re-checks, and aurea_core.advice_file checks again.
        if self.role not in roles:
            raise _error(Exception(words))

    async def read_advice_file(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import advice_file

        self._advice_may(ADVICE_READERS, "The advice file is read by advisers, paraplanners, the portfolio team, compliance and branch leaders.")
        try:
            if inputs.get("household_id"):
                try:
                    household_id = uuid.UUID(str(inputs["household_id"]))
                except (TypeError, ValueError):
                    raise _error(Exception("household_id must be an id from a read tool."))
                result = await advice_file.for_household(self.session, household_id, firm_id=self.firm_id)
                if result is None:
                    raise _error(Exception(f"Household {household_id} not found or access denied"))
                o = result["implementation"]["overall"]
                result["message"] = (f"{result['household']}: {o['made']} recommendation(s) on file, {o['implemented']} implemented, "
                                     f"{o['accepted']} accepted, {o['declined']} declined, {o['open']} open; {result['overdue']} overdue, "
                                     f"{result['revisit_due']} due a revisit. {advice_file.NOT_ON_FILE}")
                return result
            result = await advice_file.for_firm(self.session, self.firm_id, since=inputs.get("since"), until=inputs.get("until"),
                                                category=inputs.get("category"))
        except advice_file.AdviceError as exc:
            raise _error(exc)
        imp = result["implementation"]
        o = imp["overall"]
        rate = f"{o['rate'] * 100:.0f}%" if o["rate"] is not None else "no rate (none made)"
        result["message"] = (f"{imp['since']} to {imp['until']}: {o['made']} made, {o['implemented']} implemented ({rate}), {o['accepted']} accepted, "
                             f"{o['declined']} declined, {o['open']} open; {result['summary']['overdue']} overdue. {advice_file.NOT_ON_FILE}")
        return result

    async def record_recommendation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import advice_file

        self._advice_may(ADVICE_RECORDERS, advice_file.REFUSED_ROLE)
        try:
            row = await advice_file.record_recommendation(self.session, self.firm_id, recorded_by=await self._shelf_user(), role=str(self.role),
                                                          by_user=self.user_id, **{k: inputs.get(k) for k in _RECOMMENDATION_TERMS})
        except advice_file.AdviceError as exc:
            raise _error(exc)
        row["message"] = (f"{row['household']}: {advice_file.CATEGORY_TEXT[row['category']].lower()} recommendation on file, "
                          f"{row['status']}, owned by {row['owner'] or 'nobody yet'}, against assumption version {row['assumption_version']}. "
                          "Nothing was sent to the client.")
        return row

    async def record_advice_outcome(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import advice_file

        self._advice_may(ADVICE_RECORDERS, advice_file.REFUSED_ROLE)
        try:
            row = await advice_file.record_outcome(self.session, self.firm_id, recorded_by=await self._shelf_user(), role=str(self.role),
                                                   **{k: inputs.get(k) for k in _OUTCOME_TERMS})
        except advice_file.AdviceError as exc:
            raise _error(exc)
        was = row["history"][-2]["status"]
        row["message"] = (f"{row['household']}: now {row['status']} (was {was}), from a {row['evidence_kind']}. "
                          "The history is kept; nothing on any account changed and nothing was sent.")
        return row

    async def _advice_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What the Confirm card names, refused for the reasons recording it would be refused."""
        from app.aurea_core import advice_file, firm_time

        self._advice_may(ADVICE_RECORDERS, advice_file.REFUSED_ROLE)
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        try:
            if tool_key == "record_recommendation":
                r = await advice_file.prepare_recommendation(self.session, self.firm_id, role=str(self.role), by_user=self.user_id,
                                                             **{k: inputs.get(k) for k in _RECOMMENDATION_TERMS})
                return [("Client", r["household"].name + (f" · {r['person'].full_name}" if r["person"] else "")),
                        ("Type", advice_file.CATEGORY_TEXT[r["category"]]), ("Recommended", r["recommendation"]),
                        ("Rested on", f"{r['facts']} (as of {spoken(r['data_as_of'])})"),
                        ("Assumption version", r["assumption_version"]),
                        ("Alternatives", "; ".join(f"{a['option']}: rejected, {a['why_rejected']}" for a in r["alternatives"])),
                        ("Client's decision", advice_file.STATUS_TEXT[r["status"]]
                         + (f", {spoken(r['decided_on'])}, from a {r['evidence_kind']}: {r['evidence']}" if r["status"] != "open" else "")),
                        ("Made", spoken(r["recommended_on"])), ("Owner", r["owner"].full_name if r["owner"] else "Nobody: no adviser on record"),
                        ("Due", spoken(r["due_on"])), ("Revisit", spoken(r["revisit_on"])),
                        ("Changes", "Adds to the advice file; edits nothing and sends nothing to the client")]
            r = await advice_file.prepare_outcome(self.session, self.firm_id, role=str(self.role), **{k: inputs.get(k) for k in _OUTCOME_TERMS})
        except advice_file.AdviceError as exc:
            raise _error(exc)
        rec = r["recommendation"]
        return [("Client", r["household"].name if r["household"] else None),
                ("Recommendation", f"{advice_file.CATEGORY_TEXT[rec.category]}: {rec.recommendation}"),
                ("Now", f"{advice_file.STATUS_TEXT[r['status']]}, {spoken(r['happened_on'])}"),
                ("Was", f"{advice_file.STATUS_TEXT[r['was'].status]}, {spoken(r['was'].happened_on)}"),
                ("Evidence", f"{r['evidence_kind'].capitalize()}: {r['evidence']}"), ("Revisit", spoken(r["revisit_on"])),
                ("Changes", "Supersedes where it stood; the history is kept and nothing is sent")]
