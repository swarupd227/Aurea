"""Keep the Indian demonstration's dated scenarios true on the day it is shown (B-374).

The seed writes dates relative to the day it runs, so a month later the HUF's "loss about to go long-term" is long-term, a complaint
"at risk" is overdue and a certificate "due soon" has expired. Run from `seed.india_demo_backfill`, this moves those scenarios back to
the same distance from the firm's today.

It touches the demonstration firm alone, and in it only rows the demonstration seed wrote, found by what the seed wrote on them (its
author and its evidence, or, for a tax lot, the account, the instrument, the quantity and having been created with the firm). A row a
person has since acted on is left. What is meant to be history stays: the overdue, resolved and withdrawn complaints, the lapsed
certificate, the overdue policy, counsel's reviews, remittances and orders of the current year. A record that belongs to the current
tax year is moved only once that year has passed. An open reminder task about a moved record follows it; none is raised, closed or
reopened. Safe to run any number of times.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import adviser_certification, firm_time, governance, india_reminders, markets
from app.aurea_core.reviews import add_months
from app.models.complaints import Complaint, ComplaintEvent
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.graph import Account, Person
from app.models.identity import User
from app.models.india_batch_a import LrsRemittance, TaxGift
from app.models.india_batch_c import AdviserCertification
from app.models.india_gates import AccreditedInvestor
from app.models.india_governance import GovernancePolicy
from app.models.kyc import KycRecord
from app.models.portfolio import Holding, Instrument, TaxLot, Transaction
from app.models.sip import Sip
from app.models.tenant import Firm
from seed import india_b388, india_batch_a, india_l100a, india_row10, india_row12, india_row14
from seed.india_demo import HOUSEHOLDS, SLUG

BUILT_WITHIN = timedelta(hours=1)        # a lot the build wrote was created with the firm
SHORT_TERM_DAYS = 365                    # a lot seeded younger than this is meant to be short-term
BATCH_C_BY = "seed: India batch C (demonstration)"

# The seed's own figures, named here as the seed modules write them: (amount or certificate, days).
COMPLAINT_CLOSES = {"resolved", "withdrawn"}
COMPLAINT_LIMIT_DAYS = 21                # a case seeded older than this is meant to be overdue, and stays
POLICIES_IN_DATE = ("grievance", "business_continuity", "conflict_of_interest")
KARTHIK_KYC_DUE_IN = 40
ACCREDITED_REF, ACCREDITED_FROM, ACCREDITED_TO = "ACC/DEMO/2026/0412", -200, 165
CERTIFICATES = {"DEMO-XA-0001": (-600, 495), "DEMO-XB-0001": (-1035, 60)}
REMITTANCES = {40_000: 120, 60_000: 45}
DIVIDENDS = {("INFY.NS", 9_000): 100, ("INFY.NS", 8_000): 30, ("TCS.NS", 6_000): 60, ("HDFCBANK.NS", 11_400): 60}
GIFTS = {69_000: 150, 200_000: 75, 500_000: 50}
SIP_RUNS_FOR, SIP_EXTEND_WITHIN = 300, 60


def _taxpayers():
    """(name, spec) for each person and entity the build gave a demat."""
    for home in HOUSEHOLDS:
        for person in home["people"]:
            if person.get("taxpayer"):
                yield person["name"], person["taxpayer"]
        if home.get("entity"):
            yield home["entity"]["name"], home["entity"]["taxpayer"]


async def _follow_task(s: AsyncSession, firm: Firm, due: date, *, title: str | None = None, starts: str | None = None, new_title: str | None = None) -> int:
    """Move the open reminder task about a record to the record's new day."""
    rows = (await s.execute(select(Task).where(Task.firm_id == firm.id, Task.source == india_reminders.SOURCE, Task.status == TaskStatus.OPEN))).scalars().all()
    changed = 0
    for t in rows:
        if (title and t.title != title) or (starts and not t.title.startswith(starts)):
            continue
        t.due_date = due
        t.detail = re.sub(r"^Due \d{4}-\d{2}-\d{2}", f"Due {due.isoformat()}", india_reminders.corrected_detail(t.detail) or "")
        if new_title:
            t.title = new_title[:280]
        changed += 1
    return changed


async def _lots(s: AsyncSession, firm: Firm, today: date) -> int:
    await s.refresh(firm, ["created_at"])
    accounts = {a.name: a for a in (await s.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
    instruments = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    moved = 0
    for name, spec in _taxpayers():
        account = accounts.get(f"{name} demat")
        if account is None:
            continue
        for symbol, lots in spec["holdings"]:
            inst = instruments.get(symbol)
            holding = (await s.execute(select(Holding).where(Holding.account_id == account.id, Holding.instrument_id == inst.id))).scalars().first() if inst else None
            if holding is None:
                continue
            rows = (await s.execute(select(TaxLot).where(TaxLot.holding_id == holding.id))).scalars().all()
            built = [r for r in rows if r.source_system is None and abs(r.created_at - firm.created_at) <= BUILT_WITHIN]
            later = [r for r in rows if r not in built]
            for quantity, _cost, days in lots:
                if days >= SHORT_TERM_DAYS:
                    continue
                match = [r for r in built if float(r.quantity) == float(quantity)]
                if len(match) != 1:           # sold in part since, or not the seed's: left as it is
                    continue
                lot, target = match[0], today - timedelta(days=days)
                # Never past a lot bought since: first in, first out reads the order of the dates.
                if lot.acquired_on == target or any(lot.acquired_on < r.acquired_on <= target for r in later):
                    continue
                lot.acquired_on = target
                moved += 1
    return moved


async def _this_year(s: AsyncSession, firm: Firm, today: date) -> int:
    """What the demonstration shows in the current tax year, moved into it once the year it was written in has passed."""
    start = markets.tax_year(markets.MARKETS["IN"], today)["start"]

    def into_year(days: int) -> date:
        return max(today - timedelta(days=days), start)

    accounts = {a.id: a for a in (await s.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
    instruments = {i.id: i.symbol for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    sold = {(f"{name} demat", sym, float(qty)): (held, ago) for name, spec in _taxpayers() for sym, qty, _pct, held, ago in spec.get("sold", [])}
    moved = 0
    txns = (await s.execute(select(Transaction).where(Transaction.firm_id == firm.id, Transaction.trade_date < start,
                                                      Transaction.txn_type.in_(("sell", "dividend"))))).scalars().all()
    for t in txns:
        lineage = t.lineage or {}
        if lineage.get("source") != "demonstration":
            continue
        symbol = instruments.get(t.instrument_id)
        if t.txn_type == "dividend" and lineage.get("seed") == "india_batch_a" and (symbol, int(t.amount)) in DIVIDENDS:
            t.trade_date = into_year(DIVIDENDS[(symbol, int(t.amount))])
            moved += 1
        elif t.txn_type == "sell" and "seed" not in lineage and t.account_id in accounts:
            spec = sold.get((accounts[t.account_id].name, symbol, float(t.quantity)))
            if spec is None:
                continue
            held, ago = spec
            t.trade_date = into_year(ago)
            t.lineage = {**lineage, "lots": [{**lot, "acquired_on": (t.trade_date - timedelta(days=held)).isoformat()} for lot in lineage.get("lots", [])]}
            moved += 1
    for r in (await s.execute(select(LrsRemittance).where(LrsRemittance.firm_id == firm.id, LrsRemittance.recorded_by == india_batch_a.BY,
                                                          LrsRemittance.remitted_on < start))).scalars().all():
        if int(r.amount_usd) in REMITTANCES:
            r.remitted_on = into_year(REMITTANCES[int(r.amount_usd)])
            moved += 1
    for g in (await s.execute(select(TaxGift).where(TaxGift.firm_id == firm.id, TaxGift.recorded_by == india_batch_a.BY,
                                                    TaxGift.gifted_on < start))).scalars().all():
        if int(g.value) in GIFTS:
            g.gifted_on = into_year(GIFTS[int(g.value)])
            moved += 1
    return moved


async def _complaints(s: AsyncSession, firm: Firm, today: date) -> int:
    """The complaints meant to be open, at risk and newly received, as long ago as the seed received them."""
    moved = 0
    for _home, _who, _source, _category, days, summary, _scores, steps in india_row10.CASES:
        if days > COMPLAINT_LIMIT_DAYS or COMPLAINT_CLOSES & {k for k, *_ in steps}:
            continue
        c = (await s.execute(select(Complaint).where(Complaint.firm_id == firm.id, Complaint.recorded_by == india_row10.BY,
                                                     Complaint.summary == summary))).scalars().first()
        if c is None or india_row10.SOURCE not in (c.document or ""):
            continue
        events = (await s.execute(select(ComplaintEvent).where(ComplaintEvent.complaint_id == c.id))).scalars().all()
        if any(e.recorded_by != india_row10.BY or e.kind not in {k for k, *_ in steps} for e in events):
            continue                    # a person has acted on it since
        delta = (today - timedelta(days=days)) - c.received_on
        if not delta:
            continue
        old = c.received_on
        c.received_on = old + delta
        for e in events:
            e.happened_on = e.happened_on + delta
        task = await s.get(Task, c.task_id) if c.task_id else None
        if task is not None and task.status == TaskStatus.OPEN and task.due_date is not None:
            was, task.due_date = task.due_date, task.due_date + delta
            task.detail = (task.detail or "").replace(old.isoformat(), c.received_on.isoformat()).replace(was.isoformat(), task.due_date.isoformat())
        moved += 1
    return moved


async def _policies(s: AsyncSession, firm: Firm, today: date) -> int:
    ago = {kind: days for kind, _v, _o, days, _by, _r in india_row14.POLICIES}
    moved = 0
    for kind in POLICIES_IN_DATE:
        row = (await s.execute(select(GovernancePolicy).where(GovernancePolicy.firm_id == firm.id, GovernancePolicy.kind == kind)
                               .order_by(GovernancePolicy.created_at.desc(), GovernancePolicy.approved_on.desc()))).scalars().first()
        if row is None or row.recorded_by != india_row14.BY or india_row14.SOURCE not in row.source_document:
            continue                    # a person recorded a later version
        delta = (today - timedelta(days=ago[kind])) - row.approved_on
        if not delta:
            continue
        row.approved_on, row.next_review_on = row.approved_on + delta, row.next_review_on + delta
        await _follow_task(s, firm, row.next_review_on, title=f"India: {governance.POLICIES[kind][0]} policy review")
        moved += 1
    return moved


async def _kyc_due(s: AsyncSession, firm: Firm, today: date) -> int:
    """Karthik Iyer's re-KYC, forty days ahead."""
    row = (await s.execute(select(KycRecord).join(Person, Person.id == KycRecord.person_id).where(
        KycRecord.firm_id == firm.id, Person.full_name == "Karthik Iyer").order_by(KycRecord.created_at.desc()))).scalars().first()
    if row is None or row.recorded_by != india_row12.BY:
        return 0
    target = add_months(today, -96) + timedelta(days=KARTHIK_KYC_DUE_IN)
    if row.kyc_completed_on == target:
        return 0
    row.kyc_completed_on = target
    return 1


async def _certificates(s: AsyncSession, firm: Firm, today: date) -> int:
    """The accredited investor's certificate in force, and the adviser's two NISM certificates."""
    moved = 0
    row = (await s.execute(select(AccreditedInvestor).join(Person, Person.id == AccreditedInvestor.person_id).where(
        AccreditedInvestor.firm_id == firm.id, Person.full_name == "Nandini Kapoor").order_by(AccreditedInvestor.created_at.desc()))).scalars().first()
    if row is not None and row.recorded_by == india_l100a.BY and row.certificate_ref == ACCREDITED_REF and row.state == "granted":
        target = (today + timedelta(days=ACCREDITED_FROM), today + timedelta(days=ACCREDITED_TO))
        if (row.valid_from, row.valid_to) != target:
            row.valid_from, row.valid_to = target
            moved += 1
    rows = (await s.execute(select(AdviserCertification).where(AdviserCertification.firm_id == firm.id)
                            .order_by(AdviserCertification.created_at, AdviserCertification.issued_on))).scalars().all()
    standing = {(r.user_id, r.kind): r for r in rows}
    for r in standing.values():
        if r.recorded_by != BATCH_C_BY or r.certificate_number not in CERTIFICATES:
            continue
        issued, expires = (today + timedelta(days=d) for d in CERTIFICATES[r.certificate_number])
        if (r.issued_on, r.expires_on) == (issued, expires):
            continue
        r.issued_on, r.expires_on = issued, expires
        who = await s.get(User, r.user_id)
        if who is not None:
            starts = f"India: {adviser_certification.KINDS[r.kind]} for {who.full_name}:"
            await _follow_task(s, firm, expires, starts=starts, new_title=f"{starts} expires {expires.isoformat()}")
        moved += 1
    return moved


async def _sips(s: AsyncSession, firm: Firm, today: date) -> int:
    rows = (await s.execute(select(Sip).where(Sip.firm_id == firm.id, Sip.recorded_by.in_([india_batch_a.BY, india_b388.BY]), Sip.simulated.is_(True),
                                              Sip.ended_on.is_(None)))).scalars().all()
    moved = 0
    for r in rows:
        if r.ends_on is not None and r.ends_on < today + timedelta(days=SIP_EXTEND_WITHIN):
            r.ends_on = today + timedelta(days=SIP_RUNS_FOR)
            moved += 1
    return moved


async def refresh(s: AsyncSession, firm: Firm) -> dict[str, int]:
    """Move each dated scenario back to its distance from today. Nothing for any firm but the demonstration."""
    if firm.slug != SLUG or (firm.settings or {}).get("demonstration") is not True:
        return {}
    today = firm_time.today(firm)
    out = {"tax lots": await _lots(s, firm, today), "this tax year": await _this_year(s, firm, today),
           "complaints": await _complaints(s, firm, today), "policies": await _policies(s, firm, today),
           "KYC": await _kyc_due(s, firm, today), "certificates": await _certificates(s, firm, today), "SIPs": await _sips(s, firm, today),
           "mandates and banks": await india_b388.redate(s, firm, today)}
    await s.flush()
    return out
