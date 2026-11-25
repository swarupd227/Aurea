"""The conversation's tools for India's tax records and KYC (India rows 11 and 12).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare``: every check the write makes, nothing written. The Indian tax position
itself is read by ``read_tax_position``, whose India report now carries each taxpayer's engine block.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

INDIA_TOOLS = ("read_india_tax_records", "record_tax_loss", "record_grandfather_value", "read_kyc", "record_kyc")
_LOSS_TERMS = ("person_id", "entity_id", "tax_year", "kind", "amount", "used", "return_filed_on", "document")
_FMV_TERMS = ("instrument_id", "symbol", "fmv_per_unit", "document")
_KYC_TERMS = ("person_id", "entity_id", "case_id", "party_name", "document", "ckyc_number", "kra_name", "kra_status", "kyc_completed_on",
              "verification_mode", "ipv_on", "aadhaar_last4", "aadhaar_xml_dated", "pan_aadhaar_linked", "id_proof_kind", "id_proof_valid_to",
              "address_proof_kind", "address_proof_dated", "address_proof_valid_to", "risk_band", "pep", "structure", "trigger", "trigger_on")


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


class IndiaExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_map(self) -> dict:
        return {"read_india_tax_records": self.read_india_tax_records, "record_tax_loss": self.record_tax_loss,
                "record_grandfather_value": self.record_grandfather_value, "read_kyc": self.read_kyc, "record_kyc": self.record_kyc}

    # ── Row 11: the tax engine's records ────────────────────────────────────────────────────────────────────────
    async def read_india_tax_records(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_india_full as full

        firm = await self._firm()
        result = await full.records(self.session, firm)
        if not result["applies"]:
            return {**result, "message": "The Indian tax engine is read for an Indian firm; this firm's market is not India."}
        n = sum(len(t["losses"]) for t in result["taxpayers"])
        result["message"] = (f"{len(result['taxpayers'])} taxpayer(s) with {n} recorded capital loss(es) and {len(result['grandfather_values'])} equity "
                             f"value(s) for 31 Jan 2018. Nothing recorded does not mean none exists. {full.ESTIMATE}")
        return result

    def _loss_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {k: inputs.get(k) for k in _LOSS_TERMS}

    async def _may_record_tax(self) -> None:
        from app.aurea_core import tax_india_full as full

        if self.role not in full.RECORDERS:
            raise _error(Exception("A capital loss or a 2018 value is recorded by an adviser, a paraplanner, operations or the portfolio team."))

    async def _tax_loss_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import markets, tax_india_full as full

        await self._may_record_tax()
        try:
            p = await full.prepare_loss(self.session, await self._firm(), **self._loss_args(inputs))
        except full.TaxFactError as exc:
            raise _error(exc)
        was = p["before"]
        return [("Taxpayer", p["name"]), ("Tax year", p["tax_year"]), ("Loss", f"{full.LOSS_KINDS[p['kind']]} {markets.money(p['amount'], 'INR')}"),
                ("Already set off", markets.money(p["used"], "INR") if p["used"] else "Nothing"),
                ("Return filed", p["return_filed_on"].isoformat() if p["return_filed_on"] else "No return on file: it cannot be carried forward"),
                ("Replaces", f"{markets.money(was['amount'], 'INR')}, {was['used']:,.0f} used" if was else None), ("Evidence", p["document"]),
                ("Note", "Carried forward eight tax years, only from a return filed by its due day; a qualified person checks it")]

    async def record_tax_loss(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_india_full as full

        await self._may_record_tax()
        try:
            result = await full.record_loss(self.session, await self._firm(), by=await self._shelf_user(), **self._loss_args(inputs))
        except full.TaxFactError as exc:
            raise _error(exc)
        result["message"] = "Recorded. It stands as the latest for that taxpayer, year and kind, and the year's set-off reads it."
        return result

    async def _fmv_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import markets, tax_india_full as full

        await self._may_record_tax()
        try:
            p = await full.prepare_fmv(self.session, await self._firm(), **{k: inputs.get(k) for k in _FMV_TERMS})
        except full.TaxFactError as exc:
            raise _error(exc)
        return [("Instrument", p["instrument"].symbol), ("Value on 31 Jan 2018", markets.money(p["fmv"], "INR", dp=2) + " a unit"),
                ("Replaces", markets.money(p["before"], "INR", dp=2) if p["before"] else None), ("Source", p["document"]),
                ("Effect", "Equity bought before 1 Feb 2018 is valued at the higher of its cost and the lower of this and the sale price")]

    async def record_grandfather_value(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_india_full as full

        await self._may_record_tax()
        try:
            result = await full.record_fmv(self.session, await self._firm(), by=await self._shelf_user(), **{k: inputs.get(k) for k in _FMV_TERMS})
        except full.TaxFactError as exc:
            raise _error(exc)
        result["message"] = "Recorded. Lots of that instrument bought before 1 Feb 2018 now carry the grandfathered cost."
        return result

    # ── Row 12: KYC ─────────────────────────────────────────────────────────────────────────────────────────────
    async def read_kyc(self, inputs: dict[str, Any]) -> dict[str, Any]:
        import uuid

        from app.aurea_core import kyc_india

        firm = await self._firm()
        try:
            household = uuid.UUID(str(inputs["household_id"])) if inputs.get("household_id") else None
        except ValueError:
            raise _error(Exception("household_id must be an id from a read tool."))
        result = await kyc_india.overview(self.session, firm, household_id=household)
        if not result["applies"]:
            return {**result, "message": "KYC is read for an Indian firm; this firm's market is not India."}
        c = result["counts"]
        result["message"] = (f"{len(result['parties'])} part(ies): {c['overdue']} overdue, {c['blocked']} blocked at the KRA, {c['due']} due, "
                             f"{c['verified']} verified, {c['not_assessed']} not assessed, {c['not_on_file']} with no record on file. "
                             "Not on file does not mean no KYC was done, and real KRA and CKYC lookups are not connected.")
        return result

    def _kyc_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        args = {k: inputs.get(k) for k in _KYC_TERMS}
        args["beneficial_owners_text"] = inputs.get("beneficial_owners")
        return args

    async def _may_record_kyc(self) -> None:
        from app.aurea_core import kyc_india

        if self.role not in kyc_india.RECORDERS:
            raise _error(Exception("KYC is recorded by an adviser, a paraplanner, operations or compliance."))

    async def _kyc_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import kyc_india

        await self._may_record_kyc()
        try:
            p = await kyc_india.prepare(self.session, await self._firm(), **self._kyc_args(inputs))
        except kyc_india.KycError as exc:
            raise _error(exc)

        def show(v):
            if isinstance(v, list):
                return "; ".join(f"{o['name']} {o['share']:g}% {o['role']}" if o.get("share") is not None else f"{o['name']} {o['role']}" for o in v)
            return v

        rows = [("Client", p["name"])]
        for c in p["changes"]:
            after = kyc_india.mask_aadhaar(c["after"]) if c["field"] == "aadhaar_last4" else show(c["after"])
            if c["before"] is None:
                before = "not on file"
            else:
                before = kyc_india.mask_aadhaar(c["before"]) if c["field"] == "aadhaar_last4" else show(c["before"])
            rows.append((c["label"], f"{before} -> {after}"))
        s = p["state"]
        rows += [("State then", s["state"].replace("_", " ") + (f", next due {s['due_on']}" if s.get("due_on") else "")), ("Evidence", p["row"].document),
                 ("Note", "Recorded from the document; no KRA or CKYC lookup is made, and an Aadhaar number is never kept")]
        return rows

    async def record_kyc(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import kyc_india

        await self._may_record_kyc()
        try:
            result = await kyc_india.record(self.session, await self._firm(), by=await self._shelf_user(), **self._kyc_args(inputs))
        except kyc_india.KycError as exc:
            raise _error(exc)
        result["message"] = "Recorded. The party's state is worked out from it; the board's KYC queue and the activation gate read it."
        return result
