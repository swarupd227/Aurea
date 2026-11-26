"""The KYC registry's status, looked up first and kept apart from the account's own state (B-386, L200-1 India §2, §5.1, §10).

An Indian account has two axes: the firm's own state (`account_states.py`) and what the shared registry says about the client,
which the firm does not control and which gates what any firm may do. This module holds the second:

    not_looked_up   nobody has recorded the registry's answer. No timeline is promised for it.
    validated       every attribute was verified at source; the record is portable
    registered      the next intermediary may have to repeat the KYC
    on_hold         no transactions until the deficiency is put right
    rejected        the KYC is done again
    not_found       the registry answered and holds no record: a fresh KYC is needed

The status that stands for a party is the latest statement of it: a lookup recorded here, or the KRA status a person gave on a
KYC record (`kyc_india`). A KYC record that only carries the earlier status forward states nothing. A real KRA or CKYC lookup
needs a vendor (B-272) and registers at `register`; today the only provider is the simulation (`app/simulations/kyc_registry`),
and a person otherwise records the answer from the acknowledgement. Nothing is looked up, estimated or cleared on its own.

An on-hold or rejected record carries the coded reasons the answer gave, the steps that put each right with where each stands,
and a task for the household's adviser. Finishing the steps clears nothing: only a fresh answer from the registry does. A
recorded change of residential status raises a re-KYC. The client reads their own status, reasons and steps in plain words,
and nothing else: not the registry's free text, not a risk band or political exposure, nothing about monitoring or screening.
Nothing here sends anything to the client, changes an account or places an order, and none of it says the firm is compliant.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advisers, firm_time, kyc_india
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.graph import Household, LegalEntity, Person
from app.models.kyc import KycRecord
from app.models.kyc_registry import KycFollowUp, KycLookup
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = kyc_india.RECORDERS
NOT_LOOKED_UP = "not_looked_up"
STATUSES = {"validated": "Validated", "registered": "Registered", "on_hold": "On hold", "rejected": "Rejected",
            "not_found": "No record at the registry"}
LABELS = {**STATUSES, NOT_LOOKED_UP: "Not looked up"}
# The worst first: what a case, with several parties, says.
ORDER = ("rejected", "on_hold", NOT_LOOKED_UP, "not_found", "registered", "validated")
STOPS = ("on_hold", "rejected")
# What has to happen before a timeline may be promised, or None.
TIMELINE = {NOT_LOOKED_UP: "No timeline until the registry status is looked up", "on_hold": "No timeline until the registry hold is cleared",
            "rejected": "No timeline until a fresh KYC is accepted", "not_found": "A fresh KYC comes before a timeline",
            "registered": None, "validated": None}
CLIENT_MEANING = {"validated": "No new KYC needed", "registered": "A new firm may ask you to verify again",
                  "on_hold": "No new transactions until this is put right", "rejected": "Your KYC has to be done again",
                  "not_found": "A new KYC is needed", NOT_LOOKED_UP: "Not checked yet"}
REGISTRIES = {"kra": "KRA", "ckyc": "Central KYC registry"}
# code -> (what the answer says is wrong, the step that puts it right, the same for the client)
DEFICIENCIES = {
    "pan_aadhaar_not_linked": ("PAN is not linked to Aadhaar", "Client links PAN with Aadhaar on the income-tax e-filing portal",
                               "Link your PAN with your Aadhaar on the income-tax e-filing portal"),
    "mobile_not_verified": ("Mobile number not verified", "Client verifies the mobile number with the KRA", "Verify your mobile number with the KYC agency"),
    "email_not_verified": ("Email address not verified", "Client verifies the email address with the KRA", "Verify your email address with the KYC agency"),
    "identity_proof": ("Identity proof not accepted", "Send the KRA a current officially valid identity document", "Send a current identity document"),
    "address_proof": ("Address proof not accepted", "Send the KRA a current address proof", "Send a current address proof"),
    "details_mismatch": ("Name or date of birth does not match", "Send the document showing the correct name and date of birth",
                         "Send the document showing your correct name and date of birth"),
    "other": ("Another reason in the registry's answer", "Act on the reason in the registry's answer", "Ask your adviser what the KYC agency needs"),
    "not_stated": ("No reason in the registry's answer", "Ask the KRA what is deficient", "Ask your adviser what the KYC agency needs"),
}
FRESH_KYC = "fresh_kyc"
FRESH_STEP = ("A fresh KYC", "Complete a fresh KYC with the intermediary and submit it", "Complete a new KYC with your adviser")
STEP_STATES = {"open": "Not started", "asked": "Client asked", "received": "Received from the client", "done": "Done", "not_needed": "Not needed"}
CLIENT_STEP_STATES = {"open": "To do", "asked": "Asked of you", "received": "Received", "done": "Done", "not_needed": "Not needed"}
CHANNELS = {"phone": "Phone", "email": "Email", "meeting": "Meeting", "letter": "Letter", "portal": "Client portal"}
OUTCOMES = {"reached": "Reached", "replied": "Client replied", "left_message": "Left a message", "no_answer": "No answer"}
HOLDS = {"identity_proof": "Identity proof", "address_proof": "Address proof", "pan_card": "PAN", "photograph": "Photograph"}
AGE_BANDS = (("0_30", "0 to 30 days", 0, 30), ("31_90", "31 to 90 days", 31, 90), ("91_180", "91 to 180 days", 91, 180),
             ("over_180", "Over 180 days", 181, None))
RESIDENCY_TRIGGER = "residency_change"
TASK_SOURCE = "kyc_registry"
NOT_CONNECTED = ("No KRA or CKYC lookup is connected (it needs a vendor, B-272): record the answer from the registry's acknowledgement "
                 "with record_kyc_lookup. Nothing was looked up.")
CITATION = ("SEBI's KYC framework (the KRA regulations and the Master Circular on KYC), as the onboarding module reads it; status rules have "
            "been revised several times and the current circular is checked by a person.")
NOTE = "What the registry answered, as recorded or looked up. Steps clear nothing: only a fresh answer does. Nothing is sent to the client."

PROVIDERS: dict[str, object] = {}


class RegistryError(ValueError):
    """This cannot be looked up or recorded, with the reason."""


def register(provider) -> None:
    PROVIDERS[provider.key] = provider


def provider_for(firm: Firm):
    for key in sorted(PROVIDERS):
        if PROVIDERS[key].enabled_for(firm):
            return PROVIDERS[key]
    return None


def applies(firm: Firm | None) -> bool:
    return kyc_india.applies(firm)


def _norm(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def _aware(t: datetime | None) -> datetime:
    if t is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


# ── What stands ─────────────────────────────────────────────────────────────────────────────────────────────────
async def lookups(session: AsyncSession, firm_id: uuid.UUID) -> list[KycLookup]:
    return list((await session.execute(select(KycLookup).where(KycLookup.firm_id == firm_id).order_by(KycLookup.created_at))).scalars().all())


def _of_lookup(x: KycLookup) -> dict:
    return {"status": x.status, "label": STATUSES.get(x.status, x.status), "registry": x.registry, "agency": x.agency, "reference": x.reference,
            "answered_on": x.answered_on.isoformat(), "deficiencies": list(x.deficiencies or []), "deficiency_note": x.deficiency_note,
            "holds": list(x.holds or []), "source": "lookup", "simulated": bool(x.source_system), "recorded_by": x.recorded_by,
            "lookup_id": str(x.id), "party_name": x.party_name, "case_id": x.case_id, "document": x.document}


def _of_record(r: KycRecord) -> dict:
    on = _aware(r.created_at).date() if r.created_at else None
    return {"status": r.kra_status, "label": STATUSES.get(r.kra_status, r.kra_status), "registry": "kra", "agency": r.kra_name, "reference": None,
            "answered_on": on.isoformat() if on else None, "deficiencies": [], "deficiency_note": None, "holds": [], "source": "kyc_record",
            "simulated": False, "recorded_by": r.recorded_by, "lookup_id": None, "party_name": r.party_name, "case_id": r.case_id,
            "document": r.document}


async def _keys(session: AsyncSession, firm_id: uuid.UUID, rows: list):
    """How a row names its party, as `kyc_india.latest` does: a client or entity, else a case and a name (followed to the client
    the case created)."""
    followed = await kyc_india._followed(session, firm_id, {r.case_id for r in rows if not (r.person_id or r.entity_id)})

    def key(r) -> tuple:
        return (("person", r.person_id) if r.person_id else ("entity", r.entity_id) if r.entity_id
                else kyc_india._case_key(r.case_id, r.party_name, followed))

    return key


async def standing(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """The registry status that stands for each party: the latest statement of it, by the same keys as `kyc_india.latest`."""
    records = list((await session.execute(select(KycRecord).where(KycRecord.firm_id == firm_id).order_by(KycRecord.created_at))).scalars().all())
    looks = await lookups(session, firm_id)
    key = await _keys(session, firm_id, [*records, *looks])
    events: list[tuple] = []
    carried: dict = {}
    for r in records:
        k = key(r)
        said = r.kra_status and (r.kra_status_stated or (r.kra_status_stated is None and r.kra_status != carried.get(k)))
        if said:
            events.append((_aware(r.created_at), 0, k, _of_record(r)))
        carried[k] = r.kra_status or carried.get(k)
    events += [(_aware(x.created_at), 1, key(x), _of_lookup(x)) for x in looks]
    out: dict = {}
    for at, _, k, statement in sorted(events, key=lambda e: (e[0], e[1])):
        out[k] = {**statement, "recorded_at": at.isoformat()}
    return out


async def follow_ups(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every follow-up, oldest first, by party key."""
    rows = list((await session.execute(select(KycFollowUp).where(KycFollowUp.firm_id == firm_id)
                                       .order_by(KycFollowUp.created_at))).scalars().all())
    key = await _keys(session, firm_id, rows)
    out: dict = {}
    for r in rows:
        out.setdefault(key(r), []).append(r)
    return out


def steps_of(statement: dict | None, rows: list) -> list[dict]:
    """The remediation of a standing on-hold or rejected answer: each reason's step and where it stands."""
    if not statement or statement["status"] not in STOPS or not statement.get("lookup_id"):
        return []
    codes = list(statement.get("deficiencies") or [])
    if statement["status"] == "rejected":
        codes.append(FRESH_KYC)
    out = []
    for code in codes:
        label, action, client = FRESH_STEP if code == FRESH_KYC else DEFICIENCIES.get(code, DEFICIENCIES["not_stated"])
        last = next((r for r in reversed(rows) if r.kind == "step" and r.step == code and str(r.lookup_id) == statement["lookup_id"]), None)
        state = last.state if last else "open"
        out.append({"step": code, "reason": label, "action": action, "client_action": client, "state": state, "state_label": STEP_STATES[state],
                    "on": last.happened_on.isoformat() if last else None, "by": last.recorded_by if last else None,
                    "note": last.note if last else None})
    return out


def summary(statement: dict | None) -> dict:
    """The part of a standing answer a row shows."""
    if not statement:
        return {"status": NOT_LOOKED_UP, "label": LABELS[NOT_LOOKED_UP], "answered_on": None, "simulated": False, "source": None,
                "registry": None, "agency": None, "deficiencies": [], "holds": [], "lookup_id": None}
    return {"status": statement["status"], "label": statement["label"], "answered_on": statement["answered_on"], "simulated": statement["simulated"],
            "source": statement["source"], "registry": REGISTRIES.get(statement["registry"]), "agency": statement["agency"],
            "deficiencies": [{"code": c, "label": DEFICIENCIES.get(c, DEFICIENCIES["not_stated"])[0]} for c in statement["deficiencies"]],
            "holds": [HOLDS.get(h, h) for h in statement["holds"]], "lookup_id": statement["lookup_id"]}


def worst(statuses: list[str]) -> str:
    return min(statuses, key=ORDER.index) if statuses else NOT_LOOKED_UP


# ── A case ──────────────────────────────────────────────────────────────────────────────────────────────────────
async def case_registry(session: AsyncSession, firm: Firm, case: OnboardingCase, *, standings: dict | None = None,
                        rows: dict | None = None) -> dict | None:
    """The registry status beside the account's own state: each party the case asks KYC of, and the worst of them for the case.
    None outside India."""
    if not applies(firm):
        return None
    from app.agents.cip_identity_verifier import CIP_ROLES

    standings = await standing(session, firm.id) if standings is None else standings
    rows = await follow_ups(session, firm.id) if rows is None else rows
    parties = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id)
                                     .order_by(OnboardingParty.created_at, OnboardingParty.legal_name))).scalars().all()
    asked = {_norm(p.legal_name): p.legal_name for p in parties if p.role in CIP_ROLES | kyc_india.ALSO_ASKED}
    if case.is_entity or not asked:
        asked.setdefault(_norm(case.prospect_name), case.prospect_name)
    members: dict = {}
    home = (case.intake or {}).get("household_id")
    if home:
        for p in (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.household_id == uuid.UUID(str(home))))).scalars().all():
            members[_norm(p.full_name)] = ("person", p.id)
    created = kyc_india._created_by(case)
    out = []
    for n, name in asked.items():
        candidates = [("case", case.id, n)]
        if n == _norm(case.prospect_name):
            candidates += list(created)
        if n in members:
            candidates.append(members[n])
        found = [(k, standings[k]) for k in candidates if k in standings]
        k, statement = max(found, key=lambda f: f[1]["recorded_at"]) if found else (candidates[-1] if n in members else candidates[0], None)
        out.append({"name": name, **summary(statement), "steps": steps_of(statement, rows.get(k, [])),
                    "contacts": _contacts(rows.get(k, []), None)})
    status = worst([p["status"] for p in out])
    return {"status": status, "label": LABELS[status], "timeline": TIMELINE[status], "timeline_ok": TIMELINE[status] is None, "parties": out,
            "case_id": str(case.id), "client": case.prospect_name, "simulated": any(p["simulated"] for p in out)}


# ── The book ────────────────────────────────────────────────────────────────────────────────────────────────────
def _contacts(rows: list, since: date | None) -> dict:
    tries = [r for r in rows if r.kind == "contact" and (since is None or r.happened_on >= since)]
    last = max(tries, key=lambda r: (r.happened_on, _aware(r.created_at))) if tries else None
    return {"attempts": len(tries), "last_on": last.happened_on.isoformat() if last else None,
            "last_channel": CHANNELS.get(last.channel) if last else None, "last_outcome": OUTCOMES.get(last.outcome) if last else None}


def _open_item(p: dict, today: date) -> dict | None:
    """Why a party is in the re-KYC backlog and since when, or None. A date coming up is not yet backlog."""
    reasons: list[tuple[str, str | None]] = []
    reg = p["registry"]
    if reg["status"] in ("on_hold", "rejected", "not_found", "registered"):
        reasons.append((reg["label"], reg["answered_on"]))
    done = p.get("kyc_completed_on")
    if p["state"] == "overdue":
        past = [d for d in (p.get("due_on"), (p.get("id_proof") or {}).get("valid_to"), (p.get("address_proof") or {}).get("valid_to"))
                if d and d < today.isoformat()]
        reasons.append(("Re-KYC overdue", min(past) if past else None))
    for t in p.get("triggers") or []:
        if done and str(t.get("on")) > done:
            reasons.append((kyc_india.TRIGGERS.get(t["kind"], t["kind"]), str(t["on"])))
    if not reasons:
        return None
    dated = [d for _, d in reasons if d]
    since = date.fromisoformat(min(dated)) if dated else None
    age = (today - since).days if since else None
    band = next((k for k, _, low, high in AGE_BANDS if age is not None and age >= low and (high is None or age <= high)), None)
    return {"reasons": [r for r, _ in reasons], "since": since.isoformat() if since else None, "age_days": age, "band": band}


def _party_key(p: dict) -> tuple:
    if p.get("person_id"):
        return ("person", uuid.UUID(p["person_id"]))
    if p.get("entity_id"):
        return ("entity", uuid.UUID(p["entity_id"]))
    return ("case", uuid.UUID(p["case_id"]) if p.get("case_id") not in (None, "None") else None, _norm(p["name"]))


async def book(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None, case_id: uuid.UUID | None = None) -> dict:
    """The status mix of the book, the re-KYC backlog by age with the contact attempts made, each on-hold client's reasons and
    steps, and the open onboarding cases nobody has looked up."""
    if not applies(firm):
        return {"applies": False}
    today = firm_time.today(firm)
    if household_id is not None:
        home = await session.get(Household, household_id)
        if home is None or home.firm_id != firm.id:
            raise RegistryError("No such household at this firm.")
    one = None
    if case_id is not None:
        one = await session.get(OnboardingCase, case_id)
        if one is None or one.firm_id != firm.id:
            raise RegistryError("No such onboarding case at this firm.")
    standings, rows = await standing(session, firm.id), await follow_ups(session, firm.id)
    q = select(OnboardingCase).where(OnboardingCase.firm_id == firm.id).order_by(OnboardingCase.created_at.desc())
    cases = [] if one is not None else [c for c in (await session.execute(q)).scalars().all() if str(c.status) not in ("approved", "rejected")]
    if household_id is not None:
        cases = [c for c in cases if str((c.intake or {}).get("household_id") or "") == str(household_id)]
    case_rows = [await case_registry(session, firm, c, standings=standings, rows=rows) for c in ([one] if one is not None else cases)]
    p = provider_for(firm)
    base = {"applies": True, "as_of": today.isoformat(), "cases": case_rows,
            "household_id": str(household_id) if household_id else None, "case_id": str(case_id) if case_id else None,
            "provider": ({"key": p.key, "name": p.name, "simulated": bool(getattr(p, "simulated", False))} if p else None),
            "connected": p is not None, "not_connected": None if p else NOT_CONNECTED, "note": NOTE, "citation": CITATION}
    if one is not None:
        return {**base, "parties": [], "mix": [], "backlog": [], "bands": [], "counts": {
            "cases": 1, "cases_not_looked_up": sum(1 for c in case_rows if c["status"] == NOT_LOOKED_UP)}}
    overview = await kyc_india.overview(session, firm, household_id=household_id)
    parties = []
    for x in overview["parties"]:
        key = _party_key(x)
        mine = rows.get(key, [])
        statement = standings.get(key)
        item = _open_item(x, today)
        since = date.fromisoformat(item["since"]) if item and item["since"] else None
        parties.append({"name": x["name"], "person_id": x.get("person_id"), "entity_id": x.get("entity_id"), "case_id": x.get("case_id"),
                        "registry": x["registry"], "state": x["state"], "steps": steps_of(statement, mine),
                        "reason_recorded": not (x["registry"]["status"] in STOPS and not x["registry"]["lookup_id"]),
                        "backlog": item, "contacts": _contacts(mine, since)})
    parties.sort(key=lambda x: (ORDER.index(x["registry"]["status"]), x["name"]))
    total = len(parties)
    mix = [{"status": s, "label": LABELS[s], "count": n, "share": round(n / total, 4) if total else None}
           for s in ("validated", "registered", "on_hold", "rejected", "not_found", NOT_LOOKED_UP)
           if (n := sum(1 for x in parties if x["registry"]["status"] == s)) or s in ("validated", "registered", "on_hold", NOT_LOOKED_UP)]
    backlog = sorted((x for x in parties if x["backlog"]), key=lambda x: (-(x["backlog"]["age_days"] or -1), x["name"]))
    bands = [{"key": k, "label": label, "count": sum(1 for x in backlog if x["backlog"]["band"] == k),
              "no_contact": sum(1 for x in backlog if x["backlog"]["band"] == k and not x["contacts"]["attempts"])}
             for k, label, _, _ in AGE_BANDS]
    return {**base, "parties": parties, "mix": mix, "backlog": backlog, "bands": bands,
            "counts": {"parties": total, "backlog": len(backlog), "no_contact": sum(1 for x in backlog if not x["contacts"]["attempts"]),
                       "stopped": sum(1 for x in parties if x["registry"]["status"] in STOPS),
                       "cases": len(case_rows), "cases_not_looked_up": sum(1 for c in case_rows if c["status"] == NOT_LOOKED_UP)}}


# ── Recording ───────────────────────────────────────────────────────────────────────────────────────────────────
def _role(role) -> None:
    if UserRole(role) not in RECORDERS:
        raise RegistryError("A registry answer, a remediation step or a contact attempt is recorded by an adviser, a paraplanner, operations or compliance.")


def _text(value, label: str, limit: int, *, required: bool = False) -> str | None:
    v = " ".join(str(value or "").split())
    if kyc_india.AADHAAR_NUMBER.search(v):
        raise RegistryError("An Aadhaar number is never stored, not even in a note. Leave it out.")
    if required and len(v) < 4:
        raise RegistryError(f"Say {label}.")
    if len(v) > limit:
        raise RegistryError(f"Keep {label} to {limit} characters.")
    return v or None


def _pick(value, choices: dict, label: str) -> str:
    v = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if v not in choices:
        raise RegistryError(f"{label} is one of {', '.join(choices)}.")
    return v


def _list(value, choices: dict, label: str) -> list[str]:
    if value in (None, ""):
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).replace(";", ",").split(",")
    out = [_pick(i, choices, label) for i in items if str(i).strip()]
    return list(dict.fromkeys(out))


def _on(value, today: date) -> date:
    if value in (None, ""):
        return today
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except ValueError:
        raise RegistryError("Give the date as YYYY-MM-DD.")
    if d > today:
        raise RegistryError("The date cannot be in the future.")
    return d


async def _party(session: AsyncSession, firm: Firm, *, person_id=None, entity_id=None, case_id=None, party_name=None) -> dict:
    """Whose answer or attempt this is, inside the firm."""
    if not applies(firm):
        raise RegistryError("The KYC registry is read for an Indian firm's clients.")
    if person_id and entity_id:
        raise RegistryError("Say whose it is: one person, one entity, or a case with the party's name.")
    holder = case = household = None
    name = " ".join((party_name or "").split())
    try:
        if person_id:
            holder = await session.get(Person, uuid.UUID(str(person_id)))
        elif entity_id:
            holder = await session.get(LegalEntity, uuid.UUID(str(entity_id)))
        if case_id:
            case = await session.get(OnboardingCase, uuid.UUID(str(case_id)))
    except ValueError:
        raise RegistryError("Give the id as a read tool returned it.")
    if person_id or entity_id:
        household = await session.get(Household, holder.household_id) if holder is not None and holder.household_id else None
        if holder is None or household is None or household.firm_id != firm.id:
            raise RegistryError("No such client or entity at this firm.")
        name = holder.full_name if isinstance(holder, Person) else holder.name
    if case_id:
        if case is None or case.firm_id != firm.id:
            raise RegistryError("No such onboarding case at this firm.")
        name = name or case.prospect_name
        home = (case.intake or {}).get("household_id")
        if household is None and home:
            household = await session.get(Household, uuid.UUID(str(home)))
    if holder is None and case is None:
        raise RegistryError("Say whose it is: a client or entity, or an onboarding case (and the party's name).")
    if len(name) < 3 or len(name) > 200 or kyc_india.AADHAAR_NUMBER.search(name):
        raise RegistryError("Name the party as the document does.")
    probe = KycLookup(person_id=holder.id if isinstance(holder, Person) else None, entity_id=holder.id if isinstance(holder, LegalEntity) else None,
                      case_id=case.id if case else None, party_name=name)
    key = (await _keys(session, firm.id, [probe]))(probe)
    return {"holder": holder, "case": case, "household": household, "name": name, "key": key,
            "ids": {"person_id": probe.person_id, "entity_id": probe.entity_id, "case_id": probe.case_id}}


async def owner_of(session: AsyncSession, firm: Firm, household: Household | None, case: OnboardingCase | None):
    """Who the follow-up is for: the household's recorded adviser, else the one who opened the case. Never worked out otherwise."""
    from app.aurea_core import onboarding_start

    who = await advisers.adviser_of(session, firm.id, household.id) if household is not None else None
    if who is None and case is not None:
        who = await onboarding_start.adviser_for(session, firm, case)
    return who if who is not None and who.is_active else None


async def _open_task(session: AsyncSession, firm_id: uuid.UUID, title: str) -> Task | None:
    return (await session.execute(select(Task).where(Task.firm_id == firm_id, Task.title == title, Task.status == TaskStatus.OPEN))).scalars().first()


def _task_title(status: str, name: str) -> str:
    said = {"on_hold": "KYC on hold at the registry", "rejected": "KYC rejected at the registry", "not_found": "No KYC at the registry"}[status]
    return f"{said}: {name}"[:280]


def blocked_task_titles(name: str) -> list[str]:
    return [_task_title(s, name) for s in STOPS]


async def prepare_lookup(session: AsyncSession, firm: Firm, role, *, person_id=None, entity_id=None, case_id=None, party_name=None,
                         registry=None, status=None, agency=None, reference=None, answered_on=None, deficiencies=None,
                         deficiency_note=None, holds=None, document=None) -> dict:
    """Every check the write makes, nothing written."""
    _role(role)
    p = await _party(session, firm, person_id=person_id, entity_id=entity_id, case_id=case_id, party_name=party_name)
    today = firm_time.today(firm)
    registry = _pick(registry or "kra", REGISTRIES, "The registry")
    status = _pick(status, STATUSES, "The registry's status")
    on = _on(answered_on, today)
    reasons = _list(deficiencies, DEFICIENCIES, "A deficiency")
    note = _text(deficiency_note, "the registry's words", 300)
    if status in STOPS and not reasons:
        raise RegistryError(f"An on-hold or rejected answer carries its reason. Give deficiencies from the answer ({', '.join(DEFICIENCIES)}); "
                            "not_stated where it gives none.")
    if status not in STOPS and reasons:
        raise RegistryError("Only an on-hold or rejected answer carries deficiencies.")
    if "other" in reasons and not note:
        raise RegistryError("For another reason, give the registry's own words in deficiency_note.")
    held = _list(holds, HOLDS, "A held document")
    if held and status not in ("validated", "registered"):
        raise RegistryError("Documents held count only on a validated or registered answer; leave holds out.")
    doc = _text(document, "where the answer is filed: the KRA's acknowledgement or the registry's response", 300, required=True)
    agency, reference = _text(agency, "the agency's name", 60), _text(reference, "the reference", 120)
    before = (await standing(session, firm.id)).get(p["key"])
    if before and before["source"] == "lookup" and (before["status"], before["answered_on"], before["deficiencies"], before["reference"],
                                                    before["holds"]) == (status, on.isoformat(), reasons, reference, held):
        raise RegistryError("Nothing changes: that answer is already on file.")
    owner = await owner_of(session, firm, p["household"], p["case"])
    task = None
    if status in (*STOPS, "not_found"):
        title = _task_title(status, p["name"])
        task = {"title": title, "owner": owner, "exists": await _open_task(session, firm.id, title) is not None}
    return {**p, "registry": registry, "status": status, "answered_on": on, "deficiencies": reasons, "deficiency_note": note, "holds": held,
            "document": doc, "agency": agency, "reference": reference, "before": before, "task": task}


async def record_lookup(session: AsyncSession, firm: Firm, role, *, by: str, source_system: str | None = None, **terms) -> dict:
    p = await prepare_lookup(session, firm, role, **terms)
    row = KycLookup(firm_id=firm.id, **p["ids"], party_name=p["name"], registry=p["registry"], status=p["status"], agency=p["agency"],
                    reference=p["reference"], answered_on=p["answered_on"], deficiencies=p["deficiencies"], deficiency_note=p["deficiency_note"],
                    holds=p["holds"], document=p["document"], recorded_by=by, source_system=source_system, created_at=utcnow())
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="kyc_registry_looked_up", actor=by, content={
        "party": row.party_name, "registry": row.registry, "status": row.status, "answered_on": row.answered_on.isoformat(),
        "deficiencies": row.deficiencies, "document": row.document, "simulated": bool(source_system)})
    raised = None
    t = p["task"]
    if t and not t["exists"]:
        why = "; ".join(DEFICIENCIES[c][1] for c in p["deficiencies"]) or "A fresh KYC with the intermediary"
        session.add(Task(firm_id=firm.id, household_id=p["household"].id if p["household"] else None, title=t["title"],
                         detail=(f"Registry answer of {firm_time.spoken(p['answered_on'])}{' (simulated)' if source_system else ''}. {why}. "
                                 "Record each step and each attempt to reach the client; only a fresh answer from the registry clears it."),
                         status=TaskStatus.OPEN, due_date=firm_time.today(firm), source=source_system or TASK_SOURCE,
                         subject_label=p["household"].name if p["household"] else p["name"],
                         assigned_to=t["owner"].id if t["owner"] else None))
        await session.flush()
        raised = {"title": t["title"], "owner": t["owner"].full_name if t["owner"] else None}
    view = await _after(session, firm, p)
    return {**view, "recorded": {"party": row.party_name, "status": row.status, "label": STATUSES[row.status], "simulated": bool(source_system),
                                 "lookup_id": str(row.id)}, "task_raised": raised}


async def _after(session: AsyncSession, firm: Firm, p: dict) -> dict:
    """What a write shows back: the case where one was named, else the party's household."""
    if p["case"] is not None:
        return await book(session, firm, case_id=p["case"].id)
    return await book(session, firm, household_id=p["household"].id if p["household"] else None)


async def prepare_provider_lookup(session: AsyncSession, firm: Firm, role, *, person_id=None, entity_id=None, case_id=None, party_name=None) -> dict:
    _role(role)
    p = await _party(session, firm, person_id=person_id, entity_id=entity_id, case_id=case_id, party_name=party_name)
    provider = provider_for(firm)
    if provider is None:
        raise RegistryError(NOT_CONNECTED)
    return {**p, "provider": provider}


async def provider_lookup(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    """Ask the connected registry lookup about a party and record its answer, tagged where the provider is simulated."""
    p = await prepare_provider_lookup(session, firm, role, **terms)
    provider = p["provider"]
    answer = await provider.lookup(session, firm, name=p["name"], holder=p["holder"], case=p["case"])
    if not answer or answer.get("status") not in STATUSES:
        raise RegistryError("The registry lookup returned no status; nothing is recorded.")
    simulated = bool(getattr(provider, "simulated", False))
    return await record_lookup(
        session, firm, role, by=by, source_system=provider.key if simulated else None, **terms, registry=answer.get("registry") or "kra",
        status=answer["status"], agency=answer.get("agency"), reference=answer.get("reference"), answered_on=answer.get("answered_on"),
        deficiencies=answer.get("deficiencies"), deficiency_note=answer.get("deficiency_note"), holds=answer.get("holds"),
        document=f"{provider.name}, answer {answer.get('reference') or 'without a reference'}{' (simulated)' if simulated else ''}"[:300])


async def prepare_step(session: AsyncSession, firm: Firm, role, *, lookup_id=None, step=None, state=None, happened_on=None, note=None) -> dict:
    _role(role)
    if not applies(firm):
        raise RegistryError("The KYC registry is read for an Indian firm's clients.")
    try:
        row = await session.get(KycLookup, uuid.UUID(str(lookup_id)))
    except (ValueError, TypeError):
        raise RegistryError("Give the lookup's id, from read_kyc_registry.")
    if row is None or row.firm_id != firm.id:
        raise RegistryError("No such registry answer at this firm.")
    key = (await _keys(session, firm.id, [row]))(row)
    statement = (await standing(session, firm.id)).get(key)
    if not statement or statement.get("lookup_id") != str(row.id):
        raise RegistryError(f"A newer registry answer stands for {row.party_name}: read it again.")
    if row.status not in STOPS:
        raise RegistryError(f"{row.party_name}'s answer is {STATUSES[row.status].lower()}: there is nothing to put right.")
    steps = steps_of(statement, (await follow_ups(session, firm.id)).get(key, []))
    code = _pick(step, {s["step"]: None for s in steps}, "A step of that answer")
    state = _pick(state, STEP_STATES, "The step's state")
    current = next(s for s in steps if s["step"] == code)
    note = _text(note, "the note", 300)
    if state == current["state"] and not note:
        raise RegistryError(f"That step is already {STEP_STATES[state].lower()}.")
    if state == "not_needed" and not note:
        raise RegistryError("Say in the note why the step is not needed.")
    return {"lookup": row, "key": key, "step": current, "state": state, "on": _on(happened_on, firm_time.today(firm)), "note": note}


async def record_step(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_step(session, firm, role, **terms)
    x: KycLookup = p["lookup"]
    session.add(KycFollowUp(firm_id=firm.id, person_id=x.person_id, entity_id=x.entity_id, case_id=x.case_id, party_name=x.party_name, kind="step",
                            lookup_id=x.id, step=p["step"]["step"], state=p["state"], happened_on=p["on"], note=p["note"], recorded_by=by,
                            source_system=x.source_system, created_at=utcnow()))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="kyc_remediation_step", actor=by, content={
        "party": x.party_name, "step": p["step"]["step"], "state": p["state"], "lookup_id": str(x.id)})
    party = await _party(session, firm, person_id=x.person_id, entity_id=x.entity_id,
                         case_id=None if (x.person_id or x.entity_id) else x.case_id, party_name=x.party_name)
    return await _after(session, firm, party)


async def prepare_contact(session: AsyncSession, firm: Firm, role, *, person_id=None, entity_id=None, case_id=None, party_name=None,
                          channel=None, outcome=None, happened_on=None, note=None) -> dict:
    _role(role)
    p = await _party(session, firm, person_id=person_id, entity_id=entity_id, case_id=case_id, party_name=party_name)
    statement = (await standing(session, firm.id)).get(p["key"])
    return {**p, "channel": _pick(channel, CHANNELS, "The channel"), "outcome": _pick(outcome, OUTCOMES, "The outcome"),
            "on": _on(happened_on, firm_time.today(firm)), "note": _text(note, "the note", 300), "statement": statement}


async def record_contact(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_contact(session, firm, role, **terms)
    st = p["statement"]
    session.add(KycFollowUp(firm_id=firm.id, **p["ids"], party_name=p["name"], kind="contact",
                            lookup_id=uuid.UUID(st["lookup_id"]) if st and st["lookup_id"] else None, channel=p["channel"],
                            outcome=p["outcome"], happened_on=p["on"], note=p["note"], recorded_by=by, created_at=utcnow()))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="kyc_contact_attempt", actor=by, content={
        "party": p["name"], "channel": p["channel"], "outcome": p["outcome"], "on": p["on"].isoformat()})
    return await _after(session, firm, p)


# ── A change of residential status raises a re-KYC ──────────────────────────────────────────────────────────────
def residency_change(before: dict, after: dict, today: date) -> list[str]:
    """What of a person's residential status changed between two recorded standings. A fact recorded for the first time is not
    a change: what it was before is not known."""
    from app.aurea_core import residency

    out = []
    was, now = before.get("fema_status"), after.get("fema_status")
    if was and now and was != now:
        out.append(f"FEMA status {str(was).replace('_', ' ')} to {str(now).replace('_', ' ')}")
    was, now = before.get("country_of_residence"), after.get("country_of_residence")
    if was and now and _norm(was) != _norm(now):
        out.append(f"country of residence {was} to {now}")
    if before:
        a, b = (residency.tax_status(x, today)["status"] for x in (before, after))
        side = lambda s: "non-resident" if s == "non_resident" else "resident" if s.startswith("resident") else None  # noqa: E731
        if side(a) and side(b) and side(a) != side(b):
            out.append(f"tax residence {side(a)} to {side(b)}")
    return out


async def residency_effect(session: AsyncSession, firm: Firm, person: Person, changes: list[str]) -> dict | None:
    """What recording this change would raise. Writes nothing."""
    if not changes:
        return None
    household = await session.get(Household, person.household_id) if person.household_id else None
    owner = await owner_of(session, firm, household, None)
    on_file = ("person", person.id) in await kyc_india.latest(session, firm.id)
    return {"changes": changes, "kyc_on_file": on_file, "owner": owner, "household": household,
            "title": f"Re-KYC: {person.full_name}'s residential status changed"[:280]}


async def raise_residency_rekyc(session: AsyncSession, firm: Firm, person: Person, changes: list[str], *, document: str, by: str) -> dict | None:
    """A recorded change of residential status raises a re-KYC: the trigger goes on the KYC record where one is on file, and the
    adviser gets a task. The KYC itself is not changed."""
    e = await residency_effect(session, firm, person, changes)
    if e is None:
        return None
    if e["kyc_on_file"]:
        await kyc_india.record(session, firm, by=by, person_id=person.id, document=document, trigger=RESIDENCY_TRIGGER,
                               trigger_on=firm_time.today(firm), note="Raised by the recorded change of residential status")
    if await _open_task(session, firm.id, e["title"]) is None:
        session.add(Task(firm_id=firm.id, household_id=e["household"].id if e["household"] else None, title=e["title"],
                         detail=f"Recorded: {'; '.join(e['changes'])}. " + ("The KYC on file is marked for refresh."
                                if e["kyc_on_file"] else "No KYC record is on file: record it from the document."),
                         status=TaskStatus.OPEN, due_date=firm_time.today(firm), source=TASK_SOURCE,
                         subject_label=e["household"].name if e["household"] else person.full_name,
                         assigned_to=e["owner"].id if e["owner"] else None))
        await session.flush()
    return {"changes": e["changes"], "kyc_marked": e["kyc_on_file"], "task": e["title"], "owner": e["owner"].full_name if e["owner"] else None}


# ── The client's own reading ────────────────────────────────────────────────────────────────────────────────────
async def for_client(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """A household's own KYC status in plain words: the registry's status, the coded reasons, the steps and where each stands.
    Never the registry's free text, who recorded it, a risk band, political exposure or anything about monitoring or screening."""
    if not applies(firm):
        return {"applies": False, "household_id": str(household_id), "people": []}
    standings, rows = await standing(session, firm.id), await follow_ups(session, firm.id)
    overview = await kyc_india.overview(session, firm, household_id=household_id)
    people = []
    for x in overview["parties"]:
        if not (x.get("person_id") or x.get("entity_id")):
            continue                       # a party on a case who is not the client: not this reading's
        key = _party_key(x)
        statement = standings.get(key)
        status = statement["status"] if statement else NOT_LOOKED_UP
        steps = steps_of(statement, rows.get(key, []))
        done = x.get("kyc_completed_on")
        moved = any(t.get("kind") == RESIDENCY_TRIGGER and done and str(t.get("on")) > done for t in x.get("triggers") or [])
        reasons = [DEFICIENCIES.get(c, DEFICIENCIES["not_stated"])[0] for c in (statement or {}).get("deficiencies", [])
                   if c not in ("other", "not_stated")]
        needs = [{"action": s["client_action"], "state": s["state"], "state_label": CLIENT_STEP_STATES[s["state"]]} for s in steps]
        if status in STOPS and not needs:
            needs = [{"action": DEFICIENCIES["not_stated"][2], "state": "open", "state_label": CLIENT_STEP_STATES["open"]}]
        refresh = "Your residential status changed: your KYC needs refreshing" if moved else (
            "Your KYC needs refreshing" if x["state"] == "overdue" and status not in STOPS else None)
        people.append({"name": x["name"], "status": status, "label": LABELS[status], "meaning": CLIENT_MEANING[status],
                       "checked_on": statement["answered_on"] if statement else None, "simulated": bool(statement and statement["simulated"]),
                       "reasons": reasons, "steps": needs, "refresh": refresh})
    people.sort(key=lambda p: (ORDER.index(p["status"]), p["name"]))
    who = await advisers.adviser_of(session, firm.id, household_id)
    return {"applies": True, "household_id": str(household_id), "as_of": firm_time.today(firm).isoformat(), "people": people,
            "adviser": who.full_name if who else None}
