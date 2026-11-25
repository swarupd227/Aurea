"""Money held elsewhere, from the Account Aggregator and a consolidated account statement (India row 9).

Two ways in, one destination: both end as `held_away_holding` lines, and everything `held_away.py` and `balance_sheet.py`
say about them holds unchanged: the most detailed figure wins, net worth counts it once, performance is price return on
real closes and a line with no price here has none.

**A consent is what a person recorded.** An Account Aggregator consent artefact is the client's own consented flow of
financial information: its purpose, the kinds of information, the date range of the data, how it is fetched (once or
periodically) and when it lapses. A person records it from the artefact as filed and it is added and never edited; it is
ended once, when the client withdraws it or the aggregator says so, with the reason. Nothing is pulled without an active
consent: `permitted` is the one gate any feed reads, and a feed delivers nothing for a consent that has lapsed, was
withdrawn or has not started, nor outside its kinds, its person or its dates. A pull that no longer delivers a line ends
it, kept on file (`held_away_feeds`). Whether to delete data after a withdrawal is the firm's retention policy and its
counsel's, not decided here. The Account Aggregator itself is a source: this build has none, so the stand-in lives in
`app/simulations/aa_feed` and plugs in at `held_away_feeds.register` as the real client will.

**An e-CAS is a statement a person supplies.** A CAMS or KFintech mutual fund statement, or an NSDL or CDSL demat
statement, is read by a person into lines (account, ISIN, name, units, value) from the document. Each line is checked,
a line that fails is listed and refused, and the rest become held-away lines carrying the statement's name and date.
Nothing is invented: a figure the statement does not give is not filled, an asset class is not guessed from an ISIN,
and a PAN is neither asked for nor kept. A line already delivered by the aggregator is left to it, so nothing counts
twice. The statement's rules (monthly if active, half-yearly if not, demat-only mutual fund units not in a CAS) are the
depositories' and are not modelled. This is not a statement that the firm's data handling is compliant.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, held_away, held_away_feeds, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.aggregator import AaConsent, EcasStatement
from app.models.graph import Household, Person
from app.models.held_away import HeldAwayHolding
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
READERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE, UserRole.OPERATIONS,
                     UserRole.BRANCH_LEADER, UserRole.ADMIN})
CITATION = ("RBI Master Direction on Non-Banking Financial Company - Account Aggregator (2016) and the ReBIT specifications, "
            "as read in the research report; the texts were not re-read, so a qualified person checks them")
# The kinds of information an aggregator carries for investments, from the research report's reading of Sahamati's list
# (29 May 2026). Bonds, G-secs, EPF and PPF were still "proposed" in the latest table it found, so they are not offered.
FI_TYPES = {"mutual_funds": "Mutual funds", "equities": "Equities (demat)", "etfs": "ETFs", "reits_invits": "REITs and InvITs",
            "aif": "Alternative investment funds", "nps": "National Pension System"}
# ASSUMED: the purpose codes are ReBIT's as this build recalls them, and the list was not re-read.
PURPOSES = {"101": "Wealth management service", "103": "Aggregated statement"}
FETCH_TYPES = {"onetime": "One time", "periodic": "Periodic"}
CAS_KINDS = {"cams_kfintech": "CAMS / KFintech mutual fund statement", "nsdl": "NSDL demat statement", "cdsl": "CDSL demat statement"}
CAS_INSTITUTION = {"cams_kfintech": "Mutual fund folios (CAMS / KFintech CAS)", "nsdl": "Demat account (NSDL CAS)",
                   "cdsl": "Demat account (CDSL CAS)"}
ISIN = re.compile(r"^IN[EF][0-9A-Z]{8}[0-9]$")
MAX_LINES = 500
SOURCE_PREFIX = "aa:"


class AggregatorError(ValueError):
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
        raise AggregatorError(f"Give the {label}, from the client's page.")


def state_of(c: AaConsent, today: date) -> str:
    if c.revoked_on is not None:
        return "revoked"
    if today > c.expires_on:
        return "expired"
    if today < c.consent_start:
        return "not_started"
    return "active"


def external_prefix(c: AaConsent) -> str:
    return f"{SOURCE_PREFIX}{c.artefact_id}:"


def view(c: AaConsent, today: date, *, lines: int | None = None) -> dict:
    return {"id": str(c.id), "household_id": str(c.household_id), "person_id": str(c.person_id) if c.person_id else None,
            "artefact_id": c.artefact_id, "aggregator": c.aggregator, "purpose_code": c.purpose_code,
            "purpose": PURPOSES.get(c.purpose_code, c.purpose_code), "fi_types": list(c.fi_types or []),
            "fi_labels": [FI_TYPES.get(k, k) for k in (c.fi_types or [])], "fetch_type": c.fetch_type,
            "data_from": c.data_from.isoformat(), "data_to": c.data_to.isoformat(), "consent_start": c.consent_start.isoformat(),
            "expires_on": c.expires_on.isoformat(), "days_left": (c.expires_on - today).days if c.revoked_on is None else None,
            "state": state_of(c, today), "revoked_on": c.revoked_on.isoformat() if c.revoked_on else None,
            "revoked_reason": c.revoked_reason, "document": c.document, "recorded_by": c.recorded_by,
            "last_pull_on": c.last_pull_on.isoformat() if c.last_pull_on else None, "pulls": c.pulls or 0, "lines": lines,
            "simulated": str(c.source_system or "").startswith("simulated:")}


# -- The gate any feed reads ---------------------------------------------------------------------------------

async def permitted(session: AsyncSession, firm: Firm, today: date | None = None) -> list[AaConsent]:
    """Consents under which information may be fetched today: not revoked, started and not lapsed."""
    today = today or firm_time.today(firm)
    rows = (await session.execute(select(AaConsent).where(AaConsent.firm_id == firm.id).order_by(AaConsent.created_at))).scalars().all()
    return [c for c in rows if state_of(c, today) == "active"]


def permits(c: AaConsent, *, fi_type: str, value_on: date, person_id: uuid.UUID | None, household_id: uuid.UUID) -> bool:
    """Whether a line of this kind, as of this day, for this owner is inside the consent."""
    if household_id != c.household_id or fi_type not in (c.fi_types or []):
        return False
    if not (c.data_from <= value_on <= c.data_to):
        return False
    return c.person_id is None or person_id == c.person_id


async def mark_pulled(session: AsyncSession, consents: list[AaConsent], today: date) -> None:
    for c in consents:
        c.last_pull_on, c.pulls = today, (c.pulls or 0) + 1
    await session.flush()


# -- Consents ------------------------------------------------------------------------------------------------

async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "consents": [], "statements": [], "counts": {}}
    rows = (await session.execute(select(AaConsent).where(AaConsent.firm_id == firm.id).order_by(AaConsent.created_at.desc()))).scalars().all()
    held = (await session.execute(select(HeldAwayHolding.external_id).where(
        HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.status == "current", HeldAwayHolding.external_id.like(f"{SOURCE_PREFIX}%")))).scalars().all()
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    people = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    out = []
    for c in rows:
        v = view(c, today, lines=sum(1 for e in held if e and e.startswith(external_prefix(c))))
        v["household"], v["person"] = names.get(c.household_id), people.get(c.person_id) if c.person_id else None
        out.append(v)
    stmts = (await session.execute(select(EcasStatement).where(EcasStatement.firm_id == firm.id).order_by(EcasStatement.created_at.desc()))).scalars().all()
    counts = {s: sum(1 for v in out if v["state"] == s) for s in ("active", "expired", "revoked", "not_started")}
    return {"applies": True, "as_of": today.isoformat(), "consents": out, "counts": counts, "citation": CITATION,
            "statements": [{"id": str(s.id), "household": names.get(s.household_id), "household_id": str(s.household_id),
                            "kind": s.kind, "kind_label": CAS_KINDS.get(s.kind, s.kind), "statement_date": s.statement_date.isoformat(),
                            "document": s.document, "recorded_by": s.recorded_by, "added": s.added, "replaced": s.replaced,
                            "lines": len(s.lines or [])} for s in stmts],
            "fi_types": FI_TYPES, "note": ("Nothing is fetched without an active consent. What is delivered becomes held-away lines, "
                                           "measured only on real prices. This does not say the firm's data handling is compliant.")}


def _day(value, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise AggregatorError(f"Give the {label} as YYYY-MM-DD.")


def _list(value) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip().lower() for v in (value or []) if str(v).strip()]


async def prepare_consent(session: AsyncSession, firm: Firm, *, household_id, artefact_id: str | None, aggregator: str | None,
                          purpose_code: str | None, fi_types, data_from, data_to, expires_on, document: str | None,
                          fetch_type: str | None = "onetime", person_id=None, consent_start=None) -> dict:
    if not applies(firm):
        raise AggregatorError("An Account Aggregator consent is recorded for an Indian firm.")
    today = firm_time.today(firm)
    hh = await session.get(Household, _uuid(household_id, "household id"))
    if hh is None or hh.firm_id != firm.id:
        raise AggregatorError("That household is not this firm's.")
    person = None
    if person_id:
        person = await session.get(Person, _uuid(person_id, "person id"))
        if person is None or person.household_id != hh.id:
            raise AggregatorError("That person is not in the household.")
    art = (artefact_id or "").strip()
    if len(art) < 4:
        raise AggregatorError("Give the consent artefact's id, as the aggregator shows it.")
    if (await session.execute(select(AaConsent.id).where(AaConsent.firm_id == firm.id, AaConsent.artefact_id == art))).first():
        raise AggregatorError(f"Consent artefact {art} is already recorded. A new consent is a new artefact.")
    agg = " ".join((aggregator or "").split())
    if len(agg) < 3:
        raise AggregatorError("Name the Account Aggregator that holds the consent.")
    code = (purpose_code or "").strip()
    if code not in PURPOSES:
        raise AggregatorError(f"The purpose is one of {', '.join(f'{k} ({v})' for k, v in PURPOSES.items())}.")
    kinds = _list(fi_types)
    if not kinds or any(k not in FI_TYPES for k in kinds):
        raise AggregatorError(f"Name what the consent covers, from {', '.join(FI_TYPES)}.")
    fetch = (fetch_type or "onetime").strip().lower()
    if fetch not in FETCH_TYPES:
        raise AggregatorError("The fetch type is onetime or periodic.")
    d_from, d_to, exp = _day(data_from, "start of the data range"), _day(data_to, "end of the data range"), _day(expires_on, "expiry")
    start = _day(consent_start, "consent start") if consent_start else today
    if d_from > d_to:
        raise AggregatorError("The data range starts after it ends.")
    if d_to > today:
        raise AggregatorError("The data range cannot end in the future.")
    if exp < today:
        raise AggregatorError("That consent has already lapsed; nothing could be fetched under it, so it is not recorded as one.")
    if exp < start:
        raise AggregatorError("The consent expires before it starts.")
    if len((document or "").strip()) < 4:
        raise AggregatorError("Say where the consent artefact is filed: its notice or the aggregator's screen.")
    return {"household": hh, "person": person, "artefact_id": art, "aggregator": agg[:120], "purpose_code": code, "fi_types": kinds,
            "fetch_type": fetch, "data_from": d_from, "data_to": d_to, "consent_start": start, "expires_on": exp,
            "document": document.strip()[:300]}


async def record_consent(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, source_system: str | None = None,
                         **terms) -> dict:
    p = await prepare_consent(session, firm, **terms)
    row = AaConsent(firm_id=firm.id, household_id=p["household"].id, person_id=p["person"].id if p["person"] else None,
                    artefact_id=p["artefact_id"], aggregator=p["aggregator"], purpose_code=p["purpose_code"], fi_types=p["fi_types"],
                    fetch_type=p["fetch_type"], data_from=p["data_from"], data_to=p["data_to"], consent_start=p["consent_start"],
                    expires_on=p["expires_on"], document=p["document"], recorded_by=by, note=(note or "").strip() or None,
                    source_system=source_system, created_at=utcnow())
    session.add(row)
    await session.flush()
    if source_system is None:           # a simulation writes no ledger entry, so it can be thrown away
        await ledger.append_entry(session, firm_id=firm.id, event_type="aa_consent_recorded", actor=by, content={
            "consent_id": str(row.id), "household_id": str(row.household_id), "artefact_id": row.artefact_id, "aggregator": row.aggregator,
            "purpose": row.purpose_code, "fi_types": row.fi_types, "data_from": row.data_from.isoformat(),
            "data_to": row.data_to.isoformat(), "expires_on": row.expires_on.isoformat()})
    return await overview(session, firm)


async def prepare_revoke(session: AsyncSession, firm: Firm, *, consent_id, reason: str | None, document: str | None = None) -> dict:
    c = await session.get(AaConsent, _uuid(consent_id, "consent id"))
    if c is None or c.firm_id != firm.id:
        raise AggregatorError("That consent is not on file here. read_aggregator_consents lists them.")
    if c.revoked_on is not None:
        raise AggregatorError(f"That consent was withdrawn on {c.revoked_on.isoformat()}.")
    why = " ".join((reason or "").split())
    if len(why) < 3:
        raise AggregatorError("Say why it ends: the client withdrew it, or the aggregator revoked it.")
    return {"consent": c, "reason": why[:300], "document": (document or "").strip()[:300] or None}


async def revoke_consent(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    """Ends a consent once. Nothing is fetched under it from now on. The lines already taken stay on file until the next
    pull ends them, and are deleted only by the firm's own retention decision."""
    p = await prepare_revoke(session, firm, **terms)
    c = (await session.execute(select(AaConsent).where(AaConsent.id == p["consent"].id).with_for_update()
                               .execution_options(populate_existing=True))).scalar_one()
    if c.revoked_on is not None:
        raise AggregatorError(f"That consent was withdrawn on {c.revoked_on.isoformat()}.")
    c.revoked_on, c.revoked_by, c.revoked_reason = firm_time.today(firm), by, p["reason"]
    await session.flush()
    if not str(c.source_system or "").startswith("simulated:"):
        await ledger.append_entry(session, firm_id=firm.id, event_type="aa_consent_revoked", actor=by, content={
            "consent_id": str(c.id), "artefact_id": c.artefact_id, "reason": c.revoked_reason, "document": p["document"]})
    return await overview(session, firm)


# -- e-CAS ---------------------------------------------------------------------------------------------------

COLUMNS = {"account": ("account", "folio", "demat", "demat_account", "folio_no", "dp_id"), "isin": ("isin",),
           "name": ("name", "scheme", "security", "instrument", "scheme_name"), "units": ("units", "quantity", "balance"),
           "value": ("value", "market_value", "valuation", "amount"), "institution": ("institution", "amc", "dp", "depository"),
           "holder": ("holder", "owner", "first_holder"), "symbol": ("symbol",), "asset_class": ("asset_class",)}


def _number(text, label: str) -> float | None:
    raw = str(text or "").replace(",", "").strip()
    if raw in ("", "-"):
        return None
    try:
        out = float(raw)
    except ValueError:
        raise AggregatorError(f"the {label} {text!r} is not a number")
    if out < 0:
        raise AggregatorError(f"the {label} is negative")
    return out


def parse_cas(text: str) -> tuple[list[dict], list[dict]]:
    """The lines a person read off a CAS, from CSV text with a header row (account, isin, name, units, value; optionally
    institution, holder, symbol, asset_class). Returns (lines, refused). A line is refused for a bad ISIN, a value that is not a
    number or a missing name or account; none is repaired."""
    body = (text or "").strip()
    if not body:
        raise AggregatorError("Give the statement's lines: a header row, then one row for each folio or demat holding.")
    rows = [r for r in csv.reader(io.StringIO(body)) if any(c.strip() for c in r)]
    header = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    index: dict[str, int] = {}
    for key, aliases in COLUMNS.items():
        for a in aliases:
            if a in header:
                index[key] = header.index(a)
                break
    missing = [k for k in ("account", "isin", "name", "value") if k not in index]
    if missing:
        raise AggregatorError(f"The first row must name the columns; missing {', '.join(missing)}. Expected account, isin, name, "
                              "units, value (and optionally institution, holder, symbol, asset_class).")
    if len(rows) - 1 > MAX_LINES:
        raise AggregatorError(f"A statement here is at most {MAX_LINES} lines.")
    lines, refused = [], []
    for n, r in enumerate(rows[1:], start=2):
        def cell(k):
            i = index.get(k)
            return r[i].strip() if i is not None and i < len(r) else ""
        try:
            isin = cell("isin").upper()
            if not ISIN.match(isin):
                raise AggregatorError(f"{isin or 'no ISIN'} is not an ISIN")
            if len(cell("name")) < 2 or len(cell("account")) < 2:
                raise AggregatorError("a line names its account (folio or demat) and its holding")
            value = _number(cell("value"), "value")
            if value is None:
                raise AggregatorError("a line states its value")
            lines.append({"account": cell("account")[:120], "isin": isin, "name": cell("name")[:200], "units": _number(cell("units"), "units"),
                          "value": value, "institution": cell("institution")[:120] or None, "holder": cell("holder") or None,
                          "symbol": cell("symbol").upper() or None, "asset_class": cell("asset_class").lower() or None})
        except AggregatorError as exc:
            refused.append({"row": n, "reason": str(exc)})
    return lines, refused


async def _owners(session: AsyncSession, household_id: uuid.UUID) -> dict[str, Person]:
    return {" ".join(p.full_name.lower().split()): p for p in (await session.execute(
        select(Person).where(Person.household_id == household_id))).scalars().all()}


async def prepare_ecas(session: AsyncSession, firm: Firm, *, household_id, kind: str | None, statement_date, document: str | None,
                       lines_text: str | None, person_id=None, period_from=None) -> dict:
    if not applies(firm):
        raise AggregatorError("A consolidated account statement is recorded for an Indian firm.")
    today = firm_time.today(firm)
    hh = await session.get(Household, _uuid(household_id, "household id"))
    if hh is None or hh.firm_id != firm.id:
        raise AggregatorError("That household is not this firm's.")
    k = (kind or "").strip().lower()
    if k not in CAS_KINDS:
        raise AggregatorError(f"The statement is one of {', '.join(f'{a} ({b})' for a, b in CAS_KINDS.items())}.")
    on = _day(statement_date, "statement date")
    if on > today:
        raise AggregatorError("A statement cannot be dated in the future.")
    start = _day(period_from, "start of the period") if period_from else None
    if start and start > on:
        raise AggregatorError("The period starts after the statement's date.")
    if len((document or "").strip()) < 4:
        raise AggregatorError("Say which statement this is: the depository or registrar, the period and where it is filed. A figure "
                              "with no source cannot be taken to a client.")
    owner = None
    if person_id:
        owner = await session.get(Person, _uuid(person_id, "person id"))
        if owner is None or owner.household_id != hh.id:
            raise AggregatorError("That person is not in the household.")
    lines, refused = parse_cas(lines_text or "")
    if (await session.execute(select(EcasStatement.id).where(
            EcasStatement.firm_id == firm.id, EcasStatement.household_id == hh.id, EcasStatement.kind == k,
            EcasStatement.statement_date == on,
            EcasStatement.person_id == (owner.id if owner else None) if owner else EcasStatement.person_id.is_(None)))).first():
        raise AggregatorError(f"That {CAS_KINDS[k]} dated {on.isoformat()} is already recorded for {hh.name}.")
    people = await _owners(session, hh.id)
    instruments = (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()
    by_isin = {str((i.values_tags or {}).get("isin") or "").upper(): i for i in instruments if (i.values_tags or {}).get("isin")}
    current = (await session.execute(select(HeldAwayHolding).where(HeldAwayHolding.household_id == hh.id, HeldAwayHolding.status == "current"))).scalars().all()
    ready, skipped = [], []
    for n, ln in enumerate(lines, start=1):
        who = owner
        if ln["holder"]:
            who = people.get(" ".join(ln["holder"].lower().split()))
            if who is None:
                refused.append({"row": n + 1, "reason": f"{ln['holder']} is not a person in {hh.name}"})
                continue
        inst = by_isin.get(ln["isin"])
        symbol = ln["symbol"] or (inst.symbol if inst else None)    # the firm's own instrument for that ISIN, never a guess
        institution = ln["institution"] or CAS_INSTITUTION[k]
        same = [h for h in current if h.institution == institution and h.instrument_name == ln["name"]
                and h.person_id == (who.id if who else None) and h.entity_id is None]
        if same and any(h.source_system for h in same):
            skipped.append({"row": n + 1, "reason": f"{ln['name']} is already delivered by the aggregator, so it is left to it"})
            continue
        try:
            fields = dict(institution=institution, instrument_name=ln["name"], value=ln["value"], value_on=on,
                          source_document=f"{CAS_KINDS[k]}, {on.isoformat()}: {document.strip()}"[:255],
                          person_id=who.id if who else None, account_label=ln["account"], symbol=symbol, isin=ln["isin"],
                          asset_class=ln["asset_class"], quantity=ln["units"], replaces_id=same[0].id if same else None)
            prepared = await held_away.prepare_holding(session, hh.id, firm_id=firm.id, **fields)
        except held_away.HeldAwayError as exc:
            refused.append({"row": n + 1, "reason": str(exc)})
            continue
        ready.append({"fields": fields, "prepared": prepared, "replaces": bool(same)})
    if not ready:
        raise AggregatorError("No line could be read from that statement: " + "; ".join(f"row {r['row']}: {r['reason']}" for r in (refused + skipped)[:4]))
    return {"household": hh, "owner": owner, "kind": k, "statement_date": on, "period_from": start, "document": document.strip()[:300],
            "ready": ready, "refused": refused, "skipped": skipped, "lines": lines,
            "total": round(sum(r["fields"]["value"] for r in ready), 2)}


async def record_ecas(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    p = await prepare_ecas(session, firm, **terms)
    hh = p["household"]
    stmt = EcasStatement(firm_id=firm.id, household_id=hh.id, person_id=p["owner"].id if p["owner"] else None, kind=p["kind"],
                         statement_date=p["statement_date"], period_from=p["period_from"], document=p["document"], recorded_by=by,
                         lines=[dict(ln) for ln in p["lines"]], created_at=utcnow())
    session.add(stmt)
    await session.flush()
    added = replaced = 0
    for r in p["ready"]:
        f, old = r["fields"], r["prepared"]["replacing"]
        session.add(HeldAwayHolding(
            firm_id=firm.id, household_id=hh.id, person_id=f["person_id"], institution=r["prepared"]["institution"],
            account_label=r["prepared"]["account_label"], symbol=r["prepared"]["symbol"], isin=r["prepared"]["isin"],
            instrument_name=r["prepared"]["instrument_name"], asset_class=r["prepared"]["asset_class"],
            quantity=r["prepared"]["quantity"], value=r["prepared"]["value"], currency=r["prepared"]["currency"],
            value_on=r["prepared"]["value_on"], source_document=r["prepared"]["source"], recorded_by=by, status="current",
            attributes={"ecas_statement": str(stmt.id)}))
        if old is not None:
            old.status, old.ended_on, old.ended_by = "ended", p["statement_date"], by
            old.ended_reason = f"Replaced by the statement of {p['statement_date'].isoformat()}"
            replaced += 1
        else:
            added += 1
    stmt.added, stmt.replaced = added, replaced
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="ecas_recorded", actor=by, content={
        "statement_id": str(stmt.id), "household_id": str(hh.id), "kind": p["kind"], "statement_date": p["statement_date"].isoformat(),
        "document": p["document"], "added": added, "replaced": replaced, "refused": len(p["refused"]), "skipped": len(p["skipped"])})
    held = await held_away.for_household(session, hh.id, firm_id=firm.id)
    return {"applies": True, "statement_id": str(stmt.id), "kind_label": CAS_KINDS[p["kind"]], "household": hh.name,
            "statement_date": p["statement_date"].isoformat(), "added": added, "replaced": replaced, "refused": p["refused"],
            "skipped": p["skipped"], "total": p["total"], "currency": firm.base_currency, "held_away": held}


# -- Pulling -------------------------------------------------------------------------------------------------

async def pull(session: AsyncSession, firm: Firm, *, by: str) -> dict:
    """Ask each connected aggregator source for what its active consents allow. A source that serves a firm declares
    `account_aggregator = True` and reads `permitted()` for what it may fetch. With a consent lapsed or withdrawn it delivers
    nothing for it, and the lines it delivered earlier end, kept on file."""
    if not applies(firm):
        raise AggregatorError("The Account Aggregator is read for an Indian firm.")
    feeds = [f for f in held_away_feeds.for_firm(firm) if getattr(f, "account_aggregator", False)]
    if not feeds:
        raise AggregatorError("No Account Aggregator source is connected for this firm. A person supplies a consolidated statement instead.")
    out = {"feeds": [], "added": 0, "superseded": 0, "ended": 0, "unchanged": 0, "refused": []}
    for f in feeds:
        r = await held_away_feeds.pull(session, firm, key=f.key)
        out["feeds"].append(f.key)
        for k in ("added", "superseded", "ended", "unchanged"):
            out[k] += r[k]
        out["refused"] += r["refused"]
    out["consents_used"] = len(await permitted(session, firm))
    out["simulated"] = any(getattr(f, "simulated", False) for f in feeds)
    await ledger.append_entry(session, firm_id=firm.id, event_type="aa_pull", actor=by, content={
        "feeds": out["feeds"], "added": out["added"], "superseded": out["superseded"], "ended": out["ended"],
        "consents_used": out["consents_used"]})
    return out
