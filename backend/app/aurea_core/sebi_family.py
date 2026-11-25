"""SEBI's "family of client", from what a person recorded (India row 13, B-245).

The Investment Adviser fee cap and the adviser's pre-advice gate speak of the client's family: the individual, the spouse,
dependent children and dependent parents, as the research report reads SEBI's definition (`law_figures` `in_family_of_client`;
the clause was not re-read, counsel confirms). A household is not that: an adult child who does not depend on the client is a
family of their own, and a dependent parent may live in another household.

**Nothing is inferred.** A person records where one client sits in another's family (spouse, child or parent, with whether a
child or parent is dependent, or that they are no part of it), from a document or a declaration, named. A row is added and never
edited, and the latest for a pair stands. Dependence is never worked out from an age or an income.

**The household is the fallback, and says so.** With no row touching a household, its people are one family and the result's
`basis` is "household". Once a row is on file the household is still taken as one family except for anyone a row says is not in
it, who stands alone unless a row places them (a spouse, a dependent child or a dependent parent) in someone's family. Rows can
join people across households. Where the data is thinner than the truth the family comes out larger, which makes the fee cap
stricter, never looser. An accredited investor is not recorded, so none is exempted from the cap.

Nothing here changes a fee, a mandate or a household.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.models.graph import Household, Mandate, Person
from app.models.india_family import SebiRelation
from app.models.tenant import Firm
from app.provenance import ledger

RELATIONS = {"spouse": "Spouse", "child": "Child", "parent": "Parent", "none": "Not part of the family"}
CITATION = "SEBI (Investment Advisers) Regulations 2013 and the IA Master Circular (6 Feb 2026) para 1(iii), as read in the research report"
STANDS_IN = "The household stands in for SEBI's family of client: no family is recorded for it."
RECORDED_BASIS = "The family is as a person recorded it from a document; anyone not recorded is taken with their household."
RECORDERS_NOTE = "An adviser, a paraplanner or operations records a family."


class FamilyError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _uuid(value, label: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise FamilyError(f"Give the {label}, from the client's page.")


@dataclass
class Family:
    key: str                     # the household's id while the household stands in, else "f:" and the lowest member's id
    basis: str                   # household | recorded
    member_ids: list[uuid.UUID]
    household_ids: list[uuid.UUID]
    members: list[dict] = field(default_factory=list)

    def view(self) -> dict:
        return {"key": self.key, "basis": self.basis, "members": self.members,
                "households": [str(h) for h in self.household_ids],
                "stands_in": STANDS_IN if self.basis == "household" else None,
                "basis_note": None if self.basis == "household" else RECORDED_BASIS}


@dataclass
class Families:
    families: list[Family]
    by_person: dict[uuid.UUID, Family]
    by_household: dict[uuid.UUID, list[Family]]
    relations: list[SebiRelation]
    mandates: dict[str, list[uuid.UUID]]      # a family's key: the mandates its people hold

    def of_person(self, person_id) -> Family | None:
        return self.by_person.get(person_id)

    def key_of(self, person_id, household_id) -> str:
        """The family a mandate's owner belongs to; a mandate of an entity (a trust, an HUF) belongs to its household's first family."""
        fam = self.by_person.get(person_id) if person_id else None
        if fam is None:
            homes = self.by_household.get(household_id) or []
            fam = homes[0] if homes else None
        return fam.key if fam else str(household_id)

    def keyed(self, key: str) -> Family | None:
        return next((f for f in self.families if f.key == key), None)


async def standing(session: AsyncSession, firm_id: uuid.UUID) -> list[SebiRelation]:
    """The latest row for each pair."""
    latest: dict = {}
    for r in (await session.execute(select(SebiRelation).where(SebiRelation.firm_id == firm_id)
                                    .order_by(SebiRelation.created_at, SebiRelation.id))).scalars().all():
        latest[(r.person_id, r.of_person_id)] = r
    return list(latest.values())


async def load(session: AsyncSession, firm: Firm) -> Families:
    people = (await session.execute(select(Person).where(
        Person.firm_id == firm.id, Person.household_id.is_not(None), Person.deceased_on.is_(None)).order_by(Person.full_name))).scalars().all()
    ids = {p.id: p for p in people}
    relations = [r for r in await standing(session, firm.id) if r.person_id in ids and r.of_person_id in ids]
    parent = {pid: pid for pid in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb, key=str)] = min(ra, rb, key=str)

    positive = {(r.person_id, r.of_person_id) for r in relations if r.relation == "spouse" or (r.relation in ("child", "parent") and r.dependent)}
    placed = {p for pair in positive for p in pair}
    # Anyone a row says is not in the family stands alone, unless another row places them.
    apart = {r.person_id for r in relations if (r.relation == "none" or (r.relation in ("child", "parent") and r.dependent is False))
             and r.person_id not in placed}
    touched = {p for r in relations for p in (r.person_id, r.of_person_id)}
    homes: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for p in people:
        homes[p.household_id].append(p.id)
    for members in homes.values():
        anchor = [m for m in members if m not in apart]
        for m in anchor[1:]:
            union(anchor[0], m)
    for a, b in positive:
        union(a, b)
    groups: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for pid in ids:
        groups[find(pid)].append(pid)
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    families, by_person, by_household = [], {}, defaultdict(list)
    mandate_owner: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for m in (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.person_id.is_not(None)))).scalars().all():
        mandate_owner[m.person_id].append(m.id)
    mandates: dict[str, list[uuid.UUID]] = {}
    for members in groups.values():
        members.sort(key=str)
        hh = sorted({ids[m].household_id for m in members}, key=str)
        recorded = len(hh) > 1 or any(m in touched or m in apart for m in members) or any(
            set(homes[h]) != set(members) for h in hh)
        key = f"f:{members[0]}" if recorded else str(hh[0])
        fam = Family(key=key, basis="recorded" if recorded else "household", member_ids=members, household_ids=hh,
                     members=[{"person_id": str(m), "name": ids[m].full_name, "household": names.get(ids[m].household_id)} for m in members])
        families.append(fam)
        mandates[key] = [mid for m in members for mid in mandate_owner.get(m, [])]
        for m in members:
            by_person[m] = fam
        for h in hh:
            by_household[h].append(fam)
    families.sort(key=lambda f: f.members[0]["name"] if f.members else "")
    return Families(families, by_person, dict(by_household), relations, mandates)


def _label(r: SebiRelation, names: dict) -> dict:
    return {"id": str(r.id), "person": names.get(r.person_id), "person_id": str(r.person_id), "of": names.get(r.of_person_id),
            "of_person_id": str(r.of_person_id), "relation": r.relation, "relation_label": RELATIONS[r.relation],
            "dependent": r.dependent, "document": r.source_document, "recorded_by": r.recorded_by}


async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """The families the household's people sit in, with where each stands: recorded, or the household standing in."""
    if not applies(firm):
        return {"applies": False, "families": []}
    home = await session.get(Household, household_id)
    if home is None or home.firm_id != firm.id:
        raise ValueError("Household not found.")
    loaded = await load(session, firm)
    fams = loaded.by_household.get(household_id) or []
    names = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    mine = {p for f in fams for p in f.member_ids}
    own = [r for r in loaded.relations if r.person_id in mine or r.of_person_id in mine]
    basis = "recorded" if any(f.basis == "recorded" for f in fams) else "household"
    return {"applies": True, "household_id": str(household_id), "basis": basis, "families": [f.view() for f in fams],
            "stands_in": STANDS_IN if basis == "household" else None, "relations": [_label(r, names) for r in own],
            "citation": CITATION, "figure": law_figures.cited(["in_family_of_client"], firm_time.today(firm))}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every family on the books, and every row a person recorded."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "families": [], "relations": []}
    loaded = await load(session, firm)
    names = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    recorded = [f for f in loaded.families if f.basis == "recorded"]
    return {"applies": True, "as_of": today.isoformat(), "families": [f.view() for f in loaded.families if len(f.members) > 1 or f.basis == "recorded"],
            "recorded": len(recorded), "household_standing_in": sum(1 for f in loaded.families if f.basis == "household"),
            "relations": [_label(r, names) for r in loaded.relations], "citation": CITATION,
            "figure": law_figures.cited(["in_family_of_client"], today),
            "note": "Dependence is what a person recorded from a document: it is never worked out from an age or an income, and an accredited "
                    "investor is not recorded here, so none is exempted from the fee cap."}


async def prepare(session: AsyncSession, firm: Firm, *, person_id, of_person_id, relation: str | None, dependent: bool | None = None,
                  document: str | None) -> dict:
    if not applies(firm):
        raise FamilyError("A SEBI family is recorded for an Indian firm.")
    rel = (relation or "").strip().lower()
    if rel not in RELATIONS:
        raise FamilyError(f"A relation is one of {', '.join(RELATIONS)}.")
    a, b = _uuid(person_id, "person's id"), _uuid(of_person_id, "id of the person whose family it is")
    if a == b:
        raise FamilyError("A person is in their own family already: name two people.")
    people = {}
    for pid in (a, b):
        p = await session.get(Person, pid)
        if p is None or p.firm_id != firm.id or p.household_id is None:
            raise FamilyError("Both people must be clients of this firm.")
        people[pid] = p
    if rel in ("child", "parent") and dependent is None:
        raise FamilyError("Say whether the " + rel + " is dependent on them, from the document: dependence is never worked out.")
    if rel not in ("child", "parent"):
        dependent = None
    if len((document or "").strip()) < 4:
        raise FamilyError("Say where it is on file: the declaration or document that shows the relationship and the dependence.")
    today = firm_time.today(firm)
    pa, pb = people[a], people[b]
    if rel in ("child", "parent") and pa.date_of_birth and pb.date_of_birth:
        older, younger = (pa, pb) if rel == "parent" else (pb, pa)
        if older.date_of_birth >= younger.date_of_birth:
            raise FamilyError(f"{older.full_name} is recorded as the {'parent' if rel == 'parent' else 'child'} but is not older than {younger.full_name}.")
    before = next((r for r in await standing(session, firm.id) if r.person_id == a and r.of_person_id == b), None)
    if before is not None and before.relation == rel and before.dependent == dependent:
        raise FamilyError(f"{pa.full_name} is already recorded as that in {pb.full_name}'s family.")
    return {"person": pa, "of": pb, "relation": rel, "dependent": dependent, "document": document.strip(), "before": before,
            "as_of": today}


async def record(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, **terms)
    row = SebiRelation(firm_id=firm.id, person_id=p["person"].id, of_person_id=p["of"].id, relation=p["relation"], dependent=p["dependent"],
                       source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="sebi_family_recorded", actor=by, content={
        "person_id": str(p["person"].id), "of_person_id": str(p["of"].id), "relation": row.relation, "dependent": row.dependent,
        "document": row.source_document})
    return await overview(session, firm)
