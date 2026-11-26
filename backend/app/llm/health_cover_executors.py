"""The conversation's tools for health cover and for a savings policy's surrender, paid-up and continue layout (B-397).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written. ``ExecutorError``
is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

HEALTH_TOOLS = ("read_health_cover", "record_health_policy", "end_health_policy", "set_health_cover_figure",
                "read_policy_values", "record_policy_values")
HEALTH_TERMS = ("household_id", "kind", "provider", "sum_insured", "members", "source_document", "deductible", "policy_number",
                "room_rent_cap", "co_payment_percent", "co_payment_terms", "sub_limits", "waiting_periods", "covered_since",
                "renewal_on", "annual_premium", "replaces_id")
FIGURE_TERMS = ("family_cover", "parent_cover", "source_document")
VALUE_TERMS = ("product", "statement_date", "premiums_paid", "source_document", "policy_id", "person_id", "provider", "sum_assured",
               "policy_number", "commenced_on", "annual_premium", "premium_years_left", "surrender_value", "paid_up_value",
               "fund_value", "maturity_on", "maturity_value", "maturity_basis", "charges_stated")


def _terms(inputs: dict, keys) -> dict:
    """Every named term, the missing ones as None, so the module says what is missing in its own words."""
    return {k: (None if inputs.get(k) == "" else inputs.get(k)) for k in keys}


class HealthCoverExecutors:
    """Needs the host's session, firm_id, role, _firm() and _shelf_user()."""

    def _health_map(self) -> dict:
        return {k: getattr(self, k) for k in HEALTH_TOOLS}

    async def read_health_cover(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import health_cover, markets
        from app.llm.executors import ExecutorError, _parse_uuid

        firm = await self._firm()
        say = markets.money_for(firm)
        if not inputs.get("household_id"):
            result = await health_cover.for_firm(self.session, self.firm_id)
            if not result["applies"]:
                result["message"] = "Health cover is recorded for an Indian household."
                return result
            result["message"] = book_message(result, say)
            return result
        result = await health_cover.for_household(self.session, _parse_uuid(inputs["household_id"], "household_id"), firm_id=self.firm_id)
        if result is None:
            raise ExecutorError("Household not found or access denied")
        result["message"] = household_message(result, say)
        return result

    async def record_health_policy(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import health_cover, markets
        from app.llm.executors import ExecutorError

        try:
            result = await health_cover.record_policy(self.session, self.firm_id, recorded_by=await self._shelf_user(), role=self.role,
                                                      **_terms(inputs, HEALTH_TERMS))
        except health_cover.HealthCoverError as exc:
            raise ExecutorError(str(exc))
        say = markets.money_for(await self._firm())
        result["message"] = (f"Recorded the {result['kind_label'].lower()} with {result['provider']} for {', '.join(result['members'])}: "
                             f"{say(result['sum_insured'])}" + (f" above {say(result['deductible'])}" if result["deductible"] else "") + "."
                             + (" The schedule it replaces has ended." if result["replaced"] else ""))
        return result

    async def end_health_policy(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import health_cover
        from app.llm.executors import ExecutorError

        try:
            result = await health_cover.end_policy(self.session, self.firm_id, role=self.role, policy_id=inputs.get("policy_id"),
                                                   reason=inputs.get("reason"), by=await self._shelf_user())
        except health_cover.HealthCoverError as exc:
            raise ExecutorError(str(exc))
        result["message"] = f"The {result['kind_label'].lower()} with {result['provider']} has ended and no longer counts."
        return result

    async def set_health_cover_figure(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import health_cover, markets
        from app.llm.executors import ExecutorError

        try:
            result = await health_cover.set_figure(self.session, self.firm_id, role=self.role, set_by=await self._shelf_user(),
                                                   **_terms(inputs, FIGURE_TERMS))
        except health_cover.HealthCoverError as exc:
            raise ExecutorError(str(exc))
        say = markets.money_for(await self._firm())
        result["message"] = (f"Health cover is now measured against {say(result['family_cover'])} a person in a family"
                             + (f" and {say(result['parent_cover'])} for a parent" if result["parent_cover"] else
                                "; parents are not assessed, because no figure is stated for them") + ".")
        return result

    async def read_policy_values(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets, policy_values
        from app.llm.executors import ExecutorError, _parse_uuid

        say = markets.money_for(await self._firm())
        if inputs.get("household_id"):
            result = await policy_values.for_household(self.session, _parse_uuid(inputs["household_id"], "household_id"),
                                                       firm_id=self.firm_id)
            if result is None:
                raise ExecutorError("Household not found or access denied")
        else:
            result = await policy_values.for_firm(self.session, self.firm_id)
        result["message"] = values_message(result, say)
        return result

    async def record_policy_values(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets, policy_values
        from app.llm.executors import ExecutorError

        try:
            result = await policy_values.record_statement(self.session, self.firm_id, recorded_by=await self._shelf_user(),
                                                          role=self.role, **_terms(inputs, VALUE_TERMS))
        except policy_values.PolicyValueError as exc:
            raise ExecutorError(str(exc))
        say = markets.money_for(await self._firm())
        result["message"] = (f"Recorded the {result['product_label'].lower()} statement from {result['provider']} for {result['person']}."
                             + (f" The life cover of {say(result['sum_assured'])} is now on file and counted in the death gap."
                                if result["policy_recorded"] else "")
                             + ("" if result["stands"] else " A later statement is already on file and still stands.")
                             + " Surrender, paid-up and continue are laid out as figures; Astra recommends none of them.")
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _health_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, health_cover, markets, policy_values
        from app.llm.executors import ExecutorError

        say = markets.money_for(await self._firm())
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        some = lambda v: say(v) if v is not None else None  # noqa: E731
        try:
            if tool_key == "record_health_policy":
                p = await health_cover.prepare_policy(self.session, self.firm_id, role=self.role, **_terms(inputs, HEALTH_TERMS))
                waits = "; ".join(f"{w['condition']}: " + " ".join(x for x in (
                    f"{w['months']} months" if w["months"] is not None else "", f"until {w['ends_on']}" if w["ends_on"] else "") if x)
                    for w in p["waiting_periods"])
                old = p["replaces"]
                return [("Client", p["household"].name), ("Cover", health_cover.KIND_TEXT[p["kind"]]), ("Insurer", p["provider"]),
                        ("Policy number", p["policy_number"]), ("Insured", "; ".join(f"{m['name']} ({m['relationship']})" for m in p["members"])),
                        ("Sum insured", say(p["sum_insured"]) + (", shared" if len(p["members"]) > 1 else "")),
                        ("Pays above", some(p["deductible"])), ("Room-rent cap", p["room_rent_cap"]),
                        ("Co-payment", " ".join(x for x in (f"{p['co_payment_percent']:g}%" if p["co_payment_percent"] is not None else "",
                                                            p["co_payment_terms"] or "") if x) or None),
                        ("Sub-limits", "; ".join(f"{s['item']}: {s['limit']}" for s in p["sub_limits"]) or None),
                        ("Waiting periods", waits or None), ("Covered since", spoken(p["covered_since"])),
                        ("Renews on", spoken(p["renewal_on"])), ("Premium a year", some(p["annual_premium"])),
                        ("Replaces", f"{health_cover.KIND_TEXT[old.kind]} with {old.provider}, which ends" if old else None),
                        ("Counted", "No, " + health_cover.EMPLOYER_NOT_COUNTED if p["kind"] == "employer_group" else None),
                        ("Document", p["document"])]
            if tool_key == "end_health_policy":
                p = await health_cover.prepare_end(self.session, self.firm_id, role=self.role, policy_id=inputs.get("policy_id"),
                                                   reason=inputs.get("reason"))
                row = p["policy"]
                return [("Client", p["household"].name), ("Cover", health_cover.KIND_TEXT.get(row.kind, row.kind)), ("Insurer", row.provider),
                        ("Sum insured", say(float(row.sum_insured))), ("Insured", "; ".join(m["name"] for m in row.members)),
                        ("Reason", p["reason"])]
            if tool_key == "set_health_cover_figure":
                p = await health_cover.prepare_figure(self.session, self.firm_id, role=self.role, **_terms(inputs, FIGURE_TERMS))
                was = p["previous"]
                return [("Cover for each person in a family", say(p["family_cover"])),
                        ("Cover for a parent", say(p["parent_cover"]) if p["parent_cover"] else "Not stated: parents read not assessed"),
                        ("Replaces", (f"{say(was['family_cover'])} a family member"
                                      + (f", {say(was['parent_cover'])} a parent" if was.get("parent_cover") else "")) if was else "Nothing set"),
                        ("Applies to", "Every Indian household's health cover figure at once"), ("Source", p["source"])]
            p = await policy_values.prepare_statement(self.session, self.firm_id, role=self.role, **_terms(inputs, VALUE_TERMS))
            v, layout = p["values"], p["layout"]
            rate = layout["continuing_return"]
            lock = layout["lock_in"]
            return [("Client", p["household"].name), ("Insured", p["person"].full_name),
                    ("Policy", f"{policy_values.PRODUCT_TEXT[v['product']]} with {p['provider']}"),
                    ("Also records", f"Life cover of {say(p['sum_assured'])}, counted in the death gap" if p["new_policy"] else None),
                    ("Statement date", spoken(v["statement_date"])), ("Premiums paid", say(v["premiums_paid"])),
                    ("Premium a year", some(v["annual_premium"])), ("Premium years left", v["premium_years_left"]),
                    ("Surrender value", some(v["surrender_value"]) or "Not on the statement"),
                    ("Paid-up value", some(v["paid_up_value"]) or "Not on the statement"), ("Fund value", some(v["fund_value"])),
                    ("Maturity benefit", f"{say(v['maturity_value'])}, {v['maturity_basis']}" if v["maturity_value"] is not None else None),
                    ("Matures on", spoken(v["maturity_on"])), ("Charges stated", v["charges_stated"]),
                    ("Continuing implies", f"about {rate['rate'] * 100:.1f}% a year against surrendering" if rate["rate"] is not None else None),
                    ("Locked in", f"until {lock['until']} (assumed rule, not reviewed)" if lock and lock.get("status") == "locked" else None),
                    ("Stands", None if p["stands"] else "No: a later statement is on file"), ("Document", v["source_document"]),
                    ("Note", "Records the figures only: nothing is surrendered, changed or recommended")]
        except (health_cover.HealthCoverError, policy_values.PolicyValueError) as exc:
            raise ExecutorError(str(exc))


def _names(rows: list[str], most: int = 5) -> str:
    return ", ".join(rows[:most]) + (f" and {len(rows) - most} more" if len(rows) > most else "")


def household_message(v: dict, say) -> str:
    """One household's health cover in a few sentences, for the answer."""
    if not v.get("applies"):
        return v.get("why") or "Health cover is recorded for an Indian household."
    parts = []
    for m in v["members"]:
        if m["position"] == "own":
            text = f"{m['name']}: own cover {say(m['own_cover'])}" + (f", employer {say(m['employer_cover'])} apart" if m["employer_cover"] else "")
        elif m["position"] == "employer_only":
            text = f"{m['name']}: only the employer's cover, {say(m['employer_cover'])}, which ends with the job"
        elif m["position"] == "top_up_only":
            text = f"{m['name']}: a top-up only, with no base cover under it"
        else:
            text = f"{m['name']}: no health cover on file, which does not mean none"
        for t in m["top_ups_short"]:
            text += f" (a top-up of {say(t['sum_insured'])} is not counted: {say(t['unpaid_below'])} below it is on no own policy)"
        need = m["need"]
        if need["status"] == "gap":
            text += f"; {say(need['gap'])} short of the firm's {say(need['figure'])}"
            if need["gap_with_employer"] != need["gap"]:
                text += f" ({say(need['gap_with_employer'])} with the employer's)"
        elif need["status"] == "no_gap":
            text += f"; meets the firm's {say(need['figure'])}"
        parts.append(text)
    out = f"{v['household']}. " + ". ".join(parts) + ". "
    if v["figure"] is None:
        out += "No need is assessed: the firm has stated no health cover figure, which a branch leader or administrator can do. "
    else:
        out += f"The figure's source: {v['figure']['source']}. "
        if any("no_firm_figure_for_parents" in m["need"].get("missing", []) for m in v["members"]):
            out += "Parents are not assessed: no figure is stated for them. "
    limits = [t for m in v["members"] for t in m["limits"]]
    if limits:
        out += f"Terms that limit it: {_names(sorted(set(limits)), 8)}. "
    s = v["summary"]
    if s["renewals_passed"]:
        out += f"{s['renewals_passed']} policy renewal date(s) passed with no later schedule, so not counted. "
    if s["renewals_due"]:
        out += f"{s['renewals_due']} renewal(s) due within 60 days. "
    return out.strip()


def book_message(v: dict, say) -> str:
    s = v["summary"]
    only = [f"{m['name']} ({h['household']})" for h in v["households"] for m in h["members"] if m["position"] == "employer_only"]
    none = [f"{m['name']} ({h['household']})" for h in v["households"] for m in h["members"] if m["position"] == "none_on_file"]
    out = f"{s['households']} household(s). "
    out += f"Only an employer's cover: {_names(only, 8)}. " if only else "Nobody relies on an employer's cover alone. "
    if none:
        out += f"No health cover on file (which does not mean none): {_names(none, 8)}. "
    if v["figure"]:
        out += f"{s['with_gap']} household(s) have someone short of the firm's figure ({say(v['figure']['family_cover'])} a family member). "
    else:
        out += "No need is assessed: the firm has stated no health cover figure. "
    if s["renewals_due"] or s["renewals_passed"]:
        out += f"Renewals: {s['renewals_due']} due within 60 days, {s['renewals_passed']} passed with no later schedule. "
    return out.strip()


def values_message(v: dict, say) -> str:
    if not v.get("applies"):
        return v.get("why") or "The surrender and paid-up layout is for an Indian household."
    live = [p for p in v["policies"] if p["status"] == "in_force"]
    if not live:
        return "No endowment or unit-linked statement is on file, which does not mean there is no such policy."
    parts = []
    for p in live[:5]:
        s = p["statement"]
        show = lambda x: say(x) if x is not None else "not on the statement"  # noqa: E731
        ahead = next(o for o in p["options"] if o["key"] == "continue")
        text = (f"{p['person']}'s {p['product_label'].lower()} with {p['provider']} (statement of {s['statement_date']}): premiums paid "
                f"{say(s['premiums_paid'])}; surrender {show(s['surrender_value'])}; paid-up value {show(s['paid_up_value'])}; continuing "
                f"needs {show(ahead['premiums_still_to_pay'])} more for a maturity benefit {show(s['maturity_value'])}"
                + (f" ({s['maturity_basis']})" if s["maturity_basis"] else "") + f", with life cover {show(p['sum_assured'])}")
        rate = p["continuing_return"]["rate"]
        if rate is not None:
            text += f"; continuing implies about {rate * 100:.1f}% a year against surrendering, before tax and leaving the cover out"
        lock = p["lock_in"]
        if lock and lock["status"] == "locked":
            text += f"; locked in until {lock['until']} under an assumed rule"
        parts.append(text)
    more = f" And {len(live) - 5} more." if len(live) > 5 else ""
    return ". ".join(parts) + "." + more + " Tax on surrender or maturity is not modelled. These are figures, not a recommendation."
