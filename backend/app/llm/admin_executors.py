"""The conversation's tools for the Admin and Connectors pages (B-96).

Each write shares its one implementation with the page's route (aurea_core/platform_admin.py): the Confirm card
is the module's ``prepare_*`` (every check the write makes, nothing written), and Confirm runs the write itself.
A card names the record and each field's from and to; a change that alters nothing is refused as already so.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each card in
``preview_change``. ``ExecutorError`` is imported where it is raised, because executors.py imports this module.

What no tool here does: set an LLM key or a connector's credentials, import clients from a CSV, change the firm's
jurisdiction or base currency, or return an invite or reset link.
"""
from __future__ import annotations

import re
from typing import Any

from app.models.enums import AssetClass, MandateType, UserRole

_ADMIN = frozenset({UserRole.ADMIN})
_MODELS = frozenset({UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO, UserRole.ADMIN})
_SETUP = frozenset({UserRole.OPERATIONS, UserRole.ADMIN})
_SYNC = frozenset({UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN})

# Who may do each, as its route allows (api/admin.AdminOnly, api/portfolios.AdminDep, api/conduit.SetUp/Syncs).
WRITE_ROLES: dict[str, frozenset] = {
    "update_firm_profile": _ADMIN, "set_agent_switches": _ADMIN, "invite_user": _ADMIN, "change_user": _ADMIN,
    "save_segment": _ADMIN, "save_mandate_type": _ADMIN, "set_mandate_suitability": _ADMIN, "set_notification": _ADMIN,
    "add_research": _ADMIN, "research_action": _ADMIN, "set_cmas": _ADMIN, "set_model_routing": _ADMIN,
    "save_model_portfolio": _MODELS, "set_model_targets": _MODELS, "delete_model_portfolio": _MODELS,
    "add_connector": _SETUP, "set_connector": _SETUP, "sync_connector": _SYNC,
}
OWNERS = {_ADMIN: "an administrator", _MODELS: "the portfolio team, research or an administrator",
          _SETUP: "operations or an administrator", _SYNC: "operations, the portfolio team or an administrator"}
# A change whose reason goes on the audit log, and is asked for.
NEEDS_REASON = {"set_agent_switches", "save_segment", "save_mandate_type", "set_mandate_suitability", "set_cmas",
                "set_model_routing"}
# Writes that always make something, so "already so" does not apply.
ALWAYS_CHANGES = {"invite_user", "add_research", "add_connector", "sync_connector"}

RISK_TOLERANCES = ("conservative", "balanced", "growth", "aggressive")
HORIZONS = ("short", "medium", "long")
# Fields a card shows as a percentage, and as basis points.
_PCT = {"max_equity", "drift_band", "expected_return", "volatility", *(a.value for a in AssetClass)}
_BPS = {"fee_tier_bps"}


def _refuse(message: str) -> Exception:
    from app.llm.executors import ExecutorError

    return ExecutorError(message)


def _flag(value: Any, name: str) -> bool | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return value
    said = str(value).strip().lower()
    if said in ("true", "yes", "1", "on"):
        return True
    if said in ("false", "no", "0", "off"):
        return False
    raise _refuse(f"{name} must be true or false.")


def _number(value: Any, name: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise _refuse(f"{name} must be a number.")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    said = str(value).strip()
    return said or None


def _words(value: Any) -> list[str] | None:
    if value is None:
        return None
    raw = value if isinstance(value, list) else str(value).split(",")
    return [str(v).strip() for v in raw if str(v).strip()]


def _shown(name: str, value: Any) -> str:
    if value is None or value == [] or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if name in _PCT and isinstance(value, (int, float)):
        return f"{float(value):.1%}"
    if name in _BPS and isinstance(value, (int, float)):
        return f"{value:g} bps"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _label(field: str) -> str:
    """A field as a person reads it on the card: "schedule_enabled" is "Schedule enabled"."""
    text = str(field).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


class AdminToolExecutors:
    """The Admin and Connectors pages' tools. Needs ``self.session``, ``self.role``, ``self.firm_id``, ``_firm()``
    and ``_me()`` from ToolExecutors."""

    # ─── Read ─────────────────────────────────────────────────────────────────────────────────

    async def read_admin_settings(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import platform_admin

        section = str(inputs.get("section") or "").strip().lower()
        # The gateway checks the catalogue's roles; this re-checks, section by section.
        allowed = platform_admin.SECTION_READERS.get(self.role, set())
        if section in platform_admin.SECTIONS and section not in allowed:
            raise _refuse(f"The {section.replace('_', ' ')} settings are not this role's to read. This role reads: "
                          f"{', '.join(sorted(allowed)) or 'none'}.")
        try:
            result = await platform_admin.read_section(self.session, await self._firm(), section)
        except platform_admin.AdminError as exc:
            raise _refuse(str(exc))
        return {**result, "message": f"{result['title']}: {len(result['rows'])} row(s)."}

    # ─── Writes ───────────────────────────────────────────────────────────────────────────────

    async def update_firm_profile(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("update_firm_profile", inputs)

    async def set_agent_switches(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("set_agent_switches", inputs)

    async def invite_user(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("invite_user", inputs)

    async def change_user(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("change_user", inputs)

    async def save_segment(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("save_segment", inputs)

    async def save_mandate_type(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("save_mandate_type", inputs)

    async def set_mandate_suitability(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("set_mandate_suitability", inputs)

    async def set_notification(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("set_notification", inputs)

    async def add_research(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("add_research", inputs)

    async def research_action(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("research_action", inputs)

    async def save_model_portfolio(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("save_model_portfolio", inputs)

    async def set_model_targets(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("set_model_targets", inputs)

    async def delete_model_portfolio(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("delete_model_portfolio", inputs)

    async def add_connector(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("add_connector", inputs)

    async def set_connector(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("set_connector", inputs)

    async def sync_connector(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("sync_connector", inputs)

    async def set_cmas(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("set_cmas", inputs)

    async def set_model_routing(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._admin_write("set_model_routing", inputs)

    # ─── The card and the write share their checks ─────────────────────────────────────────

    async def _admin_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What the Confirm card names: the record, each field's from and to, and what follows from it."""
        args, plan, reason = await self._admin_checked(tool_key, inputs)
        rows: list[tuple[str, Any]] = [("Record", plan.label)]
        rows += [(_label(c["field"]), f"{_shown(c['field'], c['from'])} → {_shown(c['field'], c['to'])}") for c in plan.changes]
        rows += list(plan.notes)
        rows.append(("Reason", reason))
        return rows

    async def _admin_write(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import platform_admin

        args, plan, reason = await self._admin_checked(tool_key, inputs)
        me = await self._me()
        firm = await self._firm()
        try:
            done = await self._admin_apply(tool_key, firm, me, args, reason)
        except platform_admin.AdminError as exc:
            raise self._admin_refusal(exc)
        return {"section": done.section, "record": {"id": done.record_id, "label": done.label},
                "changes": [{"field": c["field"], "from": c["from"], "to": c["to"]} for c in done.changes],
                "message": self._admin_said(tool_key, done, args)}

    @staticmethod
    def _admin_refusal(exc) -> Exception:
        said = str(exc)
        if getattr(exc, "status", None) == 404:
            said = f"{said.rstrip('.')} or access denied"
        return _refuse(said)

    async def _admin_checked(self, tool_key: str, inputs: dict[str, Any]):
        """The role, the reason, the inputs and every check the write makes, without writing."""
        from app.aurea_core import platform_admin

        # The gateway checks the catalogue's roles; this re-checks, as the other tools that change a control do.
        roles = WRITE_ROLES[tool_key]
        if self.role not in roles:
            raise _refuse(f"Only {OWNERS[roles]} may change this.")
        reason = _text(inputs.get("reason"))
        if tool_key in NEEDS_REASON and not reason:
            raise _refuse("Say why: the reason goes on the audit log.")
        firm = await self._firm()
        args = self._admin_args(tool_key, inputs)
        try:
            plan = await self._admin_prepare(tool_key, firm, args)
        except platform_admin.AdminError as exc:
            raise self._admin_refusal(exc)
        if tool_key not in ALWAYS_CHANGES and not plan.changes:
            raise _refuse(f"Already so: nothing about {plan.label} would change.")
        return args, plan, reason

    def _admin_args(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """The inputs, read as the write takes them. A value it cannot read is refused here."""
        g = inputs.get
        if tool_key == "update_firm_profile":
            fields = {k: _text(g(k)) for k in ("name", "legal_name", "regulator")}
            branding = {"primary": _text(g("primary_color")), "accent": _text(g("accent_color")),
                        "tagline": _text(g("tagline")), "logo_text": _text(g("logo_text"))}
            for key in ("primary", "accent"):
                if branding[key] and not re.fullmatch(r"#[0-9A-Fa-f]{6}", branding[key]):
                    raise _refuse(f"A colour is six hex digits after #, e.g. #1A2B3C; '{branding[key]}' is not.")
            fields = {k: v for k, v in fields.items() if v is not None}
            branding = {k: v for k, v in branding.items() if v is not None}
            if not fields and not branding:
                raise _refuse("Give what changes: the name, legal name, regulator, colours, tagline or logo text.")
            return {"fields": fields, "branding": branding}
        if tool_key == "set_agent_switches":
            args = {"agent_key": _text(g("agent_key")), "enabled": _flag(g("enabled"), "enabled"),
                    "default_tier": _text(g("tier")), "paused": _flag(g("paused"), "paused"),
                    "schedule_cron": _text(g("schedule_cron")), "schedule_enabled": _flag(g("schedule_enabled"), "schedule_enabled")}
            if all(v is None for k, v in args.items() if k != "agent_key"):
                raise _refuse("Give what changes: enabled, tier, paused, schedule_cron or schedule_enabled.")
            return args
        if tool_key == "invite_user":
            role = (_text(g("role")) or "").lower()
            if role == UserRole.CLIENT.value:
                raise _refuse("A client's sign-in is set up from their household, not invited here.")
            return {"email": _text(g("email")) or "", "full_name": _text(g("full_name")) or "", "role": role or None,
                    "title": _text(g("title"))}
        if tool_key == "change_user":
            return {"user_id": self._admin_id(g("user_id"), "user_id"), "full_name": _text(g("full_name")),
                    "role": _text(g("role")), "title": _text(g("title")), "is_active": _flag(g("active"), "active")}
        if tool_key == "save_segment":
            fee = _number(g("fee_tier_bps"), "fee_tier_bps")
            if fee is not None and fee != int(fee):
                raise _refuse("A fee rate is a whole number of basis points.")
            return {"segment_id": self._admin_id(g("segment_id"), "segment_id") if g("segment_id") else None,
                    "slug": _text(g("slug")), "label": _text(g("label")), "fee_tier_bps": int(fee) if fee is not None else None,
                    "min_aum_usd": _number(g("min_aum_usd"), "min_aum_usd"), "description": _text(g("description")),
                    "is_active": _flag(g("active"), "active")}
        if tool_key == "save_mandate_type":
            return {"mandate_type_id": self._admin_id(g("mandate_type_id"), "mandate_type_id") if g("mandate_type_id") else None,
                    "slug": _text(g("slug")), "label": _text(g("label")),
                    "default_autonomy_tier": _text(g("default_autonomy_tier")), "description": _text(g("description"))}
        if tool_key == "set_mandate_suitability":
            tolerance, horizon, kind = (_text(g(k)) for k in ("risk_tolerance", "investment_horizon", "mandate_type"))
            if tolerance and tolerance.lower() not in RISK_TOLERANCES:
                raise _refuse(f"A risk tolerance is one of {', '.join(RISK_TOLERANCES)}.")
            if horizon and horizon.lower() not in HORIZONS:
                raise _refuse(f"An investment horizon is one of {', '.join(HORIZONS)}.")
            if kind and kind.lower() not in {m.value for m in MandateType}:
                raise _refuse(f"A mandate type is one of {', '.join(m.value for m in MandateType)}.")
            return {"mandate_id": self._admin_id(g("mandate_id"), "mandate_id"),
                    "risk_tolerance": tolerance.lower() if tolerance else None,
                    "investment_horizon": horizon.lower() if horizon else None,
                    "max_equity": _number(g("max_equity"), "max_equity"), "esg_preference": _text(g("esg_preference")),
                    "mandate_type": kind.lower() if kind else None, "is_active": _flag(g("active"), "active")}
        if tool_key == "set_notification":
            return {"event_type": _text(g("event_type")) or "", "enabled": _flag(g("enabled"), "enabled"),
                    "recipients": _words(g("recipients"))}
        if tool_key == "add_research":
            from app.aurea_core.platform_admin import RESEARCH_TYPES

            kind = (_text(g("doc_type")) or "house_view").lower()
            if kind not in RESEARCH_TYPES:
                raise _refuse(f"A research document is one of {', '.join(RESEARCH_TYPES)}.")
            return {"title": _text(g("title")) or "", "body": _text(g("body")) or "", "doc_type": kind,
                    "summary": _text(g("summary")), "tags": _words(g("tags"))}
        if tool_key == "research_action":
            return {"doc_id": self._admin_id(g("doc_id"), "doc_id"), "action": (_text(g("action")) or "").lower()}
        if tool_key == "save_model_portfolio":
            return {"model_id": self._admin_id(g("model_id"), "model_id") if g("model_id") else None,
                    "name": _text(g("name")), "description": _text(g("description")),
                    "drift_band": _number(g("drift_band"), "drift_band"), "benchmark_symbol": _text(g("benchmark_symbol"))}
        if tool_key == "set_model_targets":
            raw = g("targets")
            if isinstance(raw, str):
                import json

                try:
                    raw = json.loads(raw)
                except ValueError:
                    raise _refuse("targets is a list of {asset_class, target_weight}.")
            if not isinstance(raw, list) or not raw:
                raise _refuse("Give the targets: one {asset_class, target_weight} per class.")
            classes = {a.value for a in AssetClass}
            targets, seen = [], set()
            for t in raw:
                cls = str((t or {}).get("asset_class") or "").strip().lower() if isinstance(t, dict) else ""
                if cls not in classes:
                    raise _refuse(f"'{cls}' is not an asset class. Classes: {', '.join(sorted(classes))}.")
                if cls in seen:
                    raise _refuse(f"{cls} is given twice.")
                seen.add(cls)
                weight = _number(t.get("target_weight"), "target_weight")
                if weight is None or not 0 <= weight <= 1:
                    raise _refuse("A target weight is a fraction between 0 and 1 (0.6 for 60%).")
                targets.append({"asset_class": cls, "target_weight": weight})
            return {"model_id": self._admin_id(g("model_id"), "model_id"), "targets": targets}
        if tool_key == "delete_model_portfolio":
            return {"model_id": self._admin_id(g("model_id"), "model_id")}
        if tool_key == "add_connector":
            key = _text(g("provider_key")) or ""
            if key.startswith("custom."):
                raise _refuse("A custom connector needs its address and credentials, so it is set up on the Connectors page.")
            return {"provider_key": key}
        if tool_key == "set_connector":
            from app.aurea_core import agent_controls

            cron = _text(g("sync_cron"))
            if cron is not None:
                try:
                    cron = agent_controls.valid_cron(cron)
                except agent_controls.ControlError as exc:
                    raise _refuse(str(exc))
            live = _flag(g("live"), "live")
            args = {"connector_id": self._admin_id(g("connector_id"), "connector_id"),
                    "use_mock": (not live) if live is not None else None, "enabled": _flag(g("enabled"), "enabled"),
                    "sync_cron": cron}
            if all(args[k] is None for k in ("use_mock", "enabled", "sync_cron")):
                raise _refuse("Give what changes: live, enabled or sync_cron.")
            return args
        if tool_key == "sync_connector":
            return {"connector_id": self._admin_id(g("connector_id"), "connector_id")}
        if tool_key == "set_cmas":
            return {"asset_class": _text(g("asset_class")) or "", "expected_return": _number(g("expected_return"), "expected_return"),
                    "volatility": _number(g("volatility"), "volatility")}
        if tool_key == "set_model_routing":
            return {"task": _text(g("task")) or "", "model": _text(g("model")) or ""}
        raise _refuse(f"No admin tool called '{tool_key}'.")

    @staticmethod
    def _admin_id(value: Any, name: str):
        import uuid

        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            raise _refuse(f"Invalid {name}: {value}")

    async def _admin_prepare(self, tool_key: str, firm, a: dict[str, Any]):
        from app.aurea_core import platform_admin as pa

        s = self.session
        if tool_key == "update_firm_profile":
            return pa.prepare_firm(firm, fields=a["fields"], branding=a["branding"])
        if tool_key == "set_agent_switches":
            return await pa.prepare_agent_switches(s, firm, a["agent_key"], enabled=a["enabled"], default_tier=a["default_tier"],
                                                   paused=a["paused"], schedule_cron=a["schedule_cron"],
                                                   schedule_enabled=a["schedule_enabled"])
        if tool_key == "invite_user":
            if not a["role"]:
                raise pa.AdminError("Give the person's role.")
            return await pa.prepare_invite(s, firm, email=a["email"], full_name=a["full_name"], role=a["role"], title=a["title"])
        if tool_key == "change_user":
            return await pa.prepare_user_change(s, firm, await self._me(), a["user_id"], full_name=a["full_name"], role=a["role"],
                                                title=a["title"], is_active=a["is_active"])
        if tool_key == "save_segment":
            return await pa.prepare_segment(s, firm, **a)
        if tool_key == "save_mandate_type":
            return await pa.prepare_mandate_type(s, firm, **a)
        if tool_key == "set_mandate_suitability":
            return await pa.prepare_mandate(s, firm, a["mandate_id"], **{k: v for k, v in a.items() if k != "mandate_id"})
        if tool_key == "set_notification":
            return await pa.prepare_notification(s, firm, **a)
        if tool_key == "add_research":
            return pa.prepare_research(**a)
        if tool_key == "research_action":
            plan = await pa.prepare_research_action(s, firm, a["doc_id"], a["action"])
            if a["action"] == "publish" and plan.record.status == "published":
                raise pa.AdminError(f"Already so: {plan.label} is published.")
            return plan
        if tool_key == "save_model_portfolio":
            return await pa.prepare_model_portfolio(s, firm, **a)
        if tool_key == "set_model_targets":
            return await pa.prepare_model_targets(s, firm, a["model_id"], a["targets"])
        if tool_key == "delete_model_portfolio":
            return await pa.prepare_model_delete(s, firm, a["model_id"])
        if tool_key == "add_connector":
            return pa.prepare_add_connector(a["provider_key"])
        if tool_key == "set_connector":
            plan = await pa.prepare_connector(s, firm, a["connector_id"], use_mock=a["use_mock"], enabled=a["enabled"],
                                              sync_cron=a["sync_cron"])
            if a["use_mock"] is False and plan.record.use_mock:
                if not plan.values["supports_live"]:
                    raise pa.AdminError(f"{plan.label} has no real feed built here; it stays a mock.")
                if missing := self._missing_settings(plan.record):
                    raise pa.AdminError(f"{', '.join(missing)} is not set. Enter it on the Connectors page first; it is never given here.")
            return plan
        if tool_key == "sync_connector":
            plan = await pa.prepare_sync(s, firm, a["connector_id"])
            if str(plan.record.status) == "disabled":
                raise pa.AdminError(f"{plan.label} is switched off. Switch it on first.")
            return plan
        if tool_key == "set_cmas":
            return pa.prepare_cma(firm, **a)
        if tool_key == "set_model_routing":
            return pa.prepare_model_routing(firm, a["task"], a["model"])
        raise pa.AdminError(f"No admin tool called '{tool_key}'.")

    @staticmethod
    def _missing_settings(connector) -> list[str]:
        """The labels of the settings a live feed needs that are not on file. Never their values."""
        from app.conduit.registry import get_provider_def

        pdef = get_provider_def(connector.provider_key)
        return [f.label for f in (pdef.config_schema if pdef else []) if f.required and not (connector.config or {}).get(f.key)]

    async def _admin_apply(self, tool_key: str, firm, me, a: dict[str, Any], reason: str | None):
        from app.aurea_core import platform_admin as pa

        s = self.session
        if tool_key == "update_firm_profile":
            return await pa.update_firm(s, firm, me, fields=a["fields"], branding=a["branding"], reason=reason)
        if tool_key == "set_agent_switches":
            return await pa.set_agent_switches(s, firm, me, a["agent_key"], enabled=a["enabled"], default_tier=a["default_tier"],
                                               paused=a["paused"], paused_reason=reason if a["paused"] else None,
                                               schedule_cron=a["schedule_cron"], schedule_enabled=a["schedule_enabled"],
                                               reason=reason)
        if tool_key == "invite_user":
            done, _token = await pa.invite_user(s, firm, me, email=a["email"], full_name=a["full_name"], role=a["role"],
                                                title=a["title"])
            return done   # the token stays with the admin's own route: never in a tool's result
        if tool_key == "change_user":
            return await pa.change_user(s, firm, me, a["user_id"], full_name=a["full_name"], role=a["role"], title=a["title"],
                                        is_active=a["is_active"], reason=reason)
        if tool_key == "save_segment":
            return await pa.save_segment(s, firm, me, **a, reason=reason)
        if tool_key == "save_mandate_type":
            return await pa.save_mandate_type(s, firm, me, **a, reason=reason)
        if tool_key == "set_mandate_suitability":
            return await pa.update_mandate(s, firm, me, a["mandate_id"], reason=reason,
                                           **{k: v for k, v in a.items() if k != "mandate_id"})
        if tool_key == "set_notification":
            return await pa.set_notification(s, firm, me, **a)
        if tool_key == "add_research":
            done, _chunks = await pa.add_research(s, firm, me, author=me.full_name, **a)
            return done
        if tool_key == "research_action":
            return await pa.research_action(s, firm, me, a["doc_id"], a["action"], reason=reason)
        if tool_key == "save_model_portfolio":
            return await pa.save_model_portfolio(s, firm, me, **a,
                                                 benchmark_reason=f"Changed in the conversation by {me.full_name or me.email}.")
        if tool_key == "set_model_targets":
            return await pa.set_model_targets(s, firm, me, a["model_id"], a["targets"])
        if tool_key == "delete_model_portfolio":
            return await pa.delete_model_portfolio(s, firm, me, a["model_id"])
        if tool_key == "add_connector":
            return await pa.add_connector(s, firm, me, a["provider_key"])
        if tool_key == "set_connector":
            return await pa.set_connector(s, firm, me, a["connector_id"], use_mock=a["use_mock"], enabled=a["enabled"],
                                          sync_cron=a["sync_cron"])
        if tool_key == "sync_connector":
            done, sync = await pa.sync_connector(s, firm, me, a["connector_id"])
            done.changes.append({"field": "status", "from": None, "to": sync.status})
            return done
        if tool_key == "set_cmas":
            return await pa.set_cma(s, firm, me, **a, reason=reason)
        if tool_key == "set_model_routing":
            return await pa.set_model_routing(s, firm, me, task=a["task"], model=a["model"], reason=reason)
        raise pa.AdminError(f"No admin tool called '{tool_key}'.")

    @staticmethod
    def _admin_said(tool_key: str, done, a: dict[str, Any]) -> str:
        n = len(done.changes)
        if tool_key == "invite_user":
            return (f"Invited {done.label}. They sign in once they accept the invite; the invite link is on the Users page "
                    "for an admin.")
        if tool_key == "add_research":
            return f"Added {done.label} as a draft."
        if tool_key == "add_connector":
            return f"Added {done.label}, running on mock data."
        if tool_key == "sync_connector":
            status = next((c["to"] for c in done.changes if c["field"] == "status"), None)
            count = next((c["to"] for c in done.changes if c["field"] == "records_ingested"), 0)
            return f"Synced {done.label}: {status}, {count} record(s)."
        if tool_key in ("delete_model_portfolio",) or (tool_key == "research_action" and a.get("action") == "delete"):
            return f"Deleted {done.label}."
        return f"Saved {n} change(s) to {done.label}."
