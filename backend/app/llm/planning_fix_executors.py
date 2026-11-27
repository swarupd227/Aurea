"""The conversation's tool for what the Indian regime comparison reads about a person (B-393).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers the tool in its executor map and its Confirm card in
``preview_change``. The card is the module's ``prepare``: every check the write makes, nothing written.
"""
from __future__ import annotations

from typing import Any

TAX_INPUT_TERMS = ("person_id", "document", "salary_income", "old_regime_deductions", "regime", "marginal_rate", "basic_salary",
                   "business_income")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


class PlanningFixExecutors:
    """Needs the host's session, firm_id, role, _firm(), _shelf_user() and read_tax_position()."""

    async def _tax_inputs_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import markets, salary_tax

        try:
            p = await salary_tax.prepare(self.session, await self._firm(), self.role, **{k: inputs.get(k) for k in TAX_INPUT_TERMS})
        except salary_tax.TaxInputError as exc:
            raise _error(exc)
        income = p["annual_income"]
        return [("Taxpayer", p["person"].full_name),
                *[(c["label"], f"{salary_tax.shown(c['field'], c['before'])} -> {salary_tax.shown(c['field'], c['after'])}") for c in p["changes"]],
                ("Annual income on file", markets.money(float(income), "INR") if income is not None
                 else "Not on file: the comparison needs it"),
                ("Document", p["document"]), ("Effect", "The regime comparison and the tax estimate read these figures")]

    async def record_tax_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import salary_tax

        try:
            done = await salary_tax.record(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                           **{k: inputs.get(k) for k in TAX_INPUT_TERMS})
        except salary_tax.TaxInputError as exc:
            raise _error(exc)
        result = await self.read_tax_position({"household_id": done["household_id"]})
        mine = next((t for t in result.get("taxpayers", []) if t.get("id") == done["person_id"]), None)
        regime = ((mine or {}).get("engine") or {}).get("regime") or {}
        needs = "; ".join(regime.get("needs") or [])
        said = (f" The comparison now picks the {regime['cheaper']} regime." if regime.get("conclusive")
                else f" The comparison still needs: {needs}." if needs
                else "" if mine else " This person holds no taxable account here, so the tax position lists no block for them.")
        result["recorded"] = done
        result["message"] = f"Recorded for {done['person']}.{said} Estimates for a qualified person to check."
        return result
