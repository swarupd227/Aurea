"""Identity and bank checks, with a defined path for failure (B-387, L200-1 India §5.2 and §7).

Three checks, each with its failure path settled before anything runs:

    pan    PAN verification; the alternative is a person checking the PAN card against the income-tax portal
    bank   a penny drop: the account exists and the bank's name for it matches, scored; the alternative is a person reading a
           cancelled cheque or bank statement
    vipv   video in-person verification; the alternative is an in-person verification by an authorised person

A connected provider is asked first, at most `MAX_ATTEMPTS` times. No real provider is connected and none is implied: the only one is
the simulation (`app/simulations/identity_checks`), and with none the check is done by hand. An outcome is passed, failed, review (a
name match a person must read) or unavailable (no answer). A check is passed only where its latest attempt passed. A failure with
retries left may be retried or done by hand; a review, or a check whose retries are used up, goes to the human queue and the
provider is not asked again; only operations or compliance record the result by hand, from the document. A failed or waiting check
blocks the activation of its case, and one never run says "not run", never passed. Only a PAN's masked form and an account's last
four digits are kept. Nothing here changes a bank link, a KYC record or an account, or sends anything.

The name match is scored from 0 to 100 on how the names read (`name_score`). The pass and review thresholds are the platform's own
assumed tolerances, not a rule of any bank, regulator or provider.
"""
from __future__ import annotations

import difflib
import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, india_money, kyc_india, kyc_registry as registry, pan as pans
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.identity_checks import VerificationAttempt
from app.models.money_in import BankLink
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.tenant import Firm
from app.provenance import ledger

RUNNERS = kyc_india.RECORDERS
DECIDERS = frozenset({UserRole.OPERATIONS, UserRole.COMPLIANCE})
MAX_ATTEMPTS = 3
PASS_SCORE, REVIEW_SCORE = 85, 60
QUEUE_OWNER = "Operations (KYC team)"
CHECKS = {
    "pan": {"label": "PAN verification", "primary": "PAN verification service",
            "alternative": "A person checks the PAN card against the income-tax portal"},
    "bank": {"label": "Bank account: penny drop and name match", "primary": "Penny-drop service",
             "alternative": "A person reads a cancelled cheque or bank statement"},
    "vipv": {"label": "Video in-person verification", "primary": "Video verification service",
             "alternative": "In-person verification by an authorised person"},
}
OUTCOMES = {"passed": "Passed", "failed": "Failed", "review": "Needs a person", "unavailable": "No answer"}
REASONS = {
    "account_not_found": "No account with these details", "name_mismatch": "The name does not match", "name_partial": "The name matches only in part",
    "no_name": "No name was returned", "pan_invalid": "PAN not found", "pan_inoperative": "PAN inoperative", "liveness_failed": "Liveness check failed",
    "location_outside_india": "Location outside India", "document_unreadable": "Document not readable", "face_mismatch": "Face does not match the document",
    "no_answer": "The service did not answer",
}
STATES = {"not_run": "Not run", "passed": "Passed", "passed_alternative": "Passed by hand", "retry": "Failed: retry or check by hand",
          "review": "In the queue: name needs a person", "queued": "In the queue: retries used up", "failed": "Failed by hand"}
PASSED = ("passed", "passed_alternative")
QUEUED = ("review", "queued", "failed")
ORDER = ("failed", "review", "queued", "retry", "passed_alternative", "passed", "not_run")
NOT_CONNECTED = ("No PAN, penny-drop or video verification service is connected (it needs a vendor, B-323): a person checks the document "
                 "and records the result (record_verification_result). Nothing was checked.")
THRESHOLD_NOTE = (f"Name match: {PASS_SCORE} or more passes, {REVIEW_SCORE} to {PASS_SCORE - 1} goes to a person, less fails. These "
                  "tolerances are the platform's own assumption, not a bank's or a regulator's.")
NOTE = "A check is passed only where its latest attempt passed. Nothing here changes a bank link, a KYC record or an account."

PROVIDERS: dict[str, object] = {}


class CheckError(ValueError):
    """This check cannot be run or recorded, with the reason."""


def register(provider) -> None:
    PROVIDERS[provider.key] = provider


def provider_for(firm: Firm):
    for key in sorted(PROVIDERS):
        if PROVIDERS[key].enabled_for(firm):
            return PROVIDERS[key]
    return None


def applies(firm: Firm | None) -> bool:
    return kyc_india.applies(firm)


# ── The name match ──────────────────────────────────────────────────────────────────────────────────────────────
def name_score(shown: str | None, name: str | None) -> dict:
    """How a name a bank or a registry shows reads against the party's, 0 to 100, with what makes it so. Initials are never a
    pass: "R Sharma" could be anyone whose name begins with R."""
    a, b = india_money._tokens(shown or ""), india_money._tokens(name or "")
    if not a or not b:
        return {"score": 0, "reading": "No name to compare"}
    reading = india_money.match(shown or "", name or "")
    if sorted(a) == sorted(b):
        return {"score": 100, "reading": "The same name"}
    if reading == "full":
        return {"score": 90, "reading": "A middle name or initial differs"}
    if reading == "initials":
        return {"score": 65, "reading": "By initials only: it could be somebody else"}
    if len(a) == len(b) and len(a) >= 2:
        ratios = [difflib.SequenceMatcher(None, x, y).ratio() for x, y in zip(a, b)]
        mean = sum(ratios) / len(ratios)
        if all(x[0] == y[0] for x, y in zip(a, b)) and min(ratios) >= 0.6 and mean >= 0.8:
            return {"score": min(PASS_SCORE - 1, max(REVIEW_SCORE, round(mean * 85))),
                    "reading": "The spelling differs, as a transliteration may"}
    if set(a) & set(b):
        shared = "surname" if a[-1] == b[-1] else "one part of the name"
        return {"score": 35, "reading": f"Only the {shared} matches"}
    whole = difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    return {"score": min(REVIEW_SCORE - 1, round(whole * 50)), "reading": "A different name"}


def evaluate(kind: str, answer: dict, name: str) -> dict:
    """A provider's answer as an outcome. Anything short of a clear pass is not one."""
    result = answer.get("result")
    if result == "unavailable":
        return {"outcome": "unavailable", "reason": "no_answer", "score": None, "name_shown": None}
    shown = " ".join(str(answer.get("name") or "").split())[:200] or None
    if result != "ok":
        reason = answer.get("reason") if answer.get("reason") in REASONS else None
        return {"outcome": "failed", "reason": reason, "score": None, "name_shown": shown}
    if kind == "vipv":
        return {"outcome": "passed", "reason": None, "score": None, "name_shown": None}
    if not shown:
        return {"outcome": "failed", "reason": "no_name", "score": None, "name_shown": None}
    score = name_score(shown, name)["score"]
    if score >= PASS_SCORE:
        return {"outcome": "passed", "reason": None, "score": score, "name_shown": shown}
    if score >= REVIEW_SCORE:
        return {"outcome": "review", "reason": "name_partial", "score": score, "name_shown": shown}
    return {"outcome": "failed", "reason": "name_mismatch", "score": score, "name_shown": shown}


def state_of(attempts: list) -> dict:
    """Where one check stands, from its attempts oldest first. Pure."""
    if not attempts:
        return {"state": "not_run", "label": STATES["not_run"], "tries": 0, "since": None}
    last = attempts[-1]
    by_hand = [i for i, a in enumerate(attempts) if a.method == "alternative"]
    tries = sum(1 for a in attempts[(by_hand[-1] + 1 if by_hand else 0):] if a.method == "provider")
    if last.outcome == "passed":
        state = "passed" if last.method == "provider" else "passed_alternative"
    elif last.method == "alternative":
        state = "failed"
    elif last.outcome == "review":
        state = "review"
    else:
        state = "queued" if tries >= MAX_ATTEMPTS else "retry"
    passed = [i for i, a in enumerate(attempts) if a.outcome == "passed"]
    open_from = attempts[passed[-1] + 1:] if passed else attempts
    return {"state": state, "label": STATES[state], "tries": tries, "since": open_from[0] if state not in PASSED and open_from else None}


# ── Reading ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def _grouped(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every attempt by (party, kind, account), oldest first."""
    rows = list((await session.execute(select(VerificationAttempt).where(VerificationAttempt.firm_id == firm_id)
                                       .order_by(VerificationAttempt.created_at))).scalars().all())
    key = await registry._keys(session, firm_id, rows)
    out: dict = {}
    for r in rows:
        out.setdefault((key(r), r.kind, r.last4 if r.kind == "bank" else None), []).append(r)
    return out


def _row(attempts: list, today: date) -> dict:
    last = attempts[-1]
    st = state_of(attempts)
    since = registry._aware(st["since"].created_at).date() if st["since"] is not None else None
    can_retry = st["state"] == "retry"
    return {"party": last.party_name, "person_id": str(last.person_id) if last.person_id else None,
            "entity_id": str(last.entity_id) if last.entity_id else None, "case_id": str(last.case_id) if last.case_id else None,
            "kind": last.kind, "kind_label": CHECKS[last.kind]["label"], "state": st["state"], "state_label": st["label"],
            "passed": st["state"] in PASSED, "queued": st["state"] in QUEUED, "outcome": OUTCOMES[last.outcome],
            "reason": REASONS.get(last.reason) if last.reason else None, "score": last.score, "name_shown": last.name_shown,
            "bank": last.bank, "last4": last.last4, "pan": last.pan_masked, "tries": st["tries"], "max_tries": MAX_ATTEMPTS,
            "since": since.isoformat() if since else None, "age_days": max(0, (today - since).days) if since else None,
            "can_retry": can_retry, "alternative": CHECKS[last.kind]["alternative"], "owner": QUEUE_OWNER if st["state"] in QUEUED else None,
            "simulated": bool(last.source_system),
            "history": [{"on": registry._aware(a.created_at).date().isoformat(), "method": a.method, "outcome": OUTCOMES[a.outcome],
                         "reason": REASONS.get(a.reason) if a.reason else None, "score": a.score, "by": a.recorded_by, "document": a.document,
                         "simulated": bool(a.source_system)} for a in attempts]}


async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None, case_id: uuid.UUID | None = None) -> dict:
    """The human queue, the checks with retries left and the ones passed, for the firm, one household or one case."""
    if not applies(firm):
        return {"applies": False}
    today = firm_time.today(firm)
    grouped = await _grouped(session, firm.id)
    case = None
    if case_id is not None:
        case = await session.get(OnboardingCase, case_id)
        if case is None or case.firm_id != firm.id:
            raise CheckError("No such onboarding case at this firm.")
        grouped = _of_case(grouped, case)
    if household_id is not None:
        home = await session.get(Household, household_id)
        if home is None or home.firm_id != firm.id:
            raise CheckError("No such household at this firm.")
        mine = {("person", i) for (i,) in (await session.execute(select(Person.id).where(Person.household_id == household_id))).all()}
        mine |= {("entity", i) for (i,) in (await session.execute(select(LegalEntity.id).where(LegalEntity.household_id == household_id))).all()}
        grouped = {k: v for k, v in grouped.items() if k[0] in mine}
    rows = sorted((_row(v, today) for v in grouped.values()),
                  key=lambda r: (ORDER.index(r["state"]), -(r["age_days"] or 0), r["party"]))
    p = provider_for(firm)
    queue = [r for r in rows if r["queued"]]
    return {"applies": True, "as_of": today.isoformat(), "household_id": str(household_id) if household_id else None,
            "case_id": str(case_id) if case_id else None, "case": case.prospect_name if case else None, "checks": rows,
            "queue": queue, "counts": {"queued": len(queue), "retry": sum(1 for r in rows if r["state"] == "retry"),
                                       "passed": sum(1 for r in rows if r["passed"])},
            "defined": [{"kind": k, **v, "tries": MAX_ATTEMPTS, "then": QUEUE_OWNER} for k, v in CHECKS.items()],
            "provider": ({"key": p.key, "name": p.name, "simulated": bool(getattr(p, "simulated", False))} if p else None),
            "connected": p is not None, "not_connected": None if p else NOT_CONNECTED, "thresholds": THRESHOLD_NOTE, "note": NOTE}


def _of_case(grouped: dict, case: OnboardingCase) -> dict:
    created = kyc_india._created_by(case)
    return {k: v for k, v in grouped.items() if k[0] in created or (k[0][0] == "case" and k[0][1] == case.id) or any(a.case_id == case.id for a in v)}


async def case_status(session: AsyncSession, firm: Firm, case: OnboardingCase) -> dict | None:
    """The checks run for a case's parties, and which parties have none: what the activation gate and the KYC queue read."""
    if not applies(firm):
        return None
    from app.agents.cip_identity_verifier import CIP_ROLES

    today = firm_time.today(firm)
    rows = [_row(v, today) for v in _of_case(await _grouped(session, firm.id), case).values()]
    parties = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all()
    asked = {" ".join(p.legal_name.split()).casefold(): p.legal_name for p in parties if p.role in CIP_ROLES | kyc_india.ALSO_ASKED}
    if case.is_entity or not asked:
        asked.setdefault(" ".join(case.prospect_name.split()).casefold(), case.prospect_name)
    not_run = [f"{CHECKS[k]['label'].split(':')[0]} for {name}" for n, name in sorted(asked.items()) for k in CHECKS
               if not any(r["kind"] == k and " ".join(r["party"].split()).casefold() == n for r in rows)]
    return {"checks": rows, "not_run": not_run}


def _line(r: dict) -> str:
    account = f" (account ending {r['last4']})" if r["last4"] else ""
    why = f", {r['reason'].lower()}" if r["reason"] else ""
    return f"{r['kind_label'].split(':')[0]} for {r['party']}{account}: {r['state_label'].lower()}{why}"


def gate(status: dict | None) -> dict | None:
    """The activation gate: None where no check was run for the case; fail while any check run has not passed."""
    if not status or not status["checks"]:
        return None
    bad = [r for r in status["checks"] if not r["passed"]]
    if bad:
        return {"state": "fail", "detail": "; ".join(_line(r) for r in bad)}
    rest = f" Not run: {', '.join(status['not_run'][:6])}." if status["not_run"] else ""
    return {"state": "pass", "detail": f"{len(status['checks'])} check(s) passed.{rest}"}


def queue_lines(status: dict | None) -> list[str]:
    """What the board's KYC queue says for a case: every check run that has not passed."""
    return [_line(r) for r in (status or {}).get("checks", []) if not r["passed"]]


# ── Running and recording ───────────────────────────────────────────────────────────────────────────────────────
async def _target(session: AsyncSession, firm: Firm, *, kind, person_id, entity_id, case_id, party_name, bank_link_id, bank, last4) -> dict:
    kind = str(kind or "").strip().lower()
    if kind not in CHECKS:
        raise CheckError(f"The check is one of {', '.join(CHECKS)}.")
    try:
        party = await registry._party(session, firm, person_id=person_id, entity_id=entity_id, case_id=case_id, party_name=party_name)
    except registry.RegistryError as exc:
        raise CheckError(str(exc))
    link = None
    digits = None
    named = None
    if kind == "bank":
        if bank_link_id:
            try:
                link = await session.get(BankLink, uuid.UUID(str(bank_link_id)))
            except ValueError:
                raise CheckError("Give the registered bank account's id from read_money_in.")
            account = await session.get(Account, link.account_id) if link is not None and link.firm_id == firm.id else None
            mandate = await session.get(Mandate, account.mandate_id) if account is not None else None
            holder = party["holder"]
            owns = mandate is not None and holder is not None and (
                mandate.person_id == holder.id or mandate.entity_id == holder.id
                or (party["household"] is not None and await _mandate_home(session, mandate) == party["household"].id))
            if not owns:
                raise CheckError("That bank account is not registered on an account of this client's household.")
            digits, named = link.last4, link.bank
        else:
            digits = re.sub(r"\s", "", str(last4 or ""))
            if not re.fullmatch(r"\d{4}", digits):
                raise CheckError("For a bank check give the registered bank_link_id, or the bank and the account's last four digits only.")
            try:
                named = registry._text(bank, "the bank's name", 120, required=True)
            except registry.RegistryError as exc:
                raise CheckError(str(exc))
    key = (party["key"], kind, digits)
    attempts = (await _grouped(session, firm.id)).get(key, [])
    return {**party, "kind": kind, "link": link, "last4": digits, "bank": named, "attempts": attempts, "state": state_of(attempts)}


async def _mandate_home(session: AsyncSession, mandate: Mandate) -> uuid.UUID | None:
    if mandate.person_id:
        p = await session.get(Person, mandate.person_id)
        return p.household_id if p else None
    if mandate.entity_id:
        e = await session.get(LegalEntity, mandate.entity_id)
        return e.household_id if e else None
    return None


async def prepare_run(session: AsyncSession, firm: Firm, role, *, kind=None, person_id=None, entity_id=None, case_id=None, party_name=None,
                      bank_link_id=None, bank=None, last4=None, pan=None) -> dict:
    """Every check a provider attempt makes, nothing asked and nothing written."""
    if UserRole(role) not in RUNNERS:
        raise CheckError("A check is run by an adviser, a paraplanner, operations or compliance.")
    t = await _target(session, firm, kind=kind, person_id=person_id, entity_id=entity_id, case_id=case_id, party_name=party_name,
                      bank_link_id=bank_link_id, bank=bank, last4=last4)
    state = t["state"]["state"]
    if state in QUEUED:
        raise CheckError(f"That check is with a person ({t['state']['label'].lower()}): {QUEUE_OWNER} record the result by hand from the "
                         "document (record_verification_result). The service is not asked again.")
    if state in PASSED:
        raise CheckError("That check has already passed: there is nothing to retry.")
    provider = provider_for(firm)
    if provider is None:
        raise CheckError(NOT_CONNECTED)
    number = None
    if t["kind"] == "pan":
        number = pans.normalise(pan) or (t["holder"].pan if t["holder"] is not None else None)
        if not number or not pans.PATTERN.match(number):
            raise CheckError("No PAN to verify: none is on file for them, so give it as the PAN card shows it (five letters, four digits, a letter).")
    return {**t, "provider": provider, "pan": number, "attempt": t["state"]["tries"] + 1}


async def run(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    """Ask the connected service once and record what it answered."""
    p = await prepare_run(session, firm, role, **terms)
    provider = p["provider"]
    answer = await provider.check(session, firm, kind=p["kind"], name=p["name"], attempt=p["attempt"], bank_link=p["link"], last4=p["last4"],
                                  pan=p["pan"]) or {"result": "unavailable"}
    got = evaluate(p["kind"], answer, p["name"])
    simulated = bool(getattr(provider, "simulated", False))
    return await _write(session, firm, p, by=by, method="provider", reference=str(answer.get("reference") or "")[:120] or None,
                        pan_masked=pans.mask(p["pan"]), source_system=provider.key if simulated else None, **got)


async def prepare_result(session: AsyncSession, firm: Firm, role, *, kind=None, person_id=None, entity_id=None, case_id=None, party_name=None,
                         bank_link_id=None, bank=None, last4=None, outcome=None, document=None, name_on_document=None, note=None) -> dict:
    """The alternative method: a person's result from the document. Every check, nothing written."""
    if UserRole(role) not in DECIDERS:
        raise CheckError("A result checked by hand is recorded by operations or compliance.")
    t = await _target(session, firm, kind=kind, person_id=person_id, entity_id=entity_id, case_id=case_id, party_name=party_name,
                      bank_link_id=bank_link_id, bank=bank, last4=last4)
    outcome = str(outcome or "").strip().lower()
    if outcome not in ("passed", "failed"):
        raise CheckError("A result checked by hand is passed or failed.")
    if t["state"]["state"] in PASSED and outcome == "passed":
        raise CheckError("That check has already passed.")
    try:
        document = registry._text(document, "which document was checked and where it is filed", 300, required=True)
        shown = registry._text(name_on_document, "the name on the document", 200)
        note = registry._text(note, "the note", 300)
    except registry.RegistryError as exc:
        raise CheckError(str(exc))
    score = None
    if t["kind"] != "vipv" and shown:
        score = name_score(shown, t["name"])
    if outcome == "passed" and t["kind"] != "vipv":
        if not shown:
            raise CheckError("Say the name exactly as the document shows it: a pass rests on it.")
        if score["score"] < REVIEW_SCORE:
            raise CheckError(f"'{shown}' does not read as {t['name']} ({score['reading'].lower()}, {score['score']} of 100): this cannot be recorded "
                             "as passed.")
        if score["score"] < PASS_SCORE and not note:
            raise CheckError(f"'{shown}' reads as {t['name']} only in part ({score['reading'].lower()}). Say in the note what on the document "
                             "shows it is the same holder.")
    return {**t, "outcome": outcome, "document": document, "name_shown": shown, "score": score["score"] if score else None,
            "reading": score["reading"] if score else None, "note": note}


async def record_result(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_result(session, firm, role, **terms)
    return await _write(session, firm, p, by=by, method="alternative", outcome=p["outcome"], reason=None, score=p["score"],
                        name_shown=p["name_shown"], document=p["document"], note=p["note"])


async def _write(session: AsyncSession, firm: Firm, p: dict, *, by: str, method: str, outcome: str, reason=None, score=None, name_shown=None,
                 document=None, note=None, reference=None, pan_masked=None, source_system=None) -> dict:
    row = VerificationAttempt(firm_id=firm.id, **p["ids"], party_name=p["name"], kind=p["kind"], method=method, outcome=outcome, reason=reason,
                              score=score, name_shown=name_shown, bank=p["bank"], last4=p["last4"], pan_masked=pan_masked, reference=reference,
                              document=document, note=note, recorded_by=by, source_system=source_system, created_at=utcnow())
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="identity_check_attempt", actor=by, content={
        "party": row.party_name, "check": row.kind, "method": method, "outcome": outcome, "reason": reason, "score": score,
        "simulated": bool(source_system)})
    st = state_of([*p["attempts"], row])
    if p["case"] is not None:
        view = await overview(session, firm, case_id=p["case"].id)
    else:
        view = await overview(session, firm, household_id=p["household"].id if p["household"] else None)
    return {**view, "attempted": {"party": row.party_name, "kind": row.kind, "check": CHECKS[row.kind]["label"], "outcome": outcome,
                                  "reason": REASONS.get(reason) if reason else None, "score": score, "state": st["state"], "state_label": st["label"],
                                  "tries": st["tries"], "simulated": bool(source_system), "alternative": CHECKS[row.kind]["alternative"]}}
