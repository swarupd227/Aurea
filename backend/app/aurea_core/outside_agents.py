"""Astra's tools for an outside agent, through MCP (B-166; docs/03_Architecture/astra-mcp-server.md).

The other direction of the Foundation's MCP: an agent outside Astra calls Astra's tools. It does so as one named person,
with a key that person made for it, so it reads exactly what that person may read, in their firm, and nothing more.

- **Off until the administrator switches it on** (agent control `outside_agents`, with a reason on the audit log). Off,
  no key is made and every key already made is refused.
- **A key is a person's.** Staff make their own, shown once; only its hash is kept. It expires (at most 90 days) and
  the person or the administrator revokes it, with a reason. A client and the platform owner make none.
- **The tools are the conversation's**, the same catalogue and the same role check (`core/tools.validate_tool_call`),
  less those that only work beside the conversation (`NOT_OFFERED`). Firm scope is the person's.
- **A state-changing tool stops at a Confirm card for that person**, put on a conversation of its own ("Outside agent:
  <name>") and in Needs you, exactly as the conversation's own: checked when it is put (the same preview, refused with
  its reason if it would only fail), held server-side, carried out at most once and only when the person presses
  Confirm in Astra. The agent can read whether it was confirmed (`confirmation_status`); it cannot confirm, because
  its key opens nothing but the MCP endpoint and the confirm route takes a person's sign-in.
- **Every call is on the audit log**, with the key, the tool and whether it worked; never its inputs or its result.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import foundation
from app.core.db import utcnow
from app.core.tools import ToolChangeState, anthropic_tool_schema, get_tool, tools_for_role, validate_tool_call
from app.models.enums import UserRole
from app.models.identity import AuditEvent, User
from app.models.outside_agent import OutsideAgentAccess
from app.models.tenant import Firm
from app.models.thread import (
    MessageRole, PendingAction, PendingActionStatus, Thread, ThreadKind, ThreadMessage, ToolCall, ToolCallStatus,
)

SWITCH = "outside_agents"              # the agent control (core/foundation.DEFAULTS, rulebook.SETTINGS), administrator's
PREFIX = "astra_oa_"
MAX_DAYS = 90
DEFAULT_DAYS = 30
CONFIRM_HOURS = 24                     # as long as a conversation's confirmation waits (llm/gateway)
NOT_OFFERED = frozenset({"open_view"})  # opens a pane beside the conversation, which an outside agent has not got
STATUS_TOOL = "confirmation_status"
NO_ROLES = (UserRole.CLIENT, UserRole.SUPERADMIN)
THREAD_TITLE = "Outside agent: {label}"


class OutsideAgentError(Exception):
    """Refused, with the reason a person can act on. Never carries a key."""


class Forbidden(OutsideAgentError):
    """A tool the person's role does not hold: refused, and counted as the conversation counts it."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def switched_on(firm: Firm) -> bool:
    return bool(foundation.policy(firm).get(SWITCH))


def _audit(session: AsyncSession, firm_id, actor_id, actor_email: str, event: str, subject: str | None,
           detail: dict | None) -> None:
    session.add(AuditEvent(firm_id=firm_id, actor_id=actor_id, actor_email=actor_email,
                           event_type=f"outside_agent.{event}", subject=subject, detail=detail))


def state(row: OutsideAgentAccess, now: datetime | None = None) -> str:
    now = now or utcnow()
    if row.revoked_at:
        return "revoked"
    if _aware(row.expires_at) <= now:
        return "expired"
    return "active"


def out(row: OutsideAgentAccess, owner: str | None = None) -> dict:
    return {"id": str(row.id), "label": row.label, "key_ends": row.token_hint, "state": state(row),
            "owner": owner, "made": row.created_at.isoformat() if row.created_at else None,
            "expires": _aware(row.expires_at).isoformat(), "last_used": row.last_used_at.isoformat()
            if row.last_used_at else None, "calls": row.calls or 0,
            "revoked_by": row.revoked_by, "revoked_reason": row.revoked_reason,
            "thread_id": str(row.thread_id) if row.thread_id else None}


# ── Keys ──────────────────────────────────────────────────────────────────────────────────────────

def prepare_issue(firm: Firm, user: User, *, label: Any, days: Any = DEFAULT_DAYS) -> dict:
    if user.role in NO_ROLES:
        raise OutsideAgentError("Only a firm's staff give an outside agent a key.")
    if not switched_on(firm):
        raise OutsideAgentError("Outside agents are off for this firm. The administrator switches them on "
                                "(agent control outside_agents), with a reason.")
    name = " ".join(str(label or "").split())[:120]
    if not name:
        raise OutsideAgentError("Name the agent this key is for.")
    try:
        n = int(days)
    except (TypeError, ValueError):
        raise OutsideAgentError("Say how many days the key lasts.")
    if not 1 <= n <= MAX_DAYS:
        raise OutsideAgentError(f"A key lasts between 1 and {MAX_DAYS} days.")
    return {"label": name, "days": n}


async def issue(session: AsyncSession, firm: Firm, user: User, *, label: Any, days: Any = DEFAULT_DAYS
                ) -> tuple[OutsideAgentAccess, str]:
    """A new key for this person's outside agent. The key is returned here once and never again."""
    p = prepare_issue(firm, user, label=label, days=days)
    token = PREFIX + secrets.token_urlsafe(32)
    row = OutsideAgentAccess(firm_id=firm.id, user_id=user.id, label=p["label"], token_hash=_hash(token),
                             token_hint=token[-4:], expires_at=utcnow() + timedelta(days=p["days"]), calls=0)
    session.add(row)
    await session.flush()
    _audit(session, firm.id, user.id, user.email, "issued", p["label"], {"access_id": str(row.id), "days": p["days"]})
    return row, token


async def listing(session: AsyncSession, firm: Firm, user: User) -> list[dict]:
    """The person's own keys; the administrator sees every key in the firm, with whose it is."""
    q = select(OutsideAgentAccess, User.full_name).join(User, User.id == OutsideAgentAccess.user_id).where(
        OutsideAgentAccess.firm_id == firm.id)
    if user.role != UserRole.ADMIN:
        q = q.where(OutsideAgentAccess.user_id == user.id)
    rows = (await session.execute(q.order_by(OutsideAgentAccess.created_at.desc()))).all()
    admin = user.role == UserRole.ADMIN
    return [out(r, owner if admin else None) for r, owner in rows]


async def _owned(session: AsyncSession, firm: Firm, actor: User, access_id: Any) -> OutsideAgentAccess:
    try:
        rid = uuid.UUID(str(access_id))
    except (TypeError, ValueError):
        raise OutsideAgentError("No such outside agent key.")
    row = await session.get(OutsideAgentAccess, rid)
    if row is None or row.firm_id != firm.id:
        raise OutsideAgentError("No such outside agent key.")
    if row.user_id != actor.id and actor.role != UserRole.ADMIN:
        raise OutsideAgentError("Only the person whose key it is, or the administrator, revokes it.")
    return row


async def prepare_revoke(session: AsyncSession, firm: Firm, actor: User, *, access_id: Any, reason: Any
                         ) -> tuple[OutsideAgentAccess, str]:
    row = await _owned(session, firm, actor, access_id)
    if row.revoked_at:
        raise OutsideAgentError(f"The key for {row.label} is already revoked.")
    why = " ".join(str(reason or "").split())[:400]
    if not why:
        raise OutsideAgentError("Give the reason for revoking it.")
    return row, why


async def revoke(session: AsyncSession, firm: Firm, actor: User, *, access_id: Any, reason: Any) -> dict:
    row, why = await prepare_revoke(session, firm, actor, access_id=access_id, reason=reason)
    row.revoked_at, row.revoked_by, row.revoked_reason = utcnow(), actor.full_name or actor.email, why
    # Its waiting confirmations go with it: nothing is carried out from a key that has been withdrawn.
    if row.thread_id:
        for p in (await session.execute(select(PendingAction).where(
                PendingAction.thread_id == row.thread_id,
                PendingAction.status == PendingActionStatus.AWAITING_CONFIRMATION))).scalars().all():
            p.status = PendingActionStatus.REJECTED
    _audit(session, firm.id, actor.id, actor.email, "revoked", row.label, {"access_id": str(row.id), "reason": why})
    await session.flush()
    return out(row)


async def authenticate(session: AsyncSession, token: str | None) -> tuple[OutsideAgentAccess, User, Firm]:
    """The key, its person and their firm, or refused. Checked on every request, so a revoked key, a person
    deactivated, a role changed to one that makes no keys, or the switch turned off stops the agent at once."""
    if not token or not token.startswith(PREFIX):
        raise OutsideAgentError("An outside agent key is needed.")
    row = (await session.execute(select(OutsideAgentAccess).where(
        OutsideAgentAccess.token_hash == _hash(token)))).scalar_one_or_none()
    if row is None:
        raise OutsideAgentError("This key is not recognised.")
    if row.revoked_at:
        raise OutsideAgentError("This key has been revoked.")
    if _aware(row.expires_at) <= utcnow():
        raise OutsideAgentError("This key has expired. Its person makes a new one in Astra.")
    user = await session.get(User, row.user_id)
    firm = await session.get(Firm, row.firm_id)
    if user is None or not user.is_active or user.firm_id != row.firm_id:
        raise OutsideAgentError("The person this key belongs to cannot sign in.")
    if user.role in NO_ROLES:
        raise OutsideAgentError("The person this key belongs to no longer gives keys.")
    if firm is None or firm.is_active is False:
        raise OutsideAgentError("This firm's access has been switched off.")
    if not switched_on(firm):
        raise OutsideAgentError("Outside agents are off for this firm.")
    return row, user, firm


# ── Tools ─────────────────────────────────────────────────────────────────────────────────────────

def _changes(tool) -> bool:
    return tool.change_state != ToolChangeState.NO


def offered(role: UserRole) -> dict:
    return {k: t for k, t in tools_for_role(role).items() if k not in NOT_OFFERED}


def mcp_tools(user: User) -> list[dict]:
    """The person's tools as MCP lists them: name, description, input schema, and whether it changes anything."""
    tools = []
    for t in offered(UserRole(user.role)).values():
        s = anthropic_tool_schema(t)
        changes = _changes(t)
        desc = s["description"]
        if changes:
            desc += (f" Changes something: the call returns a confirmation put to {user.full_name} in Astra, and "
                     f"nothing happens until they confirm it there. Read its outcome with {STATUS_TOOL}.")
        tools.append({"name": s["name"], "title": t.name, "description": desc, "inputSchema": s["input_schema"],
                      "annotations": {"title": t.name, "readOnlyHint": not changes, "destructiveHint": False,
                                      "idempotentHint": not changes, "openWorldHint": False}})
    tools.append({"name": STATUS_TOOL, "title": "Confirmation status",
                  "description": "Whether a confirmation this agent asked for was confirmed, declined or lapsed, "
                                 "and what the change returned once carried out.",
                  "inputSchema": {"type": "object", "properties": {"confirmation_id": {
                      "type": "string", "description": "The confirmation_id a state-changing call returned"}},
                      "required": ["confirmation_id"]},
                  "annotations": {"title": "Confirmation status", "readOnlyHint": True, "openWorldHint": False}})
    return tools


async def _thread(session: AsyncSession, row: OutsideAgentAccess) -> Thread:
    """The conversation its confirmations are put on: one per key, the person's own, so it is in their list."""
    if row.thread_id:
        thread = await session.get(Thread, row.thread_id)
        if thread is not None:
            return thread
    thread = Thread(firm_id=row.firm_id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=row.user_id,
                    title=THREAD_TITLE.format(label=row.label)[:255])
    session.add(thread)
    await session.flush()
    row.thread_id = thread.id
    return thread


async def _status(session: AsyncSession, row: OutsideAgentAccess, args: dict) -> dict:
    try:
        pid = uuid.UUID(str(args.get("confirmation_id")))
    except (TypeError, ValueError):
        raise OutsideAgentError("Give the confirmation_id a state-changing call returned.")
    from app.llm import pending as pending_mod

    p = await session.get(PendingAction, pid)
    if p is None or not row.thread_id or p.thread_id != row.thread_id:
        raise OutsideAgentError("No such confirmation for this key.")
    await pending_mod.lapse(session, thread_ids=[row.thread_id])
    call = await session.get(ToolCall, p.tool_call_id)
    status = p.status.value if hasattr(p.status, "value") else str(p.status)
    res: dict[str, Any] = {"confirmation_id": str(p.id), "status": status,
                           "tool": call.tool_key if call else None}
    if call and call.status == ToolCallStatus.DONE:
        res["outcome"] = "done"
        res["result"] = call.result
    elif call and call.status == ToolCallStatus.FAILED and status == PendingActionStatus.CONFIRMED.value:
        res["outcome"] = "failed"
        res["error"] = call.error
    elif status == PendingActionStatus.CONFIRMED.value:
        res["outcome"] = "running"
    else:
        res["outcome"] = {"awaiting_confirmation": "waiting", "rejected": "not done",
                          "expired": "not done"}.get(status, status)
    return res


async def call(session: AsyncSession, row: OutsideAgentAccess, user: User, firm: Firm, name: str,
               args: dict | None) -> dict:
    """One tool call from the outside agent, as its person. A read answers now; a change is put to the person."""
    from app.aurea_core import firm_time
    from app.llm.executors import ToolExecutors

    args = dict(args or {})
    role = UserRole(user.role)
    if name == STATUS_TOOL:
        return await _status(session, row, args)
    if name in NOT_OFFERED:
        raise OutsideAgentError(f"'{name}' works only beside Astra's own conversation.")
    valid, error = validate_tool_call(role, name, args)
    if not valid:
        tool = get_tool(name)
        raise (Forbidden if tool and role not in tool.roles_required else OutsideAgentError)(error)
    tool = get_tool(name)
    ex = ToolExecutors(session, user.id, role, firm.id)
    if not _changes(tool):
        result = firm_time.on_firm_clock(await ex.execute(name, args), firm)
        return json.loads(json.dumps(result, default=str))
    # A change: checked now as the conversation checks it, then held for the person.
    preview = (await ex.preview_run_agent(args, record_refusal=True) if name == "run_agent"
               else await ex.preview_change(name, args))
    thread = await _thread(session, row)
    message = ThreadMessage(thread_id=thread.id, role=MessageRole.ASTRA, suggestions=[],
                            text=f"{row.label}, an outside agent using your key, asks to {tool.name.lower()}. "
                                 f"Nothing happens unless you confirm it.")
    session.add(message)
    await session.flush()
    tc = ToolCall(message_id=message.id, tool_key=name, inputs=args, status=ToolCallStatus.PENDING)
    session.add(tc)
    await session.flush()
    pending = PendingAction(thread_id=thread.id, tool_call_id=tc.id,
                            confirmation_text=tool.confirmation or f"Confirm {tool.name}?",
                            shown=preview, requires_user_id=user.id,
                            expires_at=datetime.utcnow() + timedelta(hours=CONFIRM_HOURS))
    session.add(pending)
    thread.updated_at = datetime.utcnow()
    await session.flush()
    return {"status": "awaiting_confirmation", "confirmation_id": str(pending.id),
            "put_to": user.full_name, "confirmation": pending.confirmation_text,
            "shown": json.loads(json.dumps(preview, default=str)) if preview else None,
            "expires_in_hours": CONFIRM_HOURS,
            "note": f"Nothing has changed. {user.full_name} confirms or declines this in Astra; "
                    f"read the outcome with {STATUS_TOOL}."}


async def note_forbidden(session: AsyncSession, row: OutsideAgentAccess, user: User, name: str, error: str) -> None:
    """The zero-tolerance count the conversation keeps (ai_governance.record_violation), for an outside agent too."""
    from app.aurea_core.ai_governance import record_violation

    await record_violation(session, row.firm_id, actor_user_id=user.id, actor_role=str(UserRole(user.role)),
                           violation_type="tool_role_forbidden", subject_key=name[:64],
                           detail=f"outside agent {row.label}: {error}")


def note_call(session: AsyncSession, row: OutsideAgentAccess, user: User, name: str, ok: bool) -> None:
    """Every call on the audit log and on the key: which tool, whether it worked. Never its inputs or result."""
    row.calls = (row.calls or 0) + 1
    row.last_used_at = utcnow()
    _audit(session, row.firm_id, user.id, user.email, "tool_call", name,
           {"access_id": str(row.id), "agent": row.label, "ok": ok})
