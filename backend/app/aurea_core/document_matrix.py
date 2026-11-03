"""The document matrix, the registration detail each account needs, and the checks before submission (L200-1 §4, §7).

One matrix, read by the tracks, the activation gate and the NIGO agent alike. It says, for a registration, which
documents must be on file (any one of a set satisfies a line: a passport or a driving licence), which facts the
registration needs, and what the account's state and its custodian add. A custodian's own forms are what the firm
recorded for that custodian; none recorded means none added, not none required.

The checks are the ones custodians reject packages for: a signer who did not sign, an application signed too long
ago or dated in the future, a tenancy the state does not allow, beneficiaries or owners whose shares do not make 100,
and registration detail left blank. Every fact here was recorded by a person from the application or a document;
nothing is worked out, and a check that cannot be made says what it needs. State law that varies by state and is not
encoded here, such as a UTMA's age of termination, is recorded from the state's act and range-checked only.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.models.enums import NIGORootCause, PartyRole
from app.models.onboarding import OnboardingCase, OnboardingDocument, OnboardingParty
from app.models.tenant import Firm
from app.provenance import ledger

# kind -> label. A requirement is satisfied by any one kind in its set.
DOCUMENTS = {
    "passport": "Passport", "drivers_licence": "Driving licence", "trust_certification": "Certification of trust",
    "trust_deed": "Trust deed", "entity_formation": "Formation document", "entity_resolution": "Resolution or certificate of incumbency",
    "partnership_agreement": "Partnership agreement", "death_certificate": "Death certificate",
    "letters_testamentary": "Letters testamentary or of administration", "beneficiary_form": "Beneficiary designation form",
    "custodian_application": "Custodian account application", "transfer_form": "Transfer form", "w9": "Form W-9",
    "plan_distribution_form": "Plan distribution form",
}
_ID = ("id", "Government photo ID", ("passport", "drivers_licence"))
_BENEFICIARY_FORM = ("beneficiaries", "Beneficiary designation", ("beneficiary_form",))
_ENTITY = [_ID, ("formation", "Formation document", ("entity_formation", "trust_deed")), ("authority", "Authority to act", ("entity_resolution",))]

# registration -> documents, and the registration detail it needs
MATRIX: dict[str, dict] = {
    "individual": {"documents": [_ID], "fields": []},
    "joint_jtwros": {"documents": [_ID], "fields": []},
    "joint_tic": {"documents": [_ID], "fields": []},
    "joint_tbe": {"documents": [_ID], "fields": ["married"]},
    "community_property": {"documents": [_ID], "fields": ["married"]},
    "traditional_ira": {"documents": [_ID, _BENEFICIARY_FORM], "fields": ["funding"]},
    "roth_ira": {"documents": [_ID, _BENEFICIARY_FORM], "fields": ["funding"]},
    "sep_ira": {"documents": [_ID, _BENEFICIARY_FORM], "fields": ["funding", "plan_tax_year"]},
    "simple_ira": {"documents": [_ID, _BENEFICIARY_FORM], "fields": ["funding", "plan_tax_year"]},
    "employer_rollover": {"documents": [_ID, _BENEFICIARY_FORM, ("distribution", "Plan distribution form", ("plan_distribution_form",))], "fields": []},
    "inherited_ira": {"documents": [_ID, ("death", "Death certificate", ("death_certificate",))], "fields": ["decedent_date_of_death", "year_of_death_rmd"]},
    "trust": {"documents": [_ID, ("trust", "Certification of trust", ("trust_certification", "trust_deed"))], "fields": ["trust_date", "tax_id_type", "tax_id"]},
    "entity_llc": {"documents": list(_ENTITY), "fields": ["tax_id"]},
    "entity_corp": {"documents": list(_ENTITY), "fields": ["tax_id"]},
    "entity_partnership": {"documents": [_ID, ("formation", "Partnership agreement", ("partnership_agreement", "entity_formation", "trust_deed")),
                                         ("authority", "Authority to act", ("entity_resolution",))], "fields": ["tax_id"]},
    "custodial_utma": {"documents": [_ID], "fields": ["termination_age"]},
    "custodial_ugma": {"documents": [_ID], "fields": ["termination_age"]},
    "estate_inherited": {"documents": [_ID, ("death", "Death certificate", ("death_certificate",)),
                                       ("letters", "Letters testamentary", ("letters_testamentary",))], "fields": ["decedent_date_of_death", "tax_id"]},
    "plan_529": {"documents": [_ID], "fields": ["successor"]},
    "hsa": {"documents": [_ID], "fields": ["successor"]},
    "daf": {"documents": [_ID], "fields": ["successor"]},
    # India: the karta's identity, the declaration that forms the HUF, and the HUF's own PAN card. The PAN itself
    # is recorded against the entity from that card (aurea_core/pan.py), not as a registration field.
    "huf": {"documents": [_ID, ("huf", "HUF declaration", ("huf_declaration",)), ("pan", "PAN card of the HUF", ("pan_card",))],
            "fields": []},
}
FIELD_LABELS = {
    "state": "State", "married": "Owners married to each other", "funding": "Funding (contribution, rollover, conversion or transfer)",
    "contribution_tax_year": "Contribution tax year", "plan_tax_year": "Plan tax year", "decedent_date_of_death": "Date of death",
    "year_of_death_rmd": "Year-of-death RMD", "trust_date": "Trust date", "tax_id_type": "Tax ID type", "tax_id": "EIN",
    "termination_age": "Age of termination (from the state's act)", "successor": "Successor", "signed_by": "Signed by",
    "signed_on": "Application signed on", "tod": "Transfer on death",
}
FUNDING = ("contribution", "rollover", "conversion", "transfer")
RMD_STATES = ("taken", "not_taken", "not_required")
SIGNERS = {PartyRole.OWNER, PartyRole.JOINT_OWNER, PartyRole.TRUSTEE, PartyRole.CUSTODIAN, PartyRole.CONTROL_PERSON,
           PartyRole.AUTHORISED_SIGNER, PartyRole.EXECUTOR}
SHARE_HOLDERS = {"joint_tic"}                       # owners' shares make 100
BENEFICIARY_SHARES = {"traditional_ira", "roth_ira", "sep_ira", "simple_ira", "employer_rollover"}
NEEDS_STATE = {"joint_tbe", "community_property", "custodial_utma", "custodial_ugma"}

# The firm may replace either list in its settings; these are where the law is settled.
COMMUNITY_PROPERTY_STATES = {"AZ", "CA", "ID", "LA", "NV", "NM", "TX", "WA", "WI"}
TBE_STATES = {"AR", "DE", "DC", "FL", "HI", "MD", "MA", "MS", "MO", "OK", "PA", "RI", "TN", "VT", "VA", "WY"}   # for securities, not only land
STALE_DAYS = 90
_EIN = re.compile(r"^\d{2}-\d{7}$")


class MatrixError(ValueError):
    """This cannot be recorded, with the reason."""


def _settings(firm: Firm | None) -> dict:
    return (firm.settings or {}) if firm else {}


def custodian_overlay(firm: Firm | None, custodian: str | None) -> list[tuple]:
    forms = (_settings(firm).get("custodian_documents") or {}).get(custodian or "", [])
    return [(f"custodian:{k}", DOCUMENTS.get(k, k), (k,)) for k in forms]


def requirements(registration_type: str | None, *, firm: Firm | None = None, custodian: str | None = None) -> dict:
    entry = MATRIX.get(registration_type or "individual", MATRIX["individual"])
    fields = list(entry["fields"])
    if registration_type in NEEDS_STATE:
        fields.insert(0, "state")
    return {"documents": list(entry["documents"]) + custodian_overlay(firm, custodian), "fields": fields}


def _on_file(documents: list, detail: dict) -> set[str]:
    return {d.doc_type for d in documents} | set((detail.get("documents") or {}).keys())


def missing_documents(case, documents: list, *, firm: Firm | None = None) -> list[dict]:
    have = _on_file(documents, (case.intake or {}).get("registration") or {})
    return [{"key": key, "label": label, "any_of": list(kinds)} for key, label, kinds in
            requirements(case.registration_type, firm=firm, custodian=case.custodian_name)["documents"] if not have & set(kinds)]


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _issue(cause: str, severity: str, field: str, description: str) -> dict:
    return {"root_cause": str(cause), "severity": severity, "field": field, "description": description}


def check(case, parties: list, documents: list, *, firm: Firm | None, today: date) -> dict:
    """What a custodian would reject this package for, from what is on file."""
    reg = case.registration_type or "individual"
    detail = (case.intake or {}).get("registration") or {}
    settings = _settings(firm)
    issues: list[dict] = []

    for m in missing_documents(case, documents, firm=firm):
        issues.append(_issue(NIGORootCause.MISSING_ID if m["key"] == "id" else NIGORootCause.STALE_FORM, "critical", m["key"], f"Missing: {m['label']}."))

    for f in requirements(reg)["fields"]:
        if detail.get(f) in (None, ""):
            issues.append(_issue(NIGORootCause.STALE_FORM, "high", f, f"Not recorded: {FIELD_LABELS[f]}."))

    state = (detail.get("state") or "").upper()
    if reg == "joint_tbe" and state and state not in set(settings.get("tbe_states") or TBE_STATES):
        issues.append(_issue(NIGORootCause.WRONG_TENANCY, "critical", "state", f"{state} does not recognise tenancy by the entirety for securities."))
    if reg == "community_property" and state and state not in set(settings.get("community_property_states") or COMMUNITY_PROPERTY_STATES):
        issues.append(_issue(NIGORootCause.WRONG_TENANCY, "critical", "state", f"{state} is not a community property state."))
    if reg in ("joint_tbe", "community_property") and detail.get("married") is False:
        issues.append(_issue(NIGORootCause.WRONG_TENANCY, "critical", "married", "This tenancy is only for a married couple."))

    funding = detail.get("funding")
    if funding == "conversion" and reg != "roth_ira":
        issues.append(_issue(NIGORootCause.TITLE_MISMATCH, "critical", "funding", "A conversion goes into a Roth IRA."))
    if funding == "contribution" and reg in ("traditional_ira", "roth_ira"):
        year = detail.get("contribution_tax_year")
        allowed = {today.year} | ({today.year - 1} if (today.month, today.day) <= (4, 15) else set())
        if year in (None, ""):
            issues.append(_issue(NIGORootCause.STALE_FORM, "high", "contribution_tax_year", "Not recorded: Contribution tax year."))
        elif int(year) not in allowed:
            issues.append(_issue(NIGORootCause.STALE_FORM, "critical", "contribution_tax_year", f"A contribution for {year} can no longer be made."))
    plan_year = detail.get("plan_tax_year")
    if plan_year not in (None, "") and int(plan_year) not in (today.year, today.year - 1):
        issues.append(_issue(NIGORootCause.STALE_FORM, "high", "plan_tax_year", f"Plan tax year {plan_year} is neither this year nor last."))

    tax_id = detail.get("tax_id")
    if tax_id and (detail.get("tax_id_type") or "ein") == "ein" and not _EIN.match(str(tax_id)):
        issues.append(_issue(NIGORootCause.TITLE_MISMATCH, "critical", "tax_id", "An EIN is written NN-NNNNNNN."))
    for f in ("trust_date", "decedent_date_of_death"):
        d = _day(detail.get(f))
        if d and d > today:
            issues.append(_issue(NIGORootCause.STALE_FORM, "critical", f, f"{FIELD_LABELS[f]} is in the future."))
    if detail.get("year_of_death_rmd") == "not_taken":
        issues.append(_issue(NIGORootCause.STALE_FORM, "medium", "year_of_death_rmd", "The year-of-death RMD has not been taken; the beneficiary takes it."))

    age = detail.get("termination_age")
    if age not in (None, ""):
        allowed_ages = range(18, 26) if reg == "custodial_utma" else (18, 21)
        if int(age) not in allowed_ages:
            issues.append(_issue(NIGORootCause.STALE_FORM, "critical", "termination_age", f"{age} is not an age a state sets for this account."))
        for p in parties:
            dob = _day(p.dob) if p.role == PartyRole.MINOR else None
            if dob and (today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))) >= int(age):
                issues.append(_issue(NIGORootCause.TITLE_MISMATCH, "critical", "minor", f"{p.legal_name} is past the age of termination."))

    def shares(rows):
        return [float(p.ownership_pct) for p in rows if p.ownership_pct is not None], len(rows)

    if reg in SHARE_HOLDERS:
        got, n = shares([p for p in parties if p.role in (PartyRole.OWNER, PartyRole.JOINT_OWNER)])
        if len(got) < n or abs(sum(got) - 100) > 0.01:
            issues.append(_issue(NIGORootCause.TITLE_MISMATCH, "critical", "ownership", f"Owners' shares make {sum(got):g}% of 100%{'' if len(got) == n else ', and not every owner has one'}."))
    if reg in BENEFICIARY_SHARES or detail.get("tod"):
        got, n = shares([p for p in parties if p.role == PartyRole.BENEFICIARY])
        if n and (len(got) < n or abs(sum(got) - 100) > 0.01):
            issues.append(_issue(NIGORootCause.MISSING_BENEFICIARY, "critical", "beneficiaries",
                                 f"Beneficiaries' shares make {sum(got):g}% of 100%{'' if len(got) == n else ', and not every beneficiary has one'}."))

    signers = sorted({p.legal_name for p in parties if p.role in SIGNERS})
    signed_by = {s.strip().lower() for s in (detail.get("signed_by") or [])}
    signed_on = _day(detail.get("signed_on"))
    if not signed_by:
        issues.append(_issue(NIGORootCause.MISSING_SIGNATURE, "critical", "signed_by", "No signatures recorded on the application."))
    else:
        for name in signers:
            if name.lower() not in signed_by:
                issues.append(_issue(NIGORootCause.MISSING_SIGNATURE, "critical", "signed_by", f"{name} has not signed."))
    stale = int(settings.get("application_stale_days") or STALE_DAYS)
    if signed_on and signed_on > today:
        issues.append(_issue(NIGORootCause.STALE_FORM, "critical", "signed_on", "The application is dated in the future."))
    elif signed_on and (today - signed_on).days > stale:
        issues.append(_issue(NIGORootCause.STALE_FORM, "high", "signed_on", f"Signed {(today - signed_on).days} days ago; custodians take {stale}."))
    elif signed_by and not signed_on:
        issues.append(_issue(NIGORootCause.STALE_FORM, "high", "signed_on", "Not recorded: the day the application was signed."))

    weight = {"critical": 20, "high": 10, "medium": 5, "low": 2}
    blocking = [i for i in issues if i["severity"] in ("critical", "high")]
    return {"registration_type": reg, "state": state or None, "custodian": case.custodian_name, "issues": issues,
            "in_good_order": not blocking, "readiness_score": max(0, 100 - sum(weight[i["severity"]] for i in issues)),
            "signers": signers, "detail": detail,
            "requirements": {"documents": [{"key": k, "label": label, "any_of": list(kinds), "on_file": bool(_on_file(documents, detail) & set(kinds))}
                                           for k, label, kinds in requirements(reg, firm=firm, custodian=case.custodian_name)["documents"]],
                             "fields": [{"field": f, "label": FIELD_LABELS[f], "value": detail.get(f)} for f in requirements(reg)["fields"]]}}


async def check_case(session: AsyncSession, case: OnboardingCase, firm: Firm) -> dict:
    parties = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all()
    documents = (await session.execute(select(OnboardingDocument).where(OnboardingDocument.case_id == case.id))).scalars().all()
    return check(case, list(parties), list(documents), firm=firm, today=firm_time.today(firm))


def matrix(firm: Firm) -> dict:
    """The firm's matrix, as the view shows it."""
    settings = _settings(firm)
    rows = [{"registration_type": reg, "documents": [{"label": label, "any_of": [DOCUMENTS.get(k, k) for k in kinds]} for _, label, kinds in entry["documents"]],
             "fields": [FIELD_LABELS[f] for f in requirements(reg)["fields"]]} for reg, entry in MATRIX.items()]
    return {"registrations": rows, "custodians": {c: [DOCUMENTS.get(k, k) for k in forms] for c, forms in (settings.get("custodian_documents") or {}).items()},
            "states": {"tenancy_by_the_entirety": sorted(settings.get("tbe_states") or TBE_STATES),
                       "community_property": sorted(settings.get("community_property_states") or COMMUNITY_PROPERTY_STATES)},
            "stale_days": int(settings.get("application_stale_days") or STALE_DAYS), "documents": DOCUMENTS}


# ── Recording, from the application or a document ────────────────────────────

def _clean(field: str, value, reg: str):
    if field in ("married", "tod"):
        if isinstance(value, str):
            value = value.strip().lower() in ("yes", "true", "1", "y")
        return bool(value)
    if field == "state":
        v = str(value).strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise MatrixError("A state is its two-letter code.")
        return v
    if field == "funding":
        v = str(value).strip().lower()
        if v not in FUNDING:
            raise MatrixError(f"Funding is one of {', '.join(FUNDING)}.")
        return v
    if field in ("contribution_tax_year", "plan_tax_year", "termination_age"):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise MatrixError(f"{FIELD_LABELS[field]} is a number.")
    if field == "year_of_death_rmd":
        v = str(value).strip().lower().replace(" ", "_")
        if v not in RMD_STATES:
            raise MatrixError(f"The year-of-death RMD is one of {', '.join(RMD_STATES)}.")
        return v
    if field == "tax_id_type":
        v = str(value).strip().lower()
        if v not in ("ein", "ssn"):
            raise MatrixError("The tax ID is an EIN, or the grantor's SSN for a grantor trust.")
        return v
    if field in ("trust_date", "decedent_date_of_death", "signed_on"):
        d = _day(value)
        if d is None:
            raise MatrixError(f"{FIELD_LABELS[field]} is a date, YYYY-MM-DD.")
        return d.isoformat()
    if field == "signed_by":
        names = [n.strip() for n in (value if isinstance(value, list) else str(value).split(",")) if n.strip()]
        if not names:
            raise MatrixError("Name who signed.")
        return names
    return str(value).strip()


async def prepare_detail(session: AsyncSession, firm_id: uuid.UUID, *, case_id: uuid.UUID, fields: dict, documents: list[str] | None,
                         evidence: str | None) -> dict:
    case = await session.get(OnboardingCase, case_id)
    if case is None or case.firm_id != firm_id:
        raise MatrixError("Case not found.")
    if case.activated_at:
        raise MatrixError("This account is open; its registration changes through the custodian, not here.")
    named = (evidence or "").strip()
    if len(named) < 5:
        raise MatrixError("Say where it comes from: the application, the trust certification, the death certificate.")
    reg = case.registration_type or "individual"
    clean = {}
    for f, v in (fields or {}).items():
        if v in (None, ""):
            continue
        if f not in FIELD_LABELS:
            raise MatrixError(f"{f} is not a registration detail.")
        clean[f] = _clean(f, v, reg)
    kinds = []
    for k in documents or []:
        k = k.strip().lower()
        if k not in DOCUMENTS:
            raise MatrixError(f"A document is one of {', '.join(DOCUMENTS)}.")
        kinds.append(k)
    if not clean and not kinds:
        raise MatrixError("Nothing to record.")
    return {"case": case, "fields": clean, "documents": kinds, "evidence": named}


async def record_detail(session: AsyncSession, firm: Firm, *, by: str, **terms) -> dict:
    r = await prepare_detail(session, firm.id, **terms)
    case = r["case"]
    intake = dict(case.intake or {})
    reg = dict(intake.get("registration") or {})
    reg.update(r["fields"])
    on_file = dict(reg.get("documents") or {})
    today = firm_time.today(firm).isoformat()
    for k in r["documents"]:
        on_file[k] = {"evidence": r["evidence"], "recorded_by": by, "recorded_on": today}
    if on_file:
        reg["documents"] = on_file
    reg.setdefault("sources", []).append({"fields": sorted(r["fields"]), "documents": r["documents"], "evidence": r["evidence"], "by": by, "on": today})
    intake["registration"] = reg
    case.intake = intake
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="registration_detail_recorded", actor=by,
                              content={"case_id": str(case.id), "fields": sorted(r["fields"]), "documents": r["documents"], "evidence": r["evidence"]})
    return await check_case(session, case, firm)


async def prepare_custodian(session: AsyncSession, firm_id: uuid.UUID, *, custodian: str | None, documents: list[str] | None, source: str | None) -> dict:
    name = (custodian or "").strip()
    if len(name) < 2:
        raise MatrixError("Name the custodian.")
    kinds = []
    for k in documents or []:
        k = k.strip().lower()
        if k not in DOCUMENTS:
            raise MatrixError(f"A document is one of {', '.join(DOCUMENTS)}.")
        kinds.append(k)
    if len((source or "").strip()) < 5:
        raise MatrixError("Say where the custodian sets this out: its new-account guide, its service agreement.")
    firm = await session.get(Firm, firm_id)
    current = (_settings(firm).get("custodian_documents") or {}).get(name, [])
    return {"custodian": name, "documents": sorted(set(kinds)), "was": current, "source": source.strip()}


async def record_custodian(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_custodian(session, firm_id, **terms)
    firm = await session.get(Firm, firm_id)
    settings = dict(firm.settings or {})
    forms = dict(settings.get("custodian_documents") or {})
    forms[r["custodian"]] = r["documents"]
    settings["custodian_documents"] = forms
    firm.settings = settings
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="custodian_documents_set", actor=by,
                              content={"custodian": r["custodian"], "documents": r["documents"], "was": r["was"], "source": r["source"]})
    return matrix(firm)

