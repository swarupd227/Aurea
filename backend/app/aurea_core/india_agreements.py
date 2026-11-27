"""India's agreements and disclosures, per relationship, and the NRI intake checklist (L200-1 India sections 3, 4.1 and 5.3,
B-391).

A full-service Indian firm runs several relationships with one household at once, and each has its own paper. This keeps
what a person read off each document, and lays one household's out as figures:

  mutual funds   each fund order's EUIN, or the investor's execution-only declaration (a direct plan carries neither); the
                 scheme information document (SID) and key information memorandum (KIM) the firm holds and what the client
                 was given; and the commission disclosure, asked only where the household holds or ordered a regular plan or
                 compliance recorded that a group company earns trail from it
  PMS            for each mandate on a Portfolio Manager's track: the disclosure document (given no later than the day the
                 agreement was signed), direct onboarding offered, no upfront fee, and a power of attorney no wider than
                 operating the demat account and debiting fees
  AIF            each commitment with its placement memorandum, contribution agreement, consents and the drawdown
                 obligation discussed; each drawdown from its notice, an unpaid one a task for the recorded adviser, closed
                 when a payment covering it is recorded
  broking        for each demat account: the account opening form, rights and obligations, tariff sheet and demat
                 agreement; running account authorisation and margin terms where signed
  e-stamp        the certificate on an instrument that needed stamp duty, as it states it
  NRI intake     residence, tax residence, what the client means to do with the money against the NRE or NRO accounts on file,
                 the FATCA and CRS self-certification, and for a US or Canada resident the fund-house acceptance list

Nothing on file reads "not on file" and does not mean it was not done. Every rule is a `law_figures` row marked assumed and
unreviewed. These are checks a person reads: none of them holds an order or an approval (the fund-house list does, in
`fund_acceptance`). Nothing here moves money, places an order, files anything or says the firm complies with anything.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, fund_acceptance, law_figures, markets, pms, residency, trust_owners
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.india_agreements import (
    AgreementItem, AifCommitment, AifDrawdown, AifPayment, DistributionDisclosure, EStampRecord, MfOrderEuin, SchemeDocument,
)
from app.models.india_batch_c import GroupTrail
from app.models.portfolio import Holding, Instrument
from app.models.tenant import Firm
from app.models.trading import Order
from app.provenance import ledger

READERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                     UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.ADMIN})
RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE})
SCHEME_RECORDERS = RECORDERS | {UserRole.RESEARCH_CIO}
ON_FILE, NOT_ON_FILE, BREACH = "on_file", "not_on_file", "breach"
TASK_SOURCE = "aif_drawdown"
TRANSACTED = ("placed", "partially_filled", "filled")     # a draft is a proposal, not a transaction
EUIN = re.compile(r"^E\d{6}$")
POA_POWERS = {"operate_demat": "Operate the demat account", "debit_fees": "Debit fees"}
CATEGORIES = {"aif_1": "Category I", "aif_2": "Category II", "aif_3": "Category III"}
SCHEME_KINDS = {"sid": "Scheme information document (SID)", "kim": "Key information memorandum (KIM)"}
DISCLOSURES = {"commission": "Commission disclosure", "scheme_documents": "Scheme documents"}
METHODS = {"email": "Email", "portal": "Portal", "in_person": "In person", "post": "Post", "with_application": "With the application"}
STAMPED = {"advisory_agreement": "Advisory agreement", "pms_agreement": "PMS agreement", "aif_contribution": "AIF contribution agreement",
           "power_of_attorney": "Power of attorney", "demat_agreement": "Demat agreement", "other": "Another instrument"}
# relationship -> item -> (label, the answers with the passing ones first, how many pass, whether it is asked of everyone, law)
ITEMS: dict[str, dict[str, tuple]] = {
    "pms": {
        "disclosure_document": ("Disclosure document given", ("given", "not_given"), 1, True, "in_pms_disclosure_document"),
        "direct_onboarding": ("Direct onboarding offered", ("offered", "not_offered"), 1, True, "in_pms_direct_onboarding"),
        "upfront_fee": ("Upfront fee", ("none", "charged"), 1, True, "in_pms_no_upfront_fee"),
        "poa_scope": ("Power of attorney scope", ("limited", "none", "wider"), 2, True, "in_pms_poa_scope"),
    },
    "aif": {
        "placement_memorandum": ("Private placement memorandum", ("received", "not_received"), 1, True, "in_aif_documents"),
        "contribution_agreement": ("Contribution agreement", ("signed", "not_signed"), 1, True, "in_aif_documents"),
        "investor_consent": ("Investor consents", ("given", "not_given"), 1, True, "in_aif_documents"),
        "drawdown_discussed": ("Drawdown obligation discussed", ("discussed", "not_discussed"), 1, True, "in_aif_documents"),
    },
    "broking": {
        "account_opening_form": ("Account opening form (exchange format)", ("signed", "not_signed"), 1, True, "in_broking_demat_pack"),
        "rights_obligations": ("Rights and obligations", ("given", "not_given"), 1, True, "in_broking_demat_pack"),
        "tariff_sheet": ("Tariff sheet", ("given", "not_given"), 1, True, "in_broking_demat_pack"),
        "demat_agreement": ("Demat agreement", ("signed", "not_signed"), 1, True, "in_broking_demat_pack"),
        "running_account_authorisation": ("Running account authorisation", ("signed", "not_signed"), 1, False, "in_broking_demat_pack"),
        "margin_terms": ("Margin terms", ("signed", "not_signed"), 1, False, "in_broking_demat_pack"),
    },
}
RELATIONSHIPS = {"pms": "Portfolio Management Service", "aif": "Alternative Investment Fund", "broking": "Broking and demat"}
LAW = ["in_euin_rule", "in_mf_commission_disclosure", "in_mf_scheme_documents", "in_pms_disclosure_document", "in_pms_direct_onboarding",
       "in_pms_no_upfront_fee", "in_pms_poa_scope", "in_aif_documents", "in_aif_min_ticket", "in_broking_demat_pack", "in_estamp_rule",
       "in_amc_us_canada_acceptance", "in_kyc_bo_trust"]
NOTE = ("What a person recorded from each document. Nothing on file does not mean it was not done. These checks hold no order or "
        "approval, and they do not say the firm complies with anything. The rules are assumed, for counsel.")


class AgreementError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _need(firm: Firm, role, allowed=RECORDERS) -> None:
    if not applies(firm):
        raise AgreementError("India's agreements and disclosures are recorded for an Indian firm.")
    if role not in allowed:
        raise AgreementError("These are recorded by an adviser, a paraplanner, operations or compliance, from the document.")


def _uuid(value, what: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        raise AgreementError(f"Give the {what}'s id.")


def _day(value, label: str, today: date, *, future: bool = False, default: date | None = None) -> date:
    if value in (None, "") and default is not None:
        return default
    if isinstance(value, date):
        day = value
    else:
        try:
            day = date.fromisoformat(str(value)[:10])
        except ValueError:
            raise AgreementError(f"Give the {label} as YYYY-MM-DD.")
    if not future and day > today:
        raise AgreementError(f"The {label} cannot be in the future.")
    return day


def _text(value, label: str, *, least: int = 3, most: int = 300) -> str:
    said = " ".join(str(value or "").split())
    if len(said) < least:
        raise AgreementError(f"Give {label}.")
    if len(said) > most:
        raise AgreementError(f"{label[0].upper()}{label[1:]} is at most {most} characters.")
    return said


def _document(value) -> str:
    return _text(value, "where the document is filed", least=5)


def _amount(value, label: str) -> float:
    try:
        n = round(float(str(value).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise AgreementError(f"Give the {label} in rupees, as the document states it.")
    if not 0 < n < 1e13:
        raise AgreementError(f"The {label} must be more than nothing.")
    return n


def _money(x) -> str:
    return markets.money(float(x), "INR")


async def _household(session: AsyncSession, firm: Firm, household_id) -> Household:
    h = await session.get(Household, _uuid(household_id, "household"))
    if h is None or h.firm_id != firm.id:
        raise AgreementError("No such household at this firm.")
    return h


async def _household_of_mandate(session: AsyncSession, mandate: Mandate) -> uuid.UUID | None:
    from app.aurea_core import wash_sale

    return await wash_sale.household_of(session, mandate)


async def _instrument(session: AsyncSession, firm: Firm, symbol) -> Instrument:
    inst = (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id,
                                                           Instrument.symbol == str(symbol or "").strip()))).scalar_one_or_none()
    if inst is None:
        raise AgreementError(f"No instrument with the symbol {symbol}.")
    if not fund_acceptance.is_scheme(inst):
        raise AgreementError(f"{inst.symbol} is not a mutual fund scheme on file.")
    return inst


def _plan(inst: Instrument | None) -> str | None:
    return ((inst.values_tags or {}).get("fund_plan") if inst else None)


# ── Reading ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def _latest_by(session: AsyncSession, model, firm_id: uuid.UUID, key, *where) -> dict:
    out: dict = {}
    for r in (await session.execute(select(model).where(model.firm_id == firm_id, *where)
                                    .order_by(model.created_at, model.id))).scalars().all():
        out[key(r)] = r
    return out


class _Book:
    """What one reading of the firm needs, loaded once."""

    def __init__(self) -> None:
        self.instruments: dict = {}
        self.mandates: dict = {}          # household -> [mandate]
        self.accounts: dict = {}          # mandate -> [account]
        self.holdings: dict = {}          # account -> [instrument ids]
        self.orders: dict = {}            # account -> [order]
        self.euin: dict = {}
        self.scheme_docs: dict = {}
        self.disclosures: dict = {}       # household -> [row]
        self.items: dict = {}             # (relationship, target id, item) -> row
        self.commitments: dict = {}       # household -> [row]
        self.drawdowns: dict = {}         # commitment -> [row]
        self.payments: dict = {}          # drawdown -> [row]
        self.stamps: dict = {}            # household -> [row]
        self.trail: set = set()           # households a group company earns trail from
        self.pms: set = set()             # mandate ids on a Portfolio Manager's track


def _target(r: AgreementItem):
    return r.commitment_id if r.relationship_kind == "aif" else (r.account_id if r.relationship_kind == "broking" else r.mandate_id)


async def _load(session: AsyncSession, firm: Firm) -> _Book:
    b = _Book()
    fid = firm.id
    b.instruments = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.firm_id == fid))).scalars().all()}
    for m in (await session.execute(select(Mandate).where(Mandate.firm_id == fid, Mandate.is_active.is_(True))
                                    .order_by(Mandate.name))).scalars().all():
        b.mandates.setdefault(await _household_of_mandate(session, m), []).append(m)
    for a in (await session.execute(select(Account).where(Account.firm_id == fid).order_by(Account.name))).scalars().all():
        b.accounts.setdefault(a.mandate_id, []).append(a)
    for h in (await session.execute(select(Holding).where(Holding.firm_id == fid, Holding.quantity > 0))).scalars().all():
        b.holdings.setdefault(h.account_id, []).append(h.instrument_id)
    for o in (await session.execute(select(Order).where(Order.firm_id == fid).order_by(Order.created_at, Order.id))).scalars().all():
        if str(o.status) in TRANSACTED:
            b.orders.setdefault(o.account_id, []).append(o)
    b.euin = await _latest_by(session, MfOrderEuin, fid, lambda r: r.order_id)
    b.scheme_docs = await _latest_by(session, SchemeDocument, fid, lambda r: (r.instrument_id, r.kind))
    for r in (await session.execute(select(DistributionDisclosure).where(DistributionDisclosure.firm_id == fid)
                                    .order_by(DistributionDisclosure.given_on, DistributionDisclosure.created_at))).scalars().all():
        b.disclosures.setdefault(r.household_id, []).append(r)
    b.items = await _latest_by(session, AgreementItem, fid, lambda r: (r.relationship_kind, _target(r), r.item))
    for r in (await session.execute(select(AifCommitment).where(AifCommitment.firm_id == fid)
                                    .order_by(AifCommitment.committed_on, AifCommitment.created_at))).scalars().all():
        b.commitments.setdefault(r.household_id, []).append(r)
    for r in (await session.execute(select(AifDrawdown).where(AifDrawdown.firm_id == fid)
                                    .order_by(AifDrawdown.number, AifDrawdown.created_at))).scalars().all():
        b.drawdowns.setdefault(r.commitment_id, []).append(r)
    for r in (await session.execute(select(AifPayment).where(AifPayment.firm_id == fid)
                                    .order_by(AifPayment.paid_on, AifPayment.created_at))).scalars().all():
        b.payments.setdefault(r.drawdown_id, []).append(r)
    for r in (await session.execute(select(EStampRecord).where(EStampRecord.firm_id == fid)
                                    .order_by(EStampRecord.stamped_on, EStampRecord.created_at))).scalars().all():
        b.stamps.setdefault(r.household_id, []).append(r)
    trail: dict = {}
    for r in (await session.execute(select(GroupTrail).where(GroupTrail.firm_id == fid)
                                    .order_by(GroupTrail.statement_on, GroupTrail.created_at))).scalars().all():
        trail[(r.household_id, r.entity_name.lower())] = r.kind
    b.trail = {hid for (hid, _), kind in trail.items() if kind == "earns"}
    b.pms = {m.id for m in await pms.pms_mandates(session, firm)}
    return b


def _item_view(rel: str, key: str, row: AgreementItem | None, *, signed_on: date | None = None) -> dict:
    label, answers, passing, required, law = ITEMS[rel][key]
    base = {"item": key, "label": label, "required": required, "law": law}
    if row is None:
        return {**base, "state": NOT_ON_FILE, "answer": None}
    state = ON_FILE if answers.index(row.answer) < passing else BREACH
    why = None
    if state == BREACH:
        why = {"upfront_fee": "An upfront fee is charged", "poa_scope": "Wider than operating the demat account and debiting fees",
               "direct_onboarding": "Direct onboarding was not offered"}.get(key, f"{label}: {row.answer.replace('_', ' ')}")
    elif rel == "pms" and key == "disclosure_document" and signed_on and row.dated_on > signed_on:
        state, why = BREACH, f"Given {row.dated_on.isoformat()}, after the agreement was signed {signed_on.isoformat()}"
    return {**base, "state": state, "answer": row.answer, "answer_label": row.answer.replace("_", " "), "dated_on": row.dated_on.isoformat(),
            "document": row.document, "recorded_by": row.recorded_by, "powers": (row.detail or {}).get("powers"), "why": why,
            "note": row.note}


def _funds(b: _Book, household_id: uuid.UUID, accounts: list[Account], firm: Firm) -> dict:
    orders, symbols, regular = [], {}, False
    for a in accounts:
        for iid in b.holdings.get(a.id, []):
            inst = b.instruments.get(iid)
            if fund_acceptance.is_scheme(inst):
                symbols.setdefault(inst.id, {"held": False, "ordered": False})["held"] = True
                regular = regular or _plan(inst) == "regular"
        for o in b.orders.get(a.id, []):
            inst = b.instruments.get(o.instrument_id)
            if not fund_acceptance.is_scheme(inst):
                continue
            symbols.setdefault(inst.id, {"held": False, "ordered": False})["ordered"] = True
            regular = regular or _plan(inst) == "regular"
            rec = b.euin.get(o.id)
            if _plan(inst) == "direct":
                state = "direct_plan"
            elif rec is None:
                state = "missing"
            else:
                state = "execution_only" if rec.execution_only else "recorded"
            orders.append({"order_id": str(o.id), "account": a.name, "symbol": inst.symbol, "side": str(o.side),
                           "on": firm_time.today(firm, o.created_at).isoformat() if o.created_at else None, "plan": _plan(inst),
                           "euin_state": state, "euin": rec.euin if rec else None, "arn": rec.arn if rec else None,
                           "document": rec.document if rec else None})
    given = b.disclosures.get(household_id, [])
    schemes = []
    for iid, flags in symbols.items():
        inst = b.instruments[iid]
        mine = [d for d in given if d.kind == "scheme_documents" and d.instrument_id == iid]
        got = sorted({k for d in mine for k in (d.items or [])})
        schemes.append({"symbol": inst.symbol, "fund": inst.name, "fund_house": fund_acceptance.house_of(inst), "plan": _plan(inst), **flags,
                        "on_file": {k: (b.scheme_docs[(iid, k)].dated_on.isoformat() if (iid, k) in b.scheme_docs else None) for k in SCHEME_KINDS},
                        "given": got, "given_on": mine[-1].given_on.isoformat() if mine else None})
    schemes.sort(key=lambda s: s["symbol"])
    commissions = [d for d in given if d.kind == "commission"]
    asked = regular or household_id in b.trail
    latest = commissions[-1] if commissions else None
    commission = {"asked": asked, "state": ON_FILE if latest else (NOT_ON_FILE if asked else "not_asked"),
                  "why": ("A regular plan is held or was ordered" if regular else
                          "A group company is recorded as earning trail from this household" if household_id in b.trail else
                          "No regular plan held or ordered, and no trail recorded"),
                  "given": [{"given_on": d.given_on.isoformat(), "method": METHODS.get(d.method, d.method), "terms": d.terms,
                             "symbol": b.instruments[d.instrument_id].symbol if d.instrument_id in b.instruments else None,
                             "fund_house": d.fund_house, "document": d.document} for d in commissions]}
    orders.sort(key=lambda o: (o["euin_state"] != "missing", o["on"] or "", o["symbol"]))
    return {"schemes": schemes, "orders": orders, "missing_euin": sum(1 for o in orders if o["euin_state"] == "missing"),
            "commission": commission,
            "schemes_not_given": sum(1 for s in schemes if set(SCHEME_KINDS) - set(s["given"]))}


async def _pms(session: AsyncSession, b: _Book, mandates: list[Mandate], firm: Firm) -> list[dict]:
    out = []
    for m in mandates:
        if m.id not in b.pms:
            continue
        agreement = await pms.standing(session, firm.id, m.id)
        signed = agreement.signed_on if agreement else None
        items = [_item_view("pms", k, b.items.get(("pms", m.id, k)), signed_on=signed) for k in ITEMS["pms"]]
        out.append({"mandate_id": str(m.id), "mandate": m.name, "agreement_signed_on": signed.isoformat() if signed else None,
                    "strategy": agreement.strategy if agreement else None, "items": items})
    return out


def _commitment_view(b: _Book, c: AifCommitment, today: date) -> dict:
    draws, called, paid_all = [], 0.0, 0.0
    for d in b.drawdowns.get(c.id, []):
        payments = b.payments.get(d.id, [])
        paid = round(sum(float(p.amount) for p in payments), 2)
        unpaid = round(float(d.amount) - paid, 2)
        called += float(d.amount)
        paid_all += paid
        state = "paid" if unpaid <= 0 else ("overdue" if d.due_on < today else "unpaid")
        draws.append({"drawdown_id": str(d.id), "number": d.number, "amount": float(d.amount), "notice_on": d.notice_on.isoformat(),
                      "due_on": d.due_on.isoformat(), "paid": paid, "unpaid": max(unpaid, 0.0), "state": state,
                      "task_id": str(d.task_id) if d.task_id else None, "document": d.document,
                      "payments": [{"amount": float(p.amount), "paid_on": p.paid_on.isoformat(), "document": p.document} for p in payments]})
    minimum = law_figures.value("in_aif_min_ticket", today)
    return {"commitment_id": str(c.id), "investor": c.investor_name, "fund": c.fund_name, "category": c.category,
            "category_label": CATEGORIES.get(c.category, c.category), "commitment": float(c.commitment), "committed_on": c.committed_on.isoformat(),
            "document": c.document, "minimum": minimum, "below_minimum": float(c.commitment) < minimum,
            "called": round(called, 2), "paid": round(paid_all, 2), "uncalled": round(float(c.commitment) - called, 2),
            "unpaid": round(sum(d["unpaid"] for d in draws), 2), "overdue": sum(1 for d in draws if d["state"] == "overdue"),
            "drawdowns": draws, "items": [_item_view("aif", k, b.items.get(("aif", c.id, k))) for k in ITEMS["aif"]]}


def is_demat(account: Account) -> bool:
    return str(account.custodian or "").upper() in ("CDSL", "NSDL") or "demat" in (account.name or "").lower()


def _broking(b: _Book, accounts: list[Account]) -> list[dict]:
    return [{"account_id": str(a.id), "account": a.name, "depository": a.custodian,
             "items": [_item_view("broking", k, b.items.get(("broking", a.id, k))) for k in ITEMS["broking"]]}
            for a in accounts if is_demat(a)]


def _stamp_view(r: EStampRecord) -> dict:
    return {"instrument": STAMPED.get(r.instrument_kind, r.instrument_kind), "certificate_number": r.certificate_number, "state": r.state,
            "duty": float(r.duty), "stamped_on": r.stamped_on.isoformat(), "parties": r.parties, "document": r.document,
            "recorded_by": r.recorded_by}


async def _nri(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> list[dict]:
    """The intake checklist for each person abroad, or whom the US or Canada rule reaches, from what is on file."""
    out = []
    people = (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.household_id == household_id,
                                                         Person.deceased_on.is_(None)).order_by(Person.full_name))).scalars().all()
    for p in people:
        facts, row = residency.standing(await residency.records(session, firm.id, p.id))
        if row is None:
            continue
        reached = fund_acceptance.exposure(facts)
        home = " ".join(str(facts.get("country_of_residence") or "").split()).casefold()
        if not (facts.get("fema_status") in residency.NON_RESIDENT_FEMA or reached or (home and home != "india")):
            continue
        repat = residency.repatriation(facts)
        signed = facts.get("self_certified_on")
        accounts = [str(a).upper() for a in (facts.get("accounts") or [])]
        checks = [
            {"key": "residence", "label": "Country of residence", "state": ON_FILE if home else NOT_ON_FILE,
             "value": facts.get("country_of_residence")},
            {"key": "tax_residence", "label": "Tax residence declared", "state": ON_FILE if facts.get("tax_residencies") else NOT_ON_FILE,
             "value": ", ".join(facts.get("tax_residencies") or []) or None},
            {"key": "repatriation", "label": "Repatriation intent", "state": repat["state"], "value": repat["label"], "why": repat["issue"]},
            {"key": "accounts", "label": "NRE, NRO or FCNR account", "state": ON_FILE if accounts else NOT_ON_FILE,
             "value": ", ".join(accounts) or None},
            {"key": "fatca_crs", "label": "FATCA and CRS self-certification", "state": ON_FILE if signed else NOT_ON_FILE,
             "value": signed.isoformat() if isinstance(signed, date) else signed},
        ]
        acceptance = await fund_acceptance.client_answer(session, firm, reached) if reached else []
        out.append({"person_id": str(p.id), "name": p.full_name, "checks": checks, "reached": [
            {"country": c, "why": why} for c, why in reached.items()], "acceptance": acceptance,
            "refused": sum(1 for a in acceptance if a["state"] == "refuses"),
            "conditions": sum(1 for a in acceptance if a["state"] == "conditions"),
            "not_assessed": sum(1 for a in acceptance if a["state"] == "not_assessed")})
    return out


def _counts(v: dict) -> dict:
    pms_items = [i for m in v["pms"] for i in m["items"]]
    aif_items = [i for c in v["aif"] for i in c["items"]]
    demat = [i for a in v["broking"] for i in a["items"] if i["required"] or i["state"] == BREACH]
    return {"missing_euin": v["funds"]["missing_euin"],
            "commission_missing": int(v["funds"]["commission"]["state"] == NOT_ON_FILE),
            "schemes_not_given": v["funds"]["schemes_not_given"],
            "pms_breaches": sum(1 for i in pms_items if i["state"] == BREACH),
            "pms_not_on_file": sum(1 for i in pms_items if i["state"] == NOT_ON_FILE),
            "aif_missing": sum(1 for i in aif_items if i["state"] != ON_FILE),
            "drawdowns_unpaid": sum(1 for c in v["aif"] for d in c["drawdowns"] if d["state"] != "paid"),
            "drawdowns_overdue": sum(c["overdue"] for c in v["aif"]),
            "unpaid_amount": round(sum(c["unpaid"] for c in v["aif"]), 2),
            "broking_missing": sum(1 for i in demat if i["state"] != ON_FILE),
            "nri_issues": sum(1 for p in v["nri"] for c in p["checks"] if c["state"] != ON_FILE)}


async def _household_view(session: AsyncSession, firm: Firm, household: Household, b: _Book, today: date) -> dict:
    mandates = b.mandates.get(household.id, [])
    accounts = [a for m in mandates for a in b.accounts.get(m.id, [])]
    view = {"household": household.name, "household_id": str(household.id),
            "funds": _funds(b, household.id, accounts, firm), "pms": await _pms(session, b, mandates, firm),
            "aif": [_commitment_view(b, c, today) for c in b.commitments.get(household.id, [])],
            "broking": _broking(b, accounts), "estamps": [_stamp_view(r) for r in b.stamps.get(household.id, [])],
            "nri": await _nri(session, firm, household.id),
            "trusts": await trust_owners.cases(session, firm, household_id=household.id)}
    view["counts"] = _counts(view)
    return view


async def household_view(session: AsyncSession, firm: Firm, household_id) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat()}
    household = await _household(session, firm, household_id)
    view = await _household_view(session, firm, household, await _load(session, firm), today)
    return {"applies": True, "as_of": today.isoformat(), "scope": "household", **view, "law": law_figures.cited(LAW, today), "note": NOTE}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every household's counts, the ones with most open first, and the open trust cases."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "households": []}
    b = await _load(session, firm)
    rows = []
    for h in (await session.execute(select(Household).where(Household.firm_id == firm.id).order_by(Household.name))).scalars().all():
        v = await _household_view(session, firm, h, b, today)
        if not (b.mandates.get(h.id) or v["aif"] or v["estamps"] or v["nri"]):
            continue
        rows.append({"household": h.name, "household_id": str(h.id), "counts": v["counts"],
                     "relationships": [r for r, on in (("Mutual funds", v["funds"]["schemes"]), ("PMS", v["pms"]), ("AIF", v["aif"]),
                                                       ("Broking and demat", v["broking"]), ("NRI", v["nri"])) if on]})
    weight = lambda r: (-r["counts"]["drawdowns_overdue"], -r["counts"]["pms_breaches"], -r["counts"]["drawdowns_unpaid"],  # noqa: E731
                        -r["counts"]["missing_euin"], r["household"])
    rows.sort(key=weight)
    keys = list(rows[0]["counts"]) if rows else []
    totals = {k: round(sum(r["counts"][k] for r in rows), 2) for k in keys}
    trusts = await trust_owners.cases(session, firm)
    nri_cases = []
    from app.models.onboarding import OnboardingCase

    for c in (await session.execute(select(OnboardingCase).where(
            OnboardingCase.firm_id == firm.id, OnboardingCase.activated_at.is_(None),
            OnboardingCase.registration_type.in_(("nri_nre", "nri_nro"))).order_by(OnboardingCase.prospect_name))).scalars().all():
        if c.status == "rejected":
            continue
        intent = ((c.intake or {}).get("registration") or {}).get("repatriation_intent")
        nri_cases.append({"case_id": str(c.id), "case": c.prospect_name, "registration_type": c.registration_type,
                          "intent": intent, "intent_label": residency.REPATRIATION.get(intent or ""),
                          "mismatch": intent == "repatriable" and c.registration_type == "nri_nro"})
    return {"applies": True, "as_of": today.isoformat(), "scope": "firm", "households": rows, "totals": totals, "trusts": trusts,
            "nri_cases": nri_cases, "law": law_figures.cited(LAW, today), "note": NOTE}


# ── Recording ───────────────────────────────────────────────────────────────────────────────────────────────────
async def _written(session: AsyncSession, firm: Firm, by: str, event: str, row, content: dict, household_id) -> dict:
    row.recorded_by = by
    row.created_at = utcnow()
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type=event, actor=by, content=content)
    return await household_view(session, firm, household_id)


async def _order(session: AsyncSession, firm: Firm, order_id) -> tuple[Order, Instrument, uuid.UUID | None, Account]:
    order = await session.get(Order, _uuid(order_id, "order"))
    if order is None or order.firm_id != firm.id:
        raise AgreementError("Order not found.")
    inst = await session.get(Instrument, order.instrument_id)
    if not fund_acceptance.is_scheme(inst):
        raise AgreementError(f"{inst.symbol if inst else 'That instrument'} is not a mutual fund scheme, so it carries no EUIN.")
    account = await session.get(Account, order.account_id)
    mandate = await session.get(Mandate, account.mandate_id) if account and account.mandate_id else None
    return order, inst, (await _household_of_mandate(session, mandate) if mandate else None), account


async def prepare_euin(session: AsyncSession, firm: Firm, role, *, order_id, euin: str | None = None, arn: str | None = None,
                       execution_only=None, document: str | None = None) -> dict:
    _need(firm, role)
    order, inst, household_id, account = await _order(session, firm, order_id)
    if _plan(inst) == "direct":
        raise AgreementError(f"{inst.symbol} is a direct plan: it goes through no distributor, so it carries no EUIN or declaration.")
    only = execution_only if isinstance(execution_only, bool) else str(execution_only or "").strip().lower() in ("true", "yes", "1")
    code = "".join(str(euin or "").split()).upper() or None
    if code and only:
        raise AgreementError("Give the EUIN, or the execution-only declaration, not both: the declaration is for a transaction nobody advised.")
    if not code and not only:
        raise AgreementError("Give the EUIN on the transaction, or say the investor declared it execution-only.")
    if code and not EUIN.match(code):
        raise AgreementError("An EUIN is written E and six digits (assumed form).")
    number = " ".join(str(arn or "").split()).upper() or None
    if number and len(number) > 20:
        raise AgreementError("The ARN is at most 20 characters.")
    return {"order": order, "instrument": inst, "account": account, "household_id": household_id, "euin": code, "arn": number,
            "execution_only": only, "document": _document(document)}


async def record_euin(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_euin(session, firm, role, **terms)
    row = MfOrderEuin(firm_id=firm.id, order_id=p["order"].id, euin=p["euin"], arn=p["arn"], execution_only=p["execution_only"],
                      document=p["document"])
    return await _written(session, firm, by, "mf_order_euin_recorded", row, {
        "order_id": str(row.order_id), "euin": row.euin, "execution_only": row.execution_only, "document": row.document}, p["household_id"])


async def prepare_scheme_document(session: AsyncSession, firm: Firm, role, *, symbol=None, kind: str | None = None, dated_on=None,
                                  document: str | None = None) -> dict:
    _need(firm, role, SCHEME_RECORDERS)
    inst = await _instrument(session, firm, symbol)
    k = str(kind or "").strip().lower()
    if k not in SCHEME_KINDS:
        raise AgreementError("The document is the sid or the kim.")
    day = _day(dated_on, "date the document is dated", firm_time.today(firm))
    return {"instrument": inst, "kind": k, "dated_on": day, "document": _document(document)}


async def record_scheme_document(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_scheme_document(session, firm, role, **terms)
    row = SchemeDocument(firm_id=firm.id, instrument_id=p["instrument"].id, kind=p["kind"], dated_on=p["dated_on"], document=p["document"],
                         recorded_by=by, created_at=utcnow())
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="scheme_document_recorded", actor=by, content={
        "symbol": p["instrument"].symbol, "kind": p["kind"], "dated_on": p["dated_on"].isoformat(), "document": p["document"]})
    return {"applies": True, "scope": "scheme", "symbol": p["instrument"].symbol, "fund": p["instrument"].name, "kind": p["kind"],
            "kind_label": SCHEME_KINDS[p["kind"]], "dated_on": p["dated_on"].isoformat(), "document": p["document"],
            "on_file": await scheme_documents(session, firm, p["instrument"].id)}


async def scheme_documents(session: AsyncSession, firm: Firm, instrument_id: uuid.UUID) -> dict:
    rows = await _latest_by(session, SchemeDocument, firm.id, lambda r: r.kind, SchemeDocument.instrument_id == instrument_id)
    return {k: (rows[k].dated_on.isoformat() if k in rows else None) for k in SCHEME_KINDS}


async def prepare_disclosure(session: AsyncSession, firm: Firm, role, *, household_id=None, kind: str | None = None, symbol=None,
                             fund_house: str | None = None, terms: str | None = None, items=None, method: str | None = None,
                             given_on=None, document: str | None = None) -> dict:
    _need(firm, role)
    household = await _household(session, firm, household_id)
    k = str(kind or "").strip().lower()
    if k not in DISCLOSURES:
        raise AgreementError("Say what was given: commission (the commission disclosure) or scheme_documents (the SID or KIM).")
    inst = await _instrument(session, firm, symbol) if str(symbol or "").strip() else None
    house = " ".join(str(fund_house or "").split()) or (fund_acceptance.house_of(inst) if inst else None)
    stated, listed = None, []
    if k == "commission":
        stated = _text(terms, "the commission as the disclosure states it", least=3, most=1000)
        if inst is None and not house:
            raise AgreementError("Say which scheme or fund house the commission is for.")
    else:
        if inst is None:
            raise AgreementError("Give the scheme's symbol the documents are for.")
        raw = items if isinstance(items, list) else str(items or "").replace(";", ",").split(",")
        listed = sorted({str(x).strip().lower() for x in raw if str(x).strip()})
        if not listed or any(x not in SCHEME_KINDS for x in listed):
            raise AgreementError("Say which documents were given: sid, kim or both.")
    how = str(method or "").strip().lower().replace(" ", "_")
    if how not in METHODS:
        raise AgreementError(f"How it was given: one of {', '.join(METHODS)}.")
    day = _day(given_on, "day it was given", firm_time.today(firm))
    return {"household": household, "kind": k, "instrument": inst, "fund_house": house, "terms": stated, "items": listed, "method": how,
            "given_on": day, "document": _document(document)}


async def record_disclosure(session: AsyncSession, firm: Firm, role, *, by: str, **kw) -> dict:
    p = await prepare_disclosure(session, firm, role, **kw)
    row = DistributionDisclosure(firm_id=firm.id, household_id=p["household"].id, instrument_id=p["instrument"].id if p["instrument"] else None,
                                 fund_house=(p["fund_house"] or "")[:120] or None, kind=p["kind"], terms=p["terms"], items=p["items"],
                                 method=p["method"], given_on=p["given_on"], document=p["document"])
    return await _written(session, firm, by, "distribution_disclosure_recorded", row, {
        "household_id": str(row.household_id), "kind": row.kind, "fund_house": row.fund_house, "items": row.items,
        "given_on": row.given_on.isoformat(), "document": row.document}, row.household_id)


async def prepare_item(session: AsyncSession, firm: Firm, role, *, relationship: str | None = None, item: str | None = None, answer=None,
                       mandate_id=None, account_id=None, commitment_id=None, powers=None, dated_on=None, document: str | None = None,
                       note: str | None = None) -> dict:
    _need(firm, role)
    rel = str(relationship or "").strip().lower()
    if rel not in ITEMS:
        raise AgreementError("The relationship is pms, aif or broking.")
    key = str(item or "").strip().lower().replace(" ", "_")
    if key not in ITEMS[rel]:
        raise AgreementError(f"For {rel} the item is one of {', '.join(ITEMS[rel])}.")
    label, answers, _, _, _ = ITEMS[rel][key]
    mandate = account = commitment = None
    if rel == "pms":
        mandate = await session.get(Mandate, _uuid(mandate_id, "mandate"))
        if mandate is None or mandate.firm_id != firm.id:
            raise AgreementError("No such mandate at this firm.")
        if mandate.id not in {m.id for m in await pms.pms_mandates(session, firm)}:
            raise AgreementError(f"{mandate.name} is not on a Portfolio Manager's track.")
        household_id, subject = await _household_of_mandate(session, mandate), mandate.name
    elif rel == "broking":
        account = await session.get(Account, _uuid(account_id, "account"))
        if account is None or account.firm_id != firm.id or not account.mandate_id:
            raise AgreementError("No such account at this firm.")
        if not is_demat(account):
            raise AgreementError(f"{account.name} is not a demat account.")
        mandate = await session.get(Mandate, account.mandate_id)
        household_id, subject = await _household_of_mandate(session, mandate), account.name
    else:
        commitment = await session.get(AifCommitment, _uuid(commitment_id, "commitment"))
        if commitment is None or commitment.firm_id != firm.id:
            raise AgreementError("No such fund commitment at this firm.")
        household_id, subject = commitment.household_id, f"{commitment.investor_name}, {commitment.fund_name}"
    if household_id is None:
        raise AgreementError("That is not tied to a household, so there is nothing to record it against.")
    detail: dict = {}
    said = str(answer or "").strip().lower().replace(" ", "_")
    if key == "poa_scope":
        raw = powers if isinstance(powers, list) else str(powers or "").replace(";", ",").split(",")
        listed = [" ".join(str(x).split()) for x in raw if str(x).strip()]
        norm = [x.lower().replace(" ", "_") for x in listed]
        if said == "none":
            if listed:
                raise AgreementError("No power of attorney grants no power: leave the powers out.")
        else:
            if not listed:
                raise AgreementError("List the powers the power of attorney grants, as it states them: operate_demat, debit_fees, "
                                     "and any other in its own words. Say none if no power of attorney was given.")
            extra = [x for x, n in zip(listed, norm) if n not in POA_POWERS]
            said = "wider" if extra else "limited"            # read from the powers, never taken as said
            detail["powers"] = [POA_POWERS.get(n, x) for x, n in zip(listed, norm)]
            detail["beyond"] = extra
    if said not in answers:
        raise AgreementError(f"For {label.lower()} the answer is one of {', '.join(answers)}.")
    day = _day(dated_on, "date on the document", firm_time.today(firm))
    return {"relationship": rel, "item": key, "label": label, "answer": said, "detail": detail, "dated_on": day, "document": _document(document),
            "note": " ".join(str(note or "").split()) or None, "household_id": household_id, "subject": subject,
            "mandate_id": mandate.id if rel == "pms" else None, "account_id": account.id if account else None,
            "commitment_id": commitment.id if commitment else None,
            "view": _item_view(rel, key, AgreementItem(answer=said, dated_on=day, detail=detail, document="", recorded_by=""),
                               signed_on=(a.signed_on if rel == "pms" and (a := await pms.standing(session, firm.id, mandate.id)) else None))}


async def record_item(session: AsyncSession, firm: Firm, role, *, by: str, **kw) -> dict:
    p = await prepare_item(session, firm, role, **kw)
    row = AgreementItem(firm_id=firm.id, household_id=p["household_id"], mandate_id=p["mandate_id"], account_id=p["account_id"],
                        commitment_id=p["commitment_id"], relationship_kind=p["relationship"], item=p["item"], answer=p["answer"],
                        detail=p["detail"], dated_on=p["dated_on"], document=p["document"], note=p["note"])
    return await _written(session, firm, by, "agreement_item_recorded", row, {
        "relationship": row.relationship_kind, "item": row.item, "answer": row.answer, "subject": p["subject"],
        "dated_on": row.dated_on.isoformat(), "document": row.document, "powers": row.detail.get("powers")}, row.household_id)


async def prepare_commitment(session: AsyncSession, firm: Firm, role, *, person_id=None, entity_id=None, fund_name: str | None = None,
                             category: str | None = None, commitment=None, committed_on=None, document: str | None = None) -> dict:
    _need(firm, role)
    if bool(person_id) == bool(entity_id):
        raise AgreementError("Say who committed: a person or an entity, one of them.")
    if person_id:
        holder = await session.get(Person, _uuid(person_id, "person"))
        name = holder.full_name if holder else None
    else:
        holder = await session.get(LegalEntity, _uuid(entity_id, "entity"))
        name = holder.name if holder else None
    household = await session.get(Household, holder.household_id) if holder is not None and holder.household_id else None
    if holder is None or household is None or household.firm_id != firm.id:
        raise AgreementError("No such client or entity at this firm.")
    fund = _text(fund_name, "the fund's name, as the agreement does", most=200)
    kind = str(category or "").strip().lower().replace(" ", "_").replace("category_", "aif_")
    kind = {"aif_i": "aif_1", "aif_ii": "aif_2", "aif_iii": "aif_3", "1": "aif_1", "2": "aif_2", "3": "aif_3"}.get(kind, kind)
    if kind not in CATEGORIES:
        raise AgreementError("The category is aif_1, aif_2 or aif_3, as the placement memorandum states it.")
    amount = _amount(commitment, "commitment")
    day = _day(committed_on, "day of the contribution agreement", firm_time.today(firm))
    already = (await session.execute(select(AifCommitment).where(
        AifCommitment.firm_id == firm.id, AifCommitment.household_id == household.id))).scalars().all()
    if any(c.investor_name == name and c.fund_name.casefold() == fund.casefold() for c in already):
        raise AgreementError(f"{name}'s commitment to {fund} is already on file.")
    minimum = law_figures.value("in_aif_min_ticket", firm_time.today(firm))
    return {"holder": holder, "investor": name, "household": household, "fund": fund, "category": kind, "commitment": amount,
            "committed_on": day, "document": _document(document), "below_minimum": amount < minimum, "minimum": minimum}


async def record_commitment(session: AsyncSession, firm: Firm, role, *, by: str, **kw) -> dict:
    p = await prepare_commitment(session, firm, role, **kw)
    row = AifCommitment(firm_id=firm.id, household_id=p["household"].id,
                        person_id=p["holder"].id if isinstance(p["holder"], Person) else None,
                        entity_id=p["holder"].id if isinstance(p["holder"], LegalEntity) else None, investor_name=p["investor"],
                        fund_name=p["fund"], category=p["category"], commitment=p["commitment"], committed_on=p["committed_on"],
                        document=p["document"])
    return await _written(session, firm, by, "aif_commitment_recorded", row, {
        "investor": row.investor_name, "fund": row.fund_name, "category": row.category, "commitment": float(row.commitment),
        "committed_on": row.committed_on.isoformat(), "document": row.document}, row.household_id)


async def prepare_drawdown(session: AsyncSession, firm: Firm, role, *, commitment_id=None, number=None, amount=None, notice_on=None,
                           due_on=None, document: str | None = None) -> dict:
    _need(firm, role)
    c = await session.get(AifCommitment, _uuid(commitment_id, "commitment"))
    if c is None or c.firm_id != firm.id:
        raise AgreementError("No such fund commitment at this firm.")
    draws = (await session.execute(select(AifDrawdown).where(AifDrawdown.commitment_id == c.id))).scalars().all()
    if number in (None, ""):
        n = max((d.number for d in draws), default=0) + 1
    else:
        try:
            n = int(number)
        except (TypeError, ValueError):
            raise AgreementError("The drawdown number is a whole number, as the notice states it.")
    if n < 1 or any(d.number == n for d in draws):
        raise AgreementError(f"Drawdown {n} is already on file for this commitment.")
    money = _amount(amount, "drawdown")
    called = sum(float(d.amount) for d in draws)
    if called + money > float(c.commitment) + 0.005:
        raise AgreementError(f"That would call {_money(called + money)} against a commitment of {_money(c.commitment)}. Check the notice.")
    today = firm_time.today(firm)
    noticed = _day(notice_on, "day of the notice", today)
    due = _day(due_on, "due date", today, future=True)
    if due < noticed:
        raise AgreementError("The due date cannot be before the notice.")
    from app.aurea_core import advisers

    adviser = await advisers.adviser_of(session, firm.id, c.household_id)
    return {"commitment": c, "household": await session.get(Household, c.household_id), "number": n, "amount": money, "notice_on": noticed,
            "due_on": due, "document": _document(document), "adviser": adviser if adviser and adviser.is_active else None,
            "called_after": round(called + money, 2)}


async def record_drawdown(session: AsyncSession, firm: Firm, role, *, by: str, by_user=None, **kw) -> dict:
    p = await prepare_drawdown(session, firm, role, **kw)
    c, household, adviser = p["commitment"], p["household"], p["adviser"]
    title = f"Unpaid drawdown {p['number']}: {c.fund_name}, {_money(p['amount'])}"[:280]
    task = Task(firm_id=firm.id, household_id=c.household_id, title=title, status=TaskStatus.OPEN, due_date=p["due_on"], source=TASK_SOURCE,
                subject_label=household.name if household else None, assigned_to=adviser.id if adviser else None,
                assigned_by=by_user if adviser else None,
                detail=(f"{c.investor_name}'s contribution to {c.fund_name}: {_money(p['amount'])} due {p['due_on'].isoformat()} on the notice "
                        f"filed at {p['document']}. It closes when a payment covering it is recorded; nothing here pays it."))
    session.add(task)
    await session.flush()
    row = AifDrawdown(firm_id=firm.id, commitment_id=c.id, number=p["number"], amount=p["amount"], notice_on=p["notice_on"], due_on=p["due_on"],
                      document=p["document"], task_id=task.id)
    return await _written(session, firm, by, "aif_drawdown_recorded", row, {
        "fund": c.fund_name, "investor": c.investor_name, "number": row.number, "amount": float(row.amount),
        "due_on": row.due_on.isoformat(), "document": row.document}, c.household_id)


async def prepare_payment(session: AsyncSession, firm: Firm, role, *, drawdown_id=None, amount=None, paid_on=None,
                          document: str | None = None) -> dict:
    _need(firm, role)
    d = await session.get(AifDrawdown, _uuid(drawdown_id, "drawdown"))
    if d is None or d.firm_id != firm.id:
        raise AgreementError("No such drawdown at this firm.")
    c = await session.get(AifCommitment, d.commitment_id)
    paid = sum(float(x.amount) for x in (await session.execute(select(AifPayment).where(AifPayment.drawdown_id == d.id))).scalars().all())
    unpaid = round(float(d.amount) - paid, 2)
    if unpaid <= 0:
        raise AgreementError(f"Drawdown {d.number} is already paid in full.")
    money = _amount(amount if amount not in (None, "") else unpaid, "payment")
    if money > unpaid + 0.005:
        raise AgreementError(f"Only {_money(unpaid)} of drawdown {d.number} is unpaid.")
    day = _day(paid_on, "day it was paid", firm_time.today(firm))
    if day < d.notice_on:
        raise AgreementError("It cannot be paid before the notice.")
    task = await session.get(Task, d.task_id) if d.task_id else None
    closes = bool(task and str(task.status) == TaskStatus.OPEN.value and money >= unpaid - 0.005)
    return {"drawdown": d, "commitment": c, "amount": money, "paid_on": day, "document": _document(document), "unpaid": unpaid,
            "left": round(unpaid - money, 2), "task": task if closes else None}


async def record_payment(session: AsyncSession, firm: Firm, role, *, by: str, **kw) -> dict:
    p = await prepare_payment(session, firm, role, **kw)
    d, c = p["drawdown"], p["commitment"]
    if p["task"] is not None:
        from app.aurea_core import tasks

        tasks.complete(p["task"])
    row = AifPayment(firm_id=firm.id, drawdown_id=d.id, amount=p["amount"], paid_on=p["paid_on"], document=p["document"])
    return await _written(session, firm, by, "aif_drawdown_payment_recorded", row, {
        "fund": c.fund_name, "number": d.number, "amount": float(row.amount), "paid_on": row.paid_on.isoformat(), "document": row.document,
        "left": p["left"], "task_closed": p["task"] is not None}, c.household_id)


async def prepare_estamp(session: AsyncSession, firm: Firm, role, *, household_id=None, instrument_kind: str | None = None,
                         certificate_number: str | None = None, state: str | None = None, duty=None, stamped_on=None,
                         parties: str | None = None, document: str | None = None) -> dict:
    _need(firm, role)
    household = await _household(session, firm, household_id)
    kind = str(instrument_kind or "").strip().lower().replace(" ", "_")
    if kind not in STAMPED:
        raise AgreementError(f"The instrument is one of {', '.join(STAMPED)}.")
    number = _text(certificate_number, "the certificate number", least=5, most=60).upper()
    if (await session.execute(select(EStampRecord.id).where(EStampRecord.firm_id == firm.id,
                                                             EStampRecord.certificate_number == number))).first():
        raise AgreementError(f"Certificate {number} is already on file.")
    day = _day(stamped_on, "day it was stamped", firm_time.today(firm))
    return {"household": household, "kind": kind, "certificate_number": number, "state": _text(state, "the state that stamped it", least=2, most=60),
            "duty": _amount(duty, "stamp duty"), "stamped_on": day, "parties": _text(parties, "the parties the certificate names", most=300),
            "document": _document(document)}


async def record_estamp(session: AsyncSession, firm: Firm, role, *, by: str, **kw) -> dict:
    p = await prepare_estamp(session, firm, role, **kw)
    row = EStampRecord(firm_id=firm.id, household_id=p["household"].id, instrument_kind=p["kind"], certificate_number=p["certificate_number"],
                       state=p["state"], duty=p["duty"], stamped_on=p["stamped_on"], parties=p["parties"], document=p["document"])
    return await _written(session, firm, by, "estamp_recorded", row, {
        "instrument": row.instrument_kind, "certificate_number": row.certificate_number, "state": row.state, "duty": float(row.duty),
        "stamped_on": row.stamped_on.isoformat(), "document": row.document}, row.household_id)
