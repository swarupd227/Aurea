"""Astra's tables to the contract and back, entity by entity.

Each entity names its model, the fields that carry across unchanged (contract name: model attribute), the references
to other records (by the other record's external id in the same source), and the few values named another way: an
instrument by its symbol, a person in the firm by their email, a model portfolio by its name. Anything a model has that
is not here stays Astra's and is never written by a sync.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.foundation import contract as c
from app.models.crm import CrmActivity, CrmContact, CrmOpportunity
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TaxLot, Transaction


@dataclass
class Spec:
    entity: str
    model: type
    record: type
    fields: dict[str, str]                                         # contract field -> model attribute
    refs: dict[str, tuple[str, str]] = field(default_factory=dict)  # contract field -> (model attribute, entity)
    symbol: str | None = None                                       # model attribute an instrument's symbol resolves to
    people: dict[str, str] = field(default_factory=dict)            # contract email field -> model user-id attribute
    model_name: str | None = None                                   # model attribute a model portfolio's name resolves to
    sourced: bool = True                                            # carries source_system / external_id / as_of


def _same(*names: str) -> dict[str, str]:
    return {n: n for n in names}


SPECS: dict[str, Spec] = {s.entity: s for s in (
    Spec("household", Household, c.Household,
         _same("name", "segment", "values", "notes", "review_cadence_months", "left_on")),
    Spec("person", Person, c.Person,
         _same("full_name", "preferred_name", "email", "date_of_birth", "segment", "kyc", "profile", "is_next_gen",
               "deceased_on"),
         refs={"household_ref": ("household_id", "household")}),
    Spec("legal_entity", LegalEntity, c.LegalEntity,
         _same("name", "entity_type", "governance", "impact_objectives", "jurisdiction"),
         refs={"household_ref": ("household_id", "household")}),
    Spec("mandate", Mandate, c.Mandate,
         _same("name", "mandate_type", "suitability", "constraints", "is_active"),
         refs={"person_ref": ("person_id", "person"), "entity_ref": ("entity_id", "legal_entity")},
         model_name="model_portfolio_id"),
    Spec("account", Account, c.Account,
         _same("name", "account_number", "custodian", "currency", "cash_balance", "registration_type", "status",
               "closed_on"),
         refs={"mandate_ref": ("mandate_id", "mandate")}),
    Spec("instrument", Instrument, c.Instrument,
         _same("symbol", "name", "asset_class", "market_type", "currency", "market_symbol", "values_tags",
               "private_attributes"), sourced=False),
    Spec("price", Price, c.Price, {"price_date": "as_of", "close": "close", "currency": "currency", "feed": "source",
                                   "is_real": "is_real"}, symbol="instrument_id", sourced=False),
    Spec("holding", Holding, c.Holding,
         _same("quantity", "market_value", "cost_basis", "confidence"),
         refs={"account_ref": ("account_id", "account")}, symbol="instrument_id"),
    Spec("tax_lot", TaxLot, c.TaxLot,
         _same("quantity", "cost_per_unit", "acquired_on"),
         refs={"holding_ref": ("holding_id", "holding")}),
    Spec("txn", Transaction, c.Transaction,
         _same("txn_type", "quantity", "price", "amount", "trade_date", "lineage"),
         refs={"account_ref": ("account_id", "account")}, symbol="instrument_id"),
    Spec("crm_contact", CrmContact, c.Contact,
         {**_same("full_name", "email", "phone", "contact_type", "notes", "is_active"), "lead_source": "source"},
         people={"owner_email": "owner_user_id"}),
    Spec("crm_opportunity", CrmOpportunity, c.Opportunity,
         _same("title", "stage", "estimated_aum", "probability_pct", "expected_close_date", "lost_reason", "opened_at",
               "closed_at"),
         refs={"contact_ref": ("contact_id", "crm_contact")}),
    Spec("crm_activity", CrmActivity, c.Activity,
         _same("activity_type", "occurred_at", "detail"),
         refs={"contact_ref": ("contact_id", "crm_contact"), "opportunity_ref": ("opportunity_id", "crm_opportunity")},
         people={"logged_by_email": "logged_by_user_id"}),
)}

# The tables of each domain, as the summary counts them.
DOMAIN_ENTITIES: dict[str, tuple[str, ...]] = {d: tuple(r.entity for r in recs) for d, recs in c.DOMAINS.items()}


# ── Values as compared: one plain form, whatever the database hands back ──────────────────────────────

def plain(v: Any) -> Any:
    """A value in the one form two sides are compared in: a number as a rounded float, a date as ISO, an instant in
    UTC (SQLite forgets the zone), an id as text, an enum as its value."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (Decimal, float, int)):
        return round(float(v), 6)
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    return str(v)


def snapshot(spec: Spec, values: dict[str, Any]) -> dict[str, Any]:
    return {k: plain(v) for k, v in sorted(values.items())}


def columns_of(spec: Spec) -> list[str]:
    """Every model attribute a sync may write."""
    cols = list(spec.fields.values()) + [a for a, _ in spec.refs.values()] + list(spec.people.values())
    if spec.symbol:
        cols.append(spec.symbol)
    if spec.model_name:
        cols.append(spec.model_name)
    return cols


def row_values(spec: Spec, row: Any) -> dict[str, Any]:
    return {a: getattr(row, a) for a in columns_of(spec)}


def _nullable(spec: Spec, attr: str) -> bool:
    col = spec.model.__table__.columns.get(attr)
    return True if col is None else bool(col.nullable)


# ── Out: a row as a contract record ───────────────────────────────────────────────────────────────

class Names:
    """What the firm's rows are called in a source: each row's external id, an instrument's symbol, a user's email
    and a model's name. For the native adapter a row's external id is its own Astra id."""

    def __init__(self) -> None:
        self.ids: dict[str, dict[uuid.UUID, str]] = {}
        self.symbols: dict[uuid.UUID, str] = {}
        self.emails: dict[uuid.UUID, str] = {}
        self.models: dict[uuid.UUID, str] = {}

    @classmethod
    async def native(cls, session: AsyncSession, firm_id: uuid.UUID) -> "Names":
        from app.models.identity import User

        n = cls()
        n.symbols = {i.id: i.symbol for i in (await session.execute(
            select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()}
        n.emails = {u.id: u.email for u in (await session.execute(
            select(User).where(User.firm_id == firm_id))).scalars().all()}
        n.models = {m.id: m.name for m in (await session.execute(
            select(ModelPortfolio).where(ModelPortfolio.firm_id == firm_id))).scalars().all()}
        return n

    def ext(self, entity: str, local_id: uuid.UUID | None) -> str | None:
        if local_id is None:
            return None
        return self.ids.get(entity, {}).get(local_id, str(local_id))


def to_record(spec: Spec, row: Any, names: Names, *, source: str, external_id: str, as_of: datetime | None) -> Any:
    data: dict[str, Any] = {"source": source, "external_id": external_id, "as_of": as_of}
    for cf, attr in spec.fields.items():
        v = getattr(row, attr)
        data[cf] = float(v) if isinstance(v, Decimal) else (str(v) if isinstance(v, str) else v)   # an enum as its value
    for cf, (attr, entity) in spec.refs.items():
        data[cf] = names.ext(entity, getattr(row, attr))
    if spec.symbol:
        data["symbol"] = names.symbols.get(getattr(row, spec.symbol)) if getattr(row, spec.symbol) else None
    for cf, attr in spec.people.items():
        uid = getattr(row, attr)
        data[cf] = names.emails.get(uid) if uid else None
    if spec.model_name:
        mid = getattr(row, spec.model_name)
        data["model_portfolio"] = names.models.get(mid) if mid else None
    return spec.record(**data)


async def rows_of(session: AsyncSession, spec: Spec, firm_id: uuid.UUID) -> list[Any]:
    return list((await session.execute(
        select(spec.model).where(spec.model.firm_id == firm_id).order_by(spec.model.created_at, spec.model.id)
    )).scalars().all())


# ── In: a contract record as the values a row takes ─────────────────────────────────────────────

class Unresolved(Exception):
    """A record refers to something this firm does not have (yet): it is skipped and counted, never guessed."""


class Resolver:
    """Local ids for what a record names: another record by its external id (through external_ref), an instrument by
    symbol, a person by email, a model by name, all within one firm."""

    def __init__(self, session: AsyncSession, firm_id: uuid.UUID, source: str):
        self.session, self.firm_id, self.source = session, firm_id, source
        self._refs: dict[tuple[str, str], uuid.UUID | None] = {}
        self._symbols: dict[str, uuid.UUID] | None = None
        self._emails: dict[str, uuid.UUID] | None = None
        self._models: dict[str, uuid.UUID] | None = None

    def remember(self, entity: str, external_id: str, local_id: uuid.UUID) -> None:
        self._refs[(entity, external_id)] = local_id

    def forget_symbols(self) -> None:
        self._symbols = None

    async def local(self, entity: str, external_id: str) -> uuid.UUID | None:
        key = (entity, external_id)
        if key not in self._refs:
            from app.models.foundation import ExternalRef

            self._refs[key] = (await self.session.execute(select(ExternalRef.local_id).where(
                ExternalRef.firm_id == self.firm_id, ExternalRef.source == self.source, ExternalRef.entity_type == entity,
                ExternalRef.external_id == external_id))).scalar_one_or_none()
        return self._refs[key]

    async def symbol(self, symbol: str) -> uuid.UUID | None:
        if self._symbols is None:
            self._symbols = {i.symbol: i.id for i in (await self.session.execute(
                select(Instrument).where(Instrument.firm_id == self.firm_id))).scalars().all()}
        return self._symbols.get(symbol)

    async def email(self, email: str | None) -> uuid.UUID | None:
        if not email:
            return None
        if self._emails is None:
            from app.models.identity import User

            self._emails = {u.email.lower(): u.id for u in (await self.session.execute(
                select(User).where(User.firm_id == self.firm_id))).scalars().all() if u.email}
        return self._emails.get(email.lower())

    async def model(self, name: str | None) -> uuid.UUID | None:
        if not name:
            return None
        if self._models is None:
            self._models = {}
            for m in (await self.session.execute(select(ModelPortfolio).where(
                    ModelPortfolio.firm_id == self.firm_id).order_by(ModelPortfolio.created_at))).scalars().all():
                self._models.setdefault(m.name, m.id)
        return self._models.get(name)


async def values_of(spec: Spec, record: Any, r: Resolver) -> dict[str, Any]:
    """The values a row takes from a record. Raises Unresolved for a required reference this firm cannot place."""
    out: dict[str, Any] = {}
    for cf, attr in spec.fields.items():
        out[attr] = getattr(record, cf)
    for cf, (attr, entity) in spec.refs.items():
        ext = getattr(record, cf)
        if ext is None:
            out[attr] = None
            continue
        local = await r.local(entity, ext)
        if local is None:
            raise Unresolved(f"{spec.entity} {record.external_id}: no {entity} {ext}")
        out[attr] = local
    if spec.symbol:
        sym = getattr(record, "symbol", None)
        local = await r.symbol(sym) if sym else None
        if sym and local is None:
            raise Unresolved(f"{spec.entity} {record.external_id}: no instrument {sym}")
        out[spec.symbol] = local
    for cf, attr in spec.people.items():
        out[attr] = await r.email(getattr(record, cf))   # nobody here by that email is unassigned, not guessed
    if spec.model_name:
        out[spec.model_name] = await r.model(getattr(record, "model_portfolio", None))
    # A column that takes no empty value keeps its default rather than being emptied.
    return {k: v for k, v in out.items() if v is not None or _nullable(spec, k)}
