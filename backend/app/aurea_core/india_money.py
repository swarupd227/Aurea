"""India money in and bank rules (B-388, onboarding module sections 4.2, 6.1 and 10).

**Who may pay.** The paying account must be the investor's: the person or entity the account's mandate is for (a joint bank account
passes when the investor is one of its holders). Money in is checked against the registered bank account (a `money_in` bank link,
verified from its evidence) and the name the credit advice shows, and a debit mandate is checked before it is accepted. The
recognised exceptions are recorded by a person from their evidence and checked again each time: a parent or relative paying for a
minor, up to a line that is a law figure counsel has not read, and an employer's payroll paying a SIP's instalments. A name the bank
shows in initials could be somebody else's, so it needs the full name confirmed from the bank's own document, once.

**Money out.** A redemption or payment for an Indian client goes to a registered bank account of that investor and nowhere else. A
bank registered after the account's first one is a change, and is not paid until the cooling-off the firm sets has passed. No law
figure is assumed for it: with none set, a changed bank is payable at once and every screen says so.

**Money in.** UPI, netbanking, RTGS, NEFT and cheque, in rupees, each from its credit advice. US ACH, NACHA return codes and the US
deposit hold threshold are not applied: an Indian firm has no hold unless it sets one. A credit into a non-resident's NRE or NRO
account keeps the bank certificate's reference.

**No SIP dies silently.** A mandate that is requested, failed or no longer in force, a first debit that failed or was never recorded,
and an active SIP with no mandate on file sit in a queue with their age and owner, and become a task for the household's recorded
adviser (at once on a recorded failure, otherwise past the chase line). Nothing here writes cash, places an order, presents or retries
a debit, or tells the client anything.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advisers, firm_time, law_figures, markets
from app.aurea_core.billing import _mandate_households
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.engagement import Task
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.identity import User
from app.models.india_money import IndiaCredit, InwardRemittance, MandateDebit, PayerException
from app.models.money_in import BankLink, IncomingDeposit
from app.models.sip import Sip
from app.models.tenant import Firm
from app.provenance import ledger

SETTINGS_KEY = "india_money"
COOLING_KEY = "bank_change_cooling_days"
MINOR_LINE_KEY = "in_third_party_minor_limit"

# rail -> (the value stored on the deposit, which is eight characters at most; the label)
RAILS = {"upi": ("upi", "UPI"), "netbanking": ("netbank", "Netbanking"), "rtgs": ("rtgs", "RTGS"), "neft": ("neft", "NEFT"), "cheque": ("cheque", "Cheque")}
EXCEPTION_KINDS = {"minor_relative": "A parent or relative paying for a minor", "employer_payroll": "An employer's payroll paying a SIP",
                   "name_confirmed": "The bank shows the investor's name in initials"}
RELATIVES = {"father": "Father", "mother": "Mother", "legal_guardian": "Legal guardian", "grandparent": "Grandparent", "related_person": "Related person"}
BASES = {"investor": "The investor's own account", "name_confirmed": "Investor, full name confirmed", "minor_relative": "Paid for a minor (exception)",
         "employer_payroll": "Employer payroll (exception)"}
ACCOUNT_KINDS = {"nre": "NRE", "nro": "NRO"}

# The platform's defaults for how long a mandate may sit before it is chased, by rail. Operating defaults, not law.
CHASE_DAYS = {"upi_autopay": 2, "nach": 4, "nach_paper": 15}
DEBIT_CHASE_DAYS = 3

EXCEPTION_RECORDERS = frozenset({UserRole.OPERATIONS, UserRole.COMPLIANCE})
CREDIT_RECORDERS = frozenset({UserRole.OPERATIONS})
REMITTANCE_RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
CHASE_SOURCE_NOTE = "The mandate, its first debit and the SIP as a person recorded them; the chase line is the platform's operating default"


class IndiaMoneyError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _require(firm: Firm, role, roles, who: str) -> None:
    if not applies(firm):
        raise IndiaMoneyError("This is recorded for an Indian firm.")
    if role is not None and role not in roles:
        raise IndiaMoneyError(who)


def _uuid(value, what: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise IndiaMoneyError(f"Give {what} from the screen that lists it.")


def _day(value, today: date, what: str = "the day it happened") -> date:
    if value in (None, ""):
        return today
    if not isinstance(value, date):
        try:
            value = date.fromisoformat(str(value))
        except ValueError:
            raise IndiaMoneyError(f"Give {what} as YYYY-MM-DD.")
    if value > today:
        raise IndiaMoneyError("Nothing is recorded ahead of the day it happened.")
    return value


def _text(value, what: str, least: int, most: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) < least:
        raise IndiaMoneyError(what)
    return text[:most]


def _amount(value, what: str = "Give the amount in rupees, as the advice shows it.") -> float:
    try:
        n = round(float(str(value).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise IndiaMoneyError(what)
    if not 0 < n < 1e15:
        raise IndiaMoneyError(what)
    return n


def _last4(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 4:
        raise IndiaMoneyError("Give the last four digits of the bank account, and no more.")
    return digits


def rupees(amount: float) -> str:
    return markets.money(amount, "INR")


# ── Names ─────────────────────────────────────────────────────────────────────

_HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari", "km", "master"})
_JOINT = re.compile(r"\s*(?:&|/|,|\bor\b|\band\b|\bjointly with\b)\s*", re.I)
_RANK = {"none": 0, "initials": 1, "full": 2}


def _tokens(name: str) -> list[str]:
    cleaned = re.sub(r"[’'`]", "", (name or "").lower())
    cleaned = re.sub(r"\bm\s*/\s*s\b\.?", " ", cleaned)
    return [w for w in re.findall(r"[a-z]+", cleaned) if w not in _HONORIFICS]


def _match_one(shown: list[str], name: list[str]) -> str:
    if not shown or not name:
        return "none"
    if sorted(shown) == sorted(name):
        return "full"
    s_full, n_full = [w for w in shown if len(w) > 1], [w for w in name if len(w) > 1]
    s_init, n_init = [w for w in shown if len(w) == 1], [w for w in name if len(w) == 1]
    if not s_full or not n_full:
        return "none"
    if len(s_full) >= 2 and set(s_full) <= set(n_full) and not s_init:          # a middle name left out
        return "full"
    if len(n_full) >= 2 and set(n_full) < set(s_full) and shown[0] == name[0] and not s_init:      # a middle name added
        return "full"
    if set(s_full) == set(n_full) and len(n_full) >= 2 and shown[0] == name[0]:  # a middle initial added after the given name
        return "full"

    def initials_fit(initials: list[str], used: list[str], pool: list[str]) -> bool:
        spare = [w[0] for w in pool if w not in used]
        for i in initials:
            if i not in spare:
                return False
            spare.remove(i)
        return True

    if set(s_full) <= set(n_full) and len(shown) >= 2 and initials_fit(s_init, s_full, name):
        return "initials"
    if n_init and set(n_full) <= set(s_full) and initials_fit(n_init, n_full, shown):
        return "initials"
    return "none"


def match(shown: str, name: str) -> str:
    """How a name a bank shows reads against a person's or an entity's: "full", "initials" (it could be somebody else) or "none".
    A joint account's owner passes when the name is one of its holders."""
    candidates = [shown or ""] + [p for p in _JOINT.split(shown or "") if p.strip()]
    wanted = _tokens(name)
    return max((_match_one(_tokens(c), wanted) for c in candidates), key=_RANK.get)


# ── The investor ──────────────────────────────────────────────────────────────

async def investor(session: AsyncSession, firm: Firm, account: Account) -> dict | None:
    """Whose money the account is: the person or entity its mandate is for. None when the mandate names neither."""
    mandate = await session.get(Mandate, account.mandate_id) if account.mandate_id else None
    if mandate is None:
        return None
    if mandate.person_id is not None:
        person = await session.get(Person, mandate.person_id)
        if person is None:
            return None
        from app.aurea_core import guardianship

        today = firm_time.today(firm)
        g = await guardianship.standing(session, firm.id, person.id)
        status = guardianship.status_of(person, g, today)
        return {"name": person.full_name, "person": person, "entity": None, "minor": status["minor"], "guardian": g}
    if mandate.entity_id is not None:
        entity = await session.get(LegalEntity, mandate.entity_id)
        if entity is None:
            return None
        return {"name": entity.name, "person": None, "entity": entity, "minor": False, "guardian": None}
    return None


async def _account(session: AsyncSession, firm: Firm, account_id) -> Account:
    account = await session.get(Account, _uuid(account_id, "the account"))
    if account is None or account.firm_id != firm.id:
        raise IndiaMoneyError("Account not found.")
    return account


async def _link(session: AsyncSession, firm: Firm, account: Account, bank_link_id) -> BankLink:
    link = await session.get(BankLink, _uuid(bank_link_id, "the registered bank account"))
    if link is None or link.firm_id != firm.id or link.account_id != account.id:
        raise IndiaMoneyError(f"That bank account is not registered on {account.name}.")
    if link.status != "verified":
        raise IndiaMoneyError(f"The bank account ending {link.last4} is {link.status}, not verified: it is not a registered bank.")
    return link


# ── Exceptions ────────────────────────────────────────────────────────────────

async def exceptions(session: AsyncSession, firm_id: uuid.UUID, account_ids: list[uuid.UUID] | None = None) -> list[PayerException]:
    """The latest row for each account, kind, payer and last four digits, withdrawn ones left out."""
    stmt = select(PayerException).where(PayerException.firm_id == firm_id)
    if account_ids is not None:
        stmt = stmt.where(PayerException.account_id.in_(account_ids or [uuid.uuid4()]))
    latest: dict[tuple, PayerException] = {}
    for r in (await session.execute(stmt.order_by(PayerException.created_at, PayerException.id))).scalars().all():
        latest[(r.account_id, r.kind, tuple(sorted(_tokens(r.payer_name))), r.last4)] = r
    return [r for r in latest.values() if not r.withdrawn]


async def _active_sip(session: AsyncSession, firm: Firm, account_id: uuid.UUID) -> Sip | None:
    today = firm_time.today(firm)
    rows = (await session.execute(select(Sip).where(Sip.firm_id == firm.id, Sip.account_id == account_id, Sip.ended_on.is_(None)))).scalars().all()
    return next((r for r in rows if r.ends_on is None or r.ends_on >= today), None)


async def _unfit(session: AsyncSession, firm: Firm, account: Account, inv: dict, kind: str, amount: float | None, use: str) -> str | None:
    """Why an exception of this kind does not carry this payment today, or None."""
    if kind == "minor_relative":
        if inv["person"] is None:
            return f"{inv['name']} is not a person, so not a minor"
        if inv["minor"] is None:
            return f"{inv['name']} has no date of birth on file, so whether they are a minor is not assessed"
        if not inv["minor"]:
            return f"{inv['name']} is not a minor, so the exception for paying for one no longer applies"
        line = law_figures.value(MINOR_LINE_KEY)
        if amount is not None and amount > line + 1e-9:
            return f"{rupees(amount)} is above the {rupees(line)} a payment this exception allows (assumed, not reviewed by counsel)"
        return None
    if kind == "employer_payroll":
        if use == "mandate":
            return "an employer's payroll pays instalments itself; a debit mandate is on the investor's own bank account"
        if inv["person"] is None:
            return f"{inv['name']} is not a person, so has no employer's payroll"
        if await _active_sip(session, firm, account.id) is None:
            return f"there is no active SIP on {account.name}, and the exception covers a SIP's instalments"
        return None
    return "this exception does not carry a payment from someone else"


async def check_payer(session: AsyncSession, firm: Firm, account: Account, *, payer_name: str, bank_link: BankLink | None = None,
                      last4: str | None = None, amount: float | None = None, use: str = "credit") -> dict:
    """Whether this bank account may pay (or, for use="payout", be paid) for this account. Writes nothing."""
    inv = await investor(session, firm, account)
    digits = bank_link.last4 if bank_link else (re.sub(r"\D", "", last4 or "") or None)
    out = {"ok": False, "basis": None, "label": "Refused", "investor": inv["name"] if inv else None, "account": account.name, "payer": payer_name,
           "last4": digits, "exception_id": None}

    def no(said: str) -> dict:
        return {**out, "said": said}

    def yes(basis: str, said: str, exception: PayerException | None = None) -> dict:
        return {**out, "ok": True, "basis": basis, "label": BASES[basis], "said": said, "exception_id": str(exception.id) if exception else None}

    if inv is None:
        return no(f"No investor is recorded on {account.name}'s mandate, so whether the bank account is theirs cannot be checked.")
    if bank_link is not None and match(payer_name, bank_link.owner_name) == "none":
        return no(f"The paying name {payer_name} does not match {bank_link.owner_name}, the owner of the registered account ending {bank_link.last4}.")
    readings = [match(payer_name, inv["name"])] + ([match(bank_link.owner_name, inv["name"])] if bank_link else [])
    strength = min(readings, key=_RANK.get)
    standing = await exceptions(session, firm.id, [account.id])
    if strength == "full":
        return yes("investor", f"The bank account is {inv['name']}'s own.")
    if strength == "initials":
        shown = {tuple(sorted(_tokens(payer_name)))} | ({tuple(sorted(_tokens(bank_link.owner_name)))} if bank_link else set())
        confirmed = next((e for e in standing if e.kind == "name_confirmed" and e.last4 == digits and tuple(sorted(_tokens(e.payer_name))) in shown), None)
        if confirmed:
            return yes("name_confirmed", f"The full name behind {payer_name} was confirmed as {inv['name']} from the bank's document.", confirmed)
        return no(f"{payer_name} matches {inv['name']} by initials only, which could be somebody else. Record the full name from the bank's own "
                  "document first (record_payer_exception, kind name_confirmed).")
    third = f"{payer_name} is not {inv['name']}: a third-party bank account"
    if use == "payout":
        return no(f"{third}. Money goes only to a registered bank account of the investor.")
    why = None
    for e in standing:
        if e.kind not in ("minor_relative", "employer_payroll") or e.last4 != digits or match(payer_name, e.payer_name) != "full":
            continue
        why = await _unfit(session, firm, account, inv, e.kind, amount, use)
        if why is None:
            return yes(e.kind, f"{payer_name} pays for {inv['name']} under a recorded exception: {EXCEPTION_KINDS[e.kind].lower()}.", e)
    if why:
        return no(f"{third}, and the exception on file does not carry this: {why}.")
    return no(f"{third}. The recognised exceptions are a parent or relative paying for a minor and an employer's payroll paying a SIP: record it from "
              "its evidence (record_payer_exception), or the money goes back.")


async def verdict(session: AsyncSession, firm: Firm, *, account_id, payer_name: str | None, bank_link_id=None, last4: str | None = None, amount=None,
                  use: str | None = None) -> dict:
    """The check on its own, before any money moves."""
    _require(firm, None, (), "")
    account = await _account(session, firm, account_id)
    link = await _link(session, firm, account, bank_link_id) if bank_link_id else None
    name = " ".join((payer_name or (link.owner_name if link else "") or "").split())
    if len(name) < 2:
        raise IndiaMoneyError("Name the payer as the bank shows them, or give the registered bank account.")
    if link is None:
        last4 = _last4(last4)
    kind = (use or "credit").strip().lower()
    if kind not in ("credit", "mandate", "payout"):
        raise IndiaMoneyError("The check is for money in (credit), a debit mandate (mandate) or money out (payout).")
    value = _amount(amount) if amount not in (None, "") else None
    got = await check_payer(session, firm, account, payer_name=name, bank_link=link, last4=last4, amount=value, use=kind)
    # Money from the investor's own account, a mandate and a payment each need the bank registered; only an exception's credit may not.
    if got["ok"] and link is None and (kind != "credit" or got["basis"] in ("investor", "name_confirmed")):
        got = {**got, "ok": False, "basis": None, "label": "Refused",
               "said": f"The account ending {got['last4']} was not given as a registered bank on {account.name}. Name the registered bank account, registering it "
                       "from its evidence first if it is new (record_bank_link)."}
    if got["ok"] and kind == "payout":
        state = await bank_state(session, firm, account, link)
        if not state["payable"]:
            got = {**got, "ok": False, "label": "Cooling off", "said": state["said"]}
    return {**got, "use": kind, "amount": value}


async def prepare_exception(session: AsyncSession, firm: Firm, role, *, account_id, kind: str | None, payer_name: str | None, bank: str | None = None,
                            last4: str | None = None, relationship: str | None = None, evidence: str | None = None, withdraw: bool = False,
                            reason: str | None = None) -> dict:
    _require(firm, role, EXCEPTION_RECORDERS, "Operations or compliance records a payer exception, from its evidence.")
    account = await _account(session, firm, account_id)
    kind = (kind or "").strip().lower()
    if kind not in EXCEPTION_KINDS:
        raise IndiaMoneyError(f"The exception is one of {', '.join(EXCEPTION_KINDS)}.")
    name = _text(payer_name, "Name the payer as the bank shows them.", 2, 200)
    digits = _last4(last4)
    proof = _text(evidence, "Say where the evidence is: the birth certificate and the payer's declaration, the employer's payroll letter, the bank's letter.", 5, 300)
    standing = next((e for e in await exceptions(session, firm.id, [account.id])
                     if e.kind == kind and e.last4 == digits and sorted(_tokens(e.payer_name)) == sorted(_tokens(name))), None)
    inv = await investor(session, firm, account)
    if inv is None:
        raise IndiaMoneyError(f"No investor is recorded on {account.name}'s mandate.")
    base = {"account": account, "investor": inv["name"], "kind": kind, "payer": name, "last4": digits, "evidence": proof, "withdraw": bool(withdraw),
            "line": None}
    if withdraw:
        if standing is None:
            raise IndiaMoneyError("There is no such exception on file to withdraw.")
        return {**base, "bank": standing.bank, "relationship": standing.relationship,
                "reason": _text(reason, "Say why it is withdrawn.", 5, 300)}
    if standing is not None:
        raise IndiaMoneyError("That exception is already on file.")
    bank_name = _text(bank, "Name the bank.", 2, 120)
    relation = (relationship or "").strip().lower() or None
    reading = match(name, inv["name"])
    if kind == "name_confirmed":
        if reading != "initials":
            raise IndiaMoneyError(f"{name} reads as {inv['name']} already, so there is nothing to confirm." if reading == "full"
                                  else f"{name} does not read as {inv['name']} at all: that is somebody else, not an abbreviation.")
        relation = None
    else:
        if reading == "full":
            raise IndiaMoneyError(f"{name} is {inv['name']}'s own name: no exception is needed.")
        why = await _unfit(session, firm, account, inv, kind, None, "credit")
        if why:
            raise IndiaMoneyError(f"This exception cannot be recorded: {why}.")
        if kind == "minor_relative":
            if relation not in RELATIVES:
                raise IndiaMoneyError(f"Say who the payer is to the minor: {', '.join(RELATIVES)}.")
            g = inv["guardian"]
            if relation == "legal_guardian" and (g is None or match(name, g.guardian_name) != "full"):
                raise IndiaMoneyError(f"{name} is not the guardian on file for {inv['name']}.")
            base["line"] = law_figures.value(MINOR_LINE_KEY)
        else:
            relation = "employer"
    return {**base, "bank": bank_name, "relationship": relation, "reason": (reason or "").strip()[:300] or None}


async def record_exception(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_exception(session, firm, role, **terms)
    session.add(PayerException(created_at=utcnow(), firm_id=firm.id, account_id=p["account"].id, kind=p["kind"], payer_name=p["payer"], bank=p["bank"],
                               last4=p["last4"], relationship=p["relationship"], withdrawn=p["withdraw"], evidence=p["evidence"], reason=p["reason"],
                               recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="payer_exception_recorded", actor=by, content={
        "account_id": str(p["account"].id), "kind": p["kind"], "payer": p["payer"], "last4": p["last4"], "withdrawn": p["withdraw"], "evidence": p["evidence"]})
    return await overview(session, firm, household_id=await household_of(session, firm, p["account"]))


async def household_of(session: AsyncSession, firm: Firm, account: Account) -> uuid.UUID | None:
    mandate = await session.get(Mandate, account.mandate_id) if account.mandate_id else None
    return (await _mandate_households(session, firm.id, [mandate])).get(mandate.id) if mandate else None


# ── Registered banks and the cooling-off ──────────────────────────────────────

def cooling_days(firm: Firm) -> int | None:
    value = ((firm.settings or {}).get(SETTINGS_KEY) or {}).get(COOLING_KEY)
    return int(value) if value else None


async def bank_state(session: AsyncSession, firm: Firm, account: Account, link: BankLink) -> dict:
    """Whether a registered bank of the investor is a change, and from which day it may be paid."""
    inv = await investor(session, firm, account)
    rows = (await session.execute(select(BankLink).where(BankLink.firm_id == firm.id, BankLink.account_id == account.id, BankLink.status != "failed")
                                  .order_by(BankLink.created_at, BankLink.verified_on, BankLink.id))).scalars().all()
    own = [r for r in rows if inv and match(r.owner_name, inv["name"]) != "none"]

    def key(r: BankLink):
        return (r.bank.strip().lower(), r.last4, tuple(sorted(_tokens(r.owner_name))))

    days, today = cooling_days(firm), firm_time.today(firm)
    change = bool(own) and key(link) != key(own[0])
    if not change:
        return {"change": False, "cooling_days": days, "payable_from": None, "payable": True, "said": "The account's first registered bank."}
    since = min(max(r.verified_on, firm_time.today(firm, r.created_at) if r.created_at else r.verified_on) for r in own if key(r) == key(link))
    if days is None:
        return {"change": True, "cooling_days": None, "payable_from": None, "payable": True,
                "said": "A changed bank. The firm has set no cooling-off, so it is payable at once."}
    start = since + timedelta(days=days)
    return {"change": True, "cooling_days": days, "payable_from": start, "payable": today >= start,
            "said": (f"A changed bank, registered on {since.isoformat()}: the firm's {days}-day cooling-off runs to {start.isoformat()}, so nothing is paid to it "
                     "before then.") if today < start else f"A changed bank, past the firm's {days}-day cooling-off."}


async def payout_bank(session: AsyncSession, firm: Firm, account: Account, bank_link_id) -> dict:
    """The registered bank a payment out of an Indian account goes to, or the reason there is none."""
    if bank_link_id in (None, ""):
        raise IndiaMoneyError("For an Indian client a payment goes to a registered bank account of the investor: name it (bank_link_id, from read_money_in).")
    link = await _link(session, firm, account, bank_link_id)
    got = await check_payer(session, firm, account, payer_name=link.owner_name, bank_link=link, use="payout")
    if not got["ok"]:
        raise IndiaMoneyError(got["said"])
    state = await bank_state(session, firm, account, link)
    if not state["payable"]:
        raise IndiaMoneyError(state["said"])
    return {"link": link, "payee": f"{link.owner_name}, {link.bank} ending {link.last4}"[:200], **state}


def cooling_label(state: dict) -> str:
    if state["cooling_days"] is None:
        return "No cooling-off set by the firm: a changed bank is payable at once"
    if not state["change"]:
        return f"First registered bank; a change would cool off for {state['cooling_days']} days"
    return state["said"]


def prepare_cooling(firm: Firm, *, days, reason: str | None) -> dict:
    _require(firm, None, (), "")
    why = _text(reason, "Give the reason for the change; it goes on the audit log.", 5, 300)
    if str(days).strip().lower() in ("none", "off", "0"):
        value = None
    else:
        try:
            value = int(str(days).strip())
        except (TypeError, ValueError):
            raise IndiaMoneyError("Give the cooling-off in whole days (1 to 365), or none.")
        if not 1 <= value <= 365:
            raise IndiaMoneyError("Give the cooling-off in whole days (1 to 365), or none.")
    before = cooling_days(firm)
    if before == value:
        raise IndiaMoneyError("That is already the firm's setting.")
    return {"days": value, "before": before, "reason": why}


async def set_cooling(session: AsyncSession, firm: Firm, actor, *, days, reason: str | None) -> dict:
    """The firm's cooling-off on a changed bank: an administrator's setting, with the reason on the audit log."""
    from app.aurea_core import platform_admin

    if actor is None or UserRole(actor.role) != UserRole.ADMIN:
        raise IndiaMoneyError("The firm's cooling-off on a changed bank is the administrator's setting.")
    p = prepare_cooling(firm, days=days, reason=reason)
    current = {**((firm.settings or {}).get(SETTINGS_KEY) or {}), COOLING_KEY: p["days"]}
    firm.settings = {**(firm.settings or {}), SETTINGS_KEY: current}
    platform_admin.audit(session, firm.id, actor, "india_money.cooling_set", subject="Cooling-off on a changed bank",
                         detail={"before": p["before"], "after": p["days"], "reason": p["reason"]})
    await session.flush()
    return await overview(session, firm)


# ── Money in ──────────────────────────────────────────────────────────────────

async def prepare_credit(session: AsyncSession, firm: Firm, role, *, account_id=None, rail: str | None = None, amount=None, bank_link_id=None,
                         payer_name: str | None = None, payer_last4: str | None = None, reference: str | None = None, on=None,
                         evidence: str | None = None, deposit_id=None, return_reason: str | None = None) -> dict:
    """A credit as its advice shows it, or the return of one. Every check the write makes; nothing written."""
    from app.aurea_core import money_in

    _require(firm, role, CREDIT_RECORDERS, "Operations records money in, from the credit advice.")
    today = firm_time.today(firm)
    day = _day(on, today)
    proof = _text(evidence, "Say where it comes from: the credit advice, the bank statement line, the return memo.", 5, 300)
    if deposit_id not in (None, ""):                                         # a return
        d = await session.get(IncomingDeposit, _uuid(deposit_id, "the deposit"))
        if d is None or d.firm_id != firm.id:
            raise IndiaMoneyError("Deposit not found.")
        if d.returned_on:
            raise IndiaMoneyError("This deposit is already recorded as returned.")
        if day < d.received_on:
            raise IndiaMoneyError("A return comes after the deposit.")
        why = _text(return_reason, "Give the reason as the bank's return memo states it.", 5, 300)
        detail = (await session.execute(select(IndiaCredit).where(IndiaCredit.firm_id == firm.id, IndiaCredit.deposit_id == d.id))).scalars().first()
        return {"kind": "return", "deposit": d, "detail": detail, "account": await session.get(Account, d.account_id), "reason": why, "on": day,
                "evidence": proof, "after_available": bool(d.hold_until is None or day > d.hold_until)}
    if account_id in (None, ""):
        raise IndiaMoneyError("Name the account, or the deposit being returned.")
    account = await _account(session, firm, account_id)
    way = (rail or "").strip().lower().replace(" ", "").replace("net_banking", "netbanking")
    if way not in RAILS:
        raise IndiaMoneyError("Money in comes by UPI, netbanking, RTGS, NEFT or cheque. ACH and NACHA codes are US rules.")
    value = _amount(amount)
    ref = _text(reference, "Give the bank's reference: the UTR, the UPI reference or the cheque number.", 4, 80)
    if (await session.execute(select(IndiaCredit.id).where(IndiaCredit.firm_id == firm.id, IndiaCredit.rail == way,
                                                           IndiaCredit.bank_reference == ref).limit(1))).first():
        raise IndiaMoneyError(f"A {RAILS[way][1]} credit with reference {ref} is already recorded.")
    link = await _link(session, firm, account, bank_link_id) if bank_link_id not in (None, "") else None
    name = _text(payer_name, "Give the paying name as the credit advice or the cheque shows it.", 2, 200)
    digits = link.last4 if link else _last4(payer_last4)
    if link and payer_last4 not in (None, "") and _last4(payer_last4) != link.last4:
        raise IndiaMoneyError(f"The advice shows an account ending {_last4(payer_last4)}, not the registered account ending {link.last4}.")
    got = await check_payer(session, firm, account, payer_name=name, bank_link=link, last4=digits, amount=value, use="credit")
    if not got["ok"]:
        raise IndiaMoneyError(got["said"])
    if link is None and got["basis"] in ("investor", "name_confirmed"):
        raise IndiaMoneyError(f"The name is the investor's, but the account ending {digits} was not named as a registered bank. Give the registered bank account "
                              "(bank_link_id), registering it from its evidence first if it is new.")
    policy = money_in.policy(firm)
    threshold = policy["deposit_hold_threshold"]
    days = int((policy["deposit_hold_days"] or {}).get(way, 0))
    hold = money_in.business_days_after(day, days) if threshold is not None and value >= float(threshold) and days > 0 else None
    return {"kind": "credit", "account": account, "rail": way, "amount": value, "reference": ref, "link": link, "payer": name, "last4": digits,
            "check": got, "on": day, "evidence": proof, "hold_until": hold, "hold_set": threshold is not None}


async def record_credit(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    from app.aurea_core import money_in

    r = await prepare_credit(session, firm, role, **terms)
    if r["kind"] == "return":
        d = r["deposit"]
        d.returned_on, d.return_evidence = r["on"], r["evidence"]
        if r["detail"] is not None:
            r["detail"].return_reason = r["reason"]
        await session.flush()
        await ledger.append_entry(session, firm_id=firm.id, event_type="deposit_returned", actor=by,
                                  content={"deposit_id": str(d.id), "reason": r["reason"], "evidence": r["evidence"]})
    else:
        d = IncomingDeposit(firm_id=firm.id, account_id=r["account"].id, method=RAILS[r["rail"]][0], amount=r["amount"],
                            bank_link_id=r["link"].id if r["link"] else None, received_on=r["on"], hold_until=r["hold_until"], evidence=r["evidence"],
                            recorded_by=by)
        session.add(d)
        await session.flush()
        got = r["check"]
        session.add(IndiaCredit(firm_id=firm.id, deposit_id=d.id, rail=r["rail"], bank_reference=r["reference"], payer_name=r["payer"], payer_last4=r["last4"],
                                basis=got["basis"], exception_id=uuid.UUID(got["exception_id"]) if got["exception_id"] else None))
        await session.flush()
        await ledger.append_entry(session, firm_id=firm.id, event_type="deposit_recorded", actor=by, content={
            "account_id": str(r["account"].id), "method": r["rail"], "amount": r["amount"], "reference": r["reference"], "payer": r["payer"],
            "basis": got["basis"], "hold_until": r["hold_until"].isoformat() if r["hold_until"] else None})
    return await money_in.for_firm(session, firm.id, household_id=await household_of(session, firm, r["account"]))


# ── Inward remittances ────────────────────────────────────────────────────────

async def prepare_remittance(session: AsyncSession, firm: Firm, role, *, account_id, bank_link_id=None, account_kind: str | None = None, received_on=None,
                             amount_inr=None, certificate_reference: str | None = None, remitter_name: str | None = None, foreign_amount=None,
                             foreign_currency: str | None = None, document: str | None = None) -> dict:
    from app.aurea_core import residency

    _require(firm, role, REMITTANCE_RECORDERS, "An adviser, paraplanner or operations records an inward remittance, from the bank's certificate.")
    account = await _account(session, firm, account_id)
    inv = await investor(session, firm, account)
    if inv is None or inv["person"] is None:
        raise IndiaMoneyError("An NRE or NRO credit belongs to a non-resident person, and this account's mandate names none.")
    kind = (account_kind or "").strip().lower()
    if kind not in ACCOUNT_KINDS:
        raise IndiaMoneyError("Say whether the money was credited to an NRE or an NRO account, as the certificate states.")
    registration = (account.registration_type or "").lower()
    if registration == "nri_nre" and kind == "nro":
        raise IndiaMoneyError(f"{account.name} is registered as an NRE account, which keeps money repatriable: an NRO credit does not belong on it.")
    if registration not in ("nri_nre", "nri_nro"):
        rows = await residency.records(session, firm.id, inv["person"].id)
        if not rows or rows[0].fema_status not in ("nri", "oci"):
            raise IndiaMoneyError(f"Nothing on file says {inv['name']} is non-resident (the account is not registered NRE or NRO, and no residency record gives "
                                  "NRI or OCI status). Record that from its document first.")
    link = await _link(session, firm, account, bank_link_id)
    own = await check_payer(session, firm, account, payer_name=link.owner_name, bank_link=link, use="payout")
    if not own["ok"]:
        raise IndiaMoneyError(f"The {ACCOUNT_KINDS[kind]} account must be the investor's own. {own['said']}")
    today = firm_time.today(firm)
    if received_on in (None, ""):
        raise IndiaMoneyError("Give the day the money was credited, as YYYY-MM-DD.")
    day = _day(received_on, today, "the day the money was credited")
    value = _amount(amount_inr)
    ref = _text(certificate_reference, "Give the bank certificate's reference (the inward remittance certificate or FIRC number).", 4, 80)
    if (await session.execute(select(InwardRemittance.id).where(InwardRemittance.firm_id == firm.id, InwardRemittance.certificate_reference == ref).limit(1))).first():
        raise IndiaMoneyError(f"A remittance under certificate {ref} is already recorded.")
    remitter = _text(remitter_name, "Name who sent it, as the certificate shows.", 2, 200)
    foreign, currency = None, None
    if foreign_amount not in (None, "") or foreign_currency not in (None, ""):
        foreign = _amount(foreign_amount, "Give the foreign amount with its three-letter currency, as the certificate shows, or neither.")
        currency = (foreign_currency or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha() or currency == "INR":
            raise IndiaMoneyError("Give the foreign amount with its three-letter currency, as the certificate shows, or neither.")
    proof = _text(document, "Say where the bank's certificate is filed.", 5, 300)
    return {"account": account, "investor": inv["name"], "link": link, "kind": kind, "on": day, "amount": value, "reference": ref, "remitter": remitter,
            "remitter_match": match(remitter, inv["name"]), "foreign": foreign, "currency": currency, "document": proof,
            "warning": "NRE money into an account registered NRO does not stay repatriable through it" if registration == "nri_nro" and kind == "nre" else None}


async def record_remittance(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_remittance(session, firm, role, **terms)
    session.add(InwardRemittance(created_at=utcnow(), firm_id=firm.id, account_id=p["account"].id, bank_link_id=p["link"].id, account_kind=p["kind"],
                                 received_on=p["on"], amount_inr=p["amount"], foreign_amount=p["foreign"], foreign_currency=p["currency"],
                                 remitter_name=p["remitter"], remitter_match=p["remitter_match"], certificate_reference=p["reference"],
                                 source_document=p["document"], recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="inward_remittance_recorded", actor=by, content={
        "account_id": str(p["account"].id), "kind": p["kind"], "amount_inr": p["amount"], "certificate": p["reference"], "document": p["document"]})
    return await overview(session, firm, household_id=await household_of(session, firm, p["account"]))


# ── A mandate's first debit ───────────────────────────────────────────────────

async def debits(session: AsyncSession, firm_id: uuid.UUID, account_ids: list[uuid.UUID] | None = None) -> dict[tuple, MandateDebit]:
    """The latest first-debit record for each mandate (account, reference)."""
    stmt = select(MandateDebit).where(MandateDebit.firm_id == firm_id)
    if account_ids is not None:
        stmt = stmt.where(MandateDebit.account_id.in_(account_ids or [uuid.uuid4()]))
    out: dict[tuple, MandateDebit] = {}
    for r in (await session.execute(stmt.order_by(MandateDebit.created_at, MandateDebit.id))).scalars().all():
        out[(r.account_id, r.reference)] = r
    return out


async def prepare_first_debit(session: AsyncSession, firm: Firm, role, *, account_id, reference: str | None, outcome: str | None, debited_on=None,
                              amount=None, document: str | None = None, reason: str | None = None) -> dict:
    from app.aurea_core import sip_mandates

    _require(firm, role, sip_mandates.RECORDERS, "An adviser, paraplanner or operations records a first debit, from the bank's or the fund's notice.")
    account = await _account(session, firm, account_id)
    ref = (reference or "").strip()
    row = next((r for r in await sip_mandates.standing(session, firm.id, [account.id]) if r.reference == ref), None)
    if row is None:
        raise IndiaMoneyError("There is no mandate on file under that reference on this account.")
    if row.kind != "registered" or row.status != "active":
        raise IndiaMoneyError(f"Mandate {ref} is not registered ({sip_mandates.STATES[sip_mandates.state_of(row, firm_time.today(firm))].lower()}), so it has "
                              "presented no debit. Record the registration first.")
    result = (outcome or "").strip().lower()
    if result not in ("confirmed", "failed"):
        raise IndiaMoneyError("The first debit is confirmed or failed.")
    if debited_on in (None, ""):
        raise IndiaMoneyError("Give the day of the debit, as YYYY-MM-DD.")
    day = _day(debited_on, firm_time.today(firm), "the day of the debit")
    if day < row.starts_on:
        raise IndiaMoneyError(f"The mandate starts on {row.starts_on.isoformat()}: a debit cannot be before it.")
    value = _amount(amount)
    if result == "confirmed" and value > float(row.per_debit_limit) + 1e-9:
        raise IndiaMoneyError(f"{rupees(value)} is above the mandate's limit of {rupees(float(row.per_debit_limit))} a debit, so it cannot have been debited under it.")
    proof = _text(document, "Say where the bank's or the fund's notice is filed.", 5, 300)
    before = (await debits(session, firm.id, [account.id])).get((account.id, ref))
    if before is not None and before.outcome == "confirmed":
        raise IndiaMoneyError(f"The first debit under {ref} is already recorded as confirmed on {before.debited_on.isoformat()}.")
    if before is not None and before.outcome == result and before.debited_on == day:
        raise IndiaMoneyError("That debit is already recorded.")
    if before is not None and day < before.debited_on:
        raise IndiaMoneyError(f"A failure is on file for {before.debited_on.isoformat()}: this debit is earlier than it.")
    why = _text(reason, "Give the reason as the bank's return states it.", 3, 300) if result == "failed" else None
    return {"account": account, "mandate": row, "reference": ref, "outcome": result, "on": day, "amount": value, "reason": why, "document": proof}


async def record_first_debit(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    from app.aurea_core import sip_mandates

    p = await prepare_first_debit(session, firm, role, **terms)
    session.add(MandateDebit(created_at=utcnow(), firm_id=firm.id, account_id=p["account"].id, reference=p["reference"], outcome=p["outcome"],
                             debited_on=p["on"], amount=p["amount"], reason=p["reason"], source_document=p["document"], recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="mandate_first_debit_recorded", actor=by, content={
        "account_id": str(p["account"].id), "reference": p["reference"], "outcome": p["outcome"], "on": p["on"].isoformat(), "amount": p["amount"],
        "reason": p["reason"], "document": p["document"]})
    raised = await chase_now(session, firm, p["account"]) if p["outcome"] == "failed" else []
    result = await sip_mandates.overview(session, firm, household_id=await household_of(session, firm, p["account"]))
    result["tasks_raised"] = raised
    return result


# ── The queue, and its tasks ──────────────────────────────────────────────────

QUEUE_STATES = {"requested": "Requested, not registered", "failed": "Registration failed", "rejected": "Rejected by the bank", "first_debit_failed": "First debit failed",
                "unconfirmed": "First debit not recorded", "none": "No mandate on file", "expired": "Mandate expired", "paused": "Mandate paused",
                "cancelled": "Mandate cancelled", "over_limit": "Instalment above the limit"}


def _label(entry: dict) -> str:
    """The task's title after "India: ". Stable while the item stays as it is, so one task is raised for it."""
    what = {"requested": "requested and still not registered", "failed": "registration failed", "rejected": "rejected by the bank",
            "unconfirmed": "registered, but no first debit is recorded", "expired": "has expired", "paused": "is paused", "cancelled": "is cancelled",
            "over_limit": "limit is below the instalment"}
    if entry["state"] == "none":
        return f"No debit mandate on file for the {entry['sip_label']} SIP on {entry['account']}"
    if entry["state"] == "first_debit_failed":
        return f"First debit of {entry['debit_on']} failed under mandate {entry['reference']} on {entry['account']}"
    return f"Debit mandate {entry['reference']} {what[entry['state']]}: {entry['account']}"


async def queue(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None, account_id: uuid.UUID | None = None,
                owners_and_tasks: bool = True) -> list[dict]:
    """Everything that stands between a SIP and its debit, oldest first, with its age, its owner and its task. The reminders job reads
    it per household and names the owner itself, so it asks for neither."""
    from app.aurea_core import india_reminders, sip as sip_engine, sip_mandates

    if not applies(firm):
        return []
    today = firm_time.today(firm)
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
    mandates = {m.id: m for m in (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()}
    homes = await _mandate_households(session, firm.id, list(mandates.values()))
    home_of = {a.id: homes.get(a.mandate_id) for a in accounts.values()}
    ids = [a for a in accounts if (household_id is None or home_of[a] == household_id) and (account_id is None or a == account_id)]
    rows = await sip_mandates.standing(session, firm.id, ids)
    sips = [s for s in (await session.execute(select(Sip).where(Sip.firm_id == firm.id, Sip.ended_on.is_(None)))).scalars().all()
            if s.account_id in set(ids) and (s.ends_on is None or s.ends_on >= today)]
    if not rows and not sips:
        return []
    first = await debits(session, firm.id, ids)
    funds = {}
    if sips:
        from app.models.portfolio import Instrument

        funds = {i.id: i.name for i in (await session.execute(select(Instrument).where(Instrument.id.in_([s.instrument_id for s in sips])))).scalars().all()}

    def recorded(r) -> date:
        return r.event_on or (firm_time.today(firm, r.created_at) if r.created_at else r.starts_on)

    items: dict[tuple, dict] = {}

    def add(key, account: Account, state: str, since: date, *, row=None, chase: int, extra: dict | None = None) -> dict:
        entry = items.get(key)
        if entry is None:
            entry = items[key] = {"state": state, "label": QUEUE_STATES[state], "account_id": str(account.id), "account": account.name,
                                  "household_id": str(home_of[account.id]) if home_of.get(account.id) else None,
                                  "reference": row.reference if row is not None else None,
                                  "rail": row.mandate_type if row is not None else None,
                                  "rail_label": sip_mandates.TYPES.get(row.mandate_type) if row is not None else None,
                                  "since": since.isoformat(), "age_days": max((today - since).days, 0), "chase_after_days": chase,
                                  "overdue": (today - since).days >= chase, "due_on": (since + timedelta(days=chase)).isoformat(),
                                  "reason": getattr(row, "reason", None), "sips": [], "monthly": 0.0, **(extra or {})}
        return entry

    def attach(entry: dict, s: Sip) -> None:
        name = funds.get(s.instrument_id, "a fund")
        if all(x["sip_id"] != str(s.id) for x in entry["sips"]):
            entry["sips"].append({"sip_id": str(s.id), "fund": name, "amount": float(s.amount)})
            entry["monthly"] = round(entry["monthly"] + float(s.amount), 2)

    # A mandate still asked for, or refused, until a registration recorded after it is in force.
    for r in rows:
        state = sip_mandates.state_of(r, today)
        if state not in ("requested", "failed", "rejected"):
            continue
        later = [o for o in rows if o.account_id == r.account_id and o.reference != r.reference
                 and o.created_at.replace(tzinfo=None) >= r.created_at.replace(tzinfo=None)]
        if any(sip_mandates.state_of(o, today) == "active" for o in later) or (state != "requested" and any(sip_mandates.state_of(o, today) == "requested" for o in later)):
            continue
        chase = CHASE_DAYS.get(r.mandate_type, 4) if state == "requested" else 0
        add((r.account_id, r.reference), accounts[r.account_id], state, recorded(r), row=r, chase=chase)

    for s in sips:
        account = accounts[s.account_id]
        behind = await sip_mandates.for_sip(session, s, today)
        row, state = behind["row"], behind["state"]
        mine = [e for e in items.values() if e["account_id"] == str(s.account_id)]
        if row is None:
            since = s.starts_on if s.starts_on <= today else firm_time.today(firm, s.created_at)
            entry = add((s.account_id, "sip", s.id), account, "none", since, chase=CHASE_DAYS["nach"],
                        extra={"sip_label": f"{funds.get(s.instrument_id, 'fund')} {rupees(float(s.amount))} day {s.day_of_month}"})
            attach(entry, s)
            continue
        if state in ("requested", "failed", "rejected"):
            entry = items.get((s.account_id, row.reference))
            if entry is not None:
                attach(entry, s)
            continue
        if state in ("expired", "paused", "cancelled"):
            if mine:                       # a replacement is already in the queue: the SIP is shown on it
                attach(mine[-1], s)
                continue
            since = row.expires_on + timedelta(days=1) if state == "expired" and row.expires_on else recorded(row)
            attach(add((s.account_id, row.reference), account, state, since, row=row, chase=0), s)
            continue
        if state != "active":              # not yet in force: nothing is due under it
            continue
        if float(s.amount) > float(row.per_debit_limit) + 1e-9:
            attach(add((s.account_id, row.reference, "limit"), account, "over_limit", recorded(row), row=row, chase=0), s)
        debit = first.get((s.account_id, row.reference))
        if debit is not None and debit.outcome == "failed":
            entry = add((s.account_id, row.reference, "debit"), account, "first_debit_failed", debit.debited_on, row=row, chase=0,
                        extra={"debit_on": debit.debited_on.isoformat()})
            entry["reason"] = debit.reason
            attach(entry, s)
        elif debit is None:
            expected = sip_engine.next_instalment(s.day_of_month, max(s.starts_on, row.starts_on), max(s.starts_on, row.starts_on))
            if expected < today:
                attach(add((s.account_id, row.reference, "unconfirmed"), account, "unconfirmed", expected, row=row, chase=DEBIT_CHASE_DAYS), s)

    # A failed first debit is chased even where no SIP is on file against it.
    for (aid, ref), debit in first.items():
        row = next((r for r in rows if r.account_id == aid and r.reference == ref), None)
        if row is not None and debit.outcome == "failed" and sip_mandates.state_of(row, today) == "active" and (aid, ref, "debit") not in items:
            entry = add((aid, ref, "debit"), accounts[aid], "first_debit_failed", debit.debited_on, row=row, chase=0, extra={"debit_on": debit.debited_on.isoformat()})
            entry["reason"] = debit.reason

    if not owners_and_tasks:
        return [{**entry, "title": _label(entry)} for entry in items.values()]
    tasks_by = {(t.title, t.household_id): t for t in (await session.execute(select(Task).where(
        Task.firm_id == firm.id, Task.source == india_reminders.SOURCE))).scalars().all()}
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    held = await advisers.of_households(session, firm.id) if items else {}
    people = {u.id: u.full_name for u in (await session.execute(select(User).where(
        User.id.in_(list(set(held.values())) or [uuid.uuid4()]), User.is_active.is_(True)))).scalars().all()}
    out = []
    for entry in items.values():
        hid = uuid.UUID(entry["household_id"]) if entry["household_id"] else None
        entry["household"] = names.get(hid)
        entry["owner"] = people.get(held.get(hid))
        entry["title"] = _label(entry)
        task = tasks_by.get((india_reminders._title({"label": entry["title"]}), hid))
        entry["task"] = str(task.status) if task is not None else None
        out.append(entry)
    return sorted(out, key=lambda e: (not e["overdue"], -e["age_days"], e["account"]))


def chase_item(entry: dict, today: date) -> dict:
    """The queue entry as the reminders job raises it."""
    due = date.fromisoformat(entry["due_on"])
    return {"kind": "mandate_chase", "label": entry["title"], "due_on": due.isoformat(), "state": "overdue" if due < today else "due_soon",
            "source": CHASE_SOURCE_NOTE, "assumed": None, "days": (due - today).days}


async def chase_items(session: AsyncSession, firm: Firm, household_id: uuid.UUID, today: date) -> list[dict]:
    """The household's queue entries that are past their chase line, for the daily reminders job."""
    return [chase_item(e, today) for e in await queue(session, firm, household_id=household_id, owners_and_tasks=False) if e["overdue"]]


async def chase_now(session: AsyncSession, firm: Firm, account: Account) -> list[dict]:
    """Raise, at once, the task for each failure just recorded on this account. Once each; nobody outside the firm is told."""
    from app.aurea_core import india_reminders

    hid = await household_of(session, firm, account)
    entries = [e for e in await queue(session, firm, household_id=hid, account_id=account.id) if e["state"] in ("failed", "rejected", "first_debit_failed")]
    today = firm_time.today(firm)
    return await india_reminders.raise_items(session, firm, hid, [chase_item(e, today) for e in entries])


async def task_owner_said(session: AsyncSession, firm: Firm, account: Account) -> str:
    hid = await household_of(session, firm, account)
    who = await advisers.adviser_of(session, firm.id, hid) if hid else None
    name = who.full_name if who is not None and who.is_active else None
    return f"A task for {name}" if name else "A task, assigned to nobody: the household has no adviser on record"


# ── The view ──────────────────────────────────────────────────────────────────

async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    """The firm's (or a household's) registered banks, exceptions, credits, remittances and mandate queue."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat()}
    mandates = {m.id: m for m in (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()}
    homes = await _mandate_households(session, firm.id, list(mandates.values()))
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()
                if household_id is None or homes.get(a.mandate_id) == household_id}
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    client = {aid: names.get(homes.get(a.mandate_id)) for aid, a in accounts.items()}
    ids = list(accounts) or [uuid.uuid4()]
    links = (await session.execute(select(BankLink).where(BankLink.firm_id == firm.id, BankLink.account_id.in_(ids))
                                   .order_by(BankLink.created_at, BankLink.verified_on))).scalars().all()
    banks = []
    for link in links:
        account = accounts[link.account_id]
        inv = await investor(session, firm, account)
        reading = (await check_payer(session, firm, account, payer_name=link.owner_name, bank_link=link, use="payout")) if inv else None
        registered = link.status == "verified" and bool(reading and reading["ok"])
        state = await bank_state(session, firm, account, link) if registered else None
        banks.append({"id": str(link.id), "account_id": str(account.id), "account": account.name, "client": client[account.id], "bank": link.bank,
                      "last4": link.last4, "owner": link.owner_name, "investor": inv["name"] if inv else None, "status": link.status,
                      "registered": registered, "whose": reading["label"] if reading else "No investor on file",
                      "changed": bool(state and state["change"]), "payable": bool(state and state["payable"]),
                      "payable_from": state["payable_from"].isoformat() if state and state["payable_from"] else None,
                      "said": state["said"] if state else (reading["said"] if reading else None)})
    excs = [{"id": str(e.id), "account_id": str(e.account_id), "account": accounts[e.account_id].name, "client": client[e.account_id], "kind": e.kind,
             "kind_label": EXCEPTION_KINDS[e.kind], "payer": e.payer_name, "bank": e.bank, "last4": e.last4,
             "relationship": RELATIVES.get(e.relationship) if e.relationship else None, "evidence": e.evidence}
            for e in await exceptions(session, firm.id, ids)]
    remits = (await session.execute(select(InwardRemittance).where(InwardRemittance.firm_id == firm.id, InwardRemittance.account_id.in_(ids))
                                    .order_by(InwardRemittance.received_on.desc()))).scalars().all()
    days = cooling_days(firm)
    line = law_figures.cited([MINOR_LINE_KEY], today)[0]
    return {"applies": True, "as_of": today.isoformat(), "household_id": str(household_id) if household_id else None,
            "cooling": {"days": days, "set_by_firm": days is not None,
                        "label": f"{days}-day cooling-off on a changed bank" if days else "No cooling-off set: a changed bank is payable at once"},
            "banks": banks, "exceptions": excs,
            "remittances": [{"id": str(r.id), "account": accounts[r.account_id].name, "client": client[r.account_id], "kind": ACCOUNT_KINDS[r.account_kind],
                             "received_on": r.received_on.isoformat(), "amount_inr": float(r.amount_inr),
                             "foreign_amount": float(r.foreign_amount) if r.foreign_amount is not None else None, "foreign_currency": r.foreign_currency,
                             "remitter": r.remitter_name, "remitter_match": r.remitter_match, "certificate": r.certificate_reference,
                             "document": r.source_document} for r in remits],
            "queue": await queue(session, firm, household_id=household_id), "line": line,
            "note": "Recorded from documents. Nothing here writes cash, places an order, presents or retries a debit, or tells a client."}


async def credit_rows(session: AsyncSession, firm: Firm, deposit_ids: list[uuid.UUID]) -> dict[str, dict]:
    """What each Indian credit's advice showed, by deposit id, for the money-in view."""
    rows = (await session.execute(select(IndiaCredit).where(IndiaCredit.firm_id == firm.id,
                                                            IndiaCredit.deposit_id.in_(deposit_ids or [uuid.uuid4()])))).scalars().all()
    return {str(r.deposit_id): {"rail": r.rail, "rail_label": RAILS[r.rail][1], "reference": r.bank_reference, "payer": r.payer_name,
                                "payer_last4": r.payer_last4, "basis": r.basis, "basis_label": BASES[r.basis], "return_memo": r.return_reason} for r in rows}
