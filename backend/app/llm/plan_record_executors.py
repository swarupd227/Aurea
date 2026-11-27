"""The conversation's tools for the plan of record (B-181): read it, record a baseline, a review, a delivery.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card
in ``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.models.enums import UserRole

PLAN_PREPARERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
PLAN_REVIEWERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM})
PLAN_READERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE, UserRole.BRANCH_LEADER})
PLAN_RECORD_TOOLS = ("read_plan_record", "record_plan_baseline", "record_plan_review", "record_plan_delivery")
_REVIEW_TERMS = ("household_id", "outcome", "findings")
_DELIVERY_TERMS = ("household_id", "evidence_kind", "evidence", "action_items", "due_on", "delivered_on")
STATE_TEXT = {"not_started": "not started", "data_incomplete": "facts missing", "data_complete": "facts complete",
              "baseline": "baseline recorded", "scenarios": "scenarios explored", "in_review": "waiting for review",
              "reviewed": "reviewed", "delivered": "delivered", "implementing": "delivered, tasks open", "complete": "complete"}


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _pct(v) -> str | None:
    return f"{round(float(v) * 100)}%" if v is not None else None


class PlanRecordExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _plan_record_map(self) -> dict:
        return {"read_plan_record": self.read_plan_record, "record_plan_baseline": self.record_plan_baseline,
                "record_plan_review": self.record_plan_review, "record_plan_delivery": self.record_plan_delivery}

    def _plan_may(self, roles: frozenset, words: str) -> None:
        # The gateway checks the catalogue's roles; this re-checks, and aurea_core.plan_record checks again.
        if self.role not in roles:
            raise _error(Exception(words))

    def _plan_say(self, r: dict) -> str:
        bits = [f"{r['household']}: plan v{r['version']}, {STATE_TEXT[r['state']]} ({r['owner']} next)" if r["version"]
                else f"{r['household']}: no plan recorded, {STATE_TEXT[r['state']]}"]
        if r["data"]["missing"]:
            bits.append("missing " + "; ".join(m["text"] for m in r["data"]["missing"]))
        need = r.get("review_need") or {}
        bits.append("review needed" + (" (every plan, until the firm sets a complexity line)" if need.get("line_assumed") else
                                       f" (complexity {need.get('score')} above the line of {need.get('line')})")
                    if need.get("needed") else f"no review needed (complexity {need.get('score')} within the line of {need.get('line')})")
        if r.get("freshness"):
            f = r["freshness"]
            bits.append(f"delivered {f['delivered_on']}, {'fresh' if f['fresh'] else 'stale'} against a "
                        f"{f['cadence_months']}-month cadence{' (assumed)' if f['cadence_assumed'] else ''}")
        if r.get("tasks"):
            bits.append(f"{r['open_tasks']} of {r['tasks']} task(s) open")
        held = r.get("pre_advice")
        return ("; ".join(bits) + ". Every figure is an estimate."
                + (f" Neither a delivery nor the client's summary may go yet: {held['reason']}" if held and not held["met"] else ""))

    async def read_plan_record(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import plan_record

        self._plan_may(PLAN_READERS, plan_record.REFUSED_READ)
        try:
            if inputs.get("household_id"):
                try:
                    household_id = uuid.UUID(str(inputs["household_id"]))
                except (TypeError, ValueError):
                    raise _error(Exception("household_id must be an id from a read tool."))
                result = await plan_record.for_household(self.session, household_id, firm_id=self.firm_id)
                if result is None:
                    raise _error(Exception(f"Household {household_id} not found or access denied"))
                result["message"] = self._plan_say(result)
                return result
            result = await plan_record.for_firm(self.session, self.firm_id, role=str(self.role), user_id=self.user_id,
                                                since=inputs.get("since"), until=inputs.get("until"))
        except plan_record.PlanRecordError as exc:
            raise _error(exc)
        c, f, q, d = result["coverage"], result["freshness"], result["qc"], result["scenario_depth"]
        result["message"] = (f"{'Your' if result['scope'] == 'own' else 'The firm'}'s {c['active']} active household(s): {c['delivered']} with a "
                             f"delivered plan ({_pct(c['share']) or 'none'}); {f['fresh']} of {f['delivered']} fresh; QC {q['passed']} of "
                             f"{q['reviews']} review(s) passed{f' ({_pct(q['pass_rate'])})' if q['pass_rate'] is not None else ''} from {q['since']}; "
                             f"scenario depth median {d['median'] if d['median'] is not None else 'none'} over {d['baselines']} baseline(s).")
        return result

    async def record_plan_baseline(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import plan_record

        self._plan_may(PLAN_PREPARERS, plan_record.REFUSED_PREPARE)
        try:
            row = await plan_record.record_baseline(self.session, self.firm_id, role=str(self.role), recorded_by=await self._shelf_user(),
                                                    user_id=self.user_id, household_id=inputs.get("household_id"))
        except plan_record.PlanRecordError as exc:
            raise _error(exc)
        b = row["baseline"]["figures"]
        row["message"] = (f"{row['household']}: plan v{row['version']} baselined against assumption version {b['assumption_version']}; "
                          f"{'review by a second person needed' if row['review_need']['needed'] else 'no review needed'}. "
                          "No goal, mandate or plan figure changed.")
        return row

    async def record_plan_review(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import plan_record

        self._plan_may(PLAN_REVIEWERS, plan_record.REFUSED_REVIEW)
        try:
            row = await plan_record.record_review(self.session, self.firm_id, role=str(self.role), recorded_by=await self._shelf_user(),
                                                  user_id=self.user_id, **{k: inputs.get(k) for k in _REVIEW_TERMS})
        except plan_record.PlanRecordError as exc:
            raise _error(exc)
        failed = row["sent_back"] is not None
        row["message"] = (f"{row['household']}: plan v{row['version']} review " + ("failed and sent back; a new baseline is needed." if failed
                                                                                   else "passed; it may be delivered."))
        return row

    async def record_plan_delivery(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import plan_record

        self._plan_may(PLAN_PREPARERS, plan_record.REFUSED_PREPARE)
        try:
            row = await plan_record.record_delivery(self.session, self.firm_id, role=str(self.role), recorded_by=await self._shelf_user(),
                                                    user_id=self.user_id, **{k: inputs.get(k) for k in _DELIVERY_TERMS})
        except plan_record.PlanRecordError as exc:
            raise _error(exc)
        p = row["plan_of_record"]
        row["message"] = (f"{row['household']}: plan v{p['version']} delivered {p['happened_on']}, from a {p['evidence_kind']}, its figures frozen; "
                          f"{len(p['tasks'])} task(s) raised for {p['owner'] or 'nobody: no adviser on record'}. Nothing was sent to the client.")
        return row

    async def _plan_record_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What the Confirm card names, refused for the reasons recording it would be refused."""
        from app.aurea_core import firm_time, markets, plan_record

        firm = await self._firm()
        say = markets.money_for(firm)
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        who = await self._shelf_user()
        try:
            if tool_key == "record_plan_baseline":
                self._plan_may(PLAN_PREPARERS, plan_record.REFUSED_PREPARE)
                r = await plan_record.prepare_baseline(self.session, self.firm_id, role=str(self.role), household_id=inputs.get("household_id"))
                f, need = r["figures"], r["review"]
                ret = f["retirement"] or {}
                version = (f"v{r['version']}, a new plan after the delivered one" if r["after_delivery"] else
                           f"v{r['version']}, a revised baseline" if r["revises"] else f"v{r['version']}, the first")
                return [("Client", r["household"].name), ("Plan", version),
                        ("Facts on file", f"{r['data']['on_file']} of {r['data']['read']}"),
                        ("Retirement success", _pct(ret.get("success_probability"))),
                        ("Sustainable income", say(ret["sustainable_income"]) + " a year" if ret.get("sustainable_income") is not None else None),
                        ("Needs met together", _pct(f["tiers"]["success"])),
                        ("Funded ratio", f"{f['funded']['ratio']:.2f}" if f["funded"].get("ratio") is not None else None),
                        ("Goals", "; ".join(f"{g['name']}: {_pct(g['success_probability'])}" for g in f["goals"]) or "none on file"),
                        ("Assumption version", f["assumption_version"]),
                        ("Complexity", f"{r['complexity']['score']} on file ("
                         + ", ".join(f"{p['count']} {p['part'].replace('_', ' ')}" for p in r["complexity"]["parts"] if p["count"]) + ")"),
                        ("Review", ("needed: every plan is reviewed until the firm sets its complexity line" if need["line_assumed"] else
                                    f"needed: above the firm's line of {need['line']}" if need["needed"] else f"not needed: within the firm's line of {need['line']}")),
                        ("Changes", "Freezes these figures as the plan's baseline; changes no goal, mandate or plan figure")]
            if tool_key == "record_plan_review":
                self._plan_may(PLAN_REVIEWERS, plan_record.REFUSED_REVIEW)
                r = await plan_record.prepare_review(self.session, self.firm_id, role=str(self.role), recorded_by=who, user_id=self.user_id,
                                                     **{k: inputs.get(k) for k in _REVIEW_TERMS})
                b = r["baseline"]
                return [("Client", r["household"].name), ("Baseline", f"v{b.plan_version}, {spoken(b.happened_on)}, by {b.recorded_by}"),
                        ("Outcome", "Passed" if r["outcome"] == "passed" else "Failed: sent back for a new baseline"),
                        ("Findings", r["findings"]), ("Reviewer", who),
                        ("Changes", "Adds the review to the plan record; changes no plan figure")]
            self._plan_may(PLAN_PREPARERS, plan_record.REFUSED_PREPARE)
            r = await plan_record.prepare_delivery(self.session, self.firm_id, role=str(self.role), **{k: inputs.get(k) for k in _DELIVERY_TERMS})
        except plan_record.PlanRecordError as exc:
            raise _error(exc)
        b, rev = r["baseline"], r["review"]
        f = (b.snapshot or {}).get("figures") or {}
        ret = f.get("retirement") or {}
        return [("Client", r["household"].name), ("Plan", f"v{b.plan_version}, the baseline of {spoken(b.happened_on)}"),
                ("Review", f"passed, {spoken(rev.happened_on)}, by {rev.recorded_by}" if rev is not None else "not needed: within the firm's line"),
                ("Delivered", spoken(r["delivered_on"])), ("Evidence", f"{r['evidence_kind'].capitalize()}: {r['evidence']}"),
                ("Figures frozen", ", ".join(x for x in (f"retirement {_pct(ret.get('success_probability'))}" if ret else None,
                                                          f"funded {f['funded']['ratio']:.2f}" if (f.get("funded") or {}).get("ratio") is not None else None,
                                                          f"assumptions {f.get('assumption_version')}") if x)),
                ("Action items", "; ".join(i["title"] + (f" (due {i['due_on']})" if i["due_on"] else "") for i in r["items"]) or "none"),
                ("Tasks for", (r["owner"].full_name if r["owner"] else "nobody: no adviser on record") if r["items"] else None),
                ("Changes", "Adds the delivery to the plan record with the baseline's figures as they were frozen; raises the tasks; sends nothing")]
