"""The conversation's tools for the consolidation workbench, India (B-389).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written. ``ExecutorError``
is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

CONSOLIDATION_TOOLS = ("read_consolidation", "record_holding_facts", "classify_holding", "open_consolidation_request",
                       "record_consolidation_step")
FACT_TERMS = ("line_id", "document", "lots", "tax_class", "exit_load_rate", "exit_load_days", "distributor_code", "form")
CLASSIFY_TERMS = ("line_ids", "decision", "reason", "product_reason", "planned_on", "units")
REQUEST_TERMS = ("kind", "line_ids", "evidence", "on", "from_code", "to_code", "to_account", "transfer_kind", "reason_code", "consideration",
                 "stamp_duty_stated", "manager_from", "manager_to", "note")
STEP_TERMS = ("item_id", "step", "evidence", "on", "reference", "reason", "stamp_duty_stated")


HOLDING_KEEPS = ("line_id", "owner", "institution", "account_label", "name", "units", "value", "form", "tax_class", "fund_plan", "distributor_code",
                 "decision", "item")
REDEEM_KEEPS = ("assessed", "missing", "gain", "short_term", "long_term", "tax", "exit_load", "cost_of_redeeming", "long_term_from",
                "grandfather_missing")


def slim(view: dict) -> dict:
    """The workbench as the conversation carries it: every figure a question or a card needs, once, without the keys,
    buckets and filing detail only the page reads."""
    if not view.get("holdings"):
        return view
    out = {k: v for k, v in view.items() if k != "accounts"}
    out["holdings"] = [{**{k: h.get(k) for k in HOLDING_KEEPS}, "redeem": {k: h["redeem"].get(k) for k in REDEEM_KEEPS},
                        "lots": (h.get("facts") or {}).get("lots")} for h in view["holdings"]]
    out["items"] = [{**i, "lines": [l["name"] for l in i["lines"]],
                     "steps": [{k: s[k] for k in ("label", "on", "reference", "note")} for s in i["steps"]]} for i in view["items"]]
    out["schedule"] = [{**g, "rows": [{k: v for k, v in r.items() if k not in ("buckets", "proceeds", "cost")} for r in g["rows"]]}
                       for g in view["schedule"]]
    return out


def _terms(inputs: dict, keys) -> dict:
    """Every named term, the missing ones as None, so the module says what is missing in its own words."""
    return {k: (None if inputs.get(k) == "" else inputs.get(k)) for k in keys}


class ConsolidationExecutors:
    """Needs the host's session, firm_id, role, _firm() and _shelf_user()."""

    def _consolidation_map(self) -> dict:
        return {k: getattr(self, k) for k in CONSOLIDATION_TOOLS}

    async def _consolidation_write(self, write, terms: dict) -> dict:
        from app.aurea_core import consolidation
        from app.llm.executors import ExecutorError

        try:
            return slim(await write(self.session, await self._firm(), self.role, by=await self._shelf_user(), **terms))
        except consolidation.ConsolidationError as exc:
            raise ExecutorError(str(exc))

    async def read_consolidation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import consolidation, markets
        from app.llm.executors import ExecutorError, _parse_uuid

        firm = await self._firm()
        if not consolidation.applies(firm):
            return {"applies": False, "households": [], "message": "The consolidation workbench is for an Indian firm."}
        say = markets.money_for(firm)
        if not inputs.get("household_id"):
            result = await consolidation.for_firm(self.session, self.firm_id)
            s = result["summary"]
            result["message"] = (f"{s['households']} household(s) with holdings elsewhere on file, {s['started']} being worked: "
                                 f"{s['to_classify']} holding(s) to classify, {s['open_items']} open request(s), {s['rejected']} rejected. "
                                 "Give household_id for one household's folios, requests and exit schedule.")
            return result
        result = await consolidation.for_household(self.session, _parse_uuid(inputs["household_id"], "household_id"), firm_id=self.firm_id)
        if result is None:
            raise ExecutorError("Household not found or access denied")
        result["message"] = consolidation_message(result, say)
        return slim(result)

    async def record_holding_facts(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import consolidation

        result = await self._consolidation_write(consolidation.record_facts, _terms(inputs, FACT_TERMS))
        result["message"] = f"Recorded for {result['recorded']}. No holding moved and no order was placed."
        return result

    async def classify_holding(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import consolidation

        result = await self._consolidation_write(consolidation.record_classification, _terms(inputs, CLASSIFY_TERMS))
        result["message"] = f"Classified {result['recorded']}. No holding moved and no order was placed."
        return result

    async def open_consolidation_request(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import consolidation

        result = await self._consolidation_write(consolidation.open_item, _terms(inputs, REQUEST_TERMS))
        result["message"] = f"{result['recorded']}. Nothing was sent to a registrar or depository: this records the request."
        return result

    async def record_consolidation_step(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import consolidation

        result = await self._consolidation_write(consolidation.record_step, _terms(inputs, STEP_TERMS))
        result["message"] = f"Recorded: {result['recorded']}. No holding or cash was moved here."
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _consolidation_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import consolidation as con
        from app.aurea_core import firm_time, markets
        from app.llm.executors import ExecutorError

        firm = await self._firm()
        say = markets.money_for(firm)
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731

        def holding(row: dict) -> str:
            folio = f", {row['account_label']}" if row.get("account_label") else ""
            return f"{row['name']} at {row['institution']}{folio}"

        def sale(a: dict) -> str:
            if not a["assessed"]:
                return "Not assessed: " + ", ".join(a["missing"])
            load = "exit load not recorded" if a["exit_load"] is None else f"exit load {say(a['exit_load'])}"
            tax = "tax not assessed" if a.get("tax") is None else f"tax about {say(a['tax'])}"
            return f"Gain {say(a['gain'])} (short {say(a['short_term'])}, long {say(a['long_term'])}); {tax}; {load}"

        try:
            if tool_key == "record_holding_facts":
                p = await con.prepare_facts(self.session, firm, self.role, **_terms(inputs, FACT_TERMS))
                f = p["fields"]
                lots = f["lots"]
                return [("Client", p["household"].name), ("Holding", holding(p["row"])), ("Whose", p["row"]["owner"]),
                        ("Lots", "; ".join(f"{l['units']:,.3f} at {l['cost_per_unit']:,.2f} on {l['acquired_on']}" for l in lots) if lots else None),
                        ("Tax class", con.TAX_CLASSES.get(f["tax_class"] or "")),
                        ("Exit load", None if f["exit_load_rate"] is None else f"{f['exit_load_rate'] * 100:g}%"
                         + (f" on units held under {f['exit_load_days']} days" if f["exit_load_days"] is not None else "")),
                        ("Distributor code", f["distributor_code"]), ("Held in", con.FORMS.get(f["form"] or "")),
                        ("If redeemed today", sale(p["redeem"])), ("Document", p["document"])]
            if tool_key == "classify_holding":
                p = await con.prepare_classification(self.session, firm, self.role, **_terms(inputs, CLASSIFY_TERMS))
                rows = [("Client", p["household"].name), ("Decision", con.DECISIONS[p["decision"]]),
                        ("Holdings", "; ".join(holding(r["row"]) for r in p["redeem"][:8]) + (f"; and {len(p['redeem']) - 8} more" if len(p["redeem"]) > 8 else "")),
                        ("Reason", p["reason"])]
                if p["decision"] == "exit":
                    a, b = p["redeem"][0], p["budget"]
                    rows += [("About the product", con.PRODUCT_REASONS[p["product_reason"]]), ("Planned for", spoken(p["planned_on"])),
                             ("Units", f"{p['units']:,.3f}" if p["units"] is not None else "The whole holding"),
                             ("On that day", sale(a)),
                             ("Short-term until", spoken(_day(a["long_term_from"]))),
                             ("Gain budget", f"{say(b['amount'])} for {b['tax_year']} ({b['source']})" if b else "None on file"),
                             ("Left after it", None if not b or b["left_after"] is None else
                              (say(b["left_after"]) if b["left_after"] >= 0 else f"Over by {say(-b['left_after'])}")),
                             ("Note", "Scheduled only: nothing is sold and the client has not consented to a trade by this")]
                else:
                    known = [r for r in p["redeem"] if r["assessed"] and r.get("tax") is not None]
                    rows.append(("Redeeming instead would", "Not assessed: cost not on file" if not known else
                                 f"realise {say(sum(r['gain'] for r in known))} of gain on {len(known)} holding(s), about "
                                 f"{say(sum(r['tax'] for r in known))} tax taken one at a time"))
                rows.append(("Open request affected", "; ".join(p["flagged"]) or None))
                return rows
            if tool_key == "open_consolidation_request":
                p = await con.prepare_item(self.session, firm, self.role, **_terms(inputs, REQUEST_TERMS))
                d = p["details"]
                first = p["snapshots"][0]
                rows = [("Client", p["household"].name), ("Request", con.KINDS[p["kind"]]), ("Whose", first["owner"]),
                        ("At", first["institution"]), ("Account", first["account_label"]),
                        ("Holdings", "; ".join(s["name"] for s in p["snapshots"])),
                        ("Value stated", say(sum(s["value"] for s in p["snapshots"]))),
                        ("Distributor code", f"{d['from_code']} to {d['to_code']}" if "to_code" in d else None),
                        ("Plan", "A regular plan stays a regular plan" if p["kind"] == "code_change" and any(s.get("fund_plan") == "regular" for s in p["snapshots"]) else None),
                        ("Left out", "; ".join(p["left_out"]) or None),
                        ("From", d.get("manager_from")), ("To", d.get("manager_to")), ("Receiving demat", d.get("to_account")),
                        ("Transfer", d.get("transfer_kind_label")), ("Reason code", d.get("reason_code")),
                        ("Consideration", say(d["consideration"]) if d.get("consideration") else None)]
                duty = p["stamp_duty"]
                if duty:
                    rows.append(("Stamp duty", "Not assessed: no consideration stated" if duty["estimate"] is None else
                                 f"{say(duty['estimate'])} estimated, from an assumed rule" + (f"; the document states {say(duty['stated'])}" if duty["stated"] is not None else "")))
                rows += [("Dated", spoken(p["on"])), ("Evidence", p["evidence"]),
                         ("Note", "Records the request only: nothing is sent, moved or sold")]
                return rows
            if tool_key == "record_consolidation_step":
                p = await con.prepare_step(self.session, firm, self.role, **_terms(inputs, STEP_TERMS))
                v = p["view"]
                return [("Client", p["household"].name), ("Request", v["kind_label"]), ("At", v["institution"]), ("Account", v["account_label"]),
                        ("Holdings", "; ".join(l["name"] for l in v["lines"])), ("Now", v["status_label"]),
                        ("Becomes", con.CONFIRMED_AS[v["kind"]] if p["step"] == "confirmed" else con.STEPS[p["step"]]),
                        ("On", spoken(p["on"])), ("Reference", p["reference"]), ("Reason", p["note"]),
                        ("Stamp duty stated", say(p["stamp_duty_stated"]) if p["stamp_duty_stated"] is not None else None),
                        ("Evidence", p["evidence"]), ("Note", "Records what the document shows: no holding or cash is moved here")]
        except con.ConsolidationError as exc:
            raise ExecutorError(str(exc))
        return []


def _day(value):
    from datetime import date

    return date.fromisoformat(value) if value else None


def consolidation_message(v: dict, say) -> str:
    """One household's workbench in a sentence or three, for the answer."""
    if not v.get("applies"):
        return "The consolidation workbench is for an Indian firm."
    s, t = v["summary"], v["redeem_instead"]
    c = s["classified"]
    counts = lambda d: ", ".join(f"{n} {k}" for k, n in d.items()) or "none"  # noqa: E731
    out = (f"{v['household']}: {s['lines']} holding(s) elsewhere on file in {s['folios']} folio(s) across {s['fund_houses']} fund house(s) and "
           f"{s['demat_accounts']} demat account(s), stated at {say(s['value'])}. Classified: {c['keep_remap']} keep and re-map, "
           f"{c['transfer']} transfer, {c['exit']} exit, {c['unclassified']} not yet classified. Code changes: {counts(s['code_changes'])}. "
           f"Demat transfers: {counts(s['demat_transfers'])}. ")
    if s["pms_transitions"]:
        out += f"PMS transitions: {counts(s['pms_transitions'])}. "
    for g in v["schedule"]:
        budget = "no gain budget on file" if not g["budget"] else (
            f"budget {say(g['budget']['amount'])}, " + (f"over by {say(g['over'])}" if g["over"] else f"{say(g['left'])} left"))
        out += (f"Exits for {g['taxpayer']} in {g['tax_year']}: {len(g['rows'])}, gain about {say(g['exit_gain'])}, tax about "
                f"{say(g['estimated_tax'] or 0)}, {budget}" + (f", {g['not_assessed']} not assessed" if g["not_assessed"] else "") + ". ")
    if t["assessed"]:
        load = f"{say(t['exit_load'])} of recorded exit load" + (
            f" (none recorded for {t['exit_load_not_recorded']} of them)" if t["exit_load_not_recorded"] else "")
        out += (f"Redeeming and reinvesting the keep and transfer holdings instead would crystallise about {say(t['gain'])} of gain, "
                f"{say(t['tax'])} of tax and {load} on {t['assessed']} holding(s)"
                + (f"; {t['not_assessed']} not assessed for want of cost or class" if t["not_assessed"] else "") + ". ")
    return out + "All tax figures are estimates; nothing here moves a holding or places an order."
