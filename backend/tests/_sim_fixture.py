"""A small demonstration firm for the simulation tests: slug "demo", an adviser, operations and an admin, two
households with people, a mandate, an account and holdings on real-looking prices, a trust, and pipeline contacts at
lead and qualified. A second firm ("other") that never opts in to anything. Not a seed: tests only."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from app.core.db import utcnow
from app.models.crm import CrmContact, CrmOpportunity
from app.models.enums import AssetClass, MarketType, UserRole
from app.models.graph import Account, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price, TaxLot
from app.models.tenant import Firm


@dataclass
class Fixture:
    firm: Firm
    other: Firm
    adviser: User
    operations: User
    admin: User
    households: dict
    contacts: dict


async def build(s) -> Fixture:
    firm = Firm(id=uuid.uuid4(), name="Demo Wealth", slug="demo", base_currency="NZD", jurisdiction="NZ", settings={})
    other = Firm(id=uuid.uuid4(), name="Other", slug="other", base_currency="NZD", jurisdiction="NZ", settings={})
    s.add_all([firm, other])
    await s.flush()

    def user(role, email, name, f=firm):
        u = User(id=uuid.uuid4(), firm_id=f.id, email=email, full_name=name, role=role, hashed_password="x", is_active=True)
        s.add(u)
        return u
    adviser = user(UserRole.ADVISER, "sophie.adviser@aurea.demo", "Sophie Tran")
    ops = user(UserRole.OPERATIONS, "operations@aurea.demo", "Grace Lim")
    admin = user(UserRole.ADMIN, "admin@aurea.demo", "Platform Admin")
    user(UserRole.ADVISER, "a@other", "Other Adviser", other)
    await s.flush()

    today = date.today()
    instruments = {}
    for sym, cls, ccy, px in (("AAPL", AssetClass.EQUITY, "USD", 228.0), ("MSFT", AssetClass.EQUITY, "USD", 415.0),
                              ("NZB", AssetClass.FIXED_INCOME, "NZD", 1.05)):
        i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=sym, asset_class=cls, market_type=MarketType.PUBLIC,
                       currency=ccy)
        s.add(i)
        await s.flush()
        for d in (0, 30, 90, 180, 365):
            s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today - timedelta(days=d), close=px * (1 - d / 3650),
                        currency=ccy, source="yahoo", is_real=True))
        instruments[sym] = i

    households = {}
    for name, people, book in (("The Chen Family", [("Wei Chen", date(1962, 4, 2)), ("Mei Chen", date(1965, 9, 9))],
                                [("AAPL", 400, 150.0), ("NZB", 50_000, 1.0)]),
                               ("The Nguyen Family", [("Binh Nguyen", date(1971, 5, 2))], [("MSFT", 120, 300.0)])):
        h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
        s.add(h)
        await s.flush()
        persons = []
        for full, dob in people:
            p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name=full, date_of_birth=dob)
            s.add(p)
            persons.append(p)
        await s.flush()
        s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id, to_type="household",
                               to_id=h.id, attributes={}))
        m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=persons[0].id, name=f"{name} - Growth", mandate_type="advisory")
        s.add(m)
        await s.flush()
        a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} joint", currency="NZD", cash_balance=25_000)
        s.add(a)
        await s.flush()
        for sym, qty, cost in book:
            hold = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=instruments[sym].id, quantity=qty,
                           market_value=qty * cost, cost_basis=qty * cost)
            s.add(hold)
            await s.flush()
            s.add(TaxLot(firm_id=firm.id, holding_id=hold.id, quantity=qty, cost_per_unit=cost, acquired_on=today - timedelta(days=400)))
        households[name] = {"household": h, "persons": persons, "account": a, "mandate": m}
    s.add(LegalEntity(id=uuid.uuid4(), firm_id=firm.id, household_id=households["The Chen Family"]["household"].id,
                      name="Chen Family Trust", entity_type="trust"))

    contacts = {}
    now = utcnow()
    for name, kind, stage in (("James Whitaker", "prospect", "lead"), ("Grace Liu", "prospect", "qualified"),
                              ("Dana Okafor, CPA", "centre_of_influence", None)):
        c = CrmContact(id=uuid.uuid4(), firm_id=firm.id, full_name=name, contact_type=kind, owner_user_id=adviser.id, is_active=True)
        s.add(c)
        await s.flush()
        if stage:
            s.add(CrmOpportunity(firm_id=firm.id, contact_id=c.id, title=f"{name} - new relationship", stage=stage,
                                 opened_at=now - timedelta(days=20)))
        contacts[name] = c
    await s.flush()
    return Fixture(firm, other, adviser, ops, admin, households, contacts)
