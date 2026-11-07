"""Where each account stands in onboarding, whose move it is, and what is stuck (L200-1 §1, §2, §9).

An onboarding case is one account. Its state is worked out from the evidence, as the tracks are, so it cannot drift:

    draft                 the case exists
    data_complete         the roles the registration needs are recorded, with the documents it needs
    documents_generated   the agreement has gone out to sign
    signed                the agreement is signed
    in_review             compliance's gates are being worked: screening, identity, EDD, disclosures
    submitted             activated in the book, waiting for the custodian
    open                  the custodian has opened the account
    funding_in_progress   money or assets are on their way in
    funded                what was coming in has arrived
    invested              the first purchase after funding has filled

Each state has an owner and an SLA, and its age is measured from when the account entered it. Exceptions are kept in
their own queues by kind, because a document defect, a screening hit, a custodian reject, a transfer reject and a
failed payment are worked by different people. A household's status is its least advanced account, since clients ask
"are we done?" and operations works accounts.

The paperwork, money and invested clocks are reported apart: signature to open, open to funded, funded to invested.
The money clock is mostly the delivering firm's, and the invested one is a portfolio decision, so one number for all
three would say nothing about who is slow. Holidays are not modelled, and nothing here writes.
"""
from __future__ import annotations

from datetime import datetime, timezone

STATES = ("draft", "data_complete", "documents_generated", "signed", "in_review", "submitted", "open",
          "funding_in_progress", "funded", "invested")
# state -> (owner, SLA in days). The SLA is the firm's own target for leaving the state; these are the guide's
# working figures, and EDD gets longer in review.
POLICY: dict[str, tuple[str, int | None]] = {
    "draft": ("Adviser", 3), "data_complete": ("Adviser", 2), "documents_generated": ("Adviser", 5), "signed": ("Operations", 2),
    "in_review": ("Compliance", 5), "submitted": ("Operations", 2), "open": ("Operations", 3),
    "funding_in_progress": ("Operations and the delivering firm", 10), "funded": ("Portfolio team", 5), "invested": ("Done", None),
}
EDD_REVIEW_DAYS = 10
QUEUES = {
    "nigo": ("Documents not in good order", "Operations intake"),
    "kyc": ("KYC: screening, identity or EDD", "AML operations and compliance"),
    "custodian_reject": ("Custodian reject", "Operations"),
    "transfer_reject": ("Transfer reject", "Transfers team"),
    "funding_fail": ("Funding failed", "Operations"),
}


def _aware(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def _days(a, b) -> float | None:
    a, b = _aware(a), _aware(b)
    return round((b - a).total_seconds() / 86400, 2) if a and b else None


def state_of(case, *, data_complete: bool, gates_blocking: bool, transfers: list, first_fill: datetime | None) -> dict:
    """The account's state and when it entered it. `transfers` are the case's; `first_fill` is the first purchase that
    filled on its account after funding, if any."""
    inbound = [t for t in transfers if t.direction == "in"]
    settled = [t for t in inbound if t.status == "settled" and t.settled_at]
    moving = [t for t in inbound if t.status not in ("settled", "failed")]
    funded_at = max((t.settled_at for t in settled), key=_aware) if settled and not moving else None
    opened = case.custodian_push_status == "active"
    if funded_at and first_fill and _aware(first_fill) >= _aware(funded_at):
        return {"state": "invested", "since": first_fill}
    if funded_at:
        return {"state": "funded", "since": funded_at}
    if opened and inbound and (moving or settled):
        return {"state": "funding_in_progress", "since": min((t.initiated_at or case.created_at for t in inbound), key=_aware)}
    if opened:
        return {"state": "open", "since": case.custodian_push_at or case.activated_at}
    if case.activated_at:
        return {"state": "submitted", "since": case.activated_at}
    if case.agreement_signed_at:
        return {"state": "in_review" if gates_blocking else "signed", "since": case.agreement_signed_at}
    if case.agreement_sent_at:
        return {"state": "documents_generated", "since": case.agreement_sent_at}
    if data_complete:
        return {"state": "data_complete", "since": case.updated_at or case.created_at}
    return {"state": "draft", "since": case.created_at}


def exceptions_of(case, *, kyc: list[str], transfers: list, now: datetime) -> list[dict]:
    """The case's exceptions, each in its own queue."""
    out = []

    def add(kind: str, detail: str, since) -> None:
        label, owner = QUEUES[kind]
        out.append({"queue": kind, "label": label, "owner": owner, "detail": detail, "age_days": _days(since, now)})

    if case.nigo_flag:
        add("nigo", case.nigo_reason or (case.nigo_root_cause or "Not in good order").replace("_", " "), case.updated_at or case.created_at)
    for detail in kyc:
        add("kyc", detail, case.updated_at or case.created_at)
    if case.custodian_push_status == "failed":
        add("custodian_reject", "The custodian refused to open the account.", case.custodian_push_at or case.updated_at)
    for t in transfers:
        if t.status != "failed":
            continue
        kind = "transfer_reject" if t.transfer_type == "acat" else "funding_fail"
        add(kind, f"{t.transfer_type.upper()} {t.direction}: {t.reject_reason or 'failed, with no reason recorded'}", t.initiated_at or t.created_at)
    return out


def account_row(case, *, data_complete: bool, gates_blocking: bool, kyc: list[str], transfers: list, first_fill, now: datetime) -> dict:
    st = state_of(case, data_complete=data_complete, gates_blocking=gates_blocking, transfers=transfers, first_fill=first_fill)
    owner, sla = POLICY[st["state"]]
    if st["state"] == "in_review" and case.edd_status == "edd_pending":
        sla = EDD_REVIEW_DAYS
    age = _days(st["since"], now)
    return {"case_id": str(case.id), "account": case.prospect_name, "registration_type": case.registration_type, "state": st["state"],
            "step": STATES.index(st["state"]) + 1, "steps": len(STATES), "owner": owner, "since": _aware(st["since"]).isoformat() if st["since"] else None,
            "age_days": age, "sla_days": sla, "overdue": bool(sla is not None and age is not None and age > sla),
            "exceptions": exceptions_of(case, kyc=kyc, transfers=transfers, now=now), "rejected": str(case.status) == "rejected"}


def household_of(case) -> str:
    """Which household an account belongs to: the one it was opened into, else the one its intake names."""
    return (case.materialized or {}).get("household_id") or (case.intake or {}).get("household") or case.prospect_name


def households(rows: list[dict], cases: dict) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["rejected"]:
            continue
        groups.setdefault(household_of(cases[r["case_id"]]), []).append(r)
    out = []
    for key, accounts in groups.items():
        least = min(accounts, key=lambda a: a["step"])
        name = (cases[least["case_id"]].intake or {}).get("household") or least["account"]
        out.append({"household": name, "accounts": len(accounts), "state": "done" if least["state"] == "invested" else least["state"],
                    "waiting_on": least["owner"] if least["state"] != "invested" else None,
                    "exceptions": sum(len(a["exceptions"]) for a in accounts), "overdue": sum(1 for a in accounts if a["overdue"])})
    return sorted(out, key=lambda h: (h["state"] == "done", -h["exceptions"], h["household"]))


def clocks(cases: list, *, transfers_by_case: dict, fills: dict) -> dict:
    """The three clocks, as day counts across activated accounts. Each starts where the last ends.

    A clock never runs backwards (B-157). An account whose dates are out of order - opened before its agreement was
    signed, funded before it was opened - is a data error to show, not a figure to average: it is left out of that
    clock's count and named in `out_of_order`, with the two dates, for a person to put right."""
    from app.aurea_core.onboarding_metrics import _summary

    paperwork, money, invested = [], [], []
    wrong: list[dict] = []

    def take(into: list, clock: str, case, start, end) -> None:
        d = _days(start, end)
        if d is None:
            return
        if d < 0:
            wrong.append({"case_id": str(case.id), "client": getattr(case, "prospect_name", None), "clock": clock,
                          "from": _aware(start).isoformat(), "to": _aware(end).isoformat(), "days": d})
            return
        into.append(d)

    for c in cases:
        opened = c.custodian_push_at if c.custodian_push_status == "active" else None
        if c.agreement_signed_at and opened:
            take(paperwork, "paperwork", c, c.agreement_signed_at, opened)
        inbound = [t for t in transfers_by_case.get(c.id, []) if t.direction == "in"]
        settled = [t.settled_at for t in inbound if t.status == "settled" and t.settled_at]
        if opened and settled and not [t for t in inbound if t.status not in ("settled", "failed")]:
            funded = max(settled, key=_aware)
            take(money, "money", c, opened, funded)
            fill = fills.get(c.id)
            if fill and _aware(fill) >= _aware(funded):
                invested.append(_days(funded, fill))

    def summary(values: list, clock: str) -> dict:
        return {**_summary(values), "out_of_order": sum(1 for w in wrong if w["clock"] == clock)}

    return {"paperwork": summary(paperwork, "paperwork"), "money": summary(money, "money"),
            "invested": summary(invested, "invested"), "out_of_order": wrong}
