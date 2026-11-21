"""The conversation's tools for the estate gaps (B-185) and how care will be paid (B-186).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each card in
``preview_change``. Every Confirm card is the module's ``prepare_*``: every check the write makes, nothing written.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.models.enums import UserRole

# Who raises the estate review: those who record the client file. Who records care funding: those who advise.
ESTATE_REVIEW_ROLES = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
CARE_FUNDING_ROLES = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
ESTATE_CARE_TOOLS = ("raise_estate_review", "record_care_funding")


def _uuid(value: Any, name: str) -> uuid.UUID:
    from app.llm.executors import ExecutorError

    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise ExecutorError(f"{name} must be an id from a read tool.")


class EstateCareExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _estate_care_map(self) -> dict:
        return {"raise_estate_review": self.raise_estate_review, "record_care_funding": self.record_care_funding}

    def _care_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"household_id": _uuid(inputs.get("household_id"), "household_id"), "choice": inputs.get("choice"),
                "reason": inputs.get("reason"), "earmark_amount": inputs.get("earmark_amount")}

    def _may(self, roles: frozenset, words: str) -> None:
        from app.llm.executors import ExecutorError

        # The gateway checks the catalogue's roles; this re-checks, as for the other record-keeping tools.
        if self.role not in roles:
            raise ExecutorError(words)

    async def raise_estate_review(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import client_file
        from app.llm.executors import ExecutorError
        from app.models.identity import User

        self._may(ESTATE_REVIEW_ROLES, "Raising an estate review is for advisers, paraplanners and operations.")
        household_id = _uuid(inputs.get("household_id"), "household_id")
        try:
            task = await client_file.raise_estate_review(self.session, household_id, firm_id=self.firm_id,
                                                         by=await self._shelf_user(), by_user=self.user_id)
        except client_file.ClientFileError as exc:
            raise ExecutorError(str(exc))
        result = await client_file.for_household(self.session, household_id, firm_id=self.firm_id)
        adviser = await self.session.get(User, task.assigned_to) if task.assigned_to else None
        result["task"] = {"id": str(task.id), "title": task.title, "due_date": task.due_date.isoformat() if task.due_date else None,
                          "assigned_to": str(task.assigned_to) if task.assigned_to else None,
                          "assigned_to_name": adviser.full_name if adviser else None}
        result["message"] = (f"Raised for {adviser.full_name}, the recorded adviser." if adviser else
                             "Raised, unassigned: no adviser is on record for this household.") + \
            " Nothing on any account changed and nothing was sent. " + client_file.NOT_ON_FILE
        return result

    async def record_care_funding(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets, protection
        from app.llm.executors import ExecutorError

        self._may(CARE_FUNDING_ROLES, "How a household pays for care is recorded by its adviser or paraplanner.")
        try:
            result = await protection.record_care_funding(self.session, self.firm_id, recorded_by=await self._shelf_user(),
                                                          role=str(self.role), **self._care_args(inputs))
        except protection.ProtectionError as exc:
            raise ExecutorError(str(exc))
        say = markets.money_for(await self._firm())
        choice = protection.CARE_FUNDING_TEXT[result["choice"]]
        result["message"] = (f"{result['household']}: care is to be paid by {choice.lower()}"
                             + (f", with {say(result['earmark_amount'])} earmarked" if result["earmark_amount"] else "") + "."
                             + (f" It supersedes {protection.CARE_FUNDING_TEXT[result['superseded']].lower()}." if result["superseded"] else ""))
        return result

    async def _estate_care_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What the Confirm card names, refused for the reasons doing it would be refused."""
        from app.aurea_core import client_file, firm_time, markets, protection
        from app.llm.executors import ExecutorError

        if tool_key == "raise_estate_review":
            self._may(ESTATE_REVIEW_ROLES, "Raising an estate review is for advisers, paraplanners and operations.")
            try:
                r = await client_file.prepare_estate_review(self.session, _uuid(inputs.get("household_id"), "household_id"), firm_id=self.firm_id)
            except client_file.ClientFileError as exc:
                raise ExecutorError(str(exc))
            g = r["guardianship"]
            return [("Client", r["household"].name),
                    ("Goes to", r["adviser"].full_name if r["adviser"] else "Unassigned: no adviser on record"),
                    ("May be unfunded", ", ".join(f"{t['person']}'s revocable trust" for t in r["unfunded"]) or None),
                    ("Minor, no will on file", ", ".join(f"{m['name']} ({m['age']})" for m in g["minors"]) if g else None),
                    ("Titling fixes", "; ".join(f"{f['account']}: {f['issue']}{', ' + f['name'] if f['name'] else ''}" for f in r["fixes"]) or None),
                    ("Due", firm_time.spoken(r["due_date"])), ("Changes", "Nothing on any account; nothing is sent")]
        self._may(CARE_FUNDING_ROLES, "How a household pays for care is recorded by its adviser or paraplanner.")
        try:
            r = await protection.prepare_care_funding(self.session, self.firm_id, role=str(self.role), **self._care_args(inputs))
        except protection.ProtectionError as exc:
            raise ExecutorError(str(exc))
        say = markets.money_for(await self._firm())
        was = r["previous"]
        return [("Client", r["household"].name), ("Care paid by", protection.CARE_FUNDING_TEXT[r["choice"]]),
                ("Earmarked", say(r["earmark_amount"]) if r["earmark_amount"] else None), ("Reason", r["reason"]),
                ("Supersedes", protection.CARE_FUNDING_TEXT[was["choice"]] if was["choice"] else "No decision on file")]
