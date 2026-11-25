"""A simulated LinkedIn connections export (B-225), standing in for the file an RM downloads from their own account.

`Connections.csv` beside this file is in LinkedIn's own format (its notes, then First Name, Last Name, URL, Email
Address, Company, Position, Connected On) for Sophie Tran, the demonstration adviser. Some rows match the demo firm's
contacts and clients, accent and credentials aside; the rest are invented people who match nobody and stay counted,
never contacts. Seeding imports it through the real import (`aurea_core/linkedin_import.py`) with every link tagged
`simulated:linkedin_export` in its attributes and no audit entry; discarding deletes exactly those links.
"""
from __future__ import annotations

import pathlib

from sqlalchemy import select

from app.aurea_core import linkedin_import
from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "linkedin_export"
RM_NAME = "Sophie Tran"
RM_NAME_IN = "Ananya Rao"          # the Indian demonstration firm's adviser (B-256)
FILE = pathlib.Path(__file__).with_name("Connections.csv")
FILE_IN = pathlib.Path(__file__).with_name("Connections_in.csv")


def install() -> None:
    """The import is a page upload and a tool; there is nothing to register. The file is read when seeding."""


def _india(firm) -> bool:
    from app.aurea_core import markets

    return markets.code(getattr(firm, "jurisdiction", None)) == "IN"


def export_text(firm=None) -> str:
    return (FILE_IN if firm is not None and _india(firm) else FILE).read_text(encoding="utf-8")


async def firms(session) -> list:
    from app.models.tenant import Firm

    return list((await session.execute(select(Firm).where(Firm.slug.in_(("demo", "india-demo"))))).scalars().all())


async def _rm(session, firm):
    name = RM_NAME_IN if _india(firm) else RM_NAME
    from app.models.identity import User

    return (await session.execute(select(User).where(User.firm_id == firm.id, User.full_name == name,
                                                     User.is_active.is_(True)))).scalars().first()


async def seed(session, firm) -> dict:
    await opt_in(session, firm, KEY)
    rm = await _rm(session, firm)
    if rm is None:
        return {"links": 0, "rm": None}
    out = await linkedin_import.import_connections(session, firm, rm, export_text(firm), source=tag(KEY), audit=False)
    return {"rm": rm.full_name, "rows": out["rows"], "links": out["linked"], "already": out["already"],
            "unmatched": out["unmatched"]}


async def discard(session, firm) -> dict:
    from app.models.graph import RelationshipEdge

    gone = 0
    for e in (await session.execute(select(RelationshipEdge).where(RelationshipEdge.firm_id == firm.id,
                                                                   RelationshipEdge.kind == "knows"))).scalars().all():
        if (e.attributes or {}).get("source") == tag(KEY):
            await session.delete(e)
            gone += 1
    await session.flush()
    was = enabled_for(firm, KEY)
    if was:
        await opt_out(session, firm, KEY)
    return {"links": gone, "opt_in": int(was)}


register(Simulation(
    key=KEY, name="Simulated LinkedIn export", stands_for="An RM's own LinkedIn connections export",
    seam="aurea_core.linkedin_import (the network map and the connections import, B-201 internal part)",
    install=install, seed=seed, discard=discard, firms=firms,
    notes=["Sophie Tran's invented Connections.csv; links tagged simulated:linkedin_export, no audit entry."],
))
