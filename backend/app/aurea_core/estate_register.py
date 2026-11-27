"""The asset-and-wishes register a lawyer works from, and the yearly check of a will against it (B-398, India).

The planner's work is not drafting: it is the inventory that makes the lawyer effective, and the check that the will
still matches the balance sheet. Everything here is read from what is on file and what a person recorded:

    register      the accounts on the book, what is held or owned outside it, the life and accident policies, a gratuity
                  and the debts, each with whose it is, how it is held, its figure and date where one is on file, and its
                  nomination. A balance item itemised line by line is listed by its lines, once.
    nominations   an asset that takes a nomination and has neither a nominee nor a recorded opt-out is named, non-book
                  assets included. No nomination on file does not mean none was made.
    wishes        what a person recorded the client saying, in their words.
    will check    a person records that they read a will against the register, and what they found. It is due again a year
                  on (an assumed interval), and sooner reads "changed since" when an asset was added or left the register, a
                  life event reshaped the family, or a newer will was signed. No will on file does not mean none exists.
    the will      witnesses and whether the executor has been told are facts a person records (`india_estate`); fewer than
                  two witnesses, or an executor not told, is named. The rules behind them are assumed, for counsel.
    trust triggers   facts on file that a planner briefs a trust specialist about: a minor, a recorded special-needs
                  beneficiary, a business, a recorded wish to protect assets or hand on a business. Nothing is advised.

Nothing here changes an account, a nominee, a will or a figure.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import balance_sheet, client_file, firm_time, held_away, india_estate, law_figures, nominations, reviews, tasks
from app.aurea_core.valuation import account_valuation
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.client_file import EstateDocument, LifeEvent
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.estate_nri import EstateWish, WillCheck
from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person
from app.models.india_family import AssetNomination, WillStatus
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = india_estate.RECORDERS
READERS = india_estate.READERS
WISH_KINDS = {"wish": "Wish", "special_needs_beneficiary": "Special-needs beneficiary", "business_succession": "Business succession",
              "asset_protection": "Asset protection"}
OUTCOMES = {"matches": "Matches", "needs_change": "Needs change"}
CHECK_MONTHS = 12              # assumed: planning practice, not a rule of law
CHECK_SOURCE = "will_check"
TASK_DAYS = 30
ADULT_FROM = 18
WITNESSES_FIGURE = "in_will_witnesses"
# Which recorded nomination kinds answer for which kind of asset. Property, a business and other assets pass by the will.
NOMINABLE = {"cash": ("bank_account",), "pension": ("epf", "ppf", "nps"), "held_away_investment": ("demat", "mutual_fund")}
POLICY_NOMINABLE = ("life", "tpd", "critical_illness", "personal_accident")
SCHEMES = {"epf": ("epf", "employees provident", "employees provident fund"), "ppf": ("ppf", "public provident"), "nps": ("nps", "national pension")}
NOT_ON_FILE = "Not on file does not mean none exists."


class RegisterError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    return india_estate.applies(firm)


def _uuid(value, label: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise RegisterError(f"Give the {label}, from the client's page.")


def _day(value, label: str, *, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise RegisterError(f"Give the {label} as YYYY-MM-DD.")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise RegisterError(f"Give the {label} as YYYY-MM-DD.")


def _document(value) -> str:
    named = (value or "").strip()
    if len(named) < 5:
        raise RegisterError("Say which document or meeting note this comes from and where it is filed.")
    return named[:300]


async def _person(session: AsyncSession, firm: Firm, value) -> Person:
    p = await session.get(Person, _uuid(value, "person's id"))
    if p is None or p.firm_id != firm.id or p.household_id is None:
        raise RegisterError("That person is not a client of this firm.")
    return p


def _age(p: Person, today: date) -> int | None:
    d = p.date_of_birth
    return None if d is None else today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def adult(p: Person, today: date) -> bool:
    age = _age(p, today)
    return (age >= ADULT_FROM) if age is not None else not p.is_next_gen


def _norm(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _matches(nom: AssetNomination, names: list[str]) -> bool:
    """A recorded nomination is for this asset when one names the other, as whole words."""
    label = f" {_norm(nom.label)} "
    for name in names:
        n = _norm(name)
        if len(n) >= 4 and (f" {n} " in label or f" {label.strip()} " in f" {n} "):
            return True
    return False


def _scheme(kind: str, names: list[str]) -> bool:
    """A person holds one EPF, PPF or NPS account, so its nomination answers for an asset that names the scheme."""
    said = " ".join(f" {_norm(n)} " for n in names)
    return any(f" {word} " in said for word in SCHEMES.get(kind, ()))


def _recorded(noms: list[AssetNomination], kinds: tuple[str, ...], owners: set, names: list[str]) -> dict:
    found = next((r for r in noms if r.asset_kind in kinds and r.person_id in owners and (_matches(r, names) or _scheme(r.asset_kind, names))), None)
    if found is None:
        return {"state": "not_on_file", "nominees": [], "from": None}
    return {"state": found.status, "nominees": [n["name"] for n in found.nominees or []], "from": found.source_document}


NOT_APPLICABLE = {"state": "not_applicable", "nominees": [], "from": None}


async def _members(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> tuple[Household, list[Person], list[LegalEntity]]:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise RegisterError("Household not found.")
    people = list((await session.execute(select(Person).where(Person.household_id == household_id, Person.firm_id == firm.id)
                                         .order_by(Person.full_name))).scalars().all())
    entities = list((await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id, LegalEntity.firm_id == firm.id)
                                           .order_by(LegalEntity.name))).scalars().all())
    return household, people, entities


async def assets_of(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> tuple[list[dict], list[dict]]:
    """Every asset and debt on file for the household, each with its owner, figure and nomination."""
    household, people, entities = await _members(session, firm, household_id)
    names = {p.id: p.full_name for p in people} | {e.id: e.name for e in entities}
    adults = {p.id for p in people}
    noms = await india_estate._latest(session, AssetNomination, firm.id, "person_id", "asset_kind", "label",
                                      where=[AssetNomination.household_id == household_id])
    base = (firm.base_currency or "INR").upper()
    assets: list[dict] = []

    def row(key: str, category: str, kind: str, label: str, owner_id, held_as, value, currency, value_on, source, nomination, *,
            entity: bool = False, synthetic: bool = False) -> dict:
        return {"key": key, "category": category, "kind": kind, "label": label, "owner": names.get(owner_id) if owner_id else "Joint",
                "owner_id": str(owner_id) if owner_id else None, "entity_owned": entity, "held_as": held_as,
                "value": round(float(value), 2) if value is not None else None, "currency": currency,
                "value_on": value_on.isoformat() if isinstance(value_on, date) else value_on, "synthetic": synthetic,
                "source": source, "nomination": nomination}

    owners = [c for c in (Mandate.person_id.in_([p.id for p in people]) if people else None,
                          Mandate.entity_id.in_([e.id for e in entities]) if entities else None) if c is not None]
    held = (await session.execute(select(Account, Mandate).join(Mandate, Mandate.id == Account.mandate_id).where(
        Account.firm_id == firm.id, Account.status != "closed", or_(*owners)).order_by(Account.name))).all() if owners else []
    for a, m in held:
        owner = m.person_id or m.entity_id
        if m.entity_id:
            nomination = NOT_APPLICABLE          # an HUF or a trust nominates nobody
        else:
            named = (await session.execute(select(AccountBeneficiary.beneficiary_name).where(AccountBeneficiary.account_id == a.id)
                                           .order_by(AccountBeneficiary.beneficiary_name))).scalars().all()
            nomination = ({"state": "nominated", "nominees": list(named), "from": "the account's record"} if named
                          else _recorded(noms, ("demat", "mutual_fund"), {owner}, [a.name]))
        worth = await account_valuation(session, a)
        assets.append(row(f"account:{a.id}", "account", "account", a.name, owner, a.registration_type, worth["total_value"], base,
                          None, a.custodian, nomination, entity=bool(m.entity_id), synthetic=any(x.get("price_source") == "synthetic" for x in worth.get("positions") or [])))

    sheet = await balance_sheet.for_household(session, household_id, firm_id=firm.id)
    liabilities = []
    for i in (sheet or {}).get("items", []):
        owner = uuid.UUID(i["person_id"]) if i.get("person_id") else None
        if i["side"] == "liability":
            liabilities.append({"key": f"balance:{i['id']}", "kind": i["kind"], "label": i["name"], "owner": names.get(owner) if owner else "Joint",
                                "owner_id": i.get("person_id"), "value": i["amount"], "currency": i["currency"], "value_on": i["as_of"],
                                "source": i["source_document"]})
            continue
        # An item replaced by its lines is listed by those; one left out only for its currency is still the family's.
        if i["side"] != "asset" or (not i["counted"] and (i["currency"] or base).upper() == base):
            continue
        kinds = NOMINABLE.get(i["kind"])
        nomination = _recorded(noms, kinds, {owner} if owner else adults, [i["name"]]) if kinds else NOT_APPLICABLE
        assets.append(row(f"balance:{i['id']}", "outside", i["kind"], i["name"], owner, None, i["amount"], i["currency"],
                          i["as_of"], i["source_document"], nomination))
    for line in await held_away.current_lines(session, household_id, firm_id=firm.id):
        owner = line.person_id or line.entity_id
        label = f"{line.instrument_name} at {line.institution}"
        if line.entity_id:
            nomination = NOT_APPLICABLE
        else:
            found = [n for n in (line.account_label, line.instrument_name, line.institution) if n]
            nomination = _recorded(noms, ("demat", "mutual_fund", *SCHEMES), {owner} if owner else adults, found)
        assets.append(row(f"held:{line.id}", "held_away", "held_away_investment", label, owner, line.account_label, line.value,
                          line.currency, line.value_on, line.source_document, nomination, entity=bool(line.entity_id)))

    policies = (await session.execute(select(ProtectionPolicy).where(
        ProtectionPolicy.firm_id == firm.id, ProtectionPolicy.household_id == household_id, ProtectionPolicy.status == "in_force")
        .order_by(ProtectionPolicy.kind, ProtectionPolicy.provider))).scalars().all()
    for p in policies:
        if p.kind not in POLICY_NOMINABLE or p.rider_of or p.group_cover:
            continue
        nomination = ({"state": "nominated", "nominees": [p.nominee], "from": "the policy's record"} if p.nominee
                      else {"state": "not_on_file", "nominees": [], "from": None})
        assets.append(row(f"policy:{p.id}", "policy", p.kind, f"{p.kind.replace('_', ' ').capitalize()} cover, {p.provider}"
                          + (f" {p.policy_number}" if p.policy_number else ""), p.person_id,
                          "Married Women's Property Act" if p.mwp else None, p.sum_insured, p.currency, p.as_of, p.source_document, nomination))
    for g in await india_estate.gratuity_of(session, firm.id, household_id):
        assets.append(row(f"gratuity:{g['person_id']}:{g['employer']}", "gratuity", "gratuity", f"Gratuity, {g['employer']}",
                          uuid.UUID(g["person_id"]), None, g["amount"], base, g["as_of"], g["document"], NOT_APPLICABLE))
    return assets, liabilities


def missing_nominations(assets: list[dict]) -> list[dict]:
    return [{"key": a["key"], "label": a["label"], "owner": a["owner"], "category": a["category"], "kind": a["kind"],
             "non_book": a["category"] != "account"} for a in assets if a["nomination"]["state"] == "not_on_file"]


def _own(assets: list[dict], person_id: uuid.UUID) -> list[dict]:
    """What a person's will speaks for: their own assets and the joint ones. An HUF's or a trust's are not theirs."""
    return [a for a in assets if not a["entity_owned"] and a["owner_id"] in (str(person_id), None)]


async def _wills(session: AsyncSession, firm: Firm, ids: list[uuid.UUID]) -> tuple[dict, dict, dict]:
    ids = ids or [uuid.uuid4()]
    documents = {d.person_id: d for d in (await session.execute(select(EstateDocument).where(
        EstateDocument.person_id.in_(ids), EstateDocument.kind == "will").order_by(EstateDocument.signed_on, EstateDocument.created_at))).scalars().all()}
    statuses = {r.person_id: r for r in (await session.execute(select(WillStatus).where(
        WillStatus.firm_id == firm.id, WillStatus.person_id.in_(ids)).order_by(WillStatus.created_at, WillStatus.id))).scalars().all()}
    checks = {r.person_id: r for r in (await session.execute(select(WillCheck).where(
        WillCheck.firm_id == firm.id, WillCheck.person_id.in_(ids)).order_by(WillCheck.checked_on, WillCheck.created_at))).scalars().all()}
    return documents, statuses, checks


def _identity(a: dict) -> str:
    """One asset across statements: what it is, its name and whose, not the row it is recorded on, so a newer figure for
    the same asset is not a change to the register."""
    return f"{a['category']}|{_norm(a['label'])}|{a['owner_id'] or 'joint'}"


def check_state(will: EstateDocument | None, check: WillCheck | None, assets: list[dict], events: list[LifeEvent], today: date) -> dict:
    """Where the yearly check of a will against the register stands, and what moved since the last one."""
    if will is None:
        return {"state": "no_will", "changes": [], "last_checked_on": None, "due_on": None, "outcome": None}
    if check is None:
        return {"state": "never_checked", "changes": [], "last_checked_on": None, "due_on": None, "outcome": None}
    before = {x["key"]: x["label"] for x in check.snapshot or []}
    now = {_identity(x): x["label"] for x in assets}
    changes = [f"added: {now[k]}" for k in now if k not in before] + [f"no longer on file: {before[k]}" for k in before if k not in now]
    changes += [f"{e.kind.replace('_', ' ')} on {e.happened_on.isoformat()}" for e in events
                if e.kind in client_file.RESHAPES_AN_ESTATE and e.happened_on > check.checked_on]
    if will.signed_on > check.will_signed_on:
        changes.append(f"a newer will, signed {will.signed_on.isoformat()}")
    due = reviews.add_months(check.checked_on, CHECK_MONTHS)
    state = ("changed_since" if changes else "needs_change" if check.outcome == "needs_change" else "due" if today >= due else "checked")
    return {"state": state, "changes": changes, "last_checked_on": check.checked_on.isoformat(), "due_on": due.isoformat(),
            "outcome": check.outcome, "findings": check.findings, "recorded_by": check.recorded_by}


def will_facts(will: EstateDocument | None, status: WillStatus | None, today: date) -> list[str]:
    """What the facts recorded about a will leave open. Only a will on file is read."""
    if will is None:
        return []
    need = int(law_figures.value(WITNESSES_FIGURE, today))
    flags = []
    witnesses = (status.witnesses if status else None) or []
    if not witnesses:
        flags.append("witnesses not recorded")
    elif len(witnesses) < need:
        flags.append(f"{len(witnesses)} witness recorded, {need} expected")
    if not (status and status.executor):
        flags.append("no executor recorded")
    elif status.executor_told is None:
        flags.append("whether the executor has been told is not recorded")
    elif status.executor_told is False:
        flags.append("the executor has not been told")
    heirs = {_norm(h) for h in will.named or []}
    both = [w for w in witnesses if _norm(w) in heirs]
    if both:
        flags.append(f"a witness is also named to inherit ({', '.join(both)}): for counsel")
    return flags


async def _people_rows(session: AsyncSession, firm: Firm, household_id: uuid.UUID, people: list[Person], assets: list[dict], today: date) -> list[dict]:
    grown = [p for p in people if p.deceased_on is None and adult(p, today)]
    documents, statuses, checks = await _wills(session, firm, [p.id for p in grown])
    events = list((await session.execute(select(LifeEvent).where(LifeEvent.household_id == household_id).order_by(LifeEvent.happened_on))).scalars().all())
    rows = []
    for p in grown:
        will, status = documents.get(p.id), statuses.get(p.id)
        mine = _own(assets, p.id)
        happened = [e for e in events if e.person_id in (None, p.id)]
        rows.append({
            "person_id": str(p.id), "name": p.full_name,
            "will": None if will is None else {"signed_on": will.signed_on.isoformat(), "held_by": will.held_by, "document": will.source_document,
                                               "named": list(will.named or [])},
            "witnesses": (status.witnesses if status else None), "executor": status.executor if status else None,
            "executor_told": status.executor_told if status else None,
            "executor_told_on": status.executor_told_on.isoformat() if status and status.executor_told_on else None,
            "flags": will_facts(will, status, today), "assets": len(mine),
            "check": check_state(will, checks.get(p.id), mine, happened, today)})
    return rows


async def trust_triggers(session: AsyncSession, firm: Firm, household_id: uuid.UUID, people: list[Person], entities: list[LegalEntity],
                         assets: list[dict], wishes: list[EstateWish], today: date) -> list[dict]:
    """Facts on file that bring a private trust into view. Each says what it was read from; none is advice."""
    names = {p.id: p.full_name for p in people}
    out = []
    minors = [p for p in people if p.deceased_on is None and (a := _age(p, today)) is not None and a < ADULT_FROM]
    if minors:
        out.append({"key": "minor_beneficiary", "label": "A minor in the household",
                    "evidence": [f"{p.full_name}, age {_age(p, today)}, from the date of birth on file" for p in minors]})
    for kind in ("special_needs_beneficiary", "business_succession", "asset_protection"):
        said = [w for w in wishes if w.kind == kind]
        if said:
            out.append({"key": kind, "label": WISH_KINDS[kind],
                        "evidence": [f"{names.get(w.person_id, 'A member')}, recorded {w.as_of.isoformat()}: {w.words}" for w in said]})
    business = [f"{a['label']} ({a['owner']})" for a in assets if a["kind"] == "business"]
    business += [e.name for e in entities if str(e.entity_type) in ("company", "partnership")]
    if business:
        out.append({"key": "business", "label": "A business on file", "evidence": business})
    held = [e.name for e in entities if str(e.entity_type) == "trust"]
    if held:
        out.append({"key": "trust_on_file", "label": "A trust is already on file", "evidence": held})
    return out


async def _wishes(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> list[EstateWish]:
    rows = (await session.execute(select(EstateWish).where(EstateWish.firm_id == firm_id, EstateWish.household_id == household_id)
                                  .order_by(EstateWish.created_at, EstateWish.id))).scalars().all()
    latest: dict = {}
    for w in rows:                       # the latest for one person, kind and asset stands
        latest[(w.person_id, w.kind, _norm(w.asset_label))] = w
    return list(latest.values())


async def household_register(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    if not applies(firm):
        return {"applies": False}
    household, people, entities = await _members(session, firm, household_id)
    today = firm_time.today(firm)
    assets, liabilities = await assets_of(session, firm, household_id)
    wishes = await _wishes(session, firm.id, household_id)
    names = {p.id: p.full_name for p in people}
    rows = await _people_rows(session, firm, household_id, people, assets, today)
    missing = missing_nominations(assets)
    return {"applies": True, "household_id": str(household.id), "household": household.name, "as_of": today.isoformat(),
            "assets": assets, "liabilities": liabilities, "missing_nominations": missing,
            "wishes": [{"id": str(w.id), "person_id": str(w.person_id), "person": names.get(w.person_id), "kind": w.kind,
                        "kind_label": WISH_KINDS[w.kind], "asset_label": w.asset_label, "words": w.words, "as_of": w.as_of.isoformat(),
                        "document": w.source_document} for w in wishes],
            "people": rows,
            "trust_triggers": await trust_triggers(session, firm, household_id, people, entities, assets, wishes, today),
            "counts": {"assets": len(assets), "liabilities": len(liabilities), "missing_nominations": len(missing),
                       "missing_non_book": sum(1 for m in missing if m["non_book"]),
                       "wills_to_check": sum(1 for r in rows if r["check"]["state"] in ("never_checked", "due", "changed_since", "needs_change")),
                       "without_will": sum(1 for r in rows if r["will"] is None)},
            "check_months": CHECK_MONTHS, "check_months_assumed": True,
            "figures": law_figures.cited([WITNESSES_FIGURE, "in_will_stamp_registration", "in_witness_legatee", "in_no_inheritance_tax"], today),
            "note": NOT_ON_FILE + " Laid out for the client's lawyer; nothing here is drafting or advice."}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every active household's counts: missing nominations, wills to check and trust triggers."""
    if not applies(firm):
        return {"applies": False, "households": []}
    out = []
    for h in (await session.execute(select(Household).where(Household.firm_id == firm.id, Household.left_on.is_(None))
                                    .order_by(Household.name))).scalars().all():
        v = await household_register(session, firm, h.id)
        out.append({"household_id": v["household_id"], "household": v["household"], **v["counts"],
                    "triggers": [t["label"] for t in v["trust_triggers"]],
                    "to_check": [r["name"] for r in v["people"] if r["check"]["state"] in ("never_checked", "due", "changed_since", "needs_change")]})
    return {"applies": True, "as_of": firm_time.today(firm).isoformat(), "households": out,
            "summary": {k: sum(h[k] for h in out) for k in ("missing_nominations", "missing_non_book", "wills_to_check", "without_will")},
            "check_months": CHECK_MONTHS, "note": NOT_ON_FILE}


# ── Recording ─────────────────────────────────────────────────────────────────

def _need(firm: Firm | None) -> None:
    if not applies(firm):
        raise RegisterError("This is recorded for an Indian firm.")


async def prepare_wish(session: AsyncSession, firm: Firm, *, person_id, kind: str | None, words: str | None, document: str | None,
                       asset_label: str | None = None, as_of=None) -> dict:
    _need(firm)
    person = await _person(session, firm, person_id)
    what = (kind or "wish").strip().lower().replace("-", "_").replace(" ", "_")
    if what not in WISH_KINDS:
        raise RegisterError(f"The kind is one of {', '.join(WISH_KINDS)}.")
    said = " ".join((words or "").split())
    if len(said) < 5 or len(said) > 1000:
        raise RegisterError("Write what was said, in 5 to 1,000 characters.")
    today = firm_time.today(firm)
    day = _day(as_of, "day it was said") or today
    if day > today:
        raise RegisterError("It cannot be dated in the future.")
    asset = " ".join((asset_label or "").split())[:200] or None
    for w in await _wishes(session, firm.id, person.household_id):
        if w.person_id == person.id and w.kind == what and _norm(w.asset_label) == _norm(asset) and w.words == said:
            raise RegisterError("That is already what is recorded.")
    return {"person": person, "kind": what, "words": said, "asset_label": asset, "as_of": day, "document": _document(document)}


async def record_wish(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    p = await prepare_wish(session, firm, **terms)
    row = EstateWish(firm_id=firm.id, household_id=p["person"].household_id, person_id=p["person"].id, kind=p["kind"], words=p["words"],
                     asset_label=p["asset_label"], as_of=p["as_of"], source_document=p["document"], recorded_by=by, created_at=utcnow())
    session.add(row)
    await session.flush()
    # The ledger names the record and who it is about, never the words: a wish may hold what a client would not want repeated.
    await ledger.append_entry(session, firm_id=firm.id, event_type="estate_wish_recorded", actor=by, content={
        "person_id": str(p["person"].id), "kind": row.kind, "document": row.source_document})
    return await household_register(session, firm, p["person"].household_id)


async def prepare_check(session: AsyncSession, firm: Firm, *, person_id, outcome: str | None, document: str | None,
                        findings: str | None = None, checked_on=None) -> dict:
    from app.aurea_core import advisers

    _need(firm)
    person = await _person(session, firm, person_id)
    today = firm_time.today(firm)
    if person.deceased_on:
        raise RegisterError(f"{person.full_name} has died; a will is no longer checked against what they own.")
    documents, statuses, checks = await _wills(session, firm, [person.id])
    will = documents.get(person.id)
    if will is None:
        raise RegisterError(f"{person.full_name} has no will on file: record the will as an estate document first. {NOT_ON_FILE}")
    result = (outcome or "").strip().lower().replace(" ", "_")
    if result not in OUTCOMES:
        raise RegisterError("The outcome is matches or needs_change.")
    words = " ".join((findings or "").split())
    if result == "needs_change" and len(words) < 10:
        raise RegisterError("Say what in the will no longer matches what is on file.")
    day = _day(checked_on, "day it was checked") or today
    if day > today:
        raise RegisterError("A check is recorded once it has been made.")
    if day < will.signed_on:
        raise RegisterError(f"The will on file was signed on {will.signed_on.isoformat()}; the check follows it.")
    last = checks.get(person.id)
    if last is not None and day < last.checked_on:
        raise RegisterError(f"A check of {last.checked_on.isoformat()} is already on file; a later one stands over an earlier.")
    assets, _debts = await assets_of(session, firm, person.household_id)
    mine = _own(assets, person.id)
    if last is not None and last.checked_on == day and last.outcome == result and (last.findings or "") == words \
            and {x["key"] for x in last.snapshot or []} == {_identity(a) for a in mine}:
        raise RegisterError("That check is already recorded.")
    adviser = await advisers.adviser_of(session, firm.id, person.household_id)
    adviser = adviser if adviser is not None and adviser.is_active else None
    return {"person": person, "will": will, "outcome": result, "findings": words or None, "checked_on": day, "document": _document(document),
            "assets": mine, "adviser": adviser, "task_due": today + timedelta(days=TASK_DAYS) if result == "needs_change" else None,
            "missing": missing_nominations(mine)}


async def record_check(session: AsyncSession, firm: Firm, *, by: str, by_user: uuid.UUID | None = None, **terms) -> dict:
    """Record a will check. One that needs change raises a task for the client's recorded adviser; the will is not touched."""
    p = await prepare_check(session, firm, **terms)
    person = p["person"]
    task = None
    if p["outcome"] == "needs_change":
        adviser = p["adviser"]
        task = Task(firm_id=firm.id, household_id=person.household_id, title=f"Will to be updated: {person.full_name}"[:tasks.TITLE_MAX],
                    detail=f"Checked {p['checked_on'].isoformat()} against what is on file: {p['findings']}\nDrafting is for the client's lawyer.",
                    status=TaskStatus.OPEN, due_date=p["task_due"], source=CHECK_SOURCE, subject_label=person.full_name,
                    assigned_to=adviser.id if adviser else None, assigned_by=by_user if adviser else None)
        session.add(task)
        await session.flush()
    row = WillCheck(firm_id=firm.id, household_id=person.household_id, person_id=person.id, checked_on=p["checked_on"], outcome=p["outcome"],
                    findings=p["findings"], snapshot=[{"key": _identity(a), "label": a["label"]} for a in p["assets"]],
                    will_signed_on=p["will"].signed_on, source_document=p["document"], recorded_by=by, task_id=task.id if task else None,
                    created_at=utcnow())
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="will_check_recorded", actor=by, content={
        "person_id": str(person.id), "outcome": row.outcome, "checked_on": row.checked_on.isoformat(), "assets": len(p["assets"]),
        "task_id": str(task.id) if task else None, "document": row.source_document})
    return await household_register(session, firm, person.household_id)
