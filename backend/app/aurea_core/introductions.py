"""The way in to a prospect, from people the firm already knows (B-200).

A person records who knows whom, from something they were told or saw: "Wei Chen sits on the chamber board with James
Whitaker", "Dana Okafor referred Rohan Mehta". Each link is a `RelationshipEdge` of one of `LINK_KINDS`, between two of
a client (person), a legal entity, a pipeline contact or one of the firm's own people, and it carries how they know
each other, how close they are if someone said, the evidence and who recorded it. Nothing here infers a link: a
"Referred by the Chens" in a contact's notes is not one until a person records it.

`paths` searches those links, together with what the platform already holds as fact - a family recorded on the client
graph, the people in one household, the adviser recorded for a household, and a CRM touch a staff member logged - for
the ways a member of staff can reach a target through someone in between. Each path is scored by how close each link
is, how recent it is, and the introducer's own standing with the firm (a client, someone who has referred before, a
centre of influence), and names who should ask whom. It recommends an introduction; it introduces nobody, sends
nothing and logs nothing.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crm import CrmActivity, CrmContact
from app.models.graph import Household, LegalEntity, Person, RelationshipEdge
from app.models.identity import User
from app.models.tenant import Firm

# The kinds a person records. "referred" runs from the one who referred to the one referred.
LINK_KINDS = ("knows", "referred", "colleague", "professional")
KIND_LABEL = {"knows": "knows", "referred": "referred", "colleague": "works with", "professional": "advises"}
CLOSENESS = {"close": 0.9, "known": 0.65, "met": 0.35}
UNSTATED = 0.5
# What the platform already holds as fact, weighted as a link of that strength.
FAMILY_KINDS = ("spouse", "parent", "child", "intergenerational")
W_FAMILY, W_HOUSEHOLD, W_ADVISER, W_TOUCH, W_CLIENT_CONTACT = 0.9, 0.85, 0.8, 0.55, 1.0
NODE_TYPES = ("person", "contact", "entity", "user")
MAX_EDGES = 4          # staff, then up to three people in between, then the target
MIN_SCORE = 25         # below this a path is listed but not proposed
EVIDENCE_MIN = 3


class IntroductionError(Exception):
    """Refused, with a reason fit to show the person."""


@dataclass
class Node:
    type: str
    id: uuid.UUID
    name: str
    household_id: uuid.UUID | None = None
    contact_type: str | None = None
    left: bool = False

    @property
    def key(self) -> tuple[str, uuid.UUID]:
        return (self.type, self.id)

    def ref(self) -> dict:
        return {"type": self.type, "id": str(self.id), "name": self.name}


@dataclass
class Link:
    a: tuple[str, uuid.UUID]
    b: tuple[str, uuid.UUID]
    kind: str            # a LINK_KIND, or family / household / adviser / touch / client contact
    weight: float
    label: str
    recorded: bool       # a person recorded it as a link, as against a fact the platform holds
    closeness: str | None = None
    how: str | None = None
    evidence: str | None = None
    recorded_by: str | None = None
    recorded_on: str | None = None
    last: date | None = None
    edge_id: uuid.UUID | None = None


@dataclass
class Graph:
    nodes: dict = field(default_factory=dict)
    adj: dict = field(default_factory=lambda: defaultdict(list))
    referred_count: dict = field(default_factory=lambda: defaultdict(int))
    adviser_of: dict = field(default_factory=dict)     # household id -> User
    users: dict = field(default_factory=dict)


def plain(name: str) -> str:
    """A name as typed matches it as recorded: case and accents aside ("Anders Muller" is Anders Müller)."""
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", name or "") if not unicodedata.combining(ch)).strip().lower()


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _recency(last: date | None, today: date) -> float:
    if last is None:
        return 0.75
    days = (today - last).days
    return 1.0 if days <= 180 else 0.85 if days <= 730 else 0.7


async def _graph(session: AsyncSession, firm: Firm, today: date) -> Graph:
    from app.aurea_core import advisers

    g = Graph()
    households = {h.id: h for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all():
        if p.deceased_on is not None:
            continue        # nobody introduces anyone after their death
        h = households.get(p.household_id)
        g.nodes[("person", p.id)] = Node("person", p.id, p.full_name, p.household_id, left=bool(h and h.left_on))
    for e in (await session.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id))).scalars().all():
        h = households.get(e.household_id)
        g.nodes[("entity", e.id)] = Node("entity", e.id, e.name, e.household_id, left=bool(h and h.left_on))
    contacts = (await session.execute(select(CrmContact).where(CrmContact.firm_id == firm.id, CrmContact.is_active.is_(True)))).scalars().all()
    for c in contacts:
        g.nodes[("contact", c.id)] = Node("contact", c.id, c.full_name, c.household_id, contact_type=c.contact_type)
    for u in (await session.execute(select(User).where(User.firm_id == firm.id, User.is_active.is_(True)))).scalars().all():
        if str(u.role) in ("client", "superadmin"):
            continue
        g.nodes[("user", u.id)] = Node("user", u.id, u.full_name)
        g.users[u.id] = u

    def add(link: Link) -> None:
        if link.a in g.nodes and link.b in g.nodes and link.a != link.b:
            g.adj[link.a].append(link)
            g.adj[link.b].append(link)

    # Recorded links, and the family the client graph holds.
    for e in (await session.execute(select(RelationshipEdge).where(RelationshipEdge.firm_id == firm.id))).scalars().all():
        a, b = (e.from_type, e.from_id), (e.to_type, e.to_id)
        at = e.attributes or {}
        if e.kind in LINK_KINDS:
            close = at.get("closeness")
            since = at.get("last_contact") or at.get("recorded_on")
            add(Link(a, b, e.kind, CLOSENESS.get(close, UNSTATED), KIND_LABEL[e.kind], True, close, at.get("how"),
                     at.get("evidence"), at.get("recorded_by"), at.get("recorded_on"),
                     date.fromisoformat(since[:10]) if since else None, e.id))
            if e.kind == "referred":
                g.referred_count[a] += 1
        elif e.kind in FAMILY_KINDS and e.from_type == "person" and e.to_type == "person":
            add(Link(a, b, "family", W_FAMILY, e.kind, False))

    # One household: its people know each other, and its trust or foundation is theirs.
    members = defaultdict(list)
    for n in g.nodes.values():
        if n.type in ("person", "entity") and n.household_id:
            members[n.household_id].append(n)
    for hid, ns in members.items():
        for i, x in enumerate(ns):
            for y in ns[i + 1:]:
                if x.type == "person" or y.type == "person":
                    add(Link(x.key, y.key, "household", W_HOUSEHOLD, f"same household, {households[hid].name}", False))

    # The adviser recorded for a household knows its people.
    for hid, uid in (await advisers.of_households(session, firm.id)).items():
        if uid in g.users:
            g.adviser_of[hid] = g.users[uid]
            for n in members.get(hid, []):
                add(Link(("user", uid), n.key, "adviser", W_ADVISER, "adviser to", False))

    # A contact who is a client is that client's people.
    for c in contacts:
        if c.household_id:
            for n in members.get(c.household_id, []):
                if n.type == "person":
                    add(Link(("contact", c.id), n.key, "client contact", W_CLIENT_CONTACT, "is a client contact of", False))

    # A call, email, meeting or note a member of staff logged with a contact: they have been in touch.
    last_touch: dict = {}
    for a in (await session.execute(select(CrmActivity).where(CrmActivity.firm_id == firm.id,
                                                              CrmActivity.logged_by_user_id.is_not(None)))).scalars().all():
        when = _aware(a.occurred_at).date()
        if when > today:
            continue        # a meeting booked ahead has not happened
        k = (a.logged_by_user_id, a.contact_id)
        if k not in last_touch or when > last_touch[k][0]:
            last_touch[k] = (when, a.activity_type)
    for (uid, cid), (when, kind) in last_touch.items():
        add(Link(("user", uid), ("contact", cid), "touch", W_TOUCH, f"last {kind} {when.isoformat()}", False, last=when))
    return g


def _standing(g: Graph, n: Node) -> tuple[float, list[str]]:
    """The introducer's own standing with the firm, as chips and a factor."""
    factor, chips = 1.0, []
    if n.type in ("person", "entity") and n.household_id:
        if n.left:
            chips.append("former client")
        else:
            factor *= 1.1
            chips.append("client")
            adviser = g.adviser_of.get(n.household_id)
            if adviser:
                chips.append(f"adviser {adviser.full_name}")
    if n.type == "contact" and n.contact_type == "centre_of_influence":
        factor *= 1.1
        chips.append("centre of influence")
    if n.type == "contact" and n.contact_type == "client_contact":
        factor *= 1.1
        chips.append("client")
    referred = g.referred_count.get(n.key, 0)
    if referred:
        factor *= 1.0 + 0.1 * min(referred, 3)
        chips.append(f"referred {referred} before")
    return factor, chips


def _hop(g: Graph, link: Link, frm: tuple, to: tuple) -> dict:
    return {"from": g.nodes[frm].ref(), "to": g.nodes[to].ref(), "kind": link.kind, "label": link.label,
            "recorded": link.recorded, "closeness": link.closeness, "how": link.how, "evidence": link.evidence,
            "recorded_by": link.recorded_by, "recorded_on": link.recorded_on,
            "last": link.last.isoformat() if link.last else None, "weight": round(link.weight, 2)}


def _who_asks(g: Graph, start: Node, introducer: Node) -> Node:
    """The member of staff who should make the ask: the introducer's own adviser where one is recorded, else the one
    the path starts from."""
    adviser = g.adviser_of.get(introducer.household_id) if introducer.household_id else None
    if adviser is not None:
        return g.nodes[("user", adviser.id)]
    return start


def _search(g: Graph, target: tuple, today: date, limit: int) -> tuple[list[dict], list[dict]]:
    """Every simple path from a member of staff to the target of up to MAX_EDGES links, best first; and the staff
    already in touch with the target directly."""
    found: list[dict] = []
    in_touch: list[dict] = []
    for link in g.adj.get(target, []):
        other = link.b if link.a == target else link.a
        if other[0] == "user":
            in_touch.append({"user": g.nodes[other].ref(), "label": link.label, "kind": link.kind,
                             "last": link.last.isoformat() if link.last else None})

    def found_path(path: list[tuple], links: list[Link]) -> None:
        """`path` runs from a member of staff to the target, `links` between them."""
        introducer = g.nodes[path[-2]]
        score = 1.0
        for ln in links:
            score *= ln.weight
        score *= _recency(links[-1].last, today)
        factor, chips = _standing(g, introducer)
        score = min(100.0, 100 * score * factor)
        ask = _who_asks(g, g.nodes[path[0]], introducer)
        found.append({
            "score": round(score), "proposable": score >= MIN_SCORE,
            "introducer": {**introducer.ref(), "standing": chips},
            "ask": ask.ref(),
            "hops": [_hop(g, ln, path[i], path[i + 1]) for i, ln in enumerate(links)],
            "recorded_links": sum(1 for ln in links if ln.recorded),
        })

    def walk(back: list[tuple], links: list[Link]) -> None:
        """Out from the target, which has few links, until a member of staff: far cheaper than from every member."""
        here = back[-1]
        if here[0] == "user":
            if len(links) >= 2:     # staff next to the target is being in touch, not an introduction
                found_path(back[::-1], links[::-1])
            return                  # a path runs through clients and contacts, not through more staff
        if len(links) >= MAX_EDGES:
            return
        for ln in g.adj.get(here, []):
            nxt = ln.b if ln.a == here else ln.a
            if nxt not in back:
                walk(back + [nxt], links + [ln])

    walk([target], [])
    # One path per introducer and asker: the best.
    best: dict = {}
    for p in sorted(found, key=lambda p: (-p["score"], len(p["hops"]))):
        k = (p["introducer"]["id"], p["ask"]["id"])
        if k not in best:
            best[k] = p
    paths = sorted(best.values(), key=lambda p: (-p["score"], len(p["hops"])))[:limit]
    in_touch.sort(key=lambda t: t["last"] or "", reverse=True)
    return paths, in_touch


async def _target(session: AsyncSession, firm: Firm, g: Graph, *, contact_id=None, person_id=None, name: str | None = None) -> Node:
    def parse(v, label):
        try:
            return uuid.UUID(str(v))
        except ValueError:
            raise IntroductionError(f"{label} {v!r} is not an id; read_crm_pipeline or search_households gives one.")

    if contact_id:
        n = g.nodes.get(("contact", parse(contact_id, "contact_id")))
        if n is None:
            raise IntroductionError("That contact is not one of this firm's active contacts; read_crm_pipeline lists them.")
        return n
    if person_id:
        n = g.nodes.get(("person", parse(person_id, "person_id")))
        if n is None:
            raise IntroductionError("That person is not one of this firm's clients.")
        return n
    if name:
        want = plain(name)
        hits = [n for n in g.nodes.values() if n.type in ("contact", "person") and plain(n.name) == want]
        if not hits:
            hits = [n for n in g.nodes.values() if n.type in ("contact", "person") and want in plain(n.name)]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise IntroductionError(f"No contact or client named {name!r}; read_crm_pipeline lists the contacts.")
        raise IntroductionError(f"{len(hits)} match {name!r}: " + ", ".join(f"{h.name} ({h.type})" for h in hits[:6])
                                + ". Give contact_id or person_id.")
    raise IntroductionError("Say who: contact_id, person_id or name.")


async def paths(session: AsyncSession, firm: Firm, *, contact_id=None, person_id=None, name: str | None = None,
                limit: int = 5) -> dict:
    """The ways in to one prospect or client, best first, with who should ask whom and why."""
    from app.aurea_core import firm_time

    today = firm_time.today(firm)
    g = await _graph(session, firm, today)
    target = await _target(session, firm, g, contact_id=contact_id, person_id=person_id, name=name)
    found, in_touch = _search(g, target.key, today, limit)
    recorded_here = sum(1 for ln in g.adj.get(target.key, []) if ln.recorded)
    not_on_file = []
    if recorded_here == 0:
        not_on_file.append("Anyone recorded as knowing them")
    return {
        "target": {**target.ref(), "contact_type": target.contact_type},
        "paths": found,
        "in_touch": in_touch,
        "recorded_links_to_target": recorded_here,
        "recorded_links_in_firm": sum(1 for links in g.adj.values() for ln in links if ln.recorded) // 2,
        "not_on_file": not_on_file,
        "as_of": today.isoformat(),
    }


async def open_prospects(session: AsyncSession, firm: Firm, owner: User | None = None) -> dict:
    """Prospects at lead or qualified not yet met, each with its best way in or none. The agent reads the firm's; an
    adviser's page reads the contacts they own, as the pipeline does."""
    from app.core.db import utcnow
    from app.models.crm import CrmOpportunity

    now = utcnow()
    deals = (await session.execute(select(CrmOpportunity).where(
        CrmOpportunity.firm_id == firm.id, CrmOpportunity.stage.in_(("lead", "qualified"))))).scalars().all()
    stage = {o.contact_id: o.stage for o in deals}
    if not stage:
        return {"prospects": [], "met": 0}
    met = {a.contact_id for a in (await session.execute(select(CrmActivity).where(
        CrmActivity.firm_id == firm.id, CrmActivity.contact_id.in_(list(stage)), CrmActivity.activity_type == "meeting")))
        .scalars().all() if _aware(a.occurred_at) <= now}
    q = select(CrmContact).where(CrmContact.id.in_(list(stage)), CrmContact.is_active.is_(True))
    if owner is not None:
        q = q.where(CrmContact.owner_user_id == owner.id)
    out = []
    for c in (await session.execute(q.order_by(CrmContact.full_name))).scalars().all():
        if c.id in met:
            continue        # already met: the introduction has done its work
        found = await paths(session, firm, contact_id=c.id, limit=1)
        out.append({"contact_id": str(c.id), "name": c.full_name, "stage": stage[c.id],
                    "best": found["paths"][0] if found["paths"] else None, "in_touch": found["in_touch"]})
    return {"prospects": out, "met": len(met)}


# ── Recording a link ───────────────────────────────────────────────────────────────────────────

async def _node(session: AsyncSession, firm: Firm, kind: str | None, ref: str | None, name: str | None) -> Node:
    from app.aurea_core import firm_time

    today = firm_time.today(firm)
    if kind not in NODE_TYPES:
        raise IntroductionError(f"Each end is one of {', '.join(NODE_TYPES)}.")
    if ref:
        try:
            nid = uuid.UUID(str(ref))
        except ValueError:
            raise IntroductionError(f"{ref!r} is not an id.")
        model = {"person": Person, "contact": CrmContact, "entity": LegalEntity, "user": User}[kind]
        row = await session.get(model, nid)
        if row is None or row.firm_id != firm.id:
            raise IntroductionError(f"No {kind} with that id at this firm.")
        label = getattr(row, "full_name", None) or getattr(row, "name", None)
        if kind == "person" and row.deceased_on is not None:
            raise IntroductionError(f"{label} is recorded as having died; a link is not added.")
        return Node(kind, row.id, label, getattr(row, "household_id", None), contact_type=getattr(row, "contact_type", None))
    if name:
        g = await _graph(session, firm, today)
        want = plain(name)
        hits = [n for n in g.nodes.values() if n.type == kind and plain(n.name) == want]
        if len(hits) == 1:
            return hits[0]
        raise IntroductionError(f"{'No' if not hits else len(hits)} {kind} named {name!r}" + ("" if not hits else "; give the id") + ".")
    raise IntroductionError("Each end needs an id or an exact name.")


async def prepare_link(session: AsyncSession, firm: Firm, *, from_type: str | None, from_id: str | None, from_name: str | None,
                       to_type: str | None, to_id: str | None, to_name: str | None, kind: str | None,
                       closeness: str | None, how: str | None, evidence: str | None) -> dict:
    """Check a link before its Confirm card, and say exactly what would be recorded. Raises what recording would."""
    kind = (kind or "knows").strip().lower()
    if kind not in LINK_KINDS:
        raise IntroductionError(f"kind is one of {', '.join(LINK_KINDS)}.")
    if closeness not in (None, "") and closeness not in CLOSENESS:
        raise IntroductionError("closeness is close, known or met, and only if someone said; leave it out otherwise.")
    evidence = (evidence or "").strip()
    if len(evidence) < EVIDENCE_MIN:
        raise IntroductionError("Say where this comes from (who said it, when, or the document): a link is what a person "
                                "was told or saw, never worked out.")
    a = await _node(session, firm, from_type, from_id, from_name)
    b = await _node(session, firm, to_type, to_id, to_name)
    if a.key == b.key:
        raise IntroductionError("Both ends are the same.")
    if a.type == "user" and b.type == "user":
        raise IntroductionError("Two members of staff are not a way in to anyone; record who each of them knows.")
    same = (await session.execute(select(RelationshipEdge).where(
        RelationshipEdge.firm_id == firm.id, RelationshipEdge.kind == kind,
        ((RelationshipEdge.from_type == a.type) & (RelationshipEdge.from_id == a.id)
         & (RelationshipEdge.to_type == b.type) & (RelationshipEdge.to_id == b.id))
        | ((RelationshipEdge.from_type == b.type) & (RelationshipEdge.from_id == b.id)
           & (RelationshipEdge.to_type == a.type) & (RelationshipEdge.to_id == a.id))))).scalars().first()
    if same is not None:
        at = same.attributes or {}
        raise IntroductionError(f"Already on file: {a.name} {KIND_LABEL[kind]} {b.name}, recorded "
                                f"{at.get('recorded_on') or 'earlier'} by {at.get('recorded_by') or 'someone'}.")
    return {"from": a, "to": b, "kind": kind, "closeness": closeness or None, "how": (how or "").strip() or None,
            "evidence": evidence}


async def record_link(session: AsyncSession, firm: Firm, actor: User, **terms) -> dict:
    from app.aurea_core import firm_time
    from app.models.identity import AuditEvent

    p = await prepare_link(session, firm, **terms)
    today = firm_time.today(firm)
    edge = RelationshipEdge(firm_id=firm.id, kind=p["kind"], from_type=p["from"].type, from_id=p["from"].id,
                            to_type=p["to"].type, to_id=p["to"].id,
                            attributes={"closeness": p["closeness"], "how": p["how"], "evidence": p["evidence"],
                                        "recorded_by": actor.full_name, "recorded_by_id": str(actor.id),
                                        "recorded_on": today.isoformat(), "source": "recorded"})
    session.add(edge)
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="relationship.recorded",
                           subject=f"{p['from'].name} {KIND_LABEL[p['kind']]} {p['to'].name}",
                           detail={"kind": p["kind"], "from": p["from"].ref(), "to": p["to"].ref(), "evidence": p["evidence"]}))
    await session.flush()
    return {"link_id": str(edge.id), "from": p["from"].ref(), "to": p["to"].ref(), "kind": p["kind"],
            "label": KIND_LABEL[p["kind"]], "closeness": p["closeness"], "how": p["how"], "evidence": p["evidence"],
            "recorded_by": actor.full_name, "recorded_on": today.isoformat()}
