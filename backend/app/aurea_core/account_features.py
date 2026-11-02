"""Account features, and temporary holds on suspected financial exploitation (L200-1 §4).

Margin, an options level and checkwriting are each asked for by the adviser, on the client's signed agreement, and
decided by a principal - compliance or a branch leader - who is not the person who asked. A retirement or custodial
account takes no margin and no checkwriting, and options in one stop at the firm's ceiling (level 2 unless the firm
sets another); uncovered options (level 4) are never in one. What the client said about their experience and
objective is recorded as they said it; this module does not decide suitability, the principal does.

A temporary hold under FINRA Rule 2165 is placed by compliance or a branch leader on the account of a specified adult
(65 or older from the date of birth on file, or an adult with an impairment someone recorded), with the reason the
firm believes they are being exploited. It lasts 15 business days, may be extended by 10 after an internal review,
and by 30 more once the matter has been reported to a state agency or a court, and it stops every new payment out of
the account. The trusted contacts and everyone authorised on the account are told within two business days, unless
they are the one suspected; a hold past that with nobody told says so. A US rule: a firm in another market is told
there is none here for it.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.aurea_core.billing import _mandate_households
from app.models.account_features import AccountFeature, SeniorHold
from app.models.client_file import TrustedContact
from app.models.graph import Account, Household, Mandate, Person
from app.models.tenant import Firm
from app.provenance import ledger

FEATURES = {"margin": "Margin", "options": "Options", "checkwriting": "Checkwriting"}
OPTION_LEVELS = {1: "Covered calls and protective puts", 2: "Buying calls and puts, cash-secured puts", 3: "Spreads", 4: "Uncovered writing"}
OBJECTIVES = ("income", "growth", "hedging", "speculation")
RETIREMENT = {"traditional_ira", "roth_ira", "sep_ira", "simple_ira", "employer_rollover", "inherited_ira", "hsa"}
CUSTODIAL = {"custodial_utma", "custodial_ugma", "plan_529"}
HOLD_DAYS, FIRST_EXTENSION, SECOND_EXTENSION, NOTIFY_DAYS = 15, 10, 30, 2


class FeatureError(ValueError):
    """This cannot be recorded, with the reason."""


def business_days_after(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _ira_ceiling(firm: Firm) -> int:
    return int((firm.settings or {}).get("ira_max_option_level") or 2)


async def _account(session: AsyncSession, firm_id: uuid.UUID, account_id: uuid.UUID) -> Account:
    a = await session.get(Account, account_id)
    if a is None or a.firm_id != firm_id:
        raise FeatureError("Account not found.")
    return a


async def _owner(session: AsyncSession, account: Account) -> Person | None:
    mandate = await session.get(Mandate, account.mandate_id) if account.mandate_id else None
    return await session.get(Person, mandate.person_id) if mandate and mandate.person_id else None


def hold_state(h: SeniorHold, today: date) -> str:
    if h.status == "released":
        return "released"
    return "active" if today <= h.expires_on else "expired"


async def active_hold(session: AsyncSession, firm: Firm, account_id: uuid.UUID) -> SeniorHold | None:
    today = firm_time.today(firm)
    for h in (await session.execute(select(SeniorHold).where(SeniorHold.account_id == account_id, SeniorHold.status == "active"))).scalars().all():
        if hold_state(h, today) == "active":
            return h
    return None


def _hold_view(h: SeniorHold, account: Account, client: str | None, contacts: list[str], today: date) -> dict:
    steps = h.steps or []
    notified = next((s for s in steps if s["step"] == "notify"), None)
    notify_by = business_days_after(h.placed_on, NOTIFY_DAYS)
    return {"id": str(h.id), "account_id": str(account.id), "account": account.name, "client": client, "state": hold_state(h, today),
            "placed_on": h.placed_on.isoformat(), "expires_on": h.expires_on.isoformat(), "adult_basis": h.adult_basis,
            "extensions": sum(1 for s in steps if s["step"] == "extend"), "notified": notified, "notify_by": notify_by.isoformat(),
            "notice_late": notified is None and today > notify_by and hold_state(h, today) == "active", "trusted_contacts": contacts, "steps": steps}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id))).scalars().all()
    owners = await _mandate_households(session, firm_id, list(mandates))
    by_mandate = {m.id: m for m in mandates}
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.firm_id == firm_id))).scalars().all()
                if household_id is None or owners.get(a.mandate_id) == household_id}
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    ids = list(accounts)
    features = (await session.execute(select(AccountFeature).where(AccountFeature.account_id.in_(ids)))).scalars().all() if ids else []
    holds = (await session.execute(select(SeniorHold).where(SeniorHold.account_id.in_(ids)))).scalars().all() if ids else []
    contacts: dict = {}
    for h in holds:
        m = by_mandate.get(accounts[h.account_id].mandate_id)
        if m and m.person_id and m.person_id not in contacts:
            contacts[m.person_id] = [f"{c.name} ({c.relationship})" for c in (await session.execute(
                select(TrustedContact).where(TrustedContact.person_id == m.person_id))).scalars().all()]
    client = lambda aid: names.get(owners.get(accounts[aid].mandate_id))  # noqa: E731
    person_of = lambda aid: getattr(by_mandate.get(accounts[aid].mandate_id), "person_id", None)  # noqa: E731
    return {"features": [{"id": str(f.id), "account": accounts[f.account_id].name, "client": client(f.account_id), "feature": f.feature,
                          "label": FEATURES[f.feature], "level": f.level, "level_label": OPTION_LEVELS.get(f.level) if f.level else None, "status": f.status,
                          "agreement": f.agreement, "profile": f.profile, "requested_by": f.requested_by, "steps": f.steps or []} for f in features],
            "holds": [_hold_view(h, accounts[h.account_id], client(h.account_id),
                                 contacts.get(person_of(h.account_id), []), today) for h in holds],
            "summary": {"waiting": sum(1 for f in features if f.status == "requested"),
                        "holds_active": sum(1 for h in holds if hold_state(h, today) == "active"),
                        "notices_late": sum(1 for h in holds if _hold_view(h, accounts[h.account_id], None, [], today)["notice_late"])},
            "rules": {"ira_max_option_level": _ira_ceiling(firm), "senior_holds": markets.code(firm.jurisdiction) == "US"},
            "as_of": today.isoformat(), "household_id": str(household_id) if household_id else None}


# ── Features ──────────────────────────────────────────────────────────────────

async def prepare_request(session: AsyncSession, firm_id: uuid.UUID, *, account_id: uuid.UUID, feature: str | None, level=None, agreement: str | None,
                          experience_years=None, objective: str | None = None) -> dict:
    account = await _account(session, firm_id, account_id)
    firm = await session.get(Firm, firm_id)
    feature = (feature or "").strip().lower()
    if feature not in FEATURES:
        raise FeatureError(f"The feature is one of {', '.join(FEATURES)}.")
    reg = account.registration_type or "individual"
    if feature in ("margin", "checkwriting") and (reg in RETIREMENT or reg in CUSTODIAL):
        raise FeatureError(f"A {reg.replace('_', ' ')} account takes no {feature}: "
                           + ("it cannot borrow against itself." if feature == "margin" else "money leaves it as a distribution, with its withholding."))
    lvl, profile = None, {}
    if feature == "options":
        try:
            lvl = int(level)
        except (TypeError, ValueError):
            raise FeatureError("Give the options level asked for, 1 to 4.")
        if lvl not in OPTION_LEVELS:
            raise FeatureError("Give the options level asked for, 1 to 4.")
        if reg in RETIREMENT or reg in CUSTODIAL:
            ceiling = 1 if reg in CUSTODIAL else _ira_ceiling(firm)
            if lvl > ceiling:
                raise FeatureError(f"A {reg.replace('_', ' ')} account goes no higher than options level {ceiling}.")
        if (objective or "").strip().lower() not in OBJECTIVES:
            raise FeatureError(f"Record the objective the client gave on the options agreement: one of {', '.join(OBJECTIVES)}.")
        try:
            years = int(experience_years)
        except (TypeError, ValueError):
            raise FeatureError("Record the client's years of options experience, as they gave it on the agreement.")
        profile = {"experience_years": years, "objective": objective.strip().lower()}
    if len((agreement or "").strip()) < 5:
        raise FeatureError("Name the signed agreement it rests on: the margin agreement, the options agreement, the signature card.")
    open_one = (await session.execute(select(AccountFeature).where(AccountFeature.account_id == account.id, AccountFeature.feature == feature,
                                                                   AccountFeature.status.in_(("requested", "approved"))))).scalars().first()
    if open_one and not (feature == "options" and open_one.status == "approved" and lvl != open_one.level):
        raise FeatureError(f"{FEATURES[feature]} is already {open_one.status} on this account.")
    return {"account": account, "feature": feature, "level": lvl, "agreement": agreement.strip(), "profile": profile, "replaces": open_one}


async def request(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_request(session, firm_id, **terms)
    session.add(AccountFeature(firm_id=firm_id, account_id=r["account"].id, feature=r["feature"], level=r["level"], agreement=r["agreement"],
                               profile=r["profile"], requested_by=by, steps=[]))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="account_feature_requested", actor=by,
                              content={"account_id": str(r["account"].id), "feature": r["feature"], "level": r["level"]})
    return await for_firm(session, firm_id)


async def prepare_decision(session: AsyncSession, firm_id: uuid.UUID, *, feature_id: uuid.UUID, decision: str | None, reason: str | None, by: str) -> dict:
    f = await session.get(AccountFeature, feature_id)
    if f is None or f.firm_id != firm_id:
        raise FeatureError("Feature request not found.")
    decision = (decision or "").strip().lower()
    if decision not in ("approve", "decline", "remove"):
        raise FeatureError("The decision is approve, decline or remove.")
    if decision in ("approve", "decline") and f.status != "requested":
        raise FeatureError(f"This is already {f.status}.")
    if decision == "remove" and f.status != "approved":
        raise FeatureError("Only an approved feature is removed.")
    if decision == "approve" and f.requested_by == by:
        raise FeatureError("The person who asked for a feature cannot also approve it.")
    if len((reason or "").strip()) < 10:
        raise FeatureError("Give the reason, as the principal's record of review.")
    return {"feature": f, "decision": decision, "reason": reason.strip(), "account": await session.get(Account, f.account_id)}


async def decide(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_decision(session, firm_id, by=by, **terms)
    f = r["feature"]
    today = firm_time.today(await session.get(Firm, firm_id)).isoformat()
    if r["decision"] == "approve" and f.feature == "options":
        for old in (await session.execute(select(AccountFeature).where(AccountFeature.account_id == f.account_id, AccountFeature.feature == "options",
                                                                       AccountFeature.status == "approved", AccountFeature.id != f.id))).scalars().all():
            old.status = "removed"
            old.steps = [*(old.steps or []), {"step": "replaced", "by": by, "on": today, "note": f"Level {f.level} approved"}]
    f.status = {"approve": "approved", "decline": "declined", "remove": "removed"}[r["decision"]]
    f.steps = [*(f.steps or []), {"step": r["decision"], "by": by, "on": today, "note": r["reason"]}]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="account_feature_decided", actor=by,
                              content={"feature_id": str(f.id), "decision": r["decision"]})
    return await for_firm(session, firm_id)


# ── Senior holds (FINRA Rule 2165) ────────────────────────────────────────────

async def prepare_hold(session: AsyncSession, firm_id: uuid.UUID, *, action: str | None, account_id: uuid.UUID | None = None,
                       hold_id: uuid.UUID | None = None, reason: str | None = None, impairment: str | None = None,
                       notified: str | None = None, not_notified_why: str | None = None, reference: str | None = None, on: date | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    if markets.code(firm.jurisdiction) != "US":
        raise FeatureError("A temporary hold under FINRA Rule 2165 is a US rule; nothing here applies to this firm's market.")
    today = firm_time.today(firm)
    day = on or today
    if day > today:
        raise FeatureError("Nothing is recorded ahead of the day it happened.")
    action = (action or "").strip().lower()
    if action not in ("place", "notify", "extend", "release"):
        raise FeatureError("The action is place, notify, extend or release.")
    if len((reason or "").strip()) < 15:
        raise FeatureError("Give the reason in full, as the record an examiner reads.")
    if action == "place":
        if account_id is None:
            raise FeatureError("Name the account.")
        account = await _account(session, firm_id, account_id)
        if await active_hold(session, firm, account.id):
            raise FeatureError("This account already has a hold in force.")
        owner = await _owner(session, account)
        age = None
        if owner and owner.date_of_birth:
            dob = owner.date_of_birth
            age = day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))
        if age is not None and age >= 65:
            adult = "age_65"
        elif (impairment or "").strip():
            adult = "impairment"
        else:
            raise FeatureError("A hold is for a specified adult: 65 or older on the date of birth on file, or an adult whose impairment is recorded. "
                               + ("No date of birth is on file." if age is None else f"The owner is {age}.") + " Record the impairment if there is one.")
        return {"action": action, "account": account, "adult": adult, "age": age, "on": day, "reason": reason.strip(),
                "impairment": (impairment or "").strip() or None, "expires": business_days_after(day, HOLD_DAYS)}
    h = await session.get(SeniorHold, hold_id) if hold_id else None
    if h is None or h.firm_id != firm_id:
        raise FeatureError("Hold not found.")
    state = hold_state(h, today)
    account = await session.get(Account, h.account_id)
    if action == "notify":
        people = [p.strip() for p in (notified or "").split(",") if p.strip()]
        if not people and len((not_notified_why or "").strip()) < 10:
            raise FeatureError("Name who was told (the trusted contacts and everyone authorised on the account), or why someone was not: the rule lets "
                               "the firm leave out a person it suspects.")
        return {"action": action, "hold": h, "account": account, "on": day, "reason": reason.strip(), "notified": people,
                "not_notified_why": (not_notified_why or "").strip() or None}
    if state != "active":
        raise FeatureError(f"This hold is {state}.")
    if action == "release":
        return {"action": action, "hold": h, "account": account, "on": day, "reason": reason.strip()}
    extensions = sum(1 for s in (h.steps or []) if s["step"] == "extend")
    if extensions >= 2:
        raise FeatureError("This hold has had both extensions the rule allows; it ends when it expires, or a court decides.")
    if extensions == 1 and len((reference or "").strip()) < 5:
        raise FeatureError("The second extension needs the matter reported to a state agency or a court: give that report's reference.")
    if not any(s["step"] == "notify" for s in (h.steps or [])):
        raise FeatureError("Record who was told before extending the hold.")
    days = FIRST_EXTENSION if extensions == 0 else SECOND_EXTENSION
    return {"action": action, "hold": h, "account": account, "on": day, "reason": reason.strip(), "reference": (reference or "").strip() or None,
            "expires": business_days_after(h.expires_on, days), "days": days}


async def record_hold(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_hold(session, firm_id, **terms)
    step = {"step": r["action"], "by": by, "on": r["on"].isoformat(), "note": r["reason"]}
    if r["action"] == "place":
        h = SeniorHold(firm_id=firm_id, account_id=r["account"].id, basis=r["reason"], adult_basis=r["adult"], placed_on=r["on"], expires_on=r["expires"],
                       placed_by=by, steps=[{**step, "impairment": r["impairment"]}])
        session.add(h)
    else:
        h = r["hold"]
        if r["action"] == "notify":
            step.update({"notified": r["notified"], "not_notified_why": r["not_notified_why"]})
        if r["action"] == "extend":
            step.update({"reference": r["reference"], "days": r["days"], "to": r["expires"].isoformat()})
            h.expires_on = r["expires"]
        if r["action"] == "release":
            h.status = "released"
        h.steps = [*(h.steps or []), step]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="senior_hold_" + r["action"], actor=by,
                              content={"hold_id": str(h.id), "account_id": str(h.account_id), "on": r["on"].isoformat()})
    return await for_firm(session, firm_id)
