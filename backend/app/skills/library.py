"""A firm's skills: who sees one, who may change it, and the changes themselves.

The Skills page and the conversation's skill tools both go through here. A skill is owned by its creator
and private by default; it can be shared with named colleagues or made public to the firm. Anyone who can
see one can copy it; only its owner can edit or delete it. Skills are assistive: a skill is capped at
Tier 2 (models/skill.py), which the route did not check until this module held the rule.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.identity import User
from app.models.skill import Skill

SCOPES = ("household", "book")
OUTPUT_KINDS = ("insight", "task", "note")
TIERS = ("tier_1", "tier_2")
VISIBILITIES = ("private", "shared", "public")
FIELDS = ("name", "description", "instruction", "scope", "output_kind", "default_tier", "enabled", "visibility", "shared_with")


class SkillError(ValueError):
    """A change that cannot be made, with the reason."""


class SkillForbidden(SkillError):
    """The caller may not do this to the skill."""


def visible(s: Skill, user: User) -> bool:
    return (s.created_by == user.id or s.visibility == "public"
            or (s.visibility == "shared" and str(user.id) in (s.shared_with or [])))


async def owners(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    return {u.id: u.full_name for u in (await session.execute(select(User).where(User.firm_id == firm_id))).scalars().all()}


def as_dict(s: Skill, user: User, names: dict) -> dict:
    mine = s.created_by == user.id
    return {
        "id": str(s.id), "name": s.name, "description": s.description, "instruction": s.instruction,
        "scope": s.scope, "output_kind": s.output_kind, "default_tier": s.default_tier,
        "enabled": s.enabled, "visibility": s.visibility, "shared_with": s.shared_with or [],
        "shared_with_names": [names.get(_uuid(x), "—") for x in (s.shared_with or [])],
        "owner_id": str(s.created_by) if s.created_by else None,
        "owner_name": "Firm library" if not s.created_by else names.get(s.created_by, "—"),
        "mine": mine, "can_edit": mine,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _uuid(x):
    try:
        return uuid.UUID(str(x))
    except ValueError:
        return None


async def list_for(session: AsyncSession, firm_id: uuid.UUID, user: User) -> list[dict]:
    rows = (await session.execute(select(Skill).where(Skill.firm_id == firm_id).order_by(Skill.created_at.desc()))).scalars().all()
    names = await owners(session, firm_id)
    return [as_dict(s, user, names) for s in rows if visible(s, user)]


async def colleagues(session: AsyncSession, firm_id: uuid.UUID, user: User) -> list[User]:
    rows = (await session.execute(select(User).where(User.firm_id == firm_id, User.role != UserRole.CLIENT))).scalars().all()
    return [u for u in rows if u.id != user.id]


async def resolve_colleagues(session: AsyncSession, firm_id: uuid.UUID, user: User, given: list[str]) -> list[str]:
    """Colleague ids from ids, names or emails, each matching exactly one staff colleague in the firm."""
    staff = await colleagues(session, firm_id, user)
    out = []
    for g in given:
        g = str(g).strip()
        if not g:
            continue
        hits = [u for u in staff if str(u.id) == g or u.email.lower() == g.lower() or u.full_name.lower() == g.lower()]
        if not hits:
            hits = [u for u in staff if g.lower() in u.full_name.lower()]
        if len(hits) != 1:
            raise SkillError(f"{g} is {'not a colleague' if not hits else 'more than one colleague'} here; give their full name or email.")
        out.append(str(hits[0].id))
    return list(dict.fromkeys(out))


def check(fields: dict) -> dict:
    """The fields, with each one checked against what a skill may be."""
    out = {k: v for k, v in fields.items() if k in FIELDS and v is not None}
    for key, allowed in (("scope", SCOPES), ("output_kind", OUTPUT_KINDS), ("default_tier", TIERS), ("visibility", VISIBILITIES)):
        if key in out and out[key] not in allowed:
            extra = " Skills are assistive and stop at Tier 2." if key == "default_tier" else ""
            raise SkillError(f"{key.replace('_', ' ').capitalize()} must be one of: {', '.join(allowed)}.{extra}")
    for key in ("name", "instruction"):
        if key in out:
            out[key] = str(out[key]).strip()
            if not out[key]:
                raise SkillError(f"A skill needs a {key}.")
    if "name" in out and len(out["name"]) > 120:
        raise SkillError("A skill's name is at most 120 characters.")
    if out.get("visibility") == "shared" and not out.get("shared_with") and "shared_with" in fields:
        raise SkillError("A shared skill names the colleagues it is shared with.")
    return out


async def get(session: AsyncSession, firm_id: uuid.UUID, skill_id: uuid.UUID) -> Skill:
    s = await session.get(Skill, skill_id)
    if s is None or s.firm_id != firm_id:
        raise SkillError("Skill not found.")
    return s


async def owned(session: AsyncSession, firm_id: uuid.UUID, skill_id: uuid.UUID, user: User, doing: str) -> Skill:
    s = await get(session, firm_id, skill_id)
    if s.created_by != user.id:
        raise SkillForbidden(f"Only the skill's owner can {doing} it.")
    return s


async def create(session: AsyncSession, firm_id: uuid.UUID, user: User, fields: dict) -> Skill:
    values = check(fields)
    if "name" not in values or "instruction" not in values:
        raise SkillError("A skill needs a name and an instruction.")
    s = Skill(firm_id=firm_id, created_by=user.id, **values)
    session.add(s)
    await session.flush()
    return s


async def update(session: AsyncSession, firm_id: uuid.UUID, skill_id: uuid.UUID, user: User, fields: dict) -> Skill:
    s = await owned(session, firm_id, skill_id, user, "edit")
    for k, v in check(fields).items():
        setattr(s, k, v)
    if s.visibility == "private":
        s.shared_with = []
    await session.flush()
    return s


async def delete(session: AsyncSession, firm_id: uuid.UUID, skill_id: uuid.UUID, user: User) -> dict:
    s = await owned(session, firm_id, skill_id, user, "delete")
    gone = {"id": str(s.id), "name": s.name}
    await session.delete(s)
    await session.flush()
    return gone


async def clone(session: AsyncSession, firm_id: uuid.UUID, skill_id: uuid.UUID, user: User) -> Skill:
    s = await get(session, firm_id, skill_id)
    if not visible(s, user):
        raise SkillForbidden("You don't have access to this skill.")
    copy = Skill(firm_id=firm_id, created_by=user.id, name=f"{s.name} (copy)"[:120], description=s.description,
                 instruction=s.instruction, scope=s.scope, output_kind=s.output_kind,
                 default_tier=s.default_tier if s.default_tier in TIERS else "tier_1",
                 enabled=True, visibility="private", shared_with=[])
    session.add(copy)
    await session.flush()
    return copy
