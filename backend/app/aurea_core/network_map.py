"""Who knows whom around a prospect or client, two links out (B-201, the internal part).

It draws the same graph the ways in are searched on (`introductions._graph`): the links a person recorded (by hand, or
from their own LinkedIn export, `linkedin_import.py`) and what the platform holds as fact - a family on the client
graph, the people in one household, the adviser recorded for a household, a CRM touch a member of staff logged. Each
edge says which it is, so a recorded link is never mistaken for a fact the platform holds, and a recorded link carries
its evidence. Staff are where a map stops: an adviser knows every one of their clients, and that is not who knows the
prospect. It reads; it records, introduces and sends nothing.
"""
from __future__ import annotations

from collections import deque

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import introductions
from app.models.graph import RelationshipEdge
from app.models.tenant import Firm

DEPTH = 2
MAX_NODES = 40
TYPE_LABEL = {"person": "client", "entity": "entity", "contact": "contact", "user": "staff"}


def _k(key: tuple) -> str:
    return f"{key[0]}:{key[1]}"


async def build(session: AsyncSession, firm: Firm, *, contact_id=None, person_id=None, name: str | None = None,
                depth: int = DEPTH) -> dict:
    from app.aurea_core import firm_time

    today = firm_time.today(firm)
    g = await introductions._graph(session, firm, today)
    centre = await introductions._target(session, firm, g, contact_id=contact_id, person_id=person_id, name=name)
    depth = max(1, min(int(depth or DEPTH), DEPTH))

    ring = {centre.key: 0}
    order = [centre.key]
    queue = deque([centre.key])
    truncated = 0
    while queue:
        here = queue.popleft()
        if ring[here] >= depth or (here[0] == "user" and here != centre.key):
            continue        # the map stops at staff, as a path does
        for ln in sorted(g.adj.get(here, []), key=lambda ln: (-ln.recorded, -ln.weight)):
            nxt = ln.b if ln.a == here else ln.a
            if nxt in ring:
                continue
            if len(order) >= MAX_NODES:
                truncated += 1
                continue
            ring[nxt] = ring[here] + 1
            order.append(nxt)
            queue.append(nxt)

    # Every link between two nodes on the map that the walk could see (one end inside the outer ring).
    pairs: dict[tuple, dict] = {}
    for key in order:
        for ln in g.adj.get(key, []):
            if ln.a not in ring or ln.b not in ring or min(ring[ln.a], ring[ln.b]) >= depth:
                continue
            pk = tuple(sorted((_k(ln.a), _k(ln.b))))
            e = pairs.setdefault(pk, {"a": pk[0], "b": pk[1], "labels": [], "kinds": [], "recorded": False,
                                      "evidence": [], "_ids": set(), "_seen": set()})
            sig = (ln.kind, ln.label, ln.edge_id)
            if sig in e["_seen"]:
                continue
            e["_seen"].add(sig)
            e["labels"].append(ln.label)
            e["kinds"].append(ln.kind)
            if ln.recorded:
                e["recorded"] = True
                if ln.edge_id:
                    e["_ids"].add(ln.edge_id)
                if ln.evidence:
                    e["evidence"].append({"text": ln.evidence, "by": ln.recorded_by, "on": ln.recorded_on})
    # Where each recorded link came from: a person's own word, or their LinkedIn export.
    ids = [i for e in pairs.values() for i in e["_ids"]]
    attrs = {}
    if ids:
        attrs = {r.id: (r.attributes or {}) for r in (await session.execute(select(RelationshipEdge).where(
            RelationshipEdge.firm_id == firm.id, RelationshipEdge.id.in_(ids)))).scalars().all()}
    edges = []
    for e in pairs.values():
        srcs = {str(attrs.get(i, {}).get("source") or "recorded") for i in e.pop("_ids")}
        e.pop("_seen")
        e["linkedin"] = any(s.endswith("linkedin_export") for s in srcs)
        e["simulated"] = any(s.startswith("simulated:") for s in srcs)
        edges.append(e)

    nodes = []
    for key in order:
        n = g.nodes[key]
        chips = [TYPE_LABEL[n.type]]
        if n.type in ("person", "entity") and n.household_id:
            chips = ["former client" if n.left else "client"] + ([] if n.type == "person" else ["entity"])
        if n.type == "contact" and n.contact_type:
            chips = [n.contact_type.replace("_", " ")]
        nodes.append({"key": _k(key), "type": n.type, "id": str(n.id), "name": n.name, "ring": ring[key], "chips": chips})
    recorded = sum(1 for e in edges if e["recorded"])
    return {"centre": {**centre.ref(), "key": _k(centre.key), "contact_type": centre.contact_type}, "depth": depth,
            "nodes": nodes, "edges": edges,
            "counts": {"nodes": len(nodes), "edges": len(edges), "recorded": recorded, "facts": len(edges) - recorded,
                       "linkedin": sum(1 for e in edges if e["linkedin"]), "truncated": truncated},
            "simulated": any(e["simulated"] for e in edges), "as_of": today.isoformat()}
