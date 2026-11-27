"""The conversation's tools for the Investment Adviser operating model (B-399): the registration line, retention, who is
held before advice, and compliance's sample audit of advice files.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card
in ``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

ADVICE_PRACTICE_TOOLS = ("read_advice_audit", "draw_advice_audit", "record_advice_audit")
_DRAW_TERMS = ("size", "since", "until")
_RESULT_TERMS = ("audit_id", "recommendation_id", "outcome", "findings")


def _error(exc: Any):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def practice_message(v: dict) -> str:
    if not v.get("applies"):
        return v.get("why") or "Not applicable."
    c, r, m = v["capacity"], v["retention"], v["metrics"]
    where = {"within": "within", "near": "near", "above": "above", "not_applicable": "not applied to a non-individual registration against"}
    out = (f"{c['clients']} client(s) in {c['households']} household(s) on the adviser's track, {where[c['state']]} the assumed line of "
           f"{c['line']} for an individual registration ({c['counted']}; registration type "
           f"{(c['registration'] or 'not recorded').replace('_', '-')}). Advice records are kept at least {r['years']} years (assumed, "
           "not reviewed); nothing is deleted. ")
    held = v["pre_advice"]["held"]
    out += (f"{len(held)} of {v['pre_advice']['households']} household(s) may not be advised yet: "
            + "; ".join(f"{h['household']} ({'; '.join(h['missing'])})" for h in held) + ". " if held else
            f"All {v['pre_advice']['households']} household(s) on the track have their pre-advice paperwork. ")
    if not m["audits"]:
        return out + "No sample of advice files has been drawn."
    out += (f"{m['audits']} sample(s) drawn: {m['reviewed']} of {m['drawn']} file(s) reviewed, {m['with_findings']} with findings"
            + (f" ({m['share'] * 100:.0f}%)" if m["share"] is not None else "") + f"; {m['awaiting']} awaiting review.")
    if m["by_item"]:
        out += " Findings by item: " + ", ".join(f"{v['items'][k].lower()} {n}" for k, n in sorted(m["by_item"].items())) + "."
    waiting = [f"{f['household']} (audit {a['seq']}, {f['category']})" for a in v["audits"] for f in a["files"] if not f["result"]]
    if waiting:
        out += " Next to review: " + "; ".join(waiting[:5]) + "."
    return out


class AdvicePracticeExecutors:
    """Needs the host's session, firm_id, role, _firm() and _shelf_user()."""

    def _advice_practice_map(self) -> dict:
        return {k: getattr(self, k) for k in ADVICE_PRACTICE_TOOLS}

    async def read_advice_audit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import advice_practice

        if str(self.role) not in advice_practice.READERS:
            raise _error("The advice audit is read by advisers, paraplanners, the portfolio team, compliance and branch leaders.")
        result = await advice_practice.overview(self.session, await self._firm())
        result["message"] = practice_message(result)
        return result

    async def draw_advice_audit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import advice_practice

        try:
            result = await advice_practice.record_draw(self.session, await self._firm(), role=str(self.role), by=await self._shelf_user(),
                                                       **{k: inputs.get(k) for k in _DRAW_TERMS})
        except advice_practice.AuditError as exc:
            raise _error(exc)
        a = result["audits"][0]
        result["message"] = (f"Drew sample {a['seq']}: {a['drawn']} of {a['population']} advice file(s) made {a['since']} to {a['until']}: "
                             + "; ".join(f"{f['household']} ({f['category']})" for f in a["files"])
                             + ". Each is reviewed and its result recorded one at a time.")
        return result

    async def record_advice_audit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import advice_practice

        try:
            result = await advice_practice.record_result(self.session, await self._firm(), role=str(self.role), by=await self._shelf_user(),
                                                         **{k: inputs.get(k) for k in _RESULT_TERMS})
        except advice_practice.AuditError as exc:
            raise _error(exc)
        m = result["metrics"]
        result["message"] = (f"Recorded. {m['reviewed']} of {m['drawn']} drawn file(s) reviewed, {m['with_findings']} with findings; "
                             f"{m['awaiting']} awaiting review. The advice record itself is unchanged.")
        return result

    async def _advice_audit_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import advice_practice, firm_time

        firm = await self._firm()
        try:
            if tool_key == "draw_advice_audit":
                p = await advice_practice.prepare_draw(self.session, firm, role=str(self.role), **{k: inputs.get(k) for k in _DRAW_TERMS})
                return [("Sample", f"Number {p['seq']}"), ("Advice made", f"{firm_time.spoken(p['since'])} to {firm_time.spoken(p['until'])}"),
                        ("Files in the period", p["population"]), ("Files to draw", len(p["sample"])),
                        ("Never drawn before", p["never_drawn"]), ("Chosen", "By the draw: nobody picks the files"),
                        ("Changes", "Adds a sample to the audit record; no advice record changes")]
            p = await advice_practice.prepare_result(self.session, firm, role=str(self.role), **{k: inputs.get(k) for k in _RESULT_TERMS})
        except advice_practice.AuditError as exc:
            raise _error(exc)
        rec, c = p["recommendation"], p["checks"]
        return [("Sample", f"Number {p['audit'].seq}"), ("Client", p["household"].name),
                ("File", f"{rec.category.replace('_', ' ').capitalize()}: {rec.recommendation}"),
                ("Risk profile on the record", c["risk_profile"].replace("_", " ")),
                ("Alternatives on the record", c["alternatives"]),
                ("Paperwork today", c["paperwork_today"].replace("_", " ")),
                ("Result", "Meets the standard, no finding" if p["outcome"] == "met" else f"{len(p['findings'])} finding(s)"),
                ("Findings", "; ".join(f"{advice_practice.ITEMS[f['item']]}: {f['words']}" for f in p["findings"]) or None),
                ("Changes", "Adds the result; it cannot be edited, and the advice record is unchanged")]
