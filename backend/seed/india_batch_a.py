"""India batch A on the demonstration firm: overseas remittances, a SIP's debit mandate, the shelf's other classes, a pledge of units and the
tax habits (B-293, B-295, B-296, B-299).

Every record is invented for the demonstration firm and says so in its evidence. Dates are days before the firm's today, so nothing ages. Run from
`seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on a firm built before it; it adds nothing where a remittance is on file.

  Karthik Iyer      two remittances abroad this financial year with the tax collected as the advice shows it (USD 100,000 of the limit used);
                    Lakshmi Iyer has none, so she reads "not assessed". A verified bank account and a NACH mandate behind a SIP that ends
                    next year; 5,000 of his Nifty ETF units pledged to a lender; dividends on file from two payers (one above the deduction line)
  The Kapoor trust  a SIP with no mandate on file (warned, not stopped)
  Rajesh Sharma     a PPF with the rate its statement states
  Meera Sharma      an NPS account (the exit split laid out on the corpus as stated) and 100 HDFC Bank shares given to her by her husband, with the
                    dividends her holding paid after it, added back to him
  Aarav Sharma      a cash gift from his father, a specified relative, so exempt
  Subir Banerjee    a Senior Citizens' Savings Scheme account and a government security
  Nandini Kapoor    a Sovereign Gold Bond
  Subir Banerjee    (also) a market-linked debenture
  Elaine D'Souza    a unit-linked policy with its charges laid out
  The Menons        a cash gift from Vikram to his minor daughter Ira: the clubbing rule applies, and a cash gift's income is not on file
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, india_savings, lrs, money_in, pledges, sip, sip_mandates, tax_habits
from app.core.security import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.india_batch_a import LrsRemittance
from app.models.portfolio import Instrument, Transaction
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India batch A (demonstration)"
DEMO_BANK = "A demonstration bank (invented)"


async def _account_of(s: AsyncSession, firm: Firm, person: Person | None) -> Account | None:
    if person is None:
        return None
    return (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id)
                            .where(Mandate.firm_id == firm.id, Mandate.person_id == person.id).order_by(Account.created_at))).scalars().first()


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(LrsRemittance.id).where(LrsRemittance.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    inst = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    n = 0
    karthik, lakshmi = people.get("Karthik Iyer"), people.get("Lakshmi Iyer")
    k_account = await _account_of(s, firm, karthik)

    if karthik is not None:
        await lrs.record(s, firm, UserRole.ADVISER, by=BY, person_id=karthik.id, remitted_on=today - timedelta(days=120), amount_usd=40_000, amount_inr=3_360_000,
                         purpose="investment", bank=DEMO_BANK, tcs=472_000, document=f"Bank's remittance advice, filed ({SOURCE})")
        await lrs.record(s, firm, UserRole.ADVISER, by=BY, person_id=karthik.id, remitted_on=today - timedelta(days=45), amount_usd=60_000, amount_inr=5_100_000,
                         purpose="investment", bank=DEMO_BANK, tcs=1_020_000, document=f"Bank's remittance advice, filed ({SOURCE})")
        n += 2

    if karthik is not None and k_account is not None:
        await money_in.record_link(s, firm.id, by=BY, account_id=k_account.id, bank=DEMO_BANK, last4="4821", owner_name=karthik.full_name, method="voided_cheque",
                                   evidence=f"Voided cheque, filed ({SOURCE})", verified_on=today - timedelta(days=210))
        fund = inst.get("PPFAS-FLEXI-DIRECT")
        if fund is not None:
            await sip.record(s, firm, k_account.id, by=BY, instrument=fund.symbol, amount=10_000, day_of_month=5, starts_on=today - timedelta(days=200),
                             ends_on=today + timedelta(days=300), evidence_ref=None, simulated=True)
            n += 1
        await sip_mandates.record(s, firm, UserRole.ADVISER, by=BY, account_id=k_account.id, reference="UMRN-DEMO-IYER-0001", mandate_type="nach", per_debit_limit=25_000,
                                  starts_on=today - timedelta(days=200), expires_on=today + timedelta(days=3650), last4="4821", status="active",
                                  document=f"NACH registration, filed ({SOURCE})")
        nifty = inst.get("NIFTYBEES.NS")
        if nifty is not None:
            await pledges.record(s, firm, UserRole.ADVISER, by=BY, account_id=k_account.id, instrument=nifty.symbol, lender="A demonstration lender (invented)", units=5_000,
                                 marked_on=today - timedelta(days=90), document=f"Lender's pledge confirmation, filed ({SOURCE})")
            n += 1
        n += 2
        # Dividends on file: one payer above the deduction line, one below it.
        for symbol, amount, days in (("INFY.NS", 9_000, 100), ("INFY.NS", 8_000, 30), ("TCS.NS", 6_000, 60)):
            if symbol in inst:
                s.add(Transaction(firm_id=firm.id, account_id=k_account.id, instrument_id=inst[symbol].id, txn_type="dividend", quantity=0, price=0, amount=amount,
                                  trade_date=today - timedelta(days=days), lineage={"source": "demonstration", "seed": "india_batch_a"}))
                n += 1

    kapoor_account = await _account_of(s, firm, people.get("Nandini Kapoor"))
    if kapoor_account is None:       # the trust holds the demat: find any account of the Kapoor household's mandates
        trust = homes.get("The Kapoor Family Trust")
        if trust is not None:
            from app.models.graph import LegalEntity

            entity = (await s.execute(select(LegalEntity).where(LegalEntity.household_id == trust.id))).scalars().first()
            if entity is not None:
                kapoor_account = (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id)
                                                  .where(Mandate.firm_id == firm.id, Mandate.entity_id == entity.id))).scalars().first()
    if kapoor_account is not None and "PPFAS-FLEXI-DIRECT" in inst:
        try:
            await sip.record(s, firm, kapoor_account.id, by=BY, instrument="PPFAS-FLEXI-DIRECT", amount=20_000, day_of_month=12, starts_on=today - timedelta(days=100),
                             ends_on=today + timedelta(days=300), evidence_ref=None, simulated=True)
            n += 1
        except sip.SipError:
            pass

    async def savings(home: str, who: str | None, **terms):
        nonlocal n
        h = homes.get(home)
        if h is None:
            return
        await india_savings.record(s, firm, UserRole.ADVISER, by=BY, household_id=h.id, person_id=people[who].id if who and who in people else None,
                                   value_on=today - timedelta(days=20), document=f"Statement, filed ({SOURCE})", **terms)
        n += 1

    await savings("The Sharma Family", "Rajesh Sharma", kind="ppf", institution="A demonstration bank (invented)", value=1_850_000, rate=7.1,
                  maturity_on=(today + timedelta(days=2400)).isoformat(), details={"opened_on": (today - timedelta(days=2800)).isoformat()})
    await savings("The Sharma Family", "Meera Sharma", kind="nps", institution="A demonstration pension fund manager (invented)", value=2_400_000, details={"tier": "Tier I"})
    await savings("The Banerjee Household", "Subir Banerjee", kind="scss", institution="A demonstration post office (invented)", value=3_000_000, rate=8.2,
                  maturity_on=(today + timedelta(days=1100)).isoformat())
    await savings("The Banerjee Household", "Subir Banerjee", kind="g_sec", institution="RBI Retail Direct (demonstration)", value=500_000)
    await savings("The Kapoor Family Trust", "Nandini Kapoor", kind="sgb", institution="A demonstration depository participant (invented)", value=210_000,
                  maturity_on=(today + timedelta(days=1500)).isoformat(), details={"grams": 20, "series": "A demonstration series (invented)"})
    await savings("The Banerjee Household", "Subir Banerjee", kind="mld", institution="A demonstration depository participant (invented)", value=1_000_000,
                  details={"issuer": "A demonstration issuer (invented)"})
    await savings("The D'Souza Household", "Elaine D'Souza", kind="ulip", institution="A demonstration insurer (invented)", value=1_450_000,
                  details={"started_on": (today - timedelta(days=1100)).isoformat(), "annual_premium": 500_000, "fund_management_charge": 1.35,
                           "charges_note": "Premium allocation, policy administration and mortality charges as the benefit illustration states them (demonstration)"})

    rajesh, meera, aarav = people.get("Rajesh Sharma"), people.get("Meera Sharma"), people.get("Aarav Sharma")
    meera_account = await _account_of(s, firm, meera)
    if rajesh and meera and "HDFCBANK.NS" in inst:
        await tax_habits.record_gift(s, firm, UserRole.ADVISER, by=BY, giver_id=rajesh.id, receiver_id=meera.id, relationship="spouse", gifted_on=today - timedelta(days=150),
                                     value=69_000, description="100 shares of HDFC Bank", instrument="HDFCBANK.NS", quantity=100,
                                     document=f"Gift deed and the depository's transfer slip, filed ({SOURCE})")
        n += 1
        if meera_account is not None:
            s.add(Transaction(firm_id=firm.id, account_id=meera_account.id, instrument_id=inst["HDFCBANK.NS"].id, txn_type="dividend", quantity=0, price=0, amount=11_400,
                              trade_date=today - timedelta(days=60), lineage={"source": "demonstration", "seed": "india_batch_a"}))
            n += 1
    if rajesh and aarav:
        await tax_habits.record_gift(s, firm, UserRole.ADVISER, by=BY, giver_id=rajesh.id, receiver_id=aarav.id, relationship="adult_child", gifted_on=today - timedelta(days=75),
                                     value=200_000, description="A cash gift toward his postgraduate study", document=f"Gift deed and the bank's transfer advice, filed ({SOURCE})")
        n += 1
    vikram, ira = people.get("Vikram Menon"), people.get("Ira Menon")
    if vikram and ira:
        await tax_habits.record_gift(s, firm, UserRole.ADVISER, by=BY, giver_id=vikram.id, receiver_id=ira.id, relationship="minor_child", gifted_on=today - timedelta(days=50),
                                     value=500_000, description="A cash gift into a deposit in her name", document=f"Gift deed and the bank's deposit advice, filed ({SOURCE})")
        n += 1
    await s.flush()
    return n
