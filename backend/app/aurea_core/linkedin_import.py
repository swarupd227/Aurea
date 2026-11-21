"""An RM's own LinkedIn connections, from the export they downloaded themselves (B-201, the internal part).

LinkedIn's terms bar scraping, so the only route here is the file a member exports from their own account
(Settings > Data privacy > Get a copy of your data > Connections): `Connections.csv`, a few lines of notes and then
the columns First Name, Last Name, URL, Email Address, Company, Position, Connected On.

A row whose name matches one of the firm's contacts or clients (accent and case aside, and a credential after a comma
set aside: "Dana Okafor, CPA") becomes a "knows" link from the RM to them, with its evidence: "LinkedIn connection since
<date>, from <RM>'s own export". Closeness is not said, so none is recorded. A row that matches two people is skipped
and listed; a row that matches nobody is counted and listed and never becomes a contact; one already linked is left as
it is. Nothing else in the file is kept: no email, no URL, no company.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.introductions import Node, plain
from app.models.crm import CrmContact
from app.models.graph import Person, RelationshipEdge
from app.models.identity import User
from app.models.tenant import Firm

COLUMNS = ("First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On")
SOURCE = "linkedin_export"
MAX_BYTES = 5_000_000
LISTED = 50            # unmatched rows listed by name; the rest are counted


class LinkedInImportError(Exception):
    """Refused, with a reason fit to show the person."""


def _key(name: str) -> str:
    return " ".join(plain((name or "").split(",")[0]).split())


def _date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse(text: str) -> list[dict]:
    """The rows of a Connections.csv, after its notes. Refuses a file that is not one."""
    if not text or not text.strip():
        raise LinkedInImportError("The file is empty.")
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise LinkedInImportError("That file is larger than a connections export; give LinkedIn's Connections.csv.")
    lines = text.lstrip("﻿").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip().lower().startswith("first name,last name")), None)
    if start is None:
        raise LinkedInImportError("This is not LinkedIn's Connections.csv: no header row First Name, Last Name, URL, Email "
                           "Address, Company, Position, Connected On.")
    rows = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        first, last = (r.get("First Name") or "").strip(), (r.get("Last Name") or "").strip()
        name = f"{first} {last}".strip()
        if not name:
            continue
        rows.append({"name": name, "company": (r.get("Company") or "").strip() or None,
                     "position": (r.get("Position") or "").strip() or None, "connected_on": _date(r.get("Connected On") or "")})
    if not rows:
        raise LinkedInImportError("The export has its header but no connections.")
    return rows


def evidence(rm_name: str, connected_on: date | None) -> str:
    since = f" since {connected_on.day} {connected_on:%b %Y}" if connected_on else ""
    return f"LinkedIn connection{since}, from {rm_name}'s own export"


async def prepare(session: AsyncSession, firm: Firm, rm: User, csv_text: str) -> dict:
    """What importing would do, before its Confirm card: who would be linked, matched, skipped."""
    if rm is None or rm.firm_id != firm.id or str(rm.role) in ("client", "superadmin"):
        raise LinkedInImportError("Connections are imported by a member of the firm's staff, from their own export.")
    rows = parse(csv_text)
    index: dict[str, list[Node]] = {}
    for c in (await session.execute(select(CrmContact).where(CrmContact.firm_id == firm.id, CrmContact.is_active.is_(True)))).scalars().all():
        index.setdefault(_key(c.full_name), []).append(Node("contact", c.id, c.full_name, c.household_id, contact_type=c.contact_type))
    for p in (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.deceased_on.is_(None)))).scalars().all():
        index.setdefault(_key(p.full_name), []).append(Node("person", p.id, p.full_name, p.household_id))
    linked = {(e.to_type, e.to_id) for e in (await session.execute(select(RelationshipEdge).where(
        RelationshipEdge.firm_id == firm.id, RelationshipEdge.kind == "knows", RelationshipEdge.from_type == "user",
        RelationshipEdge.from_id == rm.id))).scalars().all()}
    linked |= {(e.from_type, e.from_id) for e in (await session.execute(select(RelationshipEdge).where(
        RelationshipEdge.firm_id == firm.id, RelationshipEdge.kind == "knows", RelationshipEdge.to_type == "user",
        RelationshipEdge.to_id == rm.id))).scalars().all()}
    to_link, already, unmatched, ambiguous, seen = [], [], [], [], set()
    for r in rows:
        hits = index.get(_key(r["name"]), [])
        if not hits:
            unmatched.append({"name": r["name"], "company": r["company"], "position": r["position"]})
        elif len(hits) > 1:
            ambiguous.append({"name": r["name"], "matches": [f"{h.name} ({h.type})" for h in hits[:4]]})
        elif hits[0].key in linked or hits[0].key in seen:
            already.append({"name": hits[0].name, "type": hits[0].type})
        else:
            seen.add(hits[0].key)
            to_link.append({"node": hits[0], "connected_on": r["connected_on"], "row_name": r["name"]})
    return {"rm": rm, "rows": len(rows), "to_link": to_link, "already": already, "unmatched": unmatched,
            "ambiguous": ambiguous, "matched": len(to_link) + len(already)}


def summary(p: dict) -> dict:
    return {"rm": p["rm"].full_name, "rows": p["rows"], "matched": p["matched"], "to_link": len(p["to_link"]),
            "already": len(p["already"]), "unmatched": len(p["unmatched"]), "ambiguous": len(p["ambiguous"]),
            "linking": [{"name": t["node"].name, "type": t["node"].type,
                         "connected_on": t["connected_on"].isoformat() if t["connected_on"] else None} for t in p["to_link"]],
            "unmatched_names": [u["name"] for u in p["unmatched"][:LISTED]],
            "ambiguous_names": [a["name"] for a in p["ambiguous"]]}


async def import_connections(session: AsyncSession, firm: Firm, rm: User, csv_text: str, *, source: str = SOURCE,
                             audit: bool = True) -> dict:
    """Record a "knows" link from the RM to each matched contact or client. `source` and `audit` are for a simulation,
    whose links are tagged so it can take them away and whose seed writes no audit entry (app/simulations)."""
    from app.aurea_core import firm_time
    from app.models.identity import AuditEvent

    p = await prepare(session, firm, rm, csv_text)
    today = firm_time.today(firm).isoformat()
    for t in p["to_link"]:
        n = t["node"]
        session.add(RelationshipEdge(firm_id=firm.id, kind="knows", from_type="user", from_id=rm.id, to_type=n.type, to_id=n.id,
                                     attributes={"closeness": None, "how": "LinkedIn connection",
                                                 "evidence": evidence(rm.full_name, t["connected_on"]),
                                                 "recorded_by": rm.full_name, "recorded_by_id": str(rm.id), "recorded_on": today,
                                                 "connected_on": t["connected_on"].isoformat() if t["connected_on"] else None,
                                                 "source": source}))
    out = summary(p)
    if audit:
        session.add(AuditEvent(firm_id=firm.id, actor_id=rm.id, actor_email=rm.email, event_type="relationship.linkedin_imported",
                               subject=f"{rm.full_name}'s LinkedIn connections",
                               detail={k: out[k] for k in ("rows", "matched", "to_link", "already", "unmatched", "ambiguous")}))
    await session.flush()
    out["linked"] = out["to_link"]
    out["recorded_on"] = today
    return out
