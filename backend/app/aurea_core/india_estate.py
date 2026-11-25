"""An Indian family's estate, as a person recorded it (India row 13, B-232, B-233).

Four things the research report found missing, each recorded from a document, added and never edited (the latest for its subject
stands), and each read next to what the client file already holds (`client_file.py`: a paper signed before a marriage or a death, a
beneficiary who has died). Nothing here changes an account, a nominee, a plan or a figure, and nothing is advice: succession is
laid out, "for counsel".

**Nominations the platform does not hold.** A bank account's nominees (four), EPF's (family only) and a signed opt-out for a demat
account or a folio are recorded from the form; `nominations.check` reads them against the caps, the shares, who has died and what
the owner's will names. No record does not mean no nomination: it says "not on file".

**A will's state, and the law it follows.** The will itself is the client file's estate document. Here a person records whether it
is registered (with its number), where probate stands (not sought, applied, granted, or not required) and who the executor is, and
the personal law the person's succession follows (Hindu, Muslim, Christian, Parsi, other), which is never inferred from a name or a
religion on a form. Without the law recorded the intestacy order is "not assessed".

**Intestacy, laid out.** From `law_figures` with their source: the Hindu Succession Act's order for a man (s.8) and a woman (s.15),
the Indian Succession Act's shares for a Christian or a Parsi. Muslim succession is under personal law and is named as not laid out.
The family on file (spouse, children, parents, from `sebi_family`'s rows) is placed in the classes; no share is worked out, and a
person with a will on file is told the will governs what it disposes of.

**An HUF.** Who is karta and who the coparceners are, as the deed or a declaration names them. A karta or coparcener who has died
is flagged. A daughter is a coparcener by birth (Vineeta Sharma, 2020), and nothing here assumes a karta is a man.

**Gratuity** is the lump sum an employer's statement says is payable, recorded once per statement. It is not estimated from a pay
slip and a length of service, and it is not added to the retirement pot: the plan names it beside the pot.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets, nominations, sebi_family
from app.core.security import UserRole
from app.models.client_file import EstateDocument
from app.models.enums import EntityType
from app.models.graph import Household, LegalEntity, Person
from app.models.india_family import AssetNomination, GratuityRecord, HufRecord, WillStatus
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
READERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE, UserRole.OPERATIONS,
                     UserRole.BRANCH_LEADER, UserRole.ADMIN})
LAWS = {"hindu": "Hindu (including Buddhist, Jain and Sikh)", "muslim": "Muslim", "christian": "Christian", "parsi": "Parsi", "other": "Other"}
PROBATE = {"not_sought": "Not sought", "applied": "Applied for", "granted": "Granted", "not_required": "Not required"}
FOR_COUNSEL = "Laid out from the statute, not advice and not a share: the order depends on facts a lawyer settles (self-acquired or ancestral property, a will, a trust)."
NOT_ON_FILE = "Not on file does not mean none exists."


class EstateError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    return sebi_family.applies(firm)


def _uuid(value, label: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise EstateError(f"Give the {label}, from the client's page.")


def _document(value: str | None) -> str:
    named = (value or "").strip()
    if len(named) < 5:
        raise EstateError("Say which document this comes from and where it is filed.")
    return named


def _day(value, label: str, *, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise EstateError(f"Give the {label} as YYYY-MM-DD.")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise EstateError(f"Give the {label} as YYYY-MM-DD.")


async def _person(session: AsyncSession, firm: Firm, value, label: str = "person's id") -> Person:
    p = await session.get(Person, _uuid(value, label))
    if p is None or p.firm_id != firm.id or p.household_id is None:
        raise EstateError("That person is not a client of this firm.")
    return p


def _need(firm: Firm | None) -> None:
    if not applies(firm):
        raise EstateError("This is recorded for an Indian firm.")


# ── Nominations ───────────────────────────────────────────────────────────────────────────────────────────────────────

def parse_nominees(value) -> list[dict]:
    """Nominees as a list of {name, relationship, share}, or from text: "Name:relationship:share; Name:relationship:share"."""
    if value in (None, "", []):
        return []
    items = value
    if isinstance(value, str):
        items = [x for x in value.replace("\n", ";").split(";") if x.strip()]
    out = []
    for item in items:
        if isinstance(item, dict):
            name, rel, share = item.get("name"), item.get("relationship"), item.get("share")
        else:
            parts = [x.strip() for x in str(item).split(":")]
            name, rel, share = (parts + [None, None, None])[:3]
        name = " ".join(str(name or "").split())
        if len(name) < 2:
            raise EstateError("Every nominee has a name.")
        try:
            share = float(str(share).rstrip("% ")) if share not in (None, "") else None
        except ValueError:
            raise EstateError(f"The share for {name} is a number, as a percentage.")
        out.append({"name": name, "relationship": " ".join(str(rel or "").split()).lower() or None, "share": share})
    return out


async def prepare_nomination(session: AsyncSession, firm: Firm, *, person_id, asset_kind: str | None, label: str | None, status: str | None,
                             nominees=None, declaration_on=None, document: str | None) -> dict:
    _need(firm)
    person = await _person(session, firm, person_id)
    kind = (asset_kind or "").strip().lower()
    if kind not in nominations.KINDS:
        raise EstateError(f"An asset is one of {', '.join(nominations.KINDS)}.")
    name = " ".join((label or "").split())
    if len(name) < 3:
        raise EstateError("Name the account, folio or fund as the document does.")
    state = (status or "").strip().lower()
    if state not in ("nominated", "opted_out"):
        raise EstateError("A nomination is nominated or opted_out.")
    today = firm_time.today(firm)
    listed = parse_nominees(nominees)
    decl = _day(declaration_on, "day the declaration was signed")
    if state == "opted_out":
        if kind not in nominations.OPT_OUT_KINDS:
            raise EstateError("A signed opt-out is recorded for a demat account or a mutual fund folio only.")
        if decl is None:
            raise EstateError("An opt-out needs the day the client signed the declaration.")
        if listed:
            raise EstateError("An opted-out account has no nominee: record the nomination instead.")
    else:
        if not listed:
            raise EstateError("Name the nominees, as 'name:relationship:share; ...'.")
        decl = None
        cap = nominations.cap_of_kind(kind, today)
        if cap is not None and len(listed) > cap:
            raise EstateError(f"A {nominations.KINDS[kind].lower()} takes at most {cap} nominees; {len(listed)} were given.")
        shares = [n["share"] for n in listed]
        if any(s is not None for s in shares) and (any(s is None for s in shares) or abs(sum(shares) - 100) > 0.01):
            raise EstateError("Give every nominee's share, adding to 100%, or none of them.")
    if decl is not None and decl > today:
        raise EstateError("A declaration cannot be dated in the future.")
    before = await _standing_nomination(session, firm.id, person.id, kind, name)
    if before is not None and before.status == state and (before.nominees or []) == listed and before.declaration_on == decl:
        raise EstateError("That is already what is recorded for this asset.")
    return {"person": person, "asset_kind": kind, "label": name, "status": state, "nominees": listed, "declaration_on": decl,
            "document": _document(document), "before": before}


async def _standing_nomination(session: AsyncSession, firm_id, person_id, kind: str, label: str) -> AssetNomination | None:
    return (await session.execute(select(AssetNomination).where(
        AssetNomination.firm_id == firm_id, AssetNomination.person_id == person_id, AssetNomination.asset_kind == kind,
        AssetNomination.label == label).order_by(AssetNomination.created_at.desc(), AssetNomination.id.desc()))).scalars().first()


async def record_nomination(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    p = await prepare_nomination(session, firm, **terms)
    row = AssetNomination(firm_id=firm.id, household_id=p["person"].household_id, person_id=p["person"].id, asset_kind=p["asset_kind"],
                          label=p["label"], status=p["status"], nominees=p["nominees"] or None, declaration_on=p["declaration_on"],
                          source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="nomination_recorded", actor=by, content={
        "person_id": str(p["person"].id), "asset_kind": row.asset_kind, "label": row.label, "status": row.status,
        "nominees": [n["name"] for n in p["nominees"]], "document": row.source_document})
    return await household_view(session, firm, p["person"].household_id)


async def _latest(session: AsyncSession, model, firm_id, *key_columns, where=()) -> list:
    rows = (await session.execute(select(model).where(model.firm_id == firm_id, *where).order_by(model.created_at, model.id))).scalars().all()
    latest: dict = {}
    for r in rows:
        latest[tuple(getattr(r, c) for c in key_columns)] = r
    return list(latest.values())


def _nomination_view(r: AssetNomination, names: dict) -> dict:
    return {"id": str(r.id), "person_id": str(r.person_id), "person": names.get(r.person_id), "asset_kind": r.asset_kind,
            "asset_label": nominations.KINDS[r.asset_kind], "label": r.label, "status": r.status, "nominees": r.nominees or [],
            "declaration_on": r.declaration_on.isoformat() if r.declaration_on else None, "document": r.source_document,
            "recorded_by": r.recorded_by}


# ── Will, personal law and probate ─────────────────────────────────────────────────────────────────────────────────

async def prepare_will_status(session: AsyncSession, firm: Firm, *, person_id, personal_law: str | None = None, registered: bool | None = None,
                              registration_ref: str | None = None, probate: str | None = None, probate_ref: str | None = None,
                              executor: str | None = None, as_of=None, document: str | None) -> dict:
    _need(firm)
    person = await _person(session, firm, person_id)
    today = firm_time.today(firm)
    law = (personal_law or "").strip().lower() or None
    if law is not None and law not in LAWS:
        raise EstateError(f"A personal law is one of {', '.join(LAWS)}.")
    pr = (probate or "").strip().lower() or None
    if pr is not None and pr not in PROBATE:
        raise EstateError(f"Probate is one of {', '.join(PROBATE)}.")
    if registered is True and len((registration_ref or "").strip()) < 3:
        raise EstateError("A registered will has its registration number, from the sub-registrar's endorsement.")
    if pr in ("applied", "granted") and len((probate_ref or "").strip()) < 3:
        raise EstateError("Probate applied for or granted has the court's case or grant number.")
    if law is None and registered is None and pr is None and not (executor or "").strip():
        raise EstateError("Record at least the personal law, whether the will is registered, probate or the executor.")
    day = _day(as_of, "date of the document") or today
    if day > today:
        raise EstateError("A document cannot be dated in the future.")
    will = (await session.execute(select(EstateDocument).where(EstateDocument.person_id == person.id, EstateDocument.kind == "will"))).scalars().first()
    if (registered is not None or pr in ("applied", "granted") or (executor or "").strip()) and will is None:
        raise EstateError(f"{person.full_name} has no will on file: record the will as an estate document first, then its registration and probate.")
    return {"person": person, "personal_law": law, "registered": registered, "registration_ref": (registration_ref or "").strip() or None,
            "probate": pr, "probate_ref": (probate_ref or "").strip() or None, "executor": " ".join((executor or "").split()) or None,
            "as_of": day, "document": _document(document)}


async def record_will_status(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    p = await prepare_will_status(session, firm, **terms)
    before = await _standing_will(session, firm.id, p["person"].id)
    # What is not restated stands: a new row carries forward what the earlier one said.
    merged = {k: (p[k] if p[k] is not None else getattr(before, k, None) if before else p[k])
              for k in ("personal_law", "registered", "registration_ref", "probate", "probate_ref", "executor")}
    row = WillStatus(firm_id=firm.id, person_id=p["person"].id, as_of=p["as_of"], source_document=p["document"], recorded_by=by, **merged)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="will_status_recorded", actor=by, content={
        "person_id": str(p["person"].id), **{k: v for k, v in merged.items()}, "document": row.source_document})
    return await household_view(session, firm, p["person"].household_id)


async def _standing_will(session: AsyncSession, firm_id, person_id) -> WillStatus | None:
    return (await session.execute(select(WillStatus).where(WillStatus.firm_id == firm_id, WillStatus.person_id == person_id)
                                  .order_by(WillStatus.created_at.desc(), WillStatus.id.desc()))).scalars().first()


# ── Intestacy, laid out ────────────────────────────────────────────────────────────────────────────────────────────────

def intestacy(law: str | None, *, today: date, family: dict, will: bool) -> dict:
    """The statutory order for the personal law recorded, with the people on file placed in it. No share is worked out."""
    if law is None:
        return {"state": "not_assessed", "message": "No personal law is recorded, so the order of succession is not assessed. It is never read from a name or a religion on a form.",
                "orders": []}
    cite = lambda keys: law_figures.cited(keys, today)  # noqa: E731
    spouse, kids, parents = family.get("spouse", []), family.get("children", []), family.get("parents", [])
    if law == "hindu":
        orders = [{"title": "A man dying without a will (s.8)", "figure": cite(["in_hindu_succession_class_i"])[0],
                   "on_file": {"class_i_spouse_and_children": spouse + kids, "parents": parents}},
                  {"title": "A woman dying without a will (s.15)", "figure": cite(["in_hindu_succession_female"])[0],
                   "on_file": {"first": kids + spouse, "then": parents}}]
    elif law in ("christian", "parsi"):
        orders = [{"title": "An Indian Succession Act estate without a will", "figure": cite(["in_isa_succession"])[0],
                   "on_file": {"spouse": spouse, "descendants": kids}}]
    else:
        orders = []
    note = (FOR_COUNSEL if orders else "Muslim succession is under personal law and is not laid out here: for counsel.") if law != "other" else \
        "Another personal law is not laid out here: for counsel."
    return {"state": "laid_out" if orders else "not_laid_out", "personal_law": law, "orders": orders,
            "will_on_file": will, "message": ("A will is on file: it governs what it disposes of, and this order applies to the rest. " if will else "") + note}


async def _family_on_file(session: AsyncSession, firm: Firm, person: Person) -> dict:
    """Spouse, children and parents of the person as recorded in SEBI family rows, never inferred."""
    names = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    out = {"spouse": [], "children": [], "parents": []}
    for r in await sebi_family.standing(session, firm.id):
        if r.of_person_id == person.id and r.person_id in names:
            if r.relation == "spouse":
                out["spouse"].append(names[r.person_id])
            elif r.relation == "child":
                out["children"].append(names[r.person_id])
            elif r.relation == "parent":
                out["parents"].append(names[r.person_id])
        if r.person_id == person.id and r.of_person_id in names:
            if r.relation == "spouse":
                out["spouse"].append(names[r.of_person_id])
            elif r.relation == "child":
                out["parents"].append(names[r.of_person_id])
            elif r.relation == "parent":
                out["children"].append(names[r.of_person_id])
    return {k: sorted(set(v)) for k, v in out.items()}


# ── An HUF ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def prepare_huf(session: AsyncSession, firm: Firm, *, entity_id, karta_name: str | None, coparceners=None, karta_person_id=None,
                      as_of=None, document: str | None) -> dict:
    _need(firm)
    entity = await session.get(LegalEntity, _uuid(entity_id, "HUF's id"))
    if entity is None or entity.firm_id != firm.id or str(entity.entity_type) != EntityType.HUF.value:
        raise EstateError("That is not an HUF of this firm.")
    karta = None
    if karta_person_id:
        karta = await _person(session, firm, karta_person_id, "karta's id")
    name = " ".join((karta_name or (karta.full_name if karta else "")).split())
    if len(name) < 3:
        raise EstateError("Name the karta.")
    members = parse_nominees(coparceners)
    out = []
    for m in members:
        out.append({"name": m["name"], "relationship": m["relationship"]})
    if not out:
        raise EstateError("Name the coparceners, as 'name:relationship; ...'. A karta alone is not an HUF's coparcenary.")
    day = _day(as_of, "date of the deed or declaration") or firm_time.today(firm)
    if day > firm_time.today(firm):
        raise EstateError("A deed cannot be dated in the future.")
    return {"entity": entity, "karta_name": name, "karta_person_id": karta.id if karta else None, "coparceners": out, "as_of": day,
            "document": _document(document)}


async def record_huf(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    p = await prepare_huf(session, firm, **terms)
    row = HufRecord(firm_id=firm.id, entity_id=p["entity"].id, karta_name=p["karta_name"], karta_person_id=p["karta_person_id"],
                    coparceners=p["coparceners"], as_of=p["as_of"], source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="huf_recorded", actor=by, content={
        "entity_id": str(p["entity"].id), "karta": row.karta_name, "coparceners": [c["name"] for c in p["coparceners"]], "document": row.source_document})
    return await household_view(session, firm, p["entity"].household_id)


# ── Gratuity ───────────────────────────────────────────────────────────────────────────────────────────────────────────

async def prepare_gratuity(session: AsyncSession, firm: Firm, *, person_id, employer: str | None, amount, as_of=None, payable_on=None,
                           years_of_service=None, document: str | None) -> dict:
    _need(firm)
    person = await _person(session, firm, person_id)
    org = " ".join((employer or "").split())
    if len(org) < 2:
        raise EstateError("Name the employer.")
    try:
        value = float(amount)
    except (TypeError, ValueError):
        raise EstateError("Give the lump sum the statement says is payable, as a number.")
    if value <= 0:
        raise EstateError("The gratuity payable is more than zero: with nothing payable there is nothing to record.")
    today = firm_time.today(firm)
    day = _day(as_of, "date of the statement") or today
    due = _day(payable_on, "day it is payable")
    if day > today:
        raise EstateError("A statement cannot be dated in the future.")
    years = None
    if years_of_service not in (None, ""):
        try:
            years = float(years_of_service)
        except (TypeError, ValueError):
            raise EstateError("Years of service is a number.")
    before = await _standing_gratuity(session, firm.id, person.id, org)
    if before is not None and float(before.amount) == value and before.as_of == day:
        raise EstateError("That statement is already recorded.")
    if before is not None and before.as_of > day:
        raise EstateError(f"A statement dated {before.as_of.isoformat()} is already on file, and a later one stands over an earlier.")
    return {"person": person, "employer": org, "amount": value, "as_of": day, "payable_on": due, "years": years, "document": _document(document),
            "before": before}


async def _standing_gratuity(session: AsyncSession, firm_id, person_id, employer: str) -> GratuityRecord | None:
    return (await session.execute(select(GratuityRecord).where(
        GratuityRecord.firm_id == firm_id, GratuityRecord.person_id == person_id, GratuityRecord.employer == employer)
        .order_by(GratuityRecord.as_of.desc(), GratuityRecord.created_at.desc()))).scalars().first()


async def record_gratuity(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    p = await prepare_gratuity(session, firm, **terms)
    row = GratuityRecord(firm_id=firm.id, household_id=p["person"].household_id, person_id=p["person"].id, employer=p["employer"],
                         amount=p["amount"], years_of_service=p["years"], payable_on=p["payable_on"], as_of=p["as_of"],
                         source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="gratuity_recorded", actor=by, content={
        "person_id": str(p["person"].id), "employer": row.employer, "amount": p["amount"], "as_of": p["as_of"].isoformat(),
        "document": row.source_document})
    return await household_view(session, firm, p["person"].household_id)


async def gratuity_of(session: AsyncSession, firm_id, household_id: uuid.UUID) -> list[dict]:
    """The standing gratuity statements for a household, for the plan to name. Not a figure the plan adds."""
    rows = await _latest(session, GratuityRecord, firm_id, "person_id", "employer", where=[GratuityRecord.household_id == household_id])
    names = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()}
    return [{"person_id": str(r.person_id), "person": names.get(r.person_id), "employer": r.employer, "amount": float(r.amount),
             "years_of_service": float(r.years_of_service) if r.years_of_service is not None else None,
             "payable_on": r.payable_on.isoformat() if r.payable_on else None, "as_of": r.as_of.isoformat(), "document": r.source_document}
            for r in rows]


# ── The household's view ───────────────────────────────────────────────────────────────────────────────────────────────

async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """Everything recorded about one household's family and estate, and what it does not satisfy."""
    if not applies(firm):
        return {"applies": False}
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise ValueError("Household not found.")
    today = firm_time.today(firm)
    people = (await session.execute(select(Person).where(Person.household_id == household_id).order_by(Person.full_name))).scalars().all()
    names = {p.id: p.full_name for p in people}
    ids = [p.id for p in people]
    noms = [r for r in await _latest(session, AssetNomination, firm.id, "person_id", "asset_kind", "label",
                                     where=[AssetNomination.household_id == household_id])]
    wills = {r.person_id: r for r in await _latest(session, WillStatus, firm.id, "person_id", where=[WillStatus.person_id.in_(ids or [uuid.uuid4()])])}
    docs = {d.person_id: d for d in (await session.execute(select(EstateDocument).where(
        EstateDocument.person_id.in_(ids or [uuid.uuid4()]), EstateDocument.kind == "will").order_by(EstateDocument.signed_on))).scalars().all()}
    gone = {p.full_name for p in people if p.deceased_on}
    heirs = {r.person_id: docs[r.person_id].named for r in noms if docs.get(r.person_id) is not None and docs[r.person_id].named}
    issues = nominations.check([_nomination_view(r, names) for r in noms], today=today, gone=gone,
                               heirs={str(k): v for k, v in heirs.items()})
    people_view = []
    for p in people:
        w, d = wills.get(p.id), docs.get(p.id)
        fam = await _family_on_file(session, firm, p)
        flags = []
        if d is not None and w is None:
            flags.append("a will is on file; whether it is registered and where probate stands is not recorded")
        if p.deceased_on and d is not None and (w is None or w.probate in (None, "not_sought")):
            flags.append("has died, with a will on file and no probate applied for or recorded")
        if w is not None and w.registered is False:
            flags.append("the will is not registered: registration is not compulsory, but it makes a will harder to challenge")
        people_view.append({
            "person_id": str(p.id), "name": p.full_name, "deceased": p.deceased_on.isoformat() if p.deceased_on else None,
            "will_on_file": d is not None, "will_signed_on": d.signed_on.isoformat() if d else None,
            "status": None if w is None else {
                "personal_law": w.personal_law, "personal_law_label": LAWS.get(w.personal_law or ""), "registered": w.registered,
                "registration_ref": w.registration_ref, "probate": w.probate, "probate_label": PROBATE.get(w.probate or ""), "probate_ref": w.probate_ref,
                "executor": w.executor, "as_of": w.as_of.isoformat(), "document": w.source_document},
            "flags": flags,
            "succession": intestacy(w.personal_law if w else None, today=today, family=fam, will=d is not None),
            "family_on_file": fam})
    hufs = []
    for e in (await session.execute(select(LegalEntity).where(
            LegalEntity.household_id == household_id, LegalEntity.entity_type == EntityType.HUF.value).order_by(LegalEntity.name))).scalars().all():
        latest = (await session.execute(select(HufRecord).where(HufRecord.entity_id == e.id).order_by(HufRecord.created_at.desc(), HufRecord.id.desc()))).scalars().first()
        flags = []
        if latest is None:
            flags.append("no karta or coparceners recorded")
        else:
            dead = {p.full_name.strip().lower() for p in people if p.deceased_on}
            if latest.karta_name.strip().lower() in dead or (latest.karta_person_id and any(p.id == latest.karta_person_id and p.deceased_on for p in people)):
                flags.append("the karta has died: a new karta is needed")
            flags += [f"coparcener {c['name']} has died" for c in (latest.coparceners or []) if c["name"].strip().lower() in dead]
        hufs.append({"entity_id": str(e.id), "name": e.name, "recorded": latest is not None,
                     "karta": latest.karta_name if latest else None, "coparceners": (latest.coparceners if latest else []) or [],
                     "as_of": latest.as_of.isoformat() if latest else None, "document": latest.source_document if latest else None,
                     "flags": flags})
    return {"applies": True, "household_id": str(household_id), "household": household.name, "as_of": today.isoformat(),
            "nominations": [_nomination_view(r, names) for r in sorted(noms, key=lambda r: (names.get(r.person_id) or "", r.asset_kind, r.label))],
            "nomination_issues": issues, "people": people_view, "hufs": hufs, "gratuity": await gratuity_of(session, firm.id, household_id),
            "note": NOT_ON_FILE + " " + FOR_COUNSEL,
            "figures": law_figures.cited(["in_nominee_cap_demat_mf", "in_nominee_cap_bank", "in_epf_nominee_family_only", "in_nomination_opt_out"], today)}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every household's family and estate record that has anything on it, and the firm's counts."""
    if not applies(firm):
        return {"applies": False, "households": []}
    today = firm_time.today(firm)
    out = []
    for h in (await session.execute(select(Household).where(Household.firm_id == firm.id).order_by(Household.name))).scalars().all():
        v = await household_view(session, firm, h.id)
        has = v["nominations"] or v["gratuity"] or any(p["status"] for p in v["people"]) or any(x["recorded"] for x in v["hufs"])
        if has or v["hufs"]:
            out.append(v)
    return {"applies": True, "as_of": today.isoformat(), "households": out,
            "issues": sum(len(v["nomination_issues"]) + sum(len(p["flags"]) for p in v["people"]) + sum(len(x["flags"]) for x in v["hufs"]) for v in out),
            "family": await sebi_family.overview(session, firm), "note": NOT_ON_FILE + " " + FOR_COUNSEL}
