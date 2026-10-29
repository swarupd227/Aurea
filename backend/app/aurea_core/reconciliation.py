"""Reconciling the book against the custodian's statement.

A statement is compared with the book *as it stood on the statement's date*, not as it stands
now: the book is rolled back over everything booked after that date, so a trade settled this
morning is not held against a statement struck last night. Where it cannot be rolled back
honestly - a split posted since, a movement of a kind this module does not know the cash effect
of - the thing is reported as not comparable. It is never guessed at and never raised as a break.

What this module does not do is change the book. A break says the custodian and the book
disagree; which of them is right is a judgement for a person, and the cause it offers is a likely
one with the records behind it, not a verdict. Clearing a break is done by whoever fixes the
underlying thing, or on the next statement when the difference is gone.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.corporate_actions import CorporateAction, CorporateActionEntitlement
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding, Instrument, Transaction
from app.models.reconciliation import CLOSED_STATUSES, CustodyStatement, ReconBreak
from app.models.tenant import Firm

QTY_TOLERANCE = Decimal("0.0001")
DEFAULT_CASH_TOLERANCE = Decimal("0.01")
# A trade settles at the custodian a couple of business days after it is booked here, which is
# up to four calendar days across a weekend. A difference that is exactly such a trade is timing.
SETTLEMENT_WINDOW_DAYS = 4
# A statement older than this no longer says what the account holds.
STATEMENT_FRESH_DAYS = 7

OPEN_STATUSES = ("open", "explained", "escalated")
ACTIVE_STATUSES = OPEN_STATUSES + ("dismissed",)
# open -> explained | escalated | dismissed | resolved; explained -> escalated | dismissed | resolved;
# escalated -> dismissed | resolved. A closed break stays closed; if it comes back it is a new one.
ACTIONS = {"explained": ("open",), "escalated": ("open", "explained"),
           "dismissed": ("open", "explained", "escalated"), "resolved": ("open", "explained", "escalated")}
NOTE_REQUIRED = ("dismissed", "resolved")


class ReconciliationError(Exception):
    """The statement or the action was refused. Carries a reason fit to show the user."""


def _d(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v if v is not None else 0))


def _f(v) -> float:
    return float(_d(v))


def cash_tolerance(firm: Firm | None) -> Decimal:
    cfg = ((firm.settings or {}).get("reconciliation") or {}) if firm else {}
    try:
        return Decimal(str(cfg.get("cash_tolerance", DEFAULT_CASH_TOLERANCE)))
    except Exception:
        return DEFAULT_CASH_TOLERANCE


def cash_effect(txn: Transaction) -> Decimal | None:
    """What a booked transaction did to the account's cash, or None if this kind is not known.
    Unknown is deliberate: guessing would make a cash difference look explained or unexplained
    for the wrong reason."""
    amount, kind = _d(txn.amount), str(txn.txn_type)
    fees = _d((txn.lineage or {}).get("fees", 0))
    if kind == "buy":
        return -(amount + fees)
    if kind == "sell":
        return amount - fees
    if kind == "fee":
        return -amount
    if kind in ("fee_credit", "dividend", "distribution"):
        return amount
    return None


# ── the book on the statement's date ─────────────────────────────────────────

async def book_as_of(session: AsyncSession, account: Account, as_of: date) -> dict:
    """Quantity per instrument and cash as the book stood at the end of `as_of`."""
    holdings = (await session.execute(select(Holding).where(Holding.account_id == account.id))).scalars().all()
    positions = {h.instrument_id: _d(h.quantity) for h in holdings}
    cash, cash_ok = _d(account.cash_balance), True
    after = (await session.execute(
        select(Transaction).where(Transaction.account_id == account.id, Transaction.trade_date > as_of)
        .order_by(Transaction.trade_date)
    )).scalars().all()
    for t in after:
        kind = str(t.txn_type)
        if kind == "buy" and t.instrument_id:
            positions[t.instrument_id] = positions.get(t.instrument_id, Decimal(0)) - _d(t.quantity)
        elif kind == "sell" and t.instrument_id:
            positions[t.instrument_id] = positions.get(t.instrument_id, Decimal(0)) + _d(t.quantity)
        effect = cash_effect(t)
        if effect is None:
            cash_ok = False
        else:
            cash -= effect

    # A split changes quantity without a trade, so rolling it back needs its ratio and the order
    # it was applied in. Rather than guess, the instrument is left out of the comparison.
    not_comparable: set[uuid.UUID] = set()
    rows = (await session.execute(
        select(CorporateActionEntitlement, CorporateAction)
        .join(CorporateAction, CorporateAction.id == CorporateActionEntitlement.corporate_action_id)
        .where(CorporateActionEntitlement.account_id == account.id,
               CorporateActionEntitlement.status.in_(("posted", "verified")),
               CorporateAction.action_type.in_(("split", "reverse_split")))
    )).all()
    for ent, action in rows:
        if ent.posted_at is not None and ent.posted_at.date() > as_of:
            not_comparable.add(action.instrument_id)

    return {"positions": positions, "cash": cash, "cash_comparable": cash_ok,
            "not_comparable": not_comparable, "movements_after": len(after)}


def compare(book: dict[uuid.UUID, Decimal], custodian: dict[uuid.UUID, Decimal],
            *, skip=frozenset(), tolerance: Decimal = QTY_TOLERANCE) -> list[dict]:
    """The positions the two sides disagree on. A side holding next to nothing counts as not
    holding it, so a fully-sold line the custodian has dropped is a match, not a break."""
    out = []
    for iid in sorted(set(book) | set(custodian), key=str):
        if iid in skip:
            continue
        b, c = book.get(iid, Decimal(0)), custodian.get(iid, Decimal(0))
        if abs(b - c) <= tolerance:
            continue
        kind = "missing_in_book" if b <= tolerance else "missing_at_custodian" if c <= tolerance else "quantity"
        out.append({"instrument_id": iid, "kind": kind, "book": b, "custodian": c})
    return out


# ── why they might disagree ──────────────────────────────────────────────────

def _expected_effect(action: CorporateAction, ent: CorporateActionEntitlement) -> tuple[str, Decimal] | None:
    """(what changes, by how much) if this entitlement were posted, for the events whose effect
    is mechanical. Anything else has no expected figure to match a difference against."""
    details = action.details or {}
    shares = _d(ent.shares_entitled)
    if action.action_type in ("cash_dividend", "return_of_capital"):
        return "cash", (shares * _d(details.get("cash_per_share"))).quantize(Decimal("0.01"))
    if action.action_type in ("split", "reverse_split"):
        ratio = _d(details.get("ratio") or 1)
        return "quantity", shares * (ratio - 1)
    return None


def _effective(action: CorporateAction) -> date | None:
    if action.action_type in ("split", "reverse_split"):
        return action.ex_date or action.record_date or action.payable_date
    return action.payable_date or action.ex_date or action.record_date


async def _pending(session: AsyncSession, account_id: uuid.UUID, as_of: date) -> list[tuple]:
    """Entitlements the custodian would already have acted on by `as_of` that the book has not posted."""
    rows = (await session.execute(
        select(CorporateActionEntitlement, CorporateAction)
        .join(CorporateAction, CorporateAction.id == CorporateActionEntitlement.corporate_action_id)
        .where(CorporateActionEntitlement.account_id == account_id,
               CorporateActionEntitlement.status.in_(("pending", "elected")))
    )).all()
    return [(e, a) for e, a in rows if (_effective(a) is not None and _effective(a) <= as_of)]


async def _window(session: AsyncSession, account_id: uuid.UUID, as_of: date) -> list[Transaction]:
    start = as_of - timedelta(days=SETTLEMENT_WINDOW_DAYS)
    return (await session.execute(
        select(Transaction).where(Transaction.account_id == account_id,
                                  Transaction.trade_date > start, Transaction.trade_date <= as_of)
        .order_by(Transaction.trade_date)
    )).scalars().all()


def _verdict(cause: str, confidence: float, next_step: str, evidence: dict) -> dict:
    return {"cause": cause, "confidence": confidence, "next_step": next_step, "evidence": evidence}


UNEXPLAINED = _verdict("unexplained", 0.0, "investigate", {})


async def explain(session: AsyncSession, *, account: Account, diff: dict, as_of: date,
                  cash_tol: Decimal) -> dict:
    """The most likely cause of one difference, and the records that make it so. Only a cause
    that accounts for the difference exactly is offered; otherwise it is left unexplained."""
    if diff["kind"] == "unmapped_security":
        return _verdict("unmapped_security", 0.95, "map_instrument", {"symbol": diff["symbol"]})

    window = await _window(session, account.id, as_of)
    pending = await _pending(session, account.id, as_of)

    if diff["kind"] == "cash":
        gap = diff["book"] - diff["custodian"]            # what the book has that the custodian does not
        trades = [t for t in window if str(t.txn_type) in ("buy", "sell")]
        fees = [t for t in window if str(t.txn_type) == "fee"]
        for cause, group in (("settlement_timing", trades), ("fee_timing", fees),
                             ("settlement_timing", trades + fees)):
            if not group:
                continue
            total = sum((cash_effect(t) or Decimal(0) for t in group), Decimal(0))
            if abs(gap - total) <= cash_tol and abs(total) > cash_tol:
                return _verdict(cause, 0.9, "wait_for_next_statement",
                                {"transactions": [str(t.id) for t in group], "net_cash": _f(total),
                                 "window_days": SETTLEMENT_WINDOW_DAYS})
        expected = [(e, a, _expected_effect(a, e)) for e, a in pending]
        cash_due = [(e, a, x[1]) for e, a, x in expected if x and x[0] == "cash"]
        total = sum((amt for _, _, amt in cash_due), Decimal(0))
        if cash_due and abs((-gap) - total) <= cash_tol:
            return _verdict("corporate_action_pending", 0.85, "post_entitlement",
                            {"entitlements": [str(e.id) for e, _, _ in cash_due], "expected_cash": _f(total)})
        return UNEXPLAINED

    # a position
    iid = diff["instrument_id"]
    gap = diff["book"] - diff["custodian"]
    trades = [t for t in window if t.instrument_id == iid and str(t.txn_type) in ("buy", "sell")]
    net = sum((_d(t.quantity) if str(t.txn_type) == "buy" else -_d(t.quantity) for t in trades), Decimal(0))
    if trades and abs(net) > QTY_TOLERANCE and abs(gap - net) <= QTY_TOLERANCE:
        return _verdict("settlement_timing", 0.9, "wait_for_next_statement",
                        {"transactions": [str(t.id) for t in trades], "net_quantity": _f(net),
                         "window_days": SETTLEMENT_WINDOW_DAYS})
    mine = [(e, a, _expected_effect(a, e)) for e, a in pending if a.instrument_id == iid]
    due = [(e, a, x[1]) for e, a, x in mine if x and x[0] == "quantity"]
    total = sum((q for _, _, q in due), Decimal(0))
    if due and abs((-gap) - total) <= QTY_TOLERANCE:
        return _verdict("corporate_action_pending", 0.85, "post_entitlement",
                        {"entitlements": [str(e.id) for e, _, _ in due], "expected_quantity": _f(total)})
    return UNEXPLAINED


# ── importing a statement and comparing it ───────────────────────────────────

def _clean_positions(rows) -> dict[str, Decimal]:
    if not isinstance(rows, list):
        raise ReconciliationError("Positions must be a list of {symbol, quantity}.")
    out: dict[str, Decimal] = {}
    for r in rows:
        if not isinstance(r, dict):
            raise ReconciliationError("Each position needs a symbol and a quantity.")
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym:
            raise ReconciliationError("A position has no symbol.")
        try:
            qty = Decimal(str(r.get("quantity")))
        except Exception:
            raise ReconciliationError(f"{sym}: the quantity is not a number.")
        if qty < 0:
            raise ReconciliationError(f"{sym}: the quantity is negative. This does not take short positions.")
        if sym in out:
            raise ReconciliationError(f"{sym} appears twice on the statement - which line is right?")
        out[sym] = qty
    return out


async def import_statement(
    session: AsyncSession, firm_id: uuid.UUID, *, account_id: uuid.UUID, as_of: date,
    positions, cash_balance=None, currency: str | None = None, custodian: str | None = None,
    source_document: str, imported_by: str, today: date | None = None,
) -> dict:
    """Record the custodian's statement for an account and compare it with the book.

    Refused when it cannot be taken at face value: no source document, a date in the future, a
    currency other than the account's, a statement older than the one already on file, or
    nothing on it to compare. A second statement for the same date replaces the first."""
    today = today or utcnow().date()
    account = await session.get(Account, account_id)
    if account is None or account.firm_id != firm_id:
        raise ReconciliationError("That account is not in this firm.")
    if len((source_document or "").strip()) < 3:
        raise ReconciliationError("Say which statement this comes from - its name, date and custodian. "
                                  "A figure with no source cannot be taken to a custodian.")
    if as_of > today:
        raise ReconciliationError(f"A statement cannot be dated in the future ({as_of.isoformat()}).")
    cleaned = _clean_positions(positions if positions is not None else [])
    cash = None if cash_balance in (None, "") else _d(cash_balance)
    if not cleaned and cash is None:
        raise ReconciliationError("There is nothing on this statement to compare - no positions and no cash.")
    cur = (currency or account.currency or "").upper()
    if cur != (account.currency or "").upper():
        raise ReconciliationError(f"The statement is in {cur} but the account is held in {account.currency}. "
                                  "Nothing was compared.")

    prior = (await session.execute(
        select(CustodyStatement).where(CustodyStatement.account_id == account.id,
                                       CustodyStatement.status == "current")
    )).scalars().all()
    if any(p.as_of > as_of for p in prior):
        newest = max(p.as_of for p in prior)
        raise ReconciliationError(f"A statement dated {newest.isoformat()} is already on file for this "
                                  f"account, which is newer than {as_of.isoformat()}.")
    for p in prior:
        p.status = "superseded"      # the same date, replaced; an older date, no longer the current one

    stmt = CustodyStatement(
        firm_id=firm_id, account_id=account.id, custodian=custodian or account.custodian, as_of=as_of,
        positions=[{"symbol": s, "quantity": float(q)} for s, q in cleaned.items()],
        cash_balance=cash, currency=cur, source_document=source_document.strip()[:255],
        imported_by=imported_by, status="current", result={},
    )
    session.add(stmt)
    await session.flush()
    summary = await reconcile(session, firm_id, stmt, today=today)
    hh = (await households_of(session, [account])).get(account.id)
    return {"statement_id": str(stmt.id), "account_id": str(account.id), "account": account.name,
            "household": hh[1] if hh else None, "as_of": as_of.isoformat(), **summary}


async def reconcile(session: AsyncSession, firm_id: uuid.UUID, stmt: CustodyStatement,
                    *, today: date | None = None) -> dict:
    today = today or utcnow().date()
    account = await session.get(Account, stmt.account_id)
    firm = await session.get(Firm, firm_id)
    cash_tol = cash_tolerance(firm)

    book = await book_as_of(session, account, stmt.as_of)
    instruments = (await session.execute(select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()
    by_symbol: dict[str, Instrument] = {}
    for i in instruments:
        by_symbol[i.symbol.upper()] = i
        if i.market_symbol:
            by_symbol.setdefault(i.market_symbol.upper(), i)
    names = {i.id: i for i in instruments}

    custodian: dict[uuid.UUID, Decimal] = {}
    unmapped: dict[str, Decimal] = {}
    for row in stmt.positions or []:
        sym, qty = str(row["symbol"]).upper(), _d(row["quantity"])
        inst = by_symbol.get(sym)
        if inst is None:
            unmapped[sym] = qty
        else:
            custodian[inst.id] = custodian.get(inst.id, Decimal(0)) + qty

    diffs = compare(book["positions"], custodian, skip=book["not_comparable"])
    for d in diffs:
        d["symbol"] = names[d["instrument_id"]].symbol if d["instrument_id"] in names else None
    for sym, qty in unmapped.items():
        diffs.append({"instrument_id": None, "kind": "unmapped_security", "symbol": sym,
                      "book": Decimal(0), "custodian": qty})

    cash_state = "no_cash_on_statement"
    if stmt.cash_balance is not None:
        if not book["cash_comparable"]:
            cash_state = "not_comparable"
        else:
            cash_state = "compared"
            if abs(book["cash"] - _d(stmt.cash_balance)) > cash_tol:
                diffs.append({"instrument_id": None, "kind": "cash", "symbol": None,
                              "book": book["cash"], "custodian": _d(stmt.cash_balance)})

    # What was already open on this account, by what it is about.
    existing = (await session.execute(
        select(ReconBreak).where(ReconBreak.account_id == account.id, ReconBreak.status.in_(ACTIVE_STATUSES))
    )).scalars().all()

    def identity(kind, iid, symbol):
        if kind == "cash":
            return "cash"
        return str(iid) if iid else f"sym:{symbol}"

    held = {identity(b.kind, b.instrument_id, b.symbol): b for b in existing}
    now = utcnow()
    opened = refreshed = 0
    seen: set[str] = set()
    for d in diffs:
        key = identity(d["kind"], d["instrument_id"], d["symbol"])
        seen.add(key)
        verdict = await explain(session, account=account, diff=d, as_of=stmt.as_of, cash_tol=cash_tol)
        diff_value = d["custodian"] - d["book"]
        row = held.get(key)
        if row is None:
            session.add(ReconBreak(
                firm_id=firm_id, account_id=account.id, statement_id=stmt.id, kind=d["kind"],
                instrument_id=d["instrument_id"], symbol=d["symbol"], book_value=d["book"],
                custodian_value=d["custodian"], difference=diff_value, status="open",
                cause=verdict["cause"], confidence=verdict["confidence"], next_step=verdict["next_step"],
                evidence=verdict["evidence"], first_seen=stmt.as_of, last_seen=stmt.as_of, times_seen=1,
                history=[{"at": now.isoformat(), "by": "reconciliation", "event": "opened",
                          "statement": str(stmt.id), "difference": float(diff_value)}]))
            opened += 1
            continue
        changed = abs(_d(row.difference) - diff_value) > QTY_TOLERANCE
        history = list(row.history or [])
        if str(row.status) in ("explained", "dismissed") and changed:
            history.append({"at": now.isoformat(), "by": "reconciliation", "event": "reopened",
                            "was": str(row.status), "difference": float(diff_value)})
            row.status = "open"
            row.resolved_by = row.resolved_at = row.resolution_note = None
        elif changed:
            history.append({"at": now.isoformat(), "by": "reconciliation", "event": "figures_changed",
                            "difference": float(diff_value)})
        # Checking the same statement again (the agent's recheck) is not seeing the break again.
        again = row.statement_id != stmt.id
        row.statement_id = stmt.id
        row.kind, row.book_value, row.custodian_value, row.difference = d["kind"], d["book"], d["custodian"], diff_value
        if verdict["cause"] != row.cause:
            history.append({"at": now.isoformat(), "by": "reconciliation", "event": "cause_changed",
                            "was": row.cause, "now": verdict["cause"]})
        row.cause, row.confidence, row.next_step, row.evidence = (
            verdict["cause"], verdict["confidence"], verdict["next_step"], verdict["evidence"])
        row.last_seen, row.history = stmt.as_of, history
        row.times_seen = int(row.times_seen or 1) + (1 if again else 0)
        refreshed += 1

    # A break the statement no longer shows has cleared - unless this statement could not speak to it.
    cleared = 0
    for key, row in held.items():
        if key in seen:
            continue
        if row.kind == "cash" and cash_state != "compared":
            continue
        if row.instrument_id in book["not_comparable"]:
            continue
        row.status = "cleared"
        row.resolved_by, row.resolved_at = "reconciliation", now
        row.resolution_note = None
        row.history = list(row.history or []) + [{
            "at": now.isoformat(), "by": "reconciliation", "event": "cleared", "statement": str(stmt.id)}]
        cleared += 1

    await session.flush()
    open_now = (await session.execute(
        select(ReconBreak).where(ReconBreak.account_id == account.id, ReconBreak.status.in_(("open", "escalated")))
    )).scalars().all()
    compared = len(set(book["positions"]) | set(custodian)) - len(book["not_comparable"])
    summary = {
        "positions_compared": max(compared, 0) + len(unmapped),
        "positions_matched": max(compared, 0) - len([d for d in diffs if d["instrument_id"] is not None]),
        "cash": cash_state,
        "not_comparable": sorted(names[i].symbol for i in book["not_comparable"] if i in names),
        "breaks": {"opened": opened, "refreshed": refreshed, "cleared": cleared, "open": len(open_now)},
    }
    stmt.result = summary
    await session.flush()
    return summary


# ── the agent's view: what has changed, and what needs a person ─────────────

# A timing difference should be gone within a settlement cycle. One still on a statement struck this
# many days after it first appeared did not clear, and is no longer timing.
TIMING_GRACE_DAYS = 5
TIMING_CAUSES = ("settlement_timing", "fee_timing")
# Why the agent puts a break in front of a person, and what it proposes doing with it.
REASONS = {
    "unexplained": "escalated",          # nothing on the book accounts for it: take it to the custodian
    "timing_not_cleared": "escalated",   # it was timing, but a later statement still shows it
    "entitlement_to_post": "explained",  # the cause is known; the entitlement needs posting here
    "unmapped_security": "explained",    # the cause is known; the instrument needs mapping here
}


async def recheck(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None) -> dict:
    """Compare every account's current statement with the book again. The book on the statement's
    date can change after the fact - an entitlement posted late, a correction - so a break may have
    cleared or changed cause without any new statement arriving. Nothing in the book is touched."""
    stmts = (await session.execute(
        select(CustodyStatement).where(CustodyStatement.firm_id == firm_id, CustodyStatement.status == "current")
    )).scalars().all()
    cleared = opened = 0
    for st in stmts:
        out = await reconcile(session, firm_id, st, today=today)
        cleared += out["breaks"]["cleared"]
        opened += out["breaks"]["opened"]
    return {"statements": len(stmts), "cleared": cleared, "opened": opened}


async def needs_attention(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None) -> dict:
    """The open breaks a person should decide on, each with the agent's proposed action, and the
    ones it is leaving to clear by themselves. Escalated, explained and set-aside breaks already have
    a decision and are not raised again."""
    today = today or utcnow().date()
    rows = (await session.execute(
        select(ReconBreak).where(ReconBreak.firm_id == firm_id, ReconBreak.status == "open")
    )).scalars().all()
    accounts = {a.id: a for a in (await session.execute(
        select(Account).where(Account.firm_id == firm_id))).scalars().all()}
    households = await households_of(session, list(accounts.values()))
    items, waiting = [], []
    for b in rows:
        lingered = (b.last_seen - b.first_seen).days
        if b.cause in TIMING_CAUSES:
            if lingered <= TIMING_GRACE_DAYS:
                waiting.append(str(b.id))
                continue
            reason = "timing_not_cleared"
        elif b.cause == "corporate_action_pending":
            reason = "entitlement_to_post"
        elif b.cause == "unmapped_security":
            reason = "unmapped_security"
        else:
            reason = "unexplained"
        acc, hh = accounts.get(b.account_id), households.get(b.account_id)
        items.append({
            "break_id": str(b.id), "account": acc.name if acc else None, "household": hh[1] if hh else None,
            "kind": b.kind, "symbol": b.symbol, "book": _f(b.book_value), "custodian": _f(b.custodian_value),
            "difference": _f(b.difference), "currency": acc.currency if acc else None, "cause": b.cause,
            "reason": reason, "action": REASONS[reason], "first_seen": b.first_seen.isoformat(),
            "days_open": (today - b.first_seen).days,
        })
    items.sort(key=lambda i: (i["action"] != "escalated", -abs(i["difference"])))
    return {"items": items, "waiting": waiting}


# ── reading it back ──────────────────────────────────────────────────────────

async def households_of(session: AsyncSession, accounts: list[Account]) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """The household each account belongs to, by way of its mandate's person or entity. An account
    with no mandate, or whose owner is in no household, is simply absent."""
    mandate_ids = {a.mandate_id for a in accounts if a.mandate_id}
    if not mandate_ids:
        return {}
    mandates = {m.id: m for m in (await session.execute(select(Mandate).where(Mandate.id.in_(mandate_ids)))).scalars().all()}
    person_ids = {m.person_id for m in mandates.values() if m.person_id}
    entity_ids = {m.entity_id for m in mandates.values() if m.entity_id}
    person_hh = {pid: hid for pid, hid in (await session.execute(
        select(Person.id, Person.household_id).where(Person.id.in_(person_ids)))).all()} if person_ids else {}
    entity_hh = {eid: hid for eid, hid in (await session.execute(
        select(LegalEntity.id, LegalEntity.household_id).where(LegalEntity.id.in_(entity_ids)))).all()} if entity_ids else {}
    hh_ids = {h for h in (*person_hh.values(), *entity_hh.values()) if h}
    names = {hid: name for hid, name in (await session.execute(
        select(Household.id, Household.name).where(Household.id.in_(hh_ids)))).all()} if hh_ids else {}
    out: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    for a in accounts:
        m = mandates.get(a.mandate_id) if a.mandate_id else None
        hid = (person_hh.get(m.person_id) if m and m.person_id else None) or (
            entity_hh.get(m.entity_id) if m and m.entity_id else None)
        if hid and hid in names:
            out[a.id] = (hid, names[hid])
    return out


def _break_row(b: ReconBreak, accounts: dict, instruments: dict, households: dict) -> dict:
    acc = accounts.get(b.account_id)
    hh = households.get(b.account_id)
    inst = instruments.get(b.instrument_id) if b.instrument_id else None
    return {
        "id": str(b.id), "account_id": str(b.account_id), "account": acc.name if acc else None,
        "household": hh[1] if hh else None, "household_id": str(hh[0]) if hh else None,
        "custodian": acc.custodian if acc else None, "kind": b.kind, "symbol": b.symbol or (inst.symbol if inst else None),
        "name": inst.name if inst else None, "currency": acc.currency if acc else None,
        "book": _f(b.book_value), "custodian_value": _f(b.custodian_value), "difference": _f(b.difference),
        "status": str(b.status), "cause": b.cause, "confidence": _f(b.confidence), "next_step": b.next_step,
        "evidence": b.evidence or {}, "first_seen": b.first_seen.isoformat(), "last_seen": b.last_seen.isoformat(),
        "times_seen": int(b.times_seen or 1), "resolved_by": b.resolved_by,
        "resolution_note": b.resolution_note,
    }


async def overview(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None) -> dict:
    """Every account's reconciliation state, and its breaks: the ones that need someone first, then
    explained ones, then differences that were set aside with a reason."""
    today = today or utcnow().date()
    accounts = (await session.execute(
        select(Account).where(Account.firm_id == firm_id).order_by(Account.name))).scalars().all()
    by_id = {a.id: a for a in accounts}
    households = await households_of(session, accounts)
    instruments = {i.id: i for i in (await session.execute(
        select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()}
    statements = {s.account_id: s for s in (await session.execute(
        select(CustodyStatement).where(CustodyStatement.firm_id == firm_id, CustodyStatement.status == "current")
    )).scalars().all()}
    breaks = (await session.execute(
        select(ReconBreak).where(ReconBreak.firm_id == firm_id, ReconBreak.status.in_(ACTIVE_STATUSES))
    )).scalars().all()
    by_account: dict[uuid.UUID, list[ReconBreak]] = {}
    for b in breaks:
        by_account.setdefault(b.account_id, []).append(b)

    rows, counts = [], {"no_statement": 0, "stale_statement": 0, "breaks": 0, "reconciled": 0}
    for a in accounts:
        st, mine = statements.get(a.id), by_account.get(a.id, [])
        age = (today - st.as_of).days if st else None
        needs = [b for b in mine if str(b.status) in ("open", "escalated")]
        state = ("no_statement" if st is None else "stale_statement" if age > STATEMENT_FRESH_DAYS
                 else "breaks" if needs else "reconciled")
        counts[state] += 1
        rows.append({
            "account_id": str(a.id), "account": a.name, "account_number": a.account_number,
            "household": households[a.id][1] if a.id in households else None,
            "household_id": str(households[a.id][0]) if a.id in households else None,
            "custodian": a.custodian, "currency": a.currency, "state": state,
            "statement_as_of": st.as_of.isoformat() if st else None, "statement_age_days": age,
            "statement_source": st.source_document if st else None,
            "open_breaks": len(needs),
            "explained_breaks": sum(1 for b in mine if str(b.status) == "explained"),
            # A difference someone set aside is still a difference: the account stays reconciled
            # only in the sense that nobody is chasing it, and it is counted here so it is seen.
            "set_aside_breaks": sum(1 for b in mine if str(b.status) == "dismissed"),
            "cash": (st.result or {}).get("cash") if st else None,
            "not_comparable": (st.result or {}).get("not_comparable", []) if st else [],
        })
    rows.sort(key=lambda r: ((r["household"] or "~").lower(), (r["account"] or "").lower()))
    rank = {"escalated": 0, "open": 1, "explained": 2, "dismissed": 3}
    shown = sorted(breaks, key=lambda b: (rank.get(str(b.status), 3), -abs(_f(b.difference))))
    by_cause: dict[str, int] = {}
    for b in breaks:
        if str(b.status) in ("open", "escalated"):
            by_cause[b.cause] = by_cause.get(b.cause, 0) + 1
    return {
        "accounts": rows,
        "breaks": [_break_row(b, by_id, instruments, households) for b in shown[:200]],
        "summary": {"accounts": len(accounts), **counts,
                    "open_breaks": sum(1 for b in breaks if str(b.status) in ("open", "escalated")),
                    "explained_breaks": sum(1 for b in breaks if str(b.status) == "explained"),
                    "set_aside_breaks": sum(1 for b in breaks if str(b.status) == "dismissed"),
                    "by_cause": by_cause, "fresh_within_days": STATEMENT_FRESH_DAYS},
    }


async def resolve_break(session: AsyncSession, firm_id: uuid.UUID, break_id: uuid.UUID, *,
                        action: str, note: str | None, by: str) -> dict:
    """Move a break on. The row is locked and its status re-read, so two people acting on the
    same break cannot both succeed; the second is told where it already stands."""
    if action not in ACTIONS:
        raise ReconciliationError(f"Unknown action '{action}'. Choose one of: {', '.join(ACTIONS)}.")
    note = (note or "").strip() or None
    if action in NOTE_REQUIRED and not note:
        raise ReconciliationError("Say why - a break set aside or marked fixed needs a reason on the record.")
    row = (await session.execute(
        select(ReconBreak).where(ReconBreak.id == break_id, ReconBreak.firm_id == firm_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if row is None:
        raise ReconciliationError("That break was not found.")
    if str(row.status) not in ACTIONS[action]:
        raise ReconciliationError(f"This break is already {row.status}, so it cannot be marked {action}.")
    now = utcnow()
    was = str(row.status)
    row.status = action
    row.resolved_by, row.resolved_at, row.resolution_note = by, now, (note[:512] if note else None)
    row.history = list(row.history or []) + [{"at": now.isoformat(), "by": by, "event": action, "was": was,
                                              "note": note}]
    await session.flush()
    return {"id": str(row.id), "status": action, "was": was, "resolved_by": by}
