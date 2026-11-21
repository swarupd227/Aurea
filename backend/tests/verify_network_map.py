"""The network map and an RM's own LinkedIn export (B-201 internal part: aurea_core/network_map.py, linkedin_import.py).

    python -m tests.verify_network_map

Proves: the map around a prospect runs two links out over what a person recorded and what the platform holds as fact,
tells the two apart, carries each recorded link's evidence and stops at staff; a LinkedIn Connections.csv is read after
its notes, and a file that is not one is refused; the Confirm card says how many will be linked, matched and skipped;
a matching row (accent, case and a credential aside) becomes a "knows" link from the RM with its evidence and nothing
else from the file; a row matching nobody is counted and listed and never becomes a contact; one matching two people
is skipped; importing again adds nothing; the import opens a way in the firm did not have; a link from the export is
marked as such on the map; another firm's people are not found; a client holds none of it.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.aurea_core import linkedin_import, network_map
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.crm import CrmContact
from app.models.enums import UserRole
from app.models.graph import RelationshipEdge
from app.models.identity import AuditEvent
from tests import _sim_fixture

PASS, FAIL = [], []

EXPORT = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Dana,"Okafor, CPA",https://www.linkedin.com/in/x-dana,dana@example.com,Okafor & Partners,Principal,14 Mar 2021
Anders,Muller,https://www.linkedin.com/in/x-anders,,Muller Timber,Owner,19 Jun 2022
BINH,nguyen,https://www.linkedin.com/in/x-binh,,Nguyen Engineering,Director,02 Sep 2019
Sam,Lee,https://www.linkedin.com/in/x-sam,,Lee Co,Partner,01 Jan 2020
Priya,Raman,https://www.linkedin.com/in/x-priya,,Harbour Analytics,Head of Data,23 Jan 2023
Tom,Ellison,https://www.linkedin.com/in/x-tom,,Ellison Freight,Manager,11 Aug 2018
"""


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, linkedin_import.LinkedInImportError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:140]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            fx = await _sim_fixture.build(s)
            firm, sophie = fx.firm, fx.adviser
            ex = lambda u, f=firm: ToolExecutors(s, u.id, u.role, f.id)  # noqa: E731
            james, dana = fx.contacts["James Whitaker"], fx.contacts["Dana Okafor, CPA"]
            wei = fx.households["The Chen Family"]["persons"][0]
            rohan = CrmContact(id=uuid.uuid4(), firm_id=firm.id, full_name="Rohan Mehta", contact_type="prospect",
                               owner_user_id=sophie.id, is_active=True)
            anders = CrmContact(id=uuid.uuid4(), firm_id=firm.id, full_name="Anders Müller", contact_type="centre_of_influence",
                                owner_user_id=sophie.id, is_active=True)
            sam1 = CrmContact(id=uuid.uuid4(), firm_id=firm.id, full_name="Sam Lee", contact_type="prospect", is_active=True)
            sam2 = CrmContact(id=uuid.uuid4(), firm_id=firm.id, full_name="Sam Lee", contact_type="referral_source", is_active=True)
            s.add_all([rohan, anders, sam1, sam2])
            await s.flush()
            await ex(sophie).execute("record_relationship", {"from_type": "person", "from_id": str(wei.id), "to_type": "contact",
                                                             "to_id": str(james.id), "kind": "colleague", "closeness": "known",
                                                             "evidence": "Wei told Sophie they sit on the chamber board, 3 Nov"})
            await ex(sophie).execute("record_relationship", {"from_type": "contact", "from_id": str(dana.id), "to_type": "contact",
                                                             "to_id": str(rohan.id), "kind": "referred",
                                                             "evidence": "Rohan said Dana referred him, first call"})

            print("\n=== The map ===")
            m = await ex(sophie).execute("read_network_map", {"contact_id": str(james.id)})
            rings = {n["name"]: n["ring"] for n in m["nodes"]}
            check("the prospect at the centre, Wei one link out, his family and adviser two",
                  (rings.get("James Whitaker"), rings.get("Wei Chen"), rings.get("Mei Chen"), rings.get("Sophie Tran")), (0, 1, 2, 2))
            check("nothing beyond two links", max(n["ring"] for n in m["nodes"]), 2)
            check("staff end the map: Sophie's other clients are not on it", "Binh Nguyen" in rings, False)
            by = {tuple(sorted((e["a"], e["b"]))): e for e in m["edges"]}
            k = lambda t, i: f"{t}:{i}"  # noqa: E731
            rec = by[tuple(sorted((k("person", wei.id), k("contact", james.id))))]
            check("a recorded link says so, with its evidence and who recorded it",
                  (rec["recorded"], rec["evidence"][0]["text"], rec["evidence"][0]["by"], rec["linkedin"]),
                  (True, "Wei told Sophie they sit on the chamber board, 3 Nov", "Sophie Tran", False))
            mei = fx.households["The Chen Family"]["persons"][1]
            fact = by[tuple(sorted((k("person", wei.id), k("person", mei.id))))]
            check("a household is a fact the platform holds, not a recorded link", (fact["recorded"], fact["kinds"]), (False, ["household"]))
            check("Sophie is you on her own map", any(n["you"] for n in m["nodes"] if n["name"] == "Sophie Tran"), True)
            check("counts tell recorded from facts", (m["counts"]["recorded"], m["counts"]["facts"] > 0, m["counts"]["linkedin"]), (1, True, 0))
            await refused("another firm's contact is not found", ex(sophie, fx.other).execute("read_network_map", {"contact_id": str(james.id)}),
                          "not one of this firm")

            print("\n=== The LinkedIn export ===")
            await refused("a file that is not the export", ex(sophie).preview_change("import_linkedin_connections",
                          {"csv_text": "name,email\nA,b"}), "not linkedin's connections.csv")
            await refused("an export with no rows", ex(sophie).preview_change("import_linkedin_connections",
                          {"csv_text": "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"}), "no connections")
            card = {r["label"]: r["value"] for r in (await ex(sophie).preview_change("import_linkedin_connections", {"csv_text": EXPORT}))["rows"]}
            check("the card names how many will be linked, matched and skipped",
                  (card["Whose export"], card["Rows in the export"], card["Will be linked"], card["Matched"],
                   card["Skipped, matching nobody"], card["Skipped, matching two people"]),
                  ("Sophie Tran", "6", "3", "3", "2", "1"))
            check("it names them and the evidence", (card["Linking"], card["Evidence"]),
                  ("Dana Okafor, CPA, Anders Müller, Binh Nguyen", "LinkedIn connection since 14 Mar 2021, from Sophie Tran's own export"))
            contacts_before = (await s.execute(select(func.count()).select_from(CrmContact))).scalar_one()
            out = await ex(sophie).execute("import_linkedin_connections", {"csv_text": EXPORT})
            check("three linked, two unmatched listed, one ambiguous", (out["linked"], out["unmatched_names"], out["ambiguous_names"]),
                  (3, ["Priya Raman", "Tom Ellison"], ["Sam Lee"]))
            check("no contact is made from a row", (await s.execute(select(func.count()).select_from(CrmContact))).scalar_one(), contacts_before)
            edges = [e for e in (await s.execute(select(RelationshipEdge).where(RelationshipEdge.kind == "knows"))).scalars().all()
                     if (e.attributes or {}).get("source") == "linkedin_export"]
            d = next(e for e in edges if e.to_id == dana.id)
            check("a knows link from the RM, with its evidence and no closeness",
                  (d.from_type, d.from_id == sophie.id, d.attributes["evidence"], d.attributes["closeness"]),
                  ("user", True, "LinkedIn connection since 14 Mar 2021, from Sophie Tran's own export", None))
            check("nothing else from the file is kept: no email, no link, no company",
                  any(v for e in edges for key, v in e.attributes.items() if key in ("email", "url", "company")) or
                  any("example.com" in str(e.attributes) or "linkedin.com" in str(e.attributes) for e in edges), False)
            check("a client person matches too, case aside", any(e.to_type == "person" for e in edges), True)
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "relationship.linkedin_imported"))).scalars().all()
            check("the import is on the audit log, as counts", [(a.detail["to_link"], a.detail["unmatched"]) for a in audit], [(3, 2)])
            again = await ex(sophie).execute("import_linkedin_connections", {"csv_text": EXPORT})
            check("importing again adds nothing", (again["linked"], again["already"]), (0, 3))

            print("\n=== What it opens ===")
            ways = await ex(sophie).execute("find_introductions", {"contact_id": str(rohan.id)})
            best = ways["paths"][0] if ways["paths"] else None
            check("a way in to Rohan through Dana, asked by Sophie", (best and best["introducer"]["name"], best and best["ask"]["you"]),
                  ("Dana Okafor, CPA", True))
            m = await ex(sophie).execute("read_network_map", {"contact_id": str(rohan.id)})
            li = [e for e in m["edges"] if e["linkedin"]]
            check("the export's link is marked on the map, as recorded", (len(li), li[0]["recorded"] if li else None, m["simulated"]), (1, True, False))

            print("\n=== Who ===")
            check("a client holds none of it", any(k_ in tools_for_role(UserRole.CLIENT) for k_ in ("read_network_map", "import_linkedin_connections")), False)
            check("the import stops for a Confirm", TOOLS["import_linkedin_connections"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("the map changes nothing", TOOLS["read_network_map"].change_state, ToolChangeState.NO)
            await refused("another firm's staff cannot import here", linkedin_import.prepare(s, firm, await s.get(type(sophie), (
                await s.execute(select(type(sophie).id).where(type(sophie).firm_id == fx.other.id))).scalar_one()), EXPORT), "member of the firm's staff")
            check("the map module is depth two", network_map.DEPTH, 2)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
