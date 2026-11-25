"""A Portfolio Manager's client, measured against what the Regulations and the Master Circular state (India row 8).

The firm holds a PMS licence (`licences.py`), and each client's mandate sits on one of its tracks: discretionary,
non-discretionary or advisory. What the licence cannot say is what this client agreed. That is the agreement a person
recorded from the signed document (`models/pms.py`): the strategy, whether it is discretionary, the opening corpus,
whether the client's securities sit in their own demat, the benchmarks named, and the fee terms as stated. Added and
never edited, latest stands. Nothing is worked out for the client and no fee is changed.

What is checked, each from `law_figures` with its source: the opening corpus against the minimum ticket; the agreement's
kind against the mandate's track; segregation; at most three benchmarks; operating expenses against the cap; a
performance fee on the client's own high-water mark; exit loads against their caps. A check with no fact is "not
assessed" and holds nothing: a missing agreement does not mean none was signed. Only a recorded breach holds an approval
(`refusal`), the way a missing agreement holds an Investment Adviser's.

The billing preview shows what the agreement's performance terms would give for the period beside the bill, as an
estimate on securities-only, gross, monthly-linked returns. It is not added to the bill by itself: a person confirms it
(`record_fee_line`, behind a Confirm card), and the draft invoice then carries it as a separate line, outside the invoice's
amount and so outside the fee cap, and nothing debits it here (B-261). It is not the manager's own computation on unit
values, which decides. The client's report carries the same returns with their basis and says it
is not publishable: a return may be stated to a client only when APMI's TWRR or a verified figure backs it (research
report R9), which this build does not supply. No benchmark value is fetched here.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, licences, markets, performance
from app.core import decision_rights
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.billing import Invoice
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.india_polish import PmsFeeLine
from app.models.pms import PmsAgreement
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.PORTFOLIO_TEAM, UserRole.OPERATIONS, UserRole.ADVISER})
PM_MC = "SEBI Master Circular for Portfolio Managers (Jul 2025)"
KINDS = {"discretionary": "Discretionary", "non_discretionary": "Non-discretionary", "advisory": "Advisory"}
TRACK_OF = {"discretionary": "pms_discretionary", "non_discretionary": "pms_non_discretionary", "advisory": "pms_advisory"}
KIND_OF = {v: k for k, v in TRACK_OF.items()}
FEE_TYPES = {"fixed": "Fixed", "performance": "Performance, above a hurdle", "hybrid": "Fixed and performance"}
PASS, FAIL, NA = "pass", "fail", "not_assessed"
LAW_KEYS = ["in_pms_min_ticket", "in_pms_opex_cap", "in_pms_benchmarks", "in_pms_exit_load_y1", "in_pms_exit_load_y2", "in_pms_exit_load_y3"]
NOT_PUBLISHABLE = ("Securities only, gross of fees, monthly-linked: not APMI's TWRR. A return is stated to a client only when "
                   "a verified figure backs it (research report R9).")


class PmsError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN" and any(l.key in TRACK_OF.values() for l in licences.held(firm))


def _check(key: str, label: str, state: str, detail: str, source: str) -> dict:
    return {"key": key, "label": label, "state": state, "detail": detail, "source": source}


async def standing(session: AsyncSession, firm_id: uuid.UUID, mandate_id: uuid.UUID) -> PmsAgreement | None:
    return (await session.execute(select(PmsAgreement).where(
        PmsAgreement.firm_id == firm_id, PmsAgreement.mandate_id == mandate_id
    ).order_by(PmsAgreement.created_at.desc()))).scalars().first()


async def pms_mandates(session: AsyncSession, firm: Firm) -> list[Mandate]:
    if not applies(firm):
        return []
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True))
                                      .order_by(Mandate.name))).scalars().all()
    return [m for m in mandates if (t := licences.track_for(firm, m)) is not None and t.key in KIND_OF]


async def _owner(session: AsyncSession, mandate: Mandate):
    owner = await session.get(Person, mandate.person_id) if mandate.person_id else None
    return owner or (await session.get(LegalEntity, mandate.entity_id) if mandate.entity_id else None)


async def _accounts(session: AsyncSession, mandate_id: uuid.UUID) -> list[Account]:
    return list((await session.execute(select(Account).where(Account.mandate_id == mandate_id).order_by(Account.name))).scalars().all())


def _pct(x) -> str:
    return f"{float(x) * 100:.2f}%"


def checks(firm: Firm, mandate: Mandate, agreement: PmsAgreement | None, accounts: list[Account], value: float, today: date) -> list[dict]:
    """The agreement against the track and the Circular's figures."""
    track = licences.track_for(firm, mandate)
    if agreement is None:
        return [_check("agreement", "Agreement on file", NA, "No agreement recorded: not assessed. That does not mean none was signed.",
                       "PM Regulations 2020")]
    out = [_check("agreement", "Agreement on file", PASS, f"{KINDS[agreement.kind]}, {agreement.strategy}, signed {agreement.signed_on.isoformat()}",
                  "PM Regulations 2020")]
    expected = TRACK_OF[agreement.kind]
    out.append(_check("kind", "Agreement matches the mandate's track", PASS if track and track.key == expected else FAIL,
                      f"agreement: {KINDS[agreement.kind].lower()}; mandate sits on {track.name if track else 'no track'}", "PM Regulations 2020"))
    minimum = law_figures.value("in_pms_min_ticket", today)
    if agreement.initial_corpus is None:
        out.append(_check("ticket", "Minimum ticket", NA, "No opening corpus recorded", "in_pms_min_ticket"))
    else:
        corpus = float(agreement.initial_corpus)
        out.append(_check("ticket", "Minimum ticket", PASS if corpus >= minimum else FAIL,
                          f"opened with {markets.money(corpus, 'INR')}; the minimum is {markets.money(minimum, 'INR')}"
                          f"{'; today it holds ' + markets.money(value, 'INR') if value else ''}", "in_pms_min_ticket"))
    if agreement.kind == "advisory":
        out.append(_check("segregation", "Client's securities in their own demat", PASS, "Advisory: no custody, no execution", PM_MC))
    elif agreement.segregated is None:
        out.append(_check("segregation", "Client's securities in their own demat", NA, "Not recorded", PM_MC))
    elif not agreement.segregated:
        out.append(_check("segregation", "Client's securities in their own demat", FAIL, "The agreement says the securities are pooled", PM_MC))
    elif not accounts:
        out.append(_check("segregation", "Client's securities in their own demat", NA, "Segregated by the agreement; no account on the mandate here", PM_MC))
    else:
        out.append(_check("segregation", "Client's securities in their own demat", PASS,
                          f"{len(accounts)} account(s) on this mandate alone" + ("" if all(a.custodian for a in accounts) else "; a custodian is not recorded"), PM_MC))
    cap_n = int(law_figures.value("in_pms_benchmarks", today))
    if not agreement.benchmarks:
        out.append(_check("benchmarks", "Benchmarks named", NA, "None recorded", "in_pms_benchmarks"))
    else:
        out.append(_check("benchmarks", "Benchmarks named", PASS if len(agreement.benchmarks) <= cap_n else FAIL,
                          f"{len(agreement.benchmarks)} named ({', '.join(agreement.benchmarks)}); at most {cap_n}", "in_pms_benchmarks"))
    cap = law_figures.value("in_pms_opex_cap", today)
    if agreement.operating_expense_rate is None:
        out.append(_check("opex", "Operating expenses within the cap", NA, "No rate stated", "in_pms_opex_cap"))
    else:
        rate = float(agreement.operating_expense_rate)
        out.append(_check("opex", "Operating expenses within the cap", PASS if rate <= cap else FAIL,
                          f"{_pct(rate)} a year excluding brokerage; the cap is {_pct(cap)} of average daily AUM", "in_pms_opex_cap"))
    if agreement.fee_type in ("performance", "hybrid"):
        if agreement.high_water_mark is None or agreement.hurdle_rate is None or agreement.performance_rate is None:
            out.append(_check("performance_fee", "Performance fee has its rate, hurdle and high-water mark", NA,
                              "A rate, a hurdle or the high-water mark is not recorded", PM_MC))
        else:
            out.append(_check("performance_fee", "Performance fee has its rate, hurdle and high-water mark",
                              PASS if agreement.high_water_mark else FAIL,
                              f"{_pct(agreement.performance_rate)} above a {_pct(agreement.hurdle_rate)} hurdle"
                              + ("" if agreement.high_water_mark else "; charged without the client's own high-water mark"), PM_MC))
    if agreement.exit_load:
        caps = [law_figures.value(k, today) for k in ("in_pms_exit_load_y1", "in_pms_exit_load_y2", "in_pms_exit_load_y3")]
        over = [f"year {i + 1}: {_pct(v)} over {_pct(caps[i])}" for i, v in enumerate(agreement.exit_load[:3]) if float(v) > caps[i]]
        out.append(_check("exit_load", "Exit load within the caps", FAIL if over else PASS,
                          "; ".join(over) if over else "Stated: " + ", ".join(_pct(v) for v in agreement.exit_load[:3]), "in_pms_exit_load_y1"))
    return out


async def _value(session: AsyncSession, accounts: list[Account]) -> float:
    from app.models.portfolio import Holding

    ids = [a.id for a in accounts]
    if not ids:
        return 0.0
    return float(sum(float(h.market_value or 0) for h in (await session.execute(select(Holding).where(Holding.account_id.in_(ids)))).scalars().all()))


async def status(session: AsyncSession, firm: Firm, mandate: Mandate) -> dict:
    today = firm_time.today(firm)
    agreement = await standing(session, firm.id, mandate.id)
    accounts = await _accounts(session, mandate.id)
    value = await _value(session, accounts)
    cs = checks(firm, mandate, agreement, accounts, value, today)
    owner = await _owner(session, mandate)
    household = await session.get(Household, owner.household_id) if owner is not None and owner.household_id else None
    track = licences.track_for(firm, mandate)
    return {"mandate_id": str(mandate.id), "mandate": mandate.name, "client": getattr(owner, "full_name", None) or getattr(owner, "name", None),
            "household": household.name if household else None, "track": track.key if track else None, "track_name": track.name if track else None,
            "agreement": ({"strategy": agreement.strategy, "kind": agreement.kind, "kind_label": KINDS[agreement.kind],
                           "signed_on": agreement.signed_on.isoformat(),
                           "initial_corpus": float(agreement.initial_corpus) if agreement.initial_corpus is not None else None,
                           "segregated": agreement.segregated, "benchmarks": list(agreement.benchmarks or []),
                           "fee_type": agreement.fee_type, "fee_label": FEE_TYPES.get(agreement.fee_type or ""),
                           "fixed_rate": float(agreement.fixed_rate) if agreement.fixed_rate is not None else None,
                           "performance_rate": float(agreement.performance_rate) if agreement.performance_rate is not None else None,
                           "hurdle_rate": float(agreement.hurdle_rate) if agreement.hurdle_rate is not None else None,
                           "high_water_mark": agreement.high_water_mark,
                           "operating_expense_rate": float(agreement.operating_expense_rate) if agreement.operating_expense_rate is not None else None,
                           "document": agreement.document, "recorded_by": agreement.recorded_by} if agreement else None),
            "value": round(value, 2), "checks": cs, "held": [c["label"] for c in cs if c["state"] == FAIL],
            "not_assessed": [c["label"] for c in cs if c["state"] == NA], "law": law_figures.cited(LAW_KEYS, today)}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    if not applies(firm):
        return {"applies": False, "clients": []}
    clients = [await status(session, firm, m) for m in await pms_mandates(session, firm)]
    return {"applies": True, "as_of": firm_time.today(firm).isoformat(), "clients": clients,
            "held": sum(1 for c in clients if c["held"]), "without_agreement": sum(1 for c in clients if c["agreement"] is None),
            "note": "What the agreement states, against what the Regulations and the Circular state. Not on file is not assessed, "
                    "and it does not mean the firm is compliant."}


async def prepare(session: AsyncSession, firm: Firm, *, mandate_id, strategy: str | None, kind: str | None, signed_on, document: str | None,
                  initial_corpus=None, segregated=None, benchmarks=None, fee_type=None, fixed_rate=None, performance_rate=None,
                  hurdle_rate=None, high_water_mark=None, operating_expense_rate=None, exit_load=None) -> dict:
    if not applies(firm):
        raise PmsError("A PMS agreement is recorded for a firm holding a Portfolio Manager licence.")
    try:
        mid = mandate_id if isinstance(mandate_id, uuid.UUID) else uuid.UUID(str(mandate_id))
    except ValueError:
        raise PmsError("Give the mandate's id, from read_pms.")
    mandate = await session.get(Mandate, mid)
    if mandate is None or mandate.firm_id != firm.id:
        raise PmsError("That mandate is not this firm's.")
    track = licences.track_for(firm, mandate)
    if track is None or track.key not in KIND_OF:
        raise PmsError("That mandate is not on a Portfolio Manager's track.")
    today = firm_time.today(firm)
    k = (kind or "").strip().lower()
    if k not in KINDS:
        raise PmsError(f"A PMS agreement is one of {', '.join(KINDS)}.")
    name = " ".join((strategy or "").split())
    if len(name) < 3:
        raise PmsError("Name the strategy: the investment approach in the agreement.")
    try:
        signed = signed_on if isinstance(signed_on, date) else date.fromisoformat(str(signed_on)[:10])
    except ValueError:
        raise PmsError("The day it was signed is a date, YYYY-MM-DD.")
    if signed > today:
        raise PmsError("An agreement cannot be signed in the future.")
    if len((document or "").strip()) < 4:
        raise PmsError("Say where the signed agreement is on file.")

    def rate(v, label, high=1.0):
        if v in (None, ""):
            return None
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise PmsError(f"{label} is a share written as a fraction: 0.02 for 2%.")
        if x < 0 or x > high:
            raise PmsError(f"{label} is between 0 and {high:g} as a fraction.")
        return x

    ft = (fee_type or "").strip().lower() or None
    if ft and ft not in FEE_TYPES:
        raise PmsError(f"A fee type is one of {', '.join(FEE_TYPES)}.")
    corpus = None
    if initial_corpus not in (None, ""):
        try:
            corpus = float(initial_corpus)
        except (TypeError, ValueError):
            raise PmsError("The opening corpus is an amount in rupees.")
        if corpus <= 0:
            raise PmsError("The opening corpus is more than nothing.")
    marks = [str(b).strip() for b in (benchmarks.replace(";", ",").split(",") if isinstance(benchmarks, str) else benchmarks or []) if str(b).strip()]
    loads = []
    if exit_load not in (None, "", []):
        raw = exit_load.replace(";", ",").split(",") if isinstance(exit_load, str) else exit_load
        loads = [rate(v, "An exit load") for v in raw][:3]
    seg = None if segregated in (None, "") else (segregated if isinstance(segregated, bool) else str(segregated).strip().lower() in ("true", "yes", "y", "1"))
    hwm = None if high_water_mark in (None, "") else (high_water_mark if isinstance(high_water_mark, bool) else str(high_water_mark).strip().lower() in ("true", "yes", "y", "1"))
    perf = rate(performance_rate, "The performance rate")
    if ft in ("performance", "hybrid") and perf is None:
        raise PmsError("A performance fee needs its rate: the share of the profit above the hurdle.")
    before = await standing(session, firm.id, mid)
    fields = {"strategy": name, "kind": k, "signed_on": signed, "initial_corpus": corpus, "segregated": seg, "benchmarks": marks,
              "fee_type": ft, "fixed_rate": rate(fixed_rate, "The fixed rate"), "performance_rate": perf, "hurdle_rate": rate(hurdle_rate, "The hurdle"),
              "high_water_mark": hwm, "operating_expense_rate": rate(operating_expense_rate, "The operating expense rate"),
              "exit_load": loads, "document": document.strip()}
    if before is not None and all(getattr(before, f) == v or (v is not None and f in ("initial_corpus", "fixed_rate", "performance_rate", "hurdle_rate", "operating_expense_rate")
                                                           and getattr(before, f) is not None and float(getattr(before, f)) == v) for f, v in fields.items() if f != "document"):
        raise PmsError("Nothing changes: this is the agreement already on file.")
    return {"mandate": mandate, "track": track, "fields": fields, "before": before}


async def record(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare(session, firm, **terms)
    row = PmsAgreement(firm_id=firm.id, mandate_id=p["mandate"].id, recorded_by=by, note=(note or "").strip() or None, created_at=utcnow(), **p["fields"])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="pms_agreement_recorded", actor=by, content={
        "mandate_id": str(p["mandate"].id), "kind": row.kind, "strategy": row.strategy, "signed_on": row.signed_on.isoformat(),
        "document": row.document})
    return await status(session, firm, p["mandate"])


async def refusal(session: AsyncSession, firm: Firm, rec) -> str | None:
    """Why this proposal may not be approved yet, or None. Only a book-moving proposal on a PMS mandate whose recorded
    agreement breaks a stated rule is held; a missing agreement is not assessed and holds nothing."""
    if not applies(firm) or not decision_rights.moves_the_book(rec.agent_key):
        return None
    if rec.subject_type != "mandate" or not rec.subject_id:
        return None
    mandate = await session.get(Mandate, rec.subject_id)
    track = licences.track_for(firm, mandate) if mandate else None
    if track is None or track.key not in KIND_OF:
        return None
    s = await status(session, firm, mandate)
    if not s["held"]:
        return None
    return (f"{s['client'] or s['mandate']}'s PMS agreement breaks what the Regulations state: {'; '.join(s['held']).lower()}. "
            f"Correct the record from the signed document, or the agreement, before approving ({PM_MC}).")


# ── Fees: what the agreement's terms would give, beside the bill ─────────────────────────────────────────────

def performance_fee(agreement: PmsAgreement, periods: list[dict], start: date, end: date) -> dict:
    """The performance fee the agreement's terms give for [start, end] on the monthly-linked returns, charged only above
    the client's own high-water mark and the hurdle for the period. An estimate; None where there is no return series."""
    series = [(date.fromisoformat(p["start"]), date.fromisoformat(p["end"]), float(p["return"]), float(p["start_value"])) for p in periods]
    if not series:
        return {"estimate": None, "reason": "No monthly returns on file for this mandate."}
    index = 1.0
    marks: list[tuple[date, float]] = [(series[0][0], 1.0)]
    for a, b, r, _ in series:
        index *= 1.0 + r
        marks.append((b, index))
    before = [i for d, i in marks if d <= start]
    idx_start = before[-1] if before else 1.0
    covered = [(a, b, r, v) for a, b, r, v in series if a >= start and b <= end]
    if not covered:
        return {"estimate": None, "reason": "The period has no monthly return yet."}
    idx_end = idx_start
    for _, _, r, _ in covered:
        idx_end *= 1.0 + r
    base_value = covered[0][3]
    days = max((end - start).days, 1)
    hurdle = float(agreement.hurdle_rate or 0) * days / 365.0
    peak = max([idx_start] + [i for d, i in marks if d <= start]) if agreement.high_water_mark else idx_start
    threshold = max(peak, idx_start * (1.0 + hurdle))
    excess = max(0.0, (idx_end - threshold) / idx_start)
    fee = round(float(agreement.performance_rate or 0) * excess * base_value, 2)
    return {"estimate": fee, "period_return": round(idx_end / idx_start - 1.0, 4), "hurdle_for_period": round(hurdle, 4),
            "high_water_mark_index": round(peak, 4), "end_index": round(idx_end, 4), "excess_return": round(excess, 4),
            "months": len(covered)}


async def apply(session: AsyncSession, firm: Firm, entries: list[dict], as_of: date) -> None:
    """Adds what a PM client's agreement says to its bill in the preview, in place. The fixed fee is the schedule's, so
    the agreement's own rate is set beside it, and a performance fee is estimated and shown, never added to the amount."""
    if not applies(firm):
        return
    pms = {str(m.id): m for m in await pms_mandates(session, firm)}
    for e in entries:
        mandate = pms.get(e["mandate_id"])
        if mandate is None:
            continue
        agreement = await standing(session, firm.id, mandate.id)
        accounts = await _accounts(session, mandate.id)
        cs = checks(firm, mandate, agreement, accounts, e.get("billable_value") or 0.0, as_of)
        view = {"kind": agreement.kind if agreement else None, "fee_type": agreement.fee_type if agreement else None,
                "fixed_rate": float(agreement.fixed_rate) if agreement and agreement.fixed_rate is not None else None,
                "performance": None, "law": law_figures.cited(LAW_KEYS[:2], as_of)}
        if agreement is None:
            e["checks"].append({"code": "no_pms_agreement", "severity": "warn",
                                "detail": "No PMS agreement is recorded for this client, so its fee terms are not checked."})
        else:
            for c in cs:
                if c["state"] == FAIL and c["key"] in ("opex", "performance_fee", "exit_load", "benchmarks"):
                    e["checks"].append({"code": f"pms_{c['key']}", "severity": "block", "detail": f"{c['label']}: {c['detail']} ({PM_MC})."})
            billed_rate = e.get("effective_bps")
            if agreement.fixed_rate is not None and billed_rate is not None and abs(billed_rate / 10_000 - float(agreement.fixed_rate)) > 0.00005:
                e["checks"].append({"code": "pms_fixed_rate_differs", "severity": "warn",
                                    "detail": f"The bill's rate is {billed_rate / 100:.2f}% and the agreement states {_pct(agreement.fixed_rate)}."})
            if agreement.fee_type in ("performance", "hybrid"):
                perf = await performance.performance_for_accounts(session, [a.id for a in accounts], firm_id=firm.id)
                view["performance"] = {**performance_fee(agreement, perf["periods"], date.fromisoformat(e["period_start"]),
                                                         date.fromisoformat(e["period_end"])),
                                       "rate": float(agreement.performance_rate), "hurdle_rate": float(agreement.hurdle_rate or 0),
                                       "high_water_mark": bool(agreement.high_water_mark), "added_to_bill": False,
                                       "basis": "Securities only, gross, monthly-linked; the manager's own computation on unit values decides"}
                line = await fee_line(session, firm.id, mandate.id, date.fromisoformat(e["period_start"]), date.fromisoformat(e["period_end"]))
                # A confirmed estimate is its own line on the bill. It is never added to the invoice's amount, so the fee cap does not read it.
                view["line"] = _line_view(line)
        e["pms_fee"] = view
        e["blocked"] = any(c["severity"] == "block" for c in e["checks"])


async def fee_line(session: AsyncSession, firm_id: uuid.UUID, mandate_id: uuid.UUID, start: date, end: date) -> PmsFeeLine | None:
    return (await session.execute(select(PmsFeeLine).where(
        PmsFeeLine.firm_id == firm_id, PmsFeeLine.mandate_id == mandate_id, PmsFeeLine.period_start == start,
        PmsFeeLine.period_end == end).order_by(PmsFeeLine.created_at.desc()))).scalars().first()


def _line_view(line: PmsFeeLine | None) -> dict | None:
    if line is None:
        return None
    return {"amount": float(line.amount), "currency": line.currency, "confirmed_by": line.confirmed_by,
            "confirmed_on": line.created_at.date().isoformat() if line.created_at else None, "working": line.working,
            "in_fee_cap": False, "debited": False,
            "note": "A separate line: the invoice's amount, and so the fee cap, is the fixed fee alone. Nothing debits this line here."}


async def prepare_fee_line(session: AsyncSession, firm: Firm, *, mandate_id, period_start=None) -> dict:
    """The performance fee the billing preview estimates for one PMS client's next bill, as it would stand on a line of its own.
    Writes nothing. A person confirms it; nothing carries an estimate onto a bill by itself."""
    from app.aurea_core import billing

    if not applies(firm):
        raise PmsError("A performance fee line is carried for a firm holding a Portfolio Manager licence.")
    try:
        mid = mandate_id if isinstance(mandate_id, uuid.UUID) else uuid.UUID(str(mandate_id))
    except ValueError:
        raise PmsError("Give the client's mandate id, from read_pms.")
    mandate = next((m for m in await pms_mandates(session, firm) if m.id == mid), None)
    if mandate is None:
        raise PmsError("That is not a PMS client of this firm. read_pms lists them.")
    pv = await billing.preview(session, firm.id, as_of=firm_time.today(firm))
    entries = [e for e in pv["arrangements"] if e["mandate_id"] == str(mid)]
    if period_start not in (None, ""):
        entries = [e for e in entries if e["period_start"] == str(period_start)[:10]]
    if not entries:
        raise PmsError(f"{mandate.name} has no bill to draft for the period: there is no fee arrangement running for it.")
    e = entries[-1]
    perf = ((e.get("pms_fee") or {}).get("performance")) or None
    if perf is None:
        raise PmsError(f"{mandate.name}'s agreement has no performance fee (or no agreement is recorded), so there is nothing to carry.")
    if perf.get("estimate") is None:
        raise PmsError(f"The performance fee is not assessed: {(perf.get('reason') or 'no return series').rstrip('.')}.")
    if perf["estimate"] <= 0:
        raise PmsError(f"The agreement's terms give no performance fee for {e['period_start']} to {e['period_end']}: the return does not clear the "
                       "hurdle and the high-water mark, so there is no line to confirm.")
    start, end = date.fromisoformat(e["period_start"]), date.fromisoformat(e["period_end"])
    if await fee_line(session, firm.id, mid, start, end) is not None:
        raise PmsError(f"A performance fee line for {mandate.name}, {e['period_start']} to {e['period_end']}, is already confirmed.")
    billed = (await session.execute(select(Invoice.id).where(
        Invoice.firm_id == firm.id, Invoice.mandate_id == mid, Invoice.period_start == start, Invoice.kind == "invoice",
        Invoice.status.in_(("issued", "debited"))).limit(1))).first()
    if billed:
        raise PmsError("That period's bill is already issued, and an issued bill is never edited: credit it and bill again to add the line.")
    return {"mandate": mandate, "period_start": start, "period_end": end, "amount": float(perf["estimate"]), "currency": e["currency"] or firm.base_currency,
            "working": {k: v for k, v in perf.items() if k not in ("added_to_bill",)}, "fixed_fee": e["amount"]}


async def record_fee_line(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare_fee_line(session, firm, **terms)
    row = PmsFeeLine(firm_id=firm.id, mandate_id=p["mandate"].id, period_start=p["period_start"], period_end=p["period_end"], amount=p["amount"],
                     currency=p["currency"], working=p["working"], confirmed_by=by, note=(note or "").strip() or None, created_at=utcnow())
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="pms_performance_fee_line_confirmed", actor=by, content={
        "mandate_id": str(p["mandate"].id), "period_start": p["period_start"].isoformat(), "period_end": p["period_end"].isoformat(),
        "amount": p["amount"], "currency": p["currency"]})
    return {"applies": True, "line": {"mandate": p["mandate"].name, "period_start": p["period_start"].isoformat(), "period_end": p["period_end"].isoformat(),
                                       **_line_view(row)}}


# ── The client's report ──────────────────────────────────────────────────────────────────────────────────────

def last_quarter(today: date) -> tuple[date, date, str]:
    """The last completed quarter on the financial year's calendar (April to June is the first)."""
    q_start_month = ((today.month - 1) // 3) * 3 + 1
    this_start = date(today.year, q_start_month, 1)
    end = this_start - timedelta(days=1)
    start = date(end.year, ((end.month - 1) // 3) * 3 + 1, 1)
    fy = f"{start.year}-{(start.year + 1) % 100:02d}" if start.month >= 4 else f"{start.year - 1}-{start.year % 100:02d}"
    q = {4: 1, 7: 2, 10: 3, 1: 4}[start.month]
    return start, end, f"Q{q} {fy}"


async def client_report(session: AsyncSession, firm: Firm, mandate_id) -> dict:
    """The quarter's report for one PMS client, from what is on file. A draft for the manager, not publishable."""
    try:
        mid = mandate_id if isinstance(mandate_id, uuid.UUID) else uuid.UUID(str(mandate_id))
    except ValueError:
        raise PmsError("Give the mandate's id, from read_pms.")
    mandate = await session.get(Mandate, mid)
    if mandate is None or mandate.firm_id != firm.id or mandate not in await pms_mandates(session, firm):
        raise PmsError("That is not a PMS client of this firm.")
    today = firm_time.today(firm)
    start, end, label = last_quarter(today)
    s = await status(session, firm, mandate)
    accounts = await _accounts(session, mandate.id)
    perf = await performance.performance_for_accounts(session, [a.id for a in accounts], firm_id=firm.id)
    months = [p for p in perf["periods"] if date.fromisoformat(p["start"]) >= start and date.fromisoformat(p["end"]) <= end]
    twr = None
    if months:
        twr = 1.0
        for p in months:
            twr *= 1.0 + float(p["return"])
        twr -= 1.0
    fees = float(sum(float(i.amount) for i in (await session.execute(select(Invoice).where(
        Invoice.firm_id == firm.id, Invoice.mandate_id == mandate.id, Invoice.status.in_(("issued", "debited", "credited")),
        Invoice.period_end >= start, Invoice.period_end <= end))).scalars().all()))
    agreement = s["agreement"]
    return {"mandate_id": str(mandate.id), "client": s["client"], "strategy": agreement["strategy"] if agreement else None,
            "kind": agreement["kind_label"] if agreement else None, "quarter": label, "start": start.isoformat(), "end": end.isoformat(),
            "opening_value": months[0]["start_value"] if months else None, "closing_value": months[-1]["end_value"] if months else None,
            "net_flow": round(sum(p["net_flow"] for p in months), 2) if months else None,
            "income": round(sum(p["income"] for p in months), 2) if months else None,
            "return": round(twr, 4) if twr is not None else None, "months": len(months),
            "benchmarks": [{"name": b, "return": None, "note": "Not on file: APMI's benchmark values are not fetched here"}
                           for b in (agreement["benchmarks"] if agreement else [])],
            "fees_billed": round(fees, 2), "basis": "monthly-linked, securities only, gross of fees",
            "publishable": False, "not_publishable_because": NOT_PUBLISHABLE,
            "disclosures": perf["disclosures"],
            "not_assessed": ["No monthly return in the quarter on file"] if twr is None else []}
