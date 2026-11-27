"""The conversation's tool for a client's own onboarding state (India, B-392).

Mixed into ``ToolExecutors`` (app/llm/executors.py). It reads the signed-in client's household and takes no household id, and it
changes nothing. ``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any


class ClientOnboardingExecutors:
    """Needs the host's session, user_id and _firm()."""

    def _client_onboarding_map(self) -> dict:
        return {"read_my_onboarding_status": self.read_my_onboarding_status}

    async def read_my_onboarding_status(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import client_onboarding, client_view
        from app.models.identity import User

        try:
            household = await client_view.household_of(self.session, await self.session.get(User, self.user_id))
        except client_view.ClientViewError as exc:
            from app.llm.executors import ExecutorError

            raise ExecutorError(str(exc))
        result = await client_onboarding.for_client(self.session, await self._firm(), household)
        if not result["applies"]:
            return {**result, "message": "There is no account opening status to show for this firm."}
        said = "; ".join(f"{a['name']} ({a['account_type']}): {a['step_label'].lower()}"
                         + (f", waiting on: {', '.join(n.lower() for n in a['needs'])}" if a["needs"] else "") for a in result["accounts"])
        result["message"] = (said or "No account is on file as opened or being opened through onboarding") + "."
        return result
