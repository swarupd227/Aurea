"""The file of a transfer into the firm, and the rollover analysis that goes with a retirement plan (L200-1 §6.1, §6.2).

A transfer is more than its four statuses. Its file holds what happened, each entry from a document: when it was
submitted and to whom, a reject and its reason, the delivering firm's asset memo (what moves in kind, what is sold,
what stays behind), a debit balance that must be cleared first, the day the assets arrived, the day cost basis arrived,
the residual swept in later, and an annuity moved by a 1035 exchange instead. From those it says where the transfer
stands, whose move it is, and what is late. The delivering firm owes the cost basis within fifteen days of settlement
(Treas. Reg. 1.6045A-1); a file past that says so. Nothing here moves holdings or cash: the custodian does, and
reconciliation reads what it did.

A private fund interest moves by assignment, not ACAT: requested, consented to by the general partner, then
re-registered, and a capital call due between the request and the re-registration is flagged until someone records
which side funds it. Restricted stock is read against Rule 144: the holding period from the acquisition date (six
months for an issuer that has reported for 90 days, a year otherwise), the affiliate's sale conditions, and a legend
that comes off only on the issuer's counsel's opinion.

A rollover from an employer plan is laid out, not advised on. From the plan statement as a person recorded it, the
analysis compares the costs, and sets out direct against indirect, net unrealized appreciation on employer stock, an
outstanding plan loan, the required distribution that must come out first, and the age-55 exception that an IRA does
not keep. A point with no fact on file is "not assessed", never filled in. The adviser's reason is theirs to record.
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.models.onboarding import OnboardingCase, OnboardingParty, TransferRequest
from app.models.tenant import Firm
from app.models.transfer_file import TransferFileEntry
from app.provenance import ledger

KINDS = {
    "submitted": "Submitted to the delivering firm", "rejected": "Rejected", "resubmitted": "Resubmitted",
    "asset_memo": "Asset memo", "debit_balance": "Debit balance", "debit_cleared": "Debit balance cleared",
    "received": "Assets received", "cost_basis": "Cost basis received", "residual": "Residual swept", "exchange_1035": "1035 exchange",
    # Private funds and restricted stock (L200-1 §6.4): they do not move by ACAT.
    "assignment_requested": "Assignment requested", "gp_consent": "General partner consented", "reregistered": "Re-registered",
    "capital_call": "Capital call", "restricted_stock": "Restricted stock", "legend_review": "Legend review",
}
PRIVATE_KINDS = {"assignment_requested", "gp_consent", "reregistered", "capital_call", "restricted_stock", "legend_review"}
REJECT_CODES = {
    "title_mismatch": "Account title does not match", "tax_id_mismatch": "Tax ID does not match", "account_number": "Account number not found",
    "account_closed": "Account closed or flat", "duplicate": "Duplicate request", "signature": "Signature missing or wrong",
    "debit_balance": "Debit balance to clear", "non_transferable": "Assets that cannot transfer", "other": "Other",
}
HOW = ("in_kind", "liquidate", "stays", "exchange_1035")
COST_BASIS_DAYS = 15                       # Treas. Reg. 1.6045A-1: the transfer statement within 15 days of settlement
STAGES = ("requested", "submitted", "rejected", "debit_open", "in_transit", "received", "complete")
OWNER = {"requested": "Operations", "submitted": "Delivering firm", "rejected": "Operations", "debit_open": "Client",
         "in_transit": "Delivering firm", "received": "Delivering firm", "complete": None}


class TransferFileError(ValueError):
    """This cannot be recorded, with the reason."""


def _entry(e: TransferFileEntry) -> dict:
    return {"id": str(e.id), "kind": e.kind, "label": KINDS.get(e.kind, e.kind), "on": e.on.isoformat(), "detail": e.detail or {},
            "evidence": e.evidence, "recorded_by": e.recorded_by}


def file_state(transfer: TransferRequest, entries: list[TransferFileEntry], today: date) -> dict:
    """Where the transfer stands, from its entries in order."""
    ordered = sorted(entries, key=lambda e: (e.on, e.created_at))
    last = {}
    for e in ordered:
        last[e.kind] = e
    rejected = last.get("rejected")
    resubmitted_after = rejected and any(e.kind in ("resubmitted",) and (e.on, e.created_at) > (rejected.on, rejected.created_at) for e in ordered)
    debit = last.get("debit_balance")
    debit_open = bool(debit and not any(e.kind == "debit_cleared" and (e.on, e.created_at) >= (debit.on, debit.created_at) for e in ordered))
    received = last.get("received")
    basis = last.get("cost_basis")
    if received and basis:
        stage = "complete"
    elif received:
        stage = "received"
    elif debit_open:
        stage = "debit_open"
    elif rejected and not resubmitted_after:
        stage = "rejected"
    elif "submitted" in last or "resubmitted" in last or "exchange_1035" in last:
        stage = "submitted" if not last.get("asset_memo") else "in_transit"
    else:
        stage = "requested"
    memo = (last.get("asset_memo").detail or {}).get("lines", []) if last.get("asset_memo") else []
    staying = [m for m in memo if m.get("how") == "stays"]
    sent = last.get("resubmitted") or last.get("submitted")
    basis_due = received.on + timedelta(days=COST_BASIS_DAYS) if received else None
    late = []
    if basis_due and not basis and today > basis_due:
        late.append({"what": "cost_basis", "due": basis_due.isoformat(), "days_late": (today - basis_due).days})
    rejects = [{"on": e.on.isoformat(), "code": (e.detail or {}).get("code"), "label": REJECT_CODES.get((e.detail or {}).get("code"), "Other")}
               for e in ordered if e.kind == "rejected"]
    return {"stage": stage, "owner": OWNER[stage], "partial": bool(staying), "staying": staying,
            "memo": memo, "debit_open": debit_open, "debit_amount": (debit.detail or {}).get("amount") if debit_open else None,
            "rejects": rejects, "submitted_on": sent.on.isoformat() if sent else None,
            "received_on": received.on.isoformat() if received else None,
            "days_to_receive": (received.on - sent.on).days if received and sent else None,
            "cost_basis_due": basis_due.isoformat() if basis_due else None, "cost_basis_on": basis.on.isoformat() if basis else None,
            "residuals": [{"on": e.on.isoformat(), "amount": (e.detail or {}).get("amount")} for e in ordered if e.kind == "residual"],
            "exchanges": [{"on": e.on.isoformat(), **(e.detail or {})} for e in ordered if e.kind == "exchange_1035"],
            "late": late, "private": private_state(ordered, today), "entries": [_entry(e) for e in ordered]}


def holding_period_met(acquired: date, today: date, *, reporting_issuer: bool) -> tuple[bool, date]:
    """Rule 144(d): six months for a company that has reported for at least 90 days, a year otherwise."""
    months = 6 if reporting_issuer else 12
    m = acquired.month - 1 + months
    year, month = acquired.year + m // 12, m % 12 + 1
    ends = date(year, month, min(acquired.day, calendar.monthrange(year, month)[1]))   # never early
    return today >= ends, ends


def private_state(ordered: list, today: date) -> dict:
    """Assignments of private fund interests, capital calls that fall across the transfer, and restricted stock."""
    funds: dict = {}
    for e in ordered:
        d = e.detail or {}
        if e.kind in ("assignment_requested", "gp_consent", "reregistered"):
            f = funds.setdefault(d.get("fund"), {"fund": d.get("fund"), "requested_on": None, "consent_on": None, "reregistered_on": None})
            f[{"assignment_requested": "requested_on", "gp_consent": "consent_on", "reregistered": "reregistered_on"}[e.kind]] = e.on.isoformat()
            if e.kind == "assignment_requested":
                f.update({"commitment": d.get("commitment"), "unfunded": d.get("unfunded")})
    for f in funds.values():
        f["stage"] = "reregistered" if f["reregistered_on"] else "gp_consented" if f["consent_on"] else "requested"
        f["owner"] = {"requested": "General partner", "gp_consented": "Fund administrator", "reregistered": None}[f["stage"]]
    calls = []
    for e in ordered:
        if e.kind != "capital_call":
            continue
        d = e.detail or {}
        f = funds.get(d.get("fund"), {})
        start, end = f.get("requested_on"), f.get("reregistered_on")
        due = d.get("due_on")
        across = bool(start and due and due >= start and (end is None or due <= end))
        calls.append({"fund": d.get("fund"), "amount": d.get("amount"), "due_on": due, "funded_by": d.get("funded_by"),
                      "across_transfer": across, "open": across and not d.get("funded_by")})
    restricted: dict = {}
    for e in ordered:
        d = e.detail or {}
        if e.kind == "restricted_stock":
            met, ends = holding_period_met(date.fromisoformat(d["acquired_on"]), today, reporting_issuer=bool(d.get("reporting_issuer")))
            restricted[d["security"]] = {"security": d["security"], "shares": d.get("shares"), "affiliate": bool(d.get("affiliate")),
                                         "reporting_issuer": bool(d.get("reporting_issuer")), "acquired_on": d["acquired_on"],
                                         "holding_period_ends": ends.isoformat(), "holding_period_met": met, "legend": "on", "opinion": None}
        if e.kind == "legend_review" and d.get("security") in restricted:
            r = restricted[d["security"]]
            r["legend"] = "removed" if d.get("outcome") == "removed" else "stays"
            r["opinion"] = d.get("opinion")
    needs = ([f"{f['fund']}: waiting on the {f['owner'].lower()}" for f in funds.values() if f["owner"]]
             + [f"{c['fund']}: a capital call due {c['due_on']} falls across the transfer; record who funds it" for c in calls if c["open"]]
             + [f"{r['security']}: legend review" for r in restricted.values() if r["legend"] == "on"])
    return {"assignments": list(funds.values()), "capital_calls": calls, "restricted": list(restricted.values()), "needs": needs}


# ── The rollover analysis ─────────────────────────────────────────────────────

ROLLOVER_FACTS = {
    "plan_name": "Plan", "plan_type": "Plan type", "balance": "Balance", "plan_cost_bps": "Plan all-in cost (bps)",
    "ira_cost_bps": "IRA all-in cost (bps)", "employer_stock": "Holds employer stock", "employer_stock_value": "Employer stock value",
    "employer_stock_basis": "Employer stock cost basis", "loan_balance": "Plan loan outstanding", "separation_year": "Year left the employer",
    "adviser_reason": "Adviser's reason", "source": "Source",
}
_NUMBERS = ("balance", "plan_cost_bps", "ira_cost_bps", "employer_stock_value", "employer_stock_basis", "loan_balance", "separation_year")


def rmd_age(dob: date) -> int:
    """The age a required distribution starts, under SECURE 2.0: 73 for those born 1951 to 1959, 75 from 1960."""
    return 75 if dob.year >= 1960 else 73 if dob.year >= 1951 else 72


def _age_at_year_end(dob: date, year: int) -> int:
    return year - dob.year


def analyse(facts: dict, *, owner_dob: date | None, today: date) -> dict:
    """The rollover's points, each from the facts on file or 'not assessed'. A layout, not a recommendation."""
    f = facts or {}
    points = []

    def point(key, label, status, figures=None, says=None):
        points.append({"key": key, "label": label, "status": status, "figures": figures or {}, "says": says})

    balance = f.get("balance")
    if f.get("plan_cost_bps") is not None and f.get("ira_cost_bps") is not None:
        diff = float(f["ira_cost_bps"]) - float(f["plan_cost_bps"])
        figures = {"plan_bps": float(f["plan_cost_bps"]), "ira_bps": float(f["ira_cost_bps"]), "difference_bps": diff}
        if balance is not None:
            figures["difference_a_year"] = round(float(balance) * diff / 10000, 2)
        point("cost", "Cost: plan against IRA", "higher_in_ira" if diff > 0 else "lower_in_ira" if diff < 0 else "same", figures)
    else:
        point("cost", "Cost: plan against IRA", "not_assessed", says="Record both all-in costs from the plan's fee disclosure and the firm's schedule.")

    figures = {"withheld_if_indirect": round(float(balance) * 0.20, 2)} if balance is not None else {}
    point("direct_or_indirect", "Direct or indirect", "laid_out", figures,
          "Direct: nothing withheld. Indirect: the plan withholds 20%, and the whole amount must reach the IRA within 60 days or the shortfall is taxed.")

    if f.get("employer_stock") is True:
        if f.get("employer_stock_value") is not None and f.get("employer_stock_basis") is not None:
            nua = float(f["employer_stock_value"]) - float(f["employer_stock_basis"])
            point("nua", "Employer stock (NUA)", "applies" if nua > 0 else "no_gain", {"nua": round(nua, 2), "basis": float(f["employer_stock_basis"])},
                  "Rolled into an IRA, the stock loses NUA treatment; distributed in kind in a lump sum, only its basis is ordinary income now.")
        else:
            point("nua", "Employer stock (NUA)", "not_assessed", says="Record the stock's value and cost basis from the plan statement.")
    elif f.get("employer_stock") is False:
        point("nua", "Employer stock (NUA)", "none")
    else:
        point("nua", "Employer stock (NUA)", "not_assessed", says="Record whether the plan holds employer stock.")

    loan = f.get("loan_balance")
    if loan is None:
        point("loan", "Plan loan", "not_assessed", says="Record any loan outstanding from the plan statement.")
    elif float(loan) > 0:
        point("loan", "Plan loan", "applies", {"loan": float(loan)},
              "Leaving the plan offsets the loan; the offset can be rolled over by the tax return due date, with extensions, or it is taxed.")
    else:
        point("loan", "Plan loan", "none")

    if owner_dob is None:
        point("rmd_first", "Required distribution first", "not_assessed", says="Record the owner's date of birth.")
        point("age_55", "Left work at 55 or later", "not_assessed", says="Record the owner's date of birth.")
    else:
        start = rmd_age(owner_dob)
        due = _age_at_year_end(owner_dob, today.year) >= start
        point("rmd_first", "Required distribution first", "applies" if due else "not_yet", {"rmd_age": start},
              "This year's required distribution cannot be rolled over; it comes out of the plan first." if due else None)
        year = f.get("separation_year")
        if year is None:
            point("age_55", "Left work at 55 or later", "not_assessed", says="Record the year the owner left the employer.")
        else:
            applies = int(year) - owner_dob.year >= 55 and _age_at_year_end(owner_dob, today.year) < 60
            point("age_55", "Left work at 55 or later", "applies" if applies else "does_not_apply", {"separation_year": int(year)},
                  "Withdrawals from this plan before 59½ escape the 10% penalty; in an IRA they would not." if applies else None)

    reason = (f.get("adviser_reason") or "").strip()
    assessed = all(p["status"] != "not_assessed" for p in points)
    return {"points": points, "adviser_reason": reason or None, "complete": assessed and bool(reason),
            "missing": [p["key"] for p in points if p["status"] == "not_assessed"] + ([] if reason else ["adviser_reason"])}


async def _owner_dob(session: AsyncSession, case: OnboardingCase) -> date | None:
    owner = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id, OnboardingParty.role == "owner"))).scalars().first()
    try:
        return date.fromisoformat(owner.dob) if owner and owner.dob else None
    except ValueError:
        return None


ROLLOVER_TYPES = {"employer_rollover", "traditional_ira", "roth_ira"}


async def rollover_for(session: AsyncSession, case: OnboardingCase, firm: Firm) -> dict | None:
    facts = (case.intake or {}).get("rollover")
    if case.registration_type not in ROLLOVER_TYPES and not facts:
        return None
    if markets.code(firm.jurisdiction) != "US":
        return {"applies": False, "why": "The rollover analysis is for a US employer plan."}
    return {"applies": True, "facts": facts or {}, **analyse(facts or {}, owner_dob=await _owner_dob(session, case), today=firm_time.today(firm))}


async def for_case(session: AsyncSession, firm_id: uuid.UUID, *, case_id: uuid.UUID | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    q = select(TransferRequest).where(TransferRequest.firm_id == firm_id, TransferRequest.direction == "in")
    case = None
    if case_id is not None:
        case = await session.get(OnboardingCase, case_id)
        if case is None:
            # A transfer's own id names its case just as well.
            moved = await session.get(TransferRequest, case_id)
            case = await session.get(OnboardingCase, moved.case_id) if moved is not None and moved.firm_id == firm_id else None
            case_id = case.id if case is not None else case_id
        if case is None or case.firm_id != firm_id:
            raise TransferFileError("Case not found.")
        q = q.where(TransferRequest.case_id == case_id)
    transfers = (await session.execute(q)).scalars().all()
    entries = (await session.execute(select(TransferFileEntry).where(TransferFileEntry.transfer_id.in_([t.id for t in transfers])))).scalars().all() if transfers else []
    by = {}
    for e in entries:
        by.setdefault(e.transfer_id, []).append(e)
    names = {c.id: c.prospect_name for c in (await session.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm_id))).scalars().all()}
    rows = [{"transfer_id": str(t.id), "case_id": str(t.case_id), "client": names.get(t.case_id), "type": t.transfer_type, "status": t.status,
             "delivering_firm": t.delivering_firm, "amount": float(t.amount) if t.amount is not None else None, "asset_description": t.asset_description,
             **file_state(t, by.get(t.id, []), today)} for t in transfers]
    rows.sort(key=lambda r: (r["stage"] == "complete", r["client"] or ""))
    counts = {}
    for r in rows:
        for j in r["rejects"]:
            counts[j["code"]] = counts.get(j["code"], 0) + 1
    out = {"transfers": rows, "rejects_by_code": [{"code": k, "label": REJECT_CODES.get(k, k), "count": n} for k, n in sorted(counts.items(), key=lambda kv: -kv[1])],
           "late": sum(1 for r in rows if r["late"]), "as_of": today.isoformat(), "case_id": str(case_id) if case_id else None}
    if case is not None:
        out["rollover"] = await rollover_for(session, case, firm)
    return out


# ── Recording ─────────────────────────────────────────────────────────────────

def _lines(text) -> list[dict]:
    """'AAPL 100 in_kind; Plan Stable Value Fund 2400 stays' -> lines."""
    if isinstance(text, list):
        return text
    out = []
    for part in [p.strip() for p in str(text or "").split(";") if p.strip()]:
        words = part.split()
        how = words[-1].lower() if words and words[-1].lower() in HOW else None
        if how is None:
            raise TransferFileError(f"Say how each line moves ({', '.join(HOW)}): '{part}'.")
        words = words[:-1]
        qty = None
        if words and words[-1].replace(",", "").replace(".", "", 1).isdigit():
            qty = float(words[-1].replace(",", ""))
            words = words[:-1]
        if not words:
            raise TransferFileError(f"Name the holding: '{part}'.")
        out.append({"description": " ".join(words), "quantity": qty, "how": how})
    if not out:
        raise TransferFileError("List the memo's lines: holding, quantity, and in_kind, liquidate, stays or exchange_1035.")
    return out


async def prepare_entry(session: AsyncSession, firm_id: uuid.UUID, *, transfer_id: uuid.UUID, kind: str | None, on: date | None,
                        evidence: str | None, code: str | None = None, amount: float | None = None, lines=None,
                        carrier: str | None = None, contract: str | None = None, note: str | None = None) -> dict:
    t = await session.get(TransferRequest, transfer_id)
    if t is None or t.firm_id != firm_id:
        raise TransferFileError("Transfer not found.")
    if t.direction != "in":
        raise TransferFileError("This file is for transfers into the firm; a transfer out is recorded on the departure.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise TransferFileError(f"What happened is one of {', '.join(KINDS)}.")
    day = on or today
    if day > today:
        raise TransferFileError("Nothing is recorded ahead of the day it happened.")
    named = (evidence or "").strip()
    if len(named) < 5:
        raise TransferFileError("Say where it comes from: the delivering firm's notice, the asset memo, the custodian's confirmation.")
    detail: dict = {}
    if kind == "rejected":
        code = (code or "").strip().lower()
        if code not in REJECT_CODES:
            raise TransferFileError(f"A reject carries its reason: one of {', '.join(REJECT_CODES)}.")
        detail["code"] = code
    if kind in ("debit_balance", "residual"):
        if amount is None or float(amount) <= 0:
            raise TransferFileError("Give the amount, as the statement shows it.")
        detail["amount"] = round(float(amount), 2)
    if kind == "asset_memo":
        detail["lines"] = _lines(lines)
    if kind == "exchange_1035":
        if not (carrier or "").strip() or not (contract or "").strip():
            raise TransferFileError("A 1035 exchange names the carrier and the contract.")
        detail.update({"carrier": carrier.strip(), "contract": contract.strip()})
    if note:
        detail["note"] = note.strip()
    entries = (await session.execute(select(TransferFileEntry).where(TransferFileEntry.transfer_id == t.id))).scalars().all()
    kinds = {e.kind for e in entries}
    if kind == "cost_basis" and "received" not in kinds:
        raise TransferFileError("Cost basis follows the assets: record them received first.")
    if kind == "debit_cleared" and "debit_balance" not in kinds:
        raise TransferFileError("No debit balance is on file to clear.")
    if kind == "resubmitted" and "rejected" not in kinds:
        raise TransferFileError("Nothing has been rejected to resubmit.")
    case = await session.get(OnboardingCase, t.case_id)
    return {"transfer": t, "client": case.prospect_name if case else None, "kind": kind, "on": day, "detail": detail, "evidence": named,
            "before": file_state(t, list(entries), today)}


async def record_entry(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_entry(session, firm_id, **terms)
    t = r["transfer"]
    session.add(TransferFileEntry(firm_id=firm_id, transfer_id=t.id, kind=r["kind"], on=r["on"], detail=r["detail"], evidence=r["evidence"], recorded_by=by))
    if r["kind"] == "rejected":
        t.reject_reason = REJECT_CODES[r["detail"]["code"]]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="transfer_file_entry", actor=by,
                              content={"transfer_id": str(t.id), "kind": r["kind"], "on": r["on"].isoformat(), "detail": r["detail"], "evidence": r["evidence"]})
    return await for_case(session, firm_id, case_id=t.case_id)


async def prepare_private(session: AsyncSession, firm_id: uuid.UUID, *, transfer_id: uuid.UUID, kind: str | None, on: date | None, evidence: str | None,
                          fund: str | None = None, commitment=None, unfunded=None, amount=None, due_on: date | None = None, funded_by: str | None = None,
                          security: str | None = None, shares=None, acquired_on: date | None = None, affiliate: bool | None = None,
                          reporting_issuer: bool | None = None, outcome: str | None = None, opinion: str | None = None) -> dict:
    t = await session.get(TransferRequest, transfer_id)
    if t is None or t.firm_id != firm_id:
        raise TransferFileError("Transfer not found.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    kind = (kind or "").strip().lower()
    if kind not in PRIVATE_KINDS:
        raise TransferFileError(f"What happened is one of {', '.join(sorted(PRIVATE_KINDS))}.")
    day = on or today
    if day > today:
        raise TransferFileError("Nothing is recorded ahead of the day it happened.")
    if len((evidence or "").strip()) < 5:
        raise TransferFileError("Say where it comes from: the assignment agreement, the consent letter, the call notice, the stock certificate, counsel's opinion.")

    def num(v, label):
        try:
            x = float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            raise TransferFileError(f"Give {label}, as the document shows it.")
        if x < 0:
            raise TransferFileError(f"{label.capitalize()} cannot be negative.")
        return round(x, 2)

    entries = (await session.execute(select(TransferFileEntry).where(TransferFileEntry.transfer_id == t.id))).scalars().all()
    detail: dict = {}
    if kind in ("assignment_requested", "gp_consent", "reregistered", "capital_call"):
        if len((fund or "").strip()) < 2:
            raise TransferFileError("Name the fund.")
        detail["fund"] = fund.strip()
        requested = any(e.kind == "assignment_requested" and (e.detail or {}).get("fund") == detail["fund"] for e in entries)
        if kind == "assignment_requested":
            detail.update({"commitment": num(commitment, "the commitment"), "unfunded": num(unfunded, "the unfunded commitment")})
        elif kind in ("gp_consent", "reregistered") and not requested:
            raise TransferFileError(f"No assignment of {detail['fund']} has been requested on this transfer.")
        if kind == "reregistered" and not any(e.kind == "gp_consent" and (e.detail or {}).get("fund") == detail["fund"] for e in entries):
            raise TransferFileError("A fund interest is re-registered only after the general partner consents.")
        if kind == "capital_call":
            if due_on is None:
                raise TransferFileError("Give the date the call is due.")
            detail.update({"amount": num(amount, "the amount called"), "due_on": due_on.isoformat(), "funded_by": (funded_by or "").strip() or None})
    if kind in ("restricted_stock", "legend_review"):
        if len((security or "").strip()) < 1:
            raise TransferFileError("Name the security.")
        detail["security"] = security.strip().upper()
    if kind == "restricted_stock":
        if acquired_on is None:
            raise TransferFileError("Give the date the shares were acquired and paid for: the holding period runs from it.")
        if affiliate is None or reporting_issuer is None:
            raise TransferFileError("Record whether the holder is an affiliate of the issuer, and whether the issuer has been reporting for at least 90 days.")
        detail.update({"shares": num(shares, "the number of shares"), "acquired_on": acquired_on.isoformat(), "affiliate": bool(affiliate),
                       "reporting_issuer": bool(reporting_issuer)})
    if kind == "legend_review":
        if not any(e.kind == "restricted_stock" and (e.detail or {}).get("security") == detail["security"] for e in entries):
            raise TransferFileError(f"{detail['security']} is not recorded as restricted on this transfer.")
        outcome = (outcome or "").strip().lower()
        if outcome not in ("removed", "stays"):
            raise TransferFileError("The review's outcome is removed or stays.")
        if outcome == "removed" and len((opinion or "").strip()) < 5:
            raise TransferFileError("A legend comes off only on the issuer's counsel's opinion: give its reference.")
        detail.update({"outcome": outcome, "opinion": (opinion or "").strip() or None})
    case = await session.get(OnboardingCase, t.case_id)
    return {"transfer": t, "client": case.prospect_name if case else None, "kind": kind, "on": day, "detail": detail, "evidence": evidence.strip(), "today": today}


async def record_private(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_private(session, firm_id, **terms)
    t = r["transfer"]
    session.add(TransferFileEntry(firm_id=firm_id, transfer_id=t.id, kind=r["kind"], on=r["on"], detail=r["detail"], evidence=r["evidence"], recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="transfer_file_entry", actor=by,
                              content={"transfer_id": str(t.id), "kind": r["kind"], "detail": r["detail"], "evidence": r["evidence"]})
    return await for_case(session, firm_id, case_id=t.case_id)


async def prepare_rollover(session: AsyncSession, firm_id: uuid.UUID, *, case_id: uuid.UUID, facts: dict, source: str | None) -> dict:
    case = await session.get(OnboardingCase, case_id)
    if case is None or case.firm_id != firm_id:
        raise TransferFileError("Case not found.")
    firm = await session.get(Firm, firm_id)
    if markets.code(firm.jurisdiction) != "US":
        raise TransferFileError(f"Nothing recorded: the rollover analysis is for a US employer plan, and this firm's market is {markets.code(firm.jurisdiction) or 'not set'}.")
    if len((source or "").strip()) < 5:
        raise TransferFileError("Say where the facts come from: the plan statement, the fee disclosure.")
    clean = {}
    for k, v in (facts or {}).items():
        if v in (None, ""):
            continue
        if k not in ROLLOVER_FACTS or k == "source":
            raise TransferFileError(f"{k} is not a rollover fact.")
        if k in _NUMBERS:
            try:
                v = float(str(v).replace(",", "").replace("$", ""))
            except ValueError:
                raise TransferFileError(f"{ROLLOVER_FACTS[k]} is a number.")
            if v < 0:
                raise TransferFileError(f"{ROLLOVER_FACTS[k]} cannot be negative.")
            if k == "separation_year":
                v = int(v)
        elif k == "employer_stock":
            v = v if isinstance(v, bool) else str(v).strip().lower() in ("yes", "true", "1", "y")
        else:
            v = str(v).strip()
        clean[k] = v
    if not clean:
        raise TransferFileError("Nothing to record.")
    merged = {**((case.intake or {}).get("rollover") or {}), **clean}
    after = analyse(merged, owner_dob=await _owner_dob(session, case), today=firm_time.today(firm))
    return {"case": case, "facts": clean, "merged": merged, "source": source.strip(), "after": after}


async def record_rollover(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_rollover(session, firm_id, **terms)
    case = r["case"]
    intake = dict(case.intake or {})
    intake["rollover"] = {**r["merged"], "source": r["source"], "recorded_by": by}
    case.intake = intake
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="rollover_facts_recorded", actor=by,
                              content={"case_id": str(case.id), "facts": sorted(r["facts"]), "source": r["source"]})
    return await for_case(session, firm_id, case_id=case.id)
