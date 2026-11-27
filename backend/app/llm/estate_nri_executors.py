"""The conversation's tools for estate and NRI planning in India (B-398): the asset-and-wishes register, the yearly will
check, treaty certificates, and moves out of and back to India.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card
in ``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

ESTATE_NRI_TOOLS = ("read_estate_register", "record_estate_wish", "record_will_check", "read_nri_planning", "record_treaty_certificate",
                    "record_residence_move")
WISH_TERMS = ("person_id", "words", "document", "kind", "asset_label", "as_of")
CHECK_TERMS = ("person_id", "outcome", "document", "findings", "checked_on")
CERTIFICATE_TERMS = ("person_id", "kind", "country", "reference", "valid_from", "valid_to", "document")
MOVE_TERMS = ("person_id", "direction", "moved_on", "country", "document")
CHECK_STATE_TEXT = {"no_will": "no will on file", "never_checked": "never checked", "due": "due", "changed_since": "changed since the check",
                    "needs_change": "needs change", "checked": "checked"}
OUTLOOK_TEXT = {"rnor": "not ordinarily resident", "ordinary": "ordinarily resident", "depends": "depends on days not yet known"}


def _error(exc: Any):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, keys) -> dict:
    return {k: (None if inputs.get(k) == "" else inputs.get(k)) for k in keys}


def _names(rows: list[str], most: int = 6) -> str:
    return ", ".join(rows[:most]) + (f" and {len(rows) - most} more" if len(rows) > most else "")


def register_message(v: dict) -> str:
    if not v.get("applies"):
        return "The asset register is read for an Indian firm."
    c = v["counts"]
    out = (f"{v['household']}: {c['assets']} asset(s) and {c['liabilities']} debt(s) on file. ")
    missing = v["missing_nominations"]
    if missing:
        out += f"No nomination or opt-out on file for {len(missing)}: " + _names([f"{m['label']} ({m['owner']})" for m in missing]) + ". "
    else:
        out += "Every asset that takes a nomination has one or an opt-out on file. "
    for p in v["people"]:
        check = p["check"]
        text = f"{p['name']}: {CHECK_STATE_TEXT[check['state']]}"
        if check["state"] == "changed_since":
            text += f" ({_names(check['changes'], 3)})"
        elif check["state"] in ("checked", "due"):
            text += f" (last {check['last_checked_on']}, due {check['due_on']}, a yearly interval that is assumed)"
        if p["flags"]:
            text += "; " + "; ".join(p["flags"])
        out += text + ". "
    if v["trust_triggers"]:
        out += ("Facts that bring a private trust into view, for a specialist: " + "; ".join(t["label"].lower() for t in v["trust_triggers"]) + ". ")
    out += v["note"]
    return out


def register_book_message(v: dict) -> str:
    if not v.get("applies"):
        return "The asset register is read for an Indian firm."
    s = v["summary"]
    due = [f"{n} ({h['household']})" for h in v["households"] for n in h["to_check"]]
    return (f"{len(v['households'])} household(s): {s['missing_nominations']} asset(s) with no nomination or opt-out on file "
            f"({s['missing_non_book']} of them outside the book); {s['wills_to_check']} will(s) to check"
            + (f": {_names(due)}" if due else "") + f"; {s['without_will']} adult(s) with no will on file. {v['note']}")


def _outlook_text(p: dict) -> str:
    o = p.get("outlook")
    if not o:
        return ""
    if o["state"] != "laid_out":
        return f" Not-ordinarily-resident outlook: {o['why']}."
    return (" Outlook: " + "; ".join(f"{r['year']} {OUTLOOK_TEXT[r['state']]}" for r in o["years"])
            + ", on assumptions listed with it.")


def nri_message(v: dict) -> str:
    if not v.get("applies"):
        return "NRI planning records are read for an Indian firm."
    households = v["households"] if "households" in v else [v]
    parts = []
    for h in households:
        for p in h["people"]:
            text = f"{p['name']} ({h['household']}): {p['tax_status'] or 'residence not on file'}"
            if p["trc"] == "not_on_file":
                text += ", no tax residency certificate on file"
            elif p["trc"] == "expired":
                text += ", tax residency certificate expired"
            elif p["trc"] == "covers":
                text += ", a certificate covers today"
            for m in p["moves"]:
                text += f"; {m['label'][0].lower()}{m['label'][1:]} {'planned ' if m['planned'] else ''}{m['moved_on']}: {m['open']} of {m['tasks']} task(s) open"
            parts.append(text + "." + _outlook_text(p))
    if not parts:
        return f"No client abroad, certificate or move is on file. {v.get('not_on_file', '')}".strip()
    return " ".join(parts[:12]) + (f" And {len(parts) - 12} more." if len(parts) > 12 else "") + f" {v['not_modelled']} The rules are assumed, for counsel."


class EstateNriExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _estate_nri_map(self) -> dict:
        return {k: getattr(self, k) for k in ESTATE_NRI_TOOLS}

    def _estate_nri_may(self, readers: bool = False) -> None:
        from app.aurea_core import estate_register

        allowed = estate_register.READERS if readers else estate_register.RECORDERS
        if self.role not in allowed:
            raise _error("Estate and NRI records are read by staff." if readers else
                         "An estate or NRI record is made by an adviser, a paraplanner or operations.")

    async def read_estate_register(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import estate_register
        from app.llm.executors import _parse_uuid

        self._estate_nri_may(readers=True)
        firm = await self._firm()
        try:
            if inputs.get("household_id"):
                result = await estate_register.household_register(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
                result["message"] = register_message(result)
            else:
                result = await estate_register.overview(self.session, firm)
                result["message"] = register_book_message(result)
        except estate_register.RegisterError as exc:
            raise _error(exc)
        return result

    async def record_estate_wish(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import estate_register

        self._estate_nri_may()
        try:
            result = await estate_register.record_wish(self.session, await self._firm(), by=await self._shelf_user(), **_terms(inputs, WISH_TERMS))
        except estate_register.RegisterError as exc:
            raise _error(exc)
        result["message"] = f"Recorded on {result['household']}'s register, in the words given. Nothing else changed."
        return result

    async def record_will_check(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import estate_register

        self._estate_nri_may()
        try:
            result = await estate_register.record_check(self.session, await self._firm(), by=await self._shelf_user(), by_user=self.user_id,
                                                        **_terms(inputs, CHECK_TERMS))
        except estate_register.RegisterError as exc:
            raise _error(exc)
        raised = str(inputs.get("outcome") or "").strip().lower().replace(" ", "_") == "needs_change"
        result["message"] = ("Recorded the will check" + (": a task to update the will is raised for the client's recorded adviser" if raised else "")
                             + ". The will itself is unchanged.")
        return result

    async def read_nri_planning(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import nri_planning
        from app.llm.executors import _parse_uuid

        self._estate_nri_may(readers=True)
        firm = await self._firm()
        try:
            result = (await nri_planning.household_view(self.session, firm, _parse_uuid(inputs["household_id"], "household_id"))
                      if inputs.get("household_id") else await nri_planning.overview(self.session, firm))
        except nri_planning.PlanningError as exc:
            raise _error(exc)
        result["message"] = nri_message(result)
        return result

    async def record_treaty_certificate(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import nri_planning

        self._estate_nri_may()
        try:
            result = await nri_planning.record_certificate(self.session, await self._firm(), by=await self._shelf_user(),
                                                           **_terms(inputs, CERTIFICATE_TERMS))
        except nri_planning.PlanningError as exc:
            raise _error(exc)
        result["message"] = f"Recorded. {nri_planning.NOT_MODELLED}"
        return result

    async def record_residence_move(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import nri_planning

        self._estate_nri_may()
        try:
            result = await nri_planning.record_move(self.session, await self._firm(), by=await self._shelf_user(), by_user=self.user_id,
                                                    **_terms(inputs, MOVE_TERMS))
        except nri_planning.PlanningError as exc:
            raise _error(exc)
        person = next(p for p in result["people"] if p["person_id"] == str(inputs.get("person_id")))
        move = next(m for m in person["moves"] if m["moved_on"] == str(inputs.get("moved_on"))[:10])
        result["message"] = (f"Recorded {person['name']} {move['label'][0].lower()}{move['label'][1:]} on {move['moved_on']}: {move['tasks']} task(s) raised for "
                             "the client's recorded adviser. No account, folio or residence record changed.")
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _estate_nri_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import estate_register, firm_time, nri_planning

        self._estate_nri_may()
        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        try:
            if tool_key == "record_estate_wish":
                p = await estate_register.prepare_wish(self.session, firm, **_terms(inputs, WISH_TERMS))
                return [("Person", p["person"].full_name), ("Kind", estate_register.WISH_KINDS[p["kind"]]), ("About", p["asset_label"]),
                        ("In their words", p["words"]), ("Said on", spoken(p["as_of"])), ("Evidence", p["document"]),
                        ("Note", "A record for the lawyer: no nominee, will or account changes")]
            if tool_key == "record_will_check":
                p = await estate_register.prepare_check(self.session, firm, **_terms(inputs, CHECK_TERMS))
                return [("Person", p["person"].full_name), ("Will signed", spoken(p["will"].signed_on)),
                        ("Checked against", f"{len(p['assets'])} asset(s) on file"),
                        ("Without a nomination", len(p["missing"]) or None),
                        ("Outcome", estate_register.OUTCOMES[p["outcome"]]), ("Found", p["findings"]), ("Checked on", spoken(p["checked_on"])),
                        ("Raises", f"A task for {p['adviser'].full_name if p['adviser'] else 'the client (no adviser on record)'}, due "
                                   f"{spoken(p['task_due'])}" if p["task_due"] else None),
                        ("Evidence", p["document"]), ("Note", "The will itself is not changed")]
            if tool_key == "record_treaty_certificate":
                p = await nri_planning.prepare_certificate(self.session, firm, **_terms(inputs, CERTIFICATE_TERMS))
                return [("Person", p["person"].full_name), ("Document type", nri_planning.CERTIFICATE_KINDS[p["kind"]]), ("Country", p["country"]),
                        ("Reference", p["reference"]), ("Valid", f"{spoken(p['valid_from'])} to {spoken(p['valid_to'])}"),
                        ("Evidence", p["document"]), ("Note", nri_planning.NOT_MODELLED)]
            p = await nri_planning.prepare_move(self.session, firm, **_terms(inputs, MOVE_TERMS))
            raised = [i for i in p["items"] if i["raises"]]
            quiet = [i for i in p["items"] if not i["raises"]]
            return [("Person", p["person"].full_name), ("Move", f"{nri_planning.DIRECTIONS[p['direction']]}, {p['country']}"),
                    ("On", spoken(p["moved_on"])),
                    *[(f"Task {n}", i["label"] + (f" ({_names(i['applies_to'], 3)})" if i["applies_to"] else "")) for n, i in enumerate(raised, 1)],
                    ("Nothing on file for", "; ".join(i["label"] for i in quiet) or None),
                    ("Assigned to", p["adviser"].full_name if p["adviser"] else "Nobody: no adviser on record"), ("Due", spoken(p["due"])),
                    ("Evidence", p["document"]),
                    ("Note", "Raises tasks only: nothing is redesignated, and the rules are assumed, for counsel")]
        except (estate_register.RegisterError, nri_planning.PlanningError) as exc:
            raise _error(exc)
