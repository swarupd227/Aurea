"""The onboarding measures an Indian firm manages by (B-392, L200-1 India §8, §9, §10).

Ten measures, each counted from what is on file, each with its definition and its count, and "not assessed" where nothing is recorded
rather than a zero: cycle time by cohort, the straight-through rate with the manual touches counted, the book's KYC registry mix and
re-KYC backlog (`kyc_registry`), first-attempt verification success and failures by cause (`identity_checks`), mandate registration and
first-debit success (`sip_mandates`, `india_money`), transaction and registrar rejects by the cause a person recorded, nomination
completeness across the book (`nomination_audit`), screening disposition time with the delta rescreen backlog (`screening_lists`), and
suspicious-report timeliness with the monthly reports (`fiu_reports`). The last is read here by compliance alone and is left out of
everyone else's reading, not blanked.

A manual touch is an exception a person had to work, as recorded on the case: routine entry, signatures and the activation approval are
not counted, and a touch nobody recorded cannot be. A cycle starts when the case is opened, because a client's first action is not
recorded, and whether a journey was digital is not recorded either, so the resident cohort is all residents. Reads only.

The RACI is the module's, with the roles the platform lets record or decide beside each activity, read from the tools so the two cannot
drift apart.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.aurea_core.onboarding_metrics import _summary
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.onboarding import OnboardingCase, OnboardingParty, TransferRequest
from app.models.tenant import Firm

NOT_ASSESSED = "not assessed"
COHORTS = (("resident", "Resident"), ("nri", "Non-resident (NRI)"), ("entity", "Entity"))
NRI_TYPES = {"nri_nre", "nri_nro"}
ENTITY_TYPES = {"huf", "trust", "entity_corp", "entity_partnership", "entity_llp", "entity_llc"}
TOUCHES = {
    "nigo": "Documents not in good order",
    "registry_by_hand": "KYC registry answer typed in by a person",
    "kyc_follow_up": "KYC remediation step or contact attempt",
    "check_by_hand": "Identity or bank check done by hand",
    "screening_decision": "Screening hit decided",
    "edd_review": "Enhanced due diligence review",
    "pep_approval": "Senior approval of a politically exposed person",
    "risk_override": "AML risk band overridden",
    "failed_transfer": "Failed transfer",
}
STR_FIGURE = "in_pml_str_working_days"
# Who reads the report measure: the module's STR work is the Principal Officer's, and the task given it is compliance's alone.
STR_READERS = frozenset({UserRole.COMPLIANCE})

# The module's matrix (section 8), column for column. `tools` are the conversation's own writes for the activity; the roles beside it
# are read from them.
RACI_COLUMNS = (("rm", "Adviser / RM"), ("ops", "Onboarding ops"), ("kyc_aml", "KYC / AML team"), ("compliance", "Compliance"),
                ("platform", "Recorded in Astra by"))
RACI = (
    ("Client data capture and documents", ("R/A", "C", "I", "I"), ("start_onboarding",)),
    ("KYC status check and remediation guidance", ("C", "R", "A", "I"), ("record_kyc_lookup", "record_kyc_step")),
    ("Screening disposition and risk rating", ("I", "I", "R/A", "C"), ("record_screening_decision",)),
    ("PEP and high-risk approval", ("I", "I", "R", "A"), ("record_pep_approval",)),
    ("Account and folio setup, nomination capture", ("C", "R/A", "I", "I"), ("record_asset_nomination",)),
    ("Mandate registration and funding", ("C", "R/A", "I", "I"), ("record_sip_mandate", "record_deposit")),
    ("Code changes and asset transfers", ("C (client consent)", "R/A", "I", "I"), ("record_consolidation_step",)),
    ("STR determination and filing", ("Never involved", "I", "R (Principal Officer)", "A"), ("record_investigation_step",)),
)
ROLE_LABELS = {UserRole.ADVISER: "Adviser", UserRole.PARAPLANNER: "Paraplanner", UserRole.OPERATIONS: "Operations",
               UserRole.COMPLIANCE: "Compliance", UserRole.BRANCH_LEADER: "Branch leader", UserRole.PORTFOLIO_TEAM: "Portfolio team"}


def applies(firm: Firm | None) -> bool:
    return firm is not None and markets.code(firm.jurisdiction) == "IN"


def raci() -> list[dict]:
    from app.core.tools import TOOLS

    out = []
    for activity, cells, tools in RACI:
        roles = set().union(*(set(TOOLS[t].roles_required) for t in tools))
        said = ", ".join(label for role, label in ROLE_LABELS.items() if role in roles)
        out.append({"activity": activity, **dict(zip(("rm", "ops", "kyc_aml", "compliance"), cells)), "platform": said or NOT_ASSESSED})
    return out


def officers(firm: Firm) -> list[dict]:
    """The two officers PMLA makes personally accountable, as the firm recorded them (`fiu_reports.officers`)."""
    from app.aurea_core import fiu_reports

    named = fiu_reports.officers(firm)
    return [{"key": k, "label": label, "name": named[k]} for k, label in
            (("principal_officer", "Principal Officer"), ("designated_director", "Designated Director"))]


def cohort_of(case) -> str:
    if case.registration_type in NRI_TYPES:
        return "nri"
    if case.is_entity or case.registration_type in ENTITY_TYPES:
        return "entity"
    return "resident"


def _aware(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


def _days(a: datetime | None, b: datetime | None) -> float | None:
    a, b = _aware(a), _aware(b)
    return round((b - a).total_seconds() / 86400, 2) if a and b else None


def ready_at(case) -> datetime | None:
    """When the account was ready: the day the custodian or depository opened it. None where no opening is recorded."""
    return case.custodian_push_at if case.custodian_push_status == "active" else None


def _pct(n: int, of: int) -> float | None:
    return round(100 * n / of, 1) if of else None


def _said(value, suffix: str = "") -> str:
    return NOT_ASSESSED if value is None else f"{value:g}{suffix}"


def _metric(key: str, label: str, definition: str, *, assessed: bool, count: str, columns: list[str], rows: list[list], data: dict,
            chips: list[str] | None = None, view: str | None = None, simulated: bool = False) -> dict:
    return {"key": key, "label": label, "definition": definition, "assessed": assessed, "count": count if assessed else NOT_ASSESSED,
            "columns": columns, "rows": rows, "chips": chips or [], "view": view, "simulated": simulated, "data": data}


async def _rows(session: AsyncSession, model, firm_id: uuid.UUID) -> list:
    return list((await session.execute(select(model).where(model.firm_id == firm_id).order_by(model.created_at))).scalars().all())


# ── 1 and 2: cycle time, straight through ────────────────────────────────────────────────────────────────────────
def cycle_time(cases: list) -> dict:
    values: dict[str, list[float]] = {k: [] for k, _ in COHORTS}
    wrong = waiting = 0
    for c in cases:
        if str(c.status) == "rejected":
            continue
        d = _days(c.created_at, ready_at(c))
        if d is None:
            waiting += 1
        elif d < 0:
            wrong += 1
        else:
            values[cohort_of(c)].append(d)
    got = {k: _summary(v) for k, v in values.items()}
    n = sum(len(v) for v in values.values())
    rows = [[label, str(got[k]["n"]), _said(got[k]["p50"], " d"), _said(got[k]["p90"], " d")] for k, label in COHORTS]
    chips = ([f"{waiting} case(s) with no account opening recorded"] if waiting else []) + (
        [f"{wrong} with dates out of order, left out"] if wrong else []) + ["Digital or assisted journey not recorded"]
    return _metric("cycle_time", "Cycle time by cohort", "Case opened to account opened by the custodian or depository; median and 90th percentile days",
                   assessed=n > 0, count=f"{n} opened account(s)", columns=["Cohort", "Opened", "P50", "P90"], rows=rows,
                   data={**got, "not_opened": waiting, "out_of_order": wrong}, chips=chips, view="/studio/onboarding-queues")


async def touches_by_case(session: AsyncSession, firm_id: uuid.UUID, cases: list, transfers: list) -> dict:
    """Each case's recorded manual touches by kind: exceptions a person worked."""
    from app.models.aml_review import EddReview, ScreeningDecision
    from app.models.aml_risk import AmlRiskRating
    from app.models.identity_checks import VerificationAttempt
    from app.models.kyc_registry import KycFollowUp, KycLookup
    from app.models.pmla import PepApproval

    out: dict = {c.id: {} for c in cases}

    def add(case_id, kind: str) -> None:
        if case_id in out:
            out[case_id][kind] = out[case_id].get(kind, 0) + 1

    for c in cases:
        if c.nigo_flag or c.nigo_root_cause:
            add(c.id, "nigo")
    for r in await _rows(session, KycLookup, firm_id):
        if not r.source_system:
            add(r.case_id, "registry_by_hand")
    for r in await _rows(session, KycFollowUp, firm_id):
        add(r.case_id, "kyc_follow_up")
    for r in await _rows(session, VerificationAttempt, firm_id):
        if r.method == "alternative":
            add(r.case_id, "check_by_hand")
    for r in await _rows(session, ScreeningDecision, firm_id):
        add(r.case_id, "screening_decision")
    for r in await _rows(session, EddReview, firm_id):
        add(r.case_id, "edd_review")
    for r in await _rows(session, AmlRiskRating, firm_id):
        if r.override_reason:
            add(r.case_id, "risk_override")
    for r in await _rows(session, PepApproval, firm_id):
        add(r.case_id, "pep_approval")
    for t in transfers:
        if t.status == "failed":
            add(t.case_id, "failed_transfer")
    return out


def straight_through(cases: list, touches: dict) -> dict:
    done = [c for c in cases if str(c.status) != "rejected" and ready_at(c) is not None]
    data: dict = {}
    rows = []
    for key, label in (*COHORTS, ("all", "All cohorts")):
        mine = [c for c in done if key == "all" or cohort_of(c) == key]
        clean = sum(1 for c in mine if not touches.get(c.id))
        count = sum(sum(touches.get(c.id, {}).values()) for c in mine)
        data[key] = {"opened": len(mine), "straight_through": clean, "rate_pct": _pct(clean, len(mine)), "touches": count}
        rows.append([label, str(len(mine)), str(clean) if mine else NOT_ASSESSED, _said(data[key]["rate_pct"], "%"), str(count) if mine else NOT_ASSESSED])
    by_kind = {k: sum(touches.get(c.id, {}).get(k, 0) for c in done) for k in TOUCHES}
    data["by_kind"] = by_kind
    chips = [f"{TOUCHES[k]} {n}" for k, n in by_kind.items() if n] + ["Routine entry, signature and approval not counted"]
    a = data["all"]
    return _metric("straight_through", "Straight-through rate", "Opened accounts with no recorded manual exception on the case, out of all opened accounts",
                   assessed=bool(done), count=f"{a['straight_through']} of {a['opened']} with no touch; {a['touches']} touch(es)",
                   columns=["Cohort", "Opened", "No touch", "Rate", "Touches"], rows=rows, data=data, chips=chips)


# ── 3 and 10: the registry's mix and the re-KYC backlog (B-386) ──────────────────────────────────────────────────
async def registry_measures(session: AsyncSession, firm: Firm) -> list[dict]:
    from app.aurea_core import kyc_registry

    book = await kyc_registry.book(session, firm)
    total = book["counts"]["parties"]
    mix_rows = [[m["label"], str(m["count"]), _said(round(100 * m["share"], 1) if m["share"] is not None else None, "%")] for m in book["mix"]]
    looked = sum(m["count"] for m in book["mix"] if m["status"] != kyc_registry.NOT_LOOKED_UP)
    simulated = any(p["registry"]["simulated"] for p in book["parties"])
    mix = _metric("kyc_mix", "KYC status mix of the book", "Each person and entity on the book by the registry answer that stands",
                  assessed=looked > 0, count=f"{looked} of {total} looked up", columns=["Registry status", "Parties", "Share"], rows=mix_rows,
                  data={"mix": {m["status"]: m["count"] for m in book["mix"]}, "parties": total},
                  chips=[f"{book['counts']['cases_not_looked_up']} of {book['counts']['cases']} open case(s) not looked up"],
                  view="/studio/kyc-registry", simulated=simulated)
    open_items = book["counts"]["backlog"]
    bands = [[b["label"], str(b["count"]), str(b["no_contact"])] for b in book["bands"]]
    undated = sum(1 for p in book["backlog"] if p["backlog"]["band"] is None)
    if undated:
        bands.append(["No date on file", str(undated), str(sum(1 for p in book["backlog"] if p["backlog"]["band"] is None and not p["contacts"]["attempts"]))])
    backlog = _metric("rekyc_backlog", "Re-KYC backlog by age", "Open re-KYC items by days since they opened, and those with no contact attempt recorded since",
                      assessed=total > 0 and (looked > 0 or open_items > 0), count=f"{open_items} open, {book['counts']['no_contact']} with no contact",
                      columns=["Age", "Open", "No contact"], rows=bands,
                      data={"open": open_items, "no_contact": book["counts"]["no_contact"], "bands": {b["key"]: b["count"] for b in book["bands"]}},
                      view="/studio/kyc-registry", simulated=simulated)
    return [mix, backlog]


# ── 4: verification success by cause (B-387) ─────────────────────────────────────────────────────────────────────
async def verification(session: AsyncSession, firm: Firm) -> dict:
    from app.aurea_core import identity_checks

    view = await identity_checks.overview(session, firm)
    names = (("ekyc", "Aadhaar eKYC"), ("vipv", "Video in-person verification"), ("pan", "PAN verification"), ("bank", "Penny drop and name match"))
    rows, data, causes = [], {}, {}
    for kind, label in names:
        mine = [r for r in view["checks"] if r["kind"] == kind]
        first = [r["history"][0] for r in mine]
        passed = sum(1 for h in first if h["outcome"] == identity_checks.OUTCOMES["passed"])
        by_hand = sum(1 for h in first if h["method"] == "alternative")
        fallback = sum(1 for r in mine if r["state"] == "passed_alternative")
        waiting = sum(1 for r in mine if not r["passed"])
        data[kind] = {"first_attempts": len(first), "first_passed": passed, "rate_pct": _pct(passed, len(first)), "first_by_hand": by_hand,
                      "passed_by_hand": fallback, "not_passed": waiting}
        rows.append([label, str(len(first)) if first else NOT_ASSESSED, str(passed) if first else NOT_ASSESSED, _said(data[kind]["rate_pct"], "%"),
                     str(by_hand) if first else NOT_ASSESSED, str(waiting) if first else NOT_ASSESSED])
        for r in mine:
            for h in r["history"]:
                if h["outcome"] == identity_checks.OUTCOMES["passed"]:
                    continue
                cause = h["reason"] or ("Failed by hand, no reason code" if h["method"] == "alternative" else "No reason returned")
                causes[(label, cause)] = causes.get((label, cause), 0) + 1
    data["causes"] = [{"check": c, "cause": why, "count": n} for (c, why), n in sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))]
    total = sum(d["first_attempts"] for k, d in data.items() if k != "causes")
    passed = sum(d["first_passed"] for k, d in data.items() if k != "causes")
    chips = [f"{c['check']}: {c['cause']} {c['count']}" for c in data["causes"]] + ["No Aadhaar eKYC check is recorded in Astra"]
    if view.get("provider") is None:
        chips.append("No verification service connected")
    return _metric("verification", "Verification pass rates", "First attempt passed, per party and check, and every attempt that did not pass by its cause",
                   assessed=total > 0, count=f"{passed} of {total} first attempt(s) passed",
                   columns=["Check", "First attempts", "Passed", "Rate", "By hand", "Not passed now"], rows=rows, data=data, chips=chips,
                   view="/studio/kyc-registry", simulated=any(r["simulated"] for r in view["checks"]))


# ── 5: mandates (B-388) ──────────────────────────────────────────────────────────────────────────────────────────
async def mandates(session: AsyncSession, firm: Firm) -> dict:
    from app.aurea_core import india_money, sip_mandates
    from app.models.india_batch_a import DebitMandate
    from app.models.india_money import MandateDebit

    today = firm_time.today(firm)
    every = await _rows(session, DebitMandate, firm.id)
    latest: dict = {}
    for r in every:
        latest[(r.account_id, r.reference)] = r
    replaced = {(r.account_id, r.supersedes_reference) for r in every if r.supersedes_reference and r.supersedes_reference != r.reference}
    first: dict = {}
    for d in await _rows(session, MandateDebit, firm.id):
        first.setdefault((d.account_id, d.reference), d)

    rows, data = [], {}
    for key, label in sip_mandates.TYPES.items():
        mine = {k: r for k, r in latest.items() if r.mandate_type == key}
        got = {"registered": 0, "failed": 0, "pending": 0, "first_confirmed": 0, "first_failed": 0, "first_not_recorded": 0}
        for k, r in mine.items():
            state = sip_mandates.state_of(r, today)
            if state == "cancelled":
                continue
            if state == "requested":
                if k not in replaced:          # a request a later registration replaced is counted there, once
                    got["pending"] += 1
                continue
            if state in ("failed", "rejected"):
                got["failed"] += 1
                continue
            got["registered"] += 1
            debit = first.get(k)
            if debit is None:
                got["first_not_recorded"] += 1
            else:
                got["first_confirmed" if debit.outcome == "confirmed" else "first_failed"] += 1
        got["registration_pct"] = _pct(got["registered"], got["registered"] + got["failed"])
        got["first_debit_pct"] = _pct(got["first_confirmed"], got["first_confirmed"] + got["first_failed"])
        data[key] = got
        known = bool(mine)
        rows.append([label, *(str(got[k]) if known else NOT_ASSESSED for k in ("registered", "failed", "pending")), _said(got["registration_pct"], "%"),
                     f"{got['first_confirmed']} of {got['first_confirmed'] + got['first_failed']}" if got["first_confirmed"] + got["first_failed"] else NOT_ASSESSED,
                     _said(got["first_debit_pct"], "%")])
    sums = {k: sum(d[k] for d in data.values()) for k in ("registered", "failed", "pending", "first_confirmed", "first_failed", "first_not_recorded")}
    queue = await india_money.queue(session, firm)
    overdue = sum(1 for q in queue if q.get("overdue"))
    data["all"] = {**sums, "registration_pct": _pct(sums["registered"], sums["registered"] + sums["failed"]),
                   "first_debit_pct": _pct(sums["first_confirmed"], sums["first_confirmed"] + sums["first_failed"]), "queue": len(queue), "past_chase_line": overdue}
    chips = [f"{len(queue)} in the mandate queue, {overdue} past the chase line", f"{sums['first_not_recorded']} registered with no first debit recorded"]
    return _metric("mandates", "Mandate and first-debit success", "Registrations succeeded out of those decided, and first debits confirmed out of those recorded",
                   assessed=bool(latest), count=f"{sums['registered']} of {sums['registered'] + sums['failed']} registered; "
                   f"{sums['first_confirmed']} of {sums['first_confirmed'] + sums['first_failed']} first debit(s) confirmed",
                   columns=["Rail", "Registered", "Failed", "Pending", "Registration", "First debit", "Rate"], rows=rows, data=data, chips=chips,
                   view="/studio/money-in")


# ── 6: rejects by cause ──────────────────────────────────────────────────────────────────────────────────────────
async def rejects(session: AsyncSession, firm: Firm, cases: list, transfers: list) -> dict:
    from app.models.consolidation import ConsolidationStep
    from app.models.india_money import IndiaCredit
    from app.models.money_in import IncomingDeposit
    from app.models.trading import Order

    sources: dict[str, dict] = {k: {"label": label, "submitted": 0, "rejected": 0, "causes": {}} for k, label in (
        ("orders", "Orders (exchange or fund)"), ("registrar", "Registrar and depository requests"), ("opening", "Account opening"),
        ("transfers", "Transfers"), ("returns", "Money returned by the bank"))}

    def reject(key: str, why: str | None) -> None:
        s = sources[key]
        s["rejected"] += 1
        cause = " ".join(str(why or "").split())[:120] or "No reason recorded"
        s["causes"][cause] = s["causes"].get(cause, 0) + 1

    for o in await _rows(session, Order, firm.id):
        status = str(o.status)
        if status in ("draft", "staged", "cancelled"):
            continue
        sources["orders"]["submitted"] += 1
        if status == "rejected":
            reject("orders", o.rejected_reason)
    for step in await _rows(session, ConsolidationStep, firm.id):
        if step.step == "submitted":
            sources["registrar"]["submitted"] += 1
        elif step.step == "rejected":
            reject("registrar", step.note)
    for c in cases:
        if c.custodian_push_status in ("active", "failed"):
            sources["opening"]["submitted"] += 1
            if c.custodian_push_status == "failed":
                reject("opening", None)
    for t in transfers:
        sources["transfers"]["submitted"] += 1
        if t.status == "failed":
            reject("transfers", t.reject_reason)
    credits = {r.deposit_id: r for r in await _rows(session, IndiaCredit, firm.id)}
    for d in await _rows(session, IncomingDeposit, firm.id):
        sources["returns"]["submitted"] += 1
        if d.returned_on:
            credit = credits.get(d.id)
            reject("returns", (credit.return_reason if credit else None) or d.return_code)
    rows = []
    for s in sources.values():
        s["rate_pct"] = _pct(s["rejected"], s["submitted"])
        if not s["submitted"] and not s["rejected"]:
            rows.append([s["label"], NOT_ASSESSED, NOT_ASSESSED, NOT_ASSESSED])
            continue
        if not s["causes"]:
            rows.append([s["label"], "None rejected", f"0 of {s['submitted']}", _said(s["rate_pct"], "%")])
        for cause, n in sorted(s["causes"].items(), key=lambda kv: (-kv[1], kv[0])):
            rows.append([s["label"], cause, f"{n} of {s['submitted']}" if s["submitted"] else str(n), _said(s["rate_pct"], "%")])
    submitted = sum(s["submitted"] for s in sources.values())
    rejected = sum(s["rejected"] for s in sources.values())
    return _metric("rejects", "Transaction and registrar rejects", "Rejected out of what was submitted, by the cause recorded; mandate failures are counted under mandates",
                   assessed=submitted > 0 or rejected > 0, count=f"{rejected} of {submitted} rejected", columns=["Where", "Cause", "Rejected", "Rate"], rows=rows,
                   data={**sources, "submitted": submitted, "rejected": rejected, "rate_pct": _pct(rejected, submitted)}, view="/studio/consolidation")


# ── 7: nomination completeness ───────────────────────────────────────────────────────────────────────────────────
async def nomination(session: AsyncSession, firm: Firm) -> dict:
    from app.aurea_core import nomination_audit

    a = await nomination_audit.audit(session, firm, None)
    pct = _pct(a["accounts_covered"], a["accounts_checked"])
    rows = [["Nominees on file", str(a["nominated"])], ["Signed opt-out on file", str(a["opted_out"])], ["Gap", str(len(a["gaps"]))],
            ["Not checked (held by an entity or no holder)", str(len(a["not_checked"]))]]
    return _metric("nomination", "Nomination completeness", "Individually held active accounts with nominees or a signed opt-out on file",
                   assessed=a["accounts_checked"] > 0, count=f"{a['accounts_covered']} of {a['accounts_checked']} covered ({_said(pct, '%')})",
                   columns=["Accounts", "Count"], rows=rows,
                   data={"total": a["accounts_total"], "checked": a["accounts_checked"], "covered": a["accounts_covered"], "nominated": a["nominated"],
                         "opted_out": a["opted_out"], "gaps": len(a["gaps"]), "not_checked": len(a["not_checked"]), "rate_pct": pct},
                   view="/studio/beneficiary-audit")


# ── 8: screening disposition ─────────────────────────────────────────────────────────────────────────────────────
def _log_times(case, party_name: str) -> list[datetime]:
    out = []
    for e in (case.screening_log or []) if case is not None else []:
        if " ".join(str(e.get("party") or "").split()).casefold() == " ".join(party_name.split()).casefold() and e.get("screened_at"):
            try:
                out.append(_aware(datetime.fromisoformat(str(e["screened_at"]))))
            except ValueError:
                continue
    return out


async def screening(session: AsyncSession, firm: Firm, cases: list, now: datetime | None = None) -> dict:
    from app.aurea_core import account_states, aml_review

    now = now or utcnow()
    sla = account_states.POLICY["in_review"][1]
    by_case = {c.id: c for c in cases}
    parties = await _rows(session, OnboardingParty, firm.id)
    decided = await aml_review.decisions_for(session, [p.id for p in parties])
    taken: list[float] = []
    open_ages: list[float] = []
    no_start = alerts = 0
    for p in parties:
        times = ([_aware(p.screened_at)] if p.screened_at else []) + _log_times(by_case.get(p.case_id), p.legal_name)
        firsts: dict = {}
        for r in decided.get(p.id, []):
            if r.kind == "disposition" and r.decision != "needs_info":      # asking for more leaves the hit open
                firsts.setdefault(r.hit_key, r)
        current = {aml_review.hit_key(h) for h in p.screening_hits or []}
        alerts += len(current | set(firsts))
        for r in firsts.values():
            at = _aware(r.created_at)
            before = [t for t in times if t <= at]
            if before:
                taken.append(_days(min(before), at))
            else:
                no_start += 1
        for key in current - set(firsts):
            if times:
                open_ages.append(max(0.0, _days(min(times), now)))
    queue = await aml_review.for_firm(session, firm.id)
    summary = queue["summary"]
    # The delta rescreen backlog the PMLA programme works out (B-390): nothing to say until a list change is recorded.
    changes = (queue.get("screening_lists") or {}).get("changes") or []
    waiting, oldest = (summary.get("rescreen_backlog", 0), summary.get("rescreen_oldest_days")) if changes else (None, None)
    within = sum(1 for d in taken if d <= sla)
    over = sum(1 for d in open_ages if d > sla)
    open_hits = alerts - len(taken) - no_start
    got = _summary(taken)
    screened = sum(1 for p in parties if (p.screening_status or "not_screened") != "not_screened")
    data = {"parties_screened": screened, "alerts": alerts, "timed": len(taken), "within_sla": within, "within_pct": _pct(within, len(taken)),
            "p50_days": got["p50"], "p90_days": got["p90"], "no_start_on_file": no_start, "open": open_hits, "open_over_sla": over, "sla_days": sla,
            "false_positive_pct": summary["false_positive_pct"], "qa_overturn_pct": summary["qa_overturn_pct"],
            "list_changes_recorded": bool(changes), "rescreen_backlog": waiting, "rescreen_oldest_days": oldest}
    rows = [["Alerts", str(alerts)], [f"Decided within {sla} days", f"{within} of {len(taken)}" if taken else NOT_ASSESSED],
            ["Days to decide, P50 and P90", f"{got['p50']:g} d and {got['p90']:g} d" if taken else NOT_ASSESSED],
            ["Open", str(open_hits)], [f"Open over {sla} days", str(over)], ["False-positive share", _said(summary["false_positive_pct"], "%")],
            ["QA overturn share", _said(summary["qa_overturn_pct"], "%")],
            ["Parties not screened since a list change", _said(waiting)], ["Oldest list change not screened", _said(oldest, " d")]]
    chips = [f"{sla}-day line: the platform's SLA for compliance review, not a rule", "Timed from the earliest screening time still on file"]
    if no_start:
        chips.append(f"{no_start} decision(s) with no earlier screening time, not timed")
    if any(c.get("demonstration") for c in changes):
        chips.append("Includes invented demonstration list entries")
    return _metric("screening", "Screening disposition", "Screening hit to the first true match or false positive a person recorded, against the review SLA; the delta rescreen backlog",
                   assessed=screened > 0 or bool(changes), count=f"{alerts} alert(s) over {screened} screened part(ies)", columns=["Measure", "Figure"], rows=rows, data=data,
                   chips=chips, view="/studio/aml")


# ── 9: suspicious-report timeliness, compliance only (B-390) ─────────────────────────────────────────────────────
async def report_timeliness(session: AsyncSession, firm: Firm) -> dict:
    """The PMLA programme's own figure (`fiu_reports.timeliness`), so this and the monitoring page cannot disagree, with the monthly
    reports compliance recorded beside it."""
    from app.aurea_core import fiu_reports, law_figures

    view = await fiu_reports.overview(session, firm)
    got, monthly = view["str"], view["counts"]
    figure = law_figures.get(STR_FIGURE, firm_time.today(firm))
    no_report = sum(1 for i in got["items"] if i["state"] == "no_report")
    data = {**{k: got[k] for k in ("line_working_days", "measured", "in_time", "late", "overdue", "open", "not_measured", "in_time_pct")},
            "no_report": no_report, "monthly": monthly}
    known = bool(got["items"])
    rows = [[label, str(data[k]) if known else NOT_ASSESSED] for k, label in (
        ("in_time", "Filed in time"), ("late", "Filed late"), ("overdue", "Not filed, overdue"), ("open", "Not filed, still within time"),
        ("no_report", "Closed without a report"), ("not_measured", "Filed with no day of suspicion, not measured"))]
    recorded = bool(view["reports"])
    rows += [[f"Monthly reports: {label}", str(monthly[k]) if recorded else NOT_ASSESSED] for k, label in (
        ("filed", "filed in time"), ("filed_late", "filed late"), ("overdue", "overdue"), ("due", "not yet due"))]
    return _metric("str_timeliness", "Suspicious-report timeliness",
                   f"STRs filed within {int(figure.value)} working days of the recorded day suspicion was formed; monthly reports to FIU-IND",
                   assessed=known,
                   count=f"{got['in_time']} of {got['measured']} in time" if got["measured"] else f"{len(got['items'])} determination(s), none due or filed",
                   columns=["Reports", "Count"], rows=rows, data=data,
                   chips=["Compliance only", f"{figure.value:g} working days: assumed, not reviewed", "Weekdays only, holidays not modelled"],
                   view="/studio/monitoring")


async def measures(session: AsyncSession, firm: Firm, role) -> dict:
    """The module's measures for an Indian firm, in its order. `role` decides whether the report measure is in the reading at all."""
    cases = await _rows(session, OnboardingCase, firm.id)
    transfers = await _rows(session, TransferRequest, firm.id)
    touches = await touches_by_case(session, firm.id, cases, transfers)
    mix, backlog = await registry_measures(session, firm)
    out = [cycle_time(cases), straight_through(cases, touches), mix, await verification(session, firm), await mandates(session, firm),
           await rejects(session, firm, cases, transfers), await nomination(session, firm), await screening(session, firm, cases)]
    if role is not None and UserRole(role) in STR_READERS:
        out.append(await report_timeliness(session, firm))
    out.append(backlog)
    today: date = firm_time.today(firm)
    return {"as_of": today.isoformat(), "metrics": out, "assessed": sum(1 for m in out if m["assessed"]), "officers": officers(firm),
            "note": "Counted from what is on file. A measure with nothing recorded is not assessed; this does not say the firm meets any rule."}


def said(india: dict) -> str:
    """The measures in a few lines for the conversation."""
    return " ".join(f"{m['label']}: {m['count']}." for m in india["metrics"])
