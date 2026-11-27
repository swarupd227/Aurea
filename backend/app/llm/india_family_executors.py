"""The conversation's tools for India row 13: SEBI's family of client, and the family's estate records (nominations, a will's state,
an HUF, gratuity).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written. ``ExecutorError`` is
imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

INDIA_FAMILY_TOOLS = ("read_sebi_family", "record_sebi_family", "read_india_estate", "record_asset_nomination", "record_will_status",
                      "record_huf", "record_gratuity")
FAMILY_TERMS = ("person_id", "of_person_id", "relation", "dependent", "document")
NOMINATION_TERMS = ("person_id", "asset_kind", "label", "status", "nominees", "declaration_on", "document")
WILL_TERMS = ("person_id", "personal_law", "registered", "registration_ref", "probate", "probate_ref", "executor", "as_of", "document",
              "witnesses", "executor_told", "executor_told_on")
HUF_TERMS = ("entity_id", "karta_name", "karta_person_id", "coparceners", "as_of", "document")
GRATUITY_TERMS = ("person_id", "employer", "amount", "as_of", "payable_on", "years_of_service", "document")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: inputs.get(k) for k in keys if inputs.get(k) not in (None, "")}


class IndiaFamilyExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_family_map(self) -> dict:
        return {k: getattr(self, k) for k in INDIA_FAMILY_TOOLS}

    def _estate_may(self) -> None:
        from app.aurea_core import india_estate

        if self.role not in india_estate.RECORDERS:
            raise _error(Exception("A family or estate record is made by an adviser, a paraplanner or operations."))

    async def read_sebi_family(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import sebi_family
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not sebi_family.applies(firm):
            return {"applies": False, "families": [], "message": "SEBI's family of client is read for an Indian firm."}
        if inputs.get("household_id"):
            try:
                result = await sebi_family.household_view(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
            except ValueError as exc:
                raise _error(exc)
            result["message"] = (sebi_family.STANDS_IN if result["basis"] == "household" else sebi_family.RECORDED_BASIS)
            return result
        result = await sebi_family.overview(self.session, firm)
        result["message"] = (f"{result['recorded']} recorded family(ies); the household stands in for the other {result['household_standing_in']}. "
                             "Dependence is what a person recorded, never worked out.")
        return result

    async def record_sebi_family(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import sebi_family

        self._estate_may()
        try:
            result = await sebi_family.record(self.session, await self._firm(), by=await self._shelf_user(), **_terms(inputs, FAMILY_TERMS))
        except sebi_family.FamilyError as exc:
            raise _error(exc)
        result["message"] = "Recorded."
        return result

    async def read_india_estate(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_estate
        from app.llm.executors import _parse_uuid

        firm = await self._firm()
        if not india_estate.applies(firm):
            return {"applies": False, "households": [], "message": "Indian estate records are read for an Indian firm."}
        if inputs.get("household_id"):
            try:
                result = await india_estate.household_view(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
            except ValueError as exc:
                raise _error(exc)
            result["message"] = (f"{len(result['nominations'])} nomination(s) on file, {len(result['nomination_issues'])} not satisfied; "
                                 f"{len(result['gratuity'])} gratuity statement(s). {india_estate.NOT_ON_FILE}")
            return result
        result = await india_estate.overview(self.session, firm)
        result["message"] = f"{len(result['households'])} household(s) with a family or estate record; {result['issues']} thing(s) to look at. {india_estate.NOT_ON_FILE}"
        return result

    async def record_asset_nomination(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_estate

        self._estate_may()
        try:
            result = await india_estate.record_nomination(self.session, await self._firm(), by=await self._shelf_user(), **_terms(inputs, NOMINATION_TERMS))
        except india_estate.EstateError as exc:
            raise _error(exc)
        result["message"] = f"Recorded. {len(result['nomination_issues'])} nomination(s) not satisfied on this household."
        return result

    async def record_will_status(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_estate

        self._estate_may()
        try:
            result = await india_estate.record_will_status(self.session, await self._firm(), by=await self._shelf_user(), **_terms(inputs, WILL_TERMS))
        except india_estate.EstateError as exc:
            raise _error(exc)
        result["message"] = "Recorded."
        return result

    async def record_huf(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_estate

        self._estate_may()
        try:
            result = await india_estate.record_huf(self.session, await self._firm(), by=await self._shelf_user(), **_terms(inputs, HUF_TERMS))
        except india_estate.EstateError as exc:
            raise _error(exc)
        result["message"] = "Recorded."
        return result

    async def record_gratuity(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_estate

        self._estate_may()
        try:
            result = await india_estate.record_gratuity(self.session, await self._firm(), by=await self._shelf_user(), **_terms(inputs, GRATUITY_TERMS))
        except india_estate.EstateError as exc:
            raise _error(exc)
        result["message"] = "Recorded. The plan names it beside the pot and does not add it."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_family_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, india_estate, markets, nominations, sebi_family

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        self._estate_may()
        try:
            if tool_key == "record_sebi_family":
                p = await sebi_family.prepare(self.session, firm, **_terms(inputs, FAMILY_TERMS))
                dep = {True: "Dependent", False: "Not dependent", None: None}[p["dependent"]]
                return [("Person", p["person"].full_name), ("In the family of", p["of"].full_name), ("Relation", sebi_family.RELATIONS[p["relation"]]),
                        ("Dependent", dep), ("Replaces", sebi_family.RELATIONS[p["before"].relation] if p["before"] else None),
                        ("Evidence", p["document"]),
                        ("Note", "The fee cap and the pre-advice gate read this; no fee, mandate or household changes")]
            if tool_key == "record_asset_nomination":
                p = await india_estate.prepare_nomination(self.session, firm, **_terms(inputs, NOMINATION_TERMS))
                return [("Holder", p["person"].full_name), ("Asset", f"{nominations.KINDS[p['asset_kind']]}: {p['label']}"),
                        ("Recorded as", "Signed opt-out" if p["status"] == "opted_out" else "Nominated"),
                        ("Declaration signed", spoken(p["declaration_on"])),
                        ("Nominees", "; ".join(f"{n['name']} ({n['relationship'] or 'relationship not given'}"
                                               f"{', ' + format(n['share'], 'g') + '%' if n['share'] is not None else ''})" for n in p["nominees"]) or None),
                        ("Evidence", p["document"]),
                        ("Note", "Nothing changes on the account: this records what the form says, and flags what it does not satisfy")]
            if tool_key == "record_will_status":
                p = await india_estate.prepare_will_status(self.session, firm, **_terms(inputs, WILL_TERMS))
                return [("Person", p["person"].full_name), ("Personal law", india_estate.LAWS.get(p["personal_law"] or "")),
                        ("Registered", {True: "Yes", False: "No", None: None}[p["registered"]]), ("Registration number", p["registration_ref"]),
                        ("Probate", india_estate.PROBATE.get(p["probate"] or "")), ("Court number", p["probate_ref"]), ("Executor", p["executor"]),
                        ("Witnesses", "; ".join(p["witnesses"]) if p["witnesses"] else None),
                        ("Executor told", {True: "Yes" + (f", {spoken(p['executor_told_on'])}" if p["executor_told_on"] else ""),
                                           False: "No", None: None}[p["executor_told"]]),
                        ("Dated", spoken(p["as_of"])), ("Evidence", p["document"]),
                        ("Note", "What is not restated carries over from the earlier record; no nominee or account changes")]
            if tool_key == "record_huf":
                p = await india_estate.prepare_huf(self.session, firm, **_terms(inputs, HUF_TERMS))
                return [("HUF", p["entity"].name), ("Karta", p["karta_name"]),
                        ("Coparceners", "; ".join(f"{c['name']} ({c['relationship'] or 'relationship not given'})" for c in p["coparceners"])),
                        ("Dated", spoken(p["as_of"])), ("Evidence", p["document"])]
            if tool_key == "record_gratuity":
                p = await india_estate.prepare_gratuity(self.session, firm, **_terms(inputs, GRATUITY_TERMS))
                return [("Employee", p["person"].full_name), ("Employer", p["employer"]), ("Payable, as stated", markets.money(p["amount"], firm.base_currency)),
                        ("Statement dated", spoken(p["as_of"])), ("Payable on", spoken(p["payable_on"])),
                        ("Replaces", markets.money(float(p["before"].amount), firm.base_currency) if p["before"] else None),
                        ("Evidence", p["document"]), ("Note", "Named beside the retirement pot, never added to it or estimated")]
        except (sebi_family.FamilyError, india_estate.EstateError) as exc:
            raise _error(exc)
        return []
