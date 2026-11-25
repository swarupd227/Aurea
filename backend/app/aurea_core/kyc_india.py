"""A client's KYC in India, as a person recorded it from the document, and where it stands today (India row 12).

KYC here is a record, never a lookup: a person reads the KRA's acknowledgement, the CKYC registry's letter or the client's own
document, and records what it says. Real KRA and CKYC lookups are not connected, so nothing is fetched, nothing is verified
against a source, and nothing is estimated. The latest record for a party stands, added and never edited.

What is recorded: the CKYC number (the 14-digit KIN), the KRA and its status (validated, registered, on hold, rejected), the day
the KYC or re-KYC was done and how it was verified (in-person, video, Aadhaar e-KYC, DigiLocker), the last four digits of the
Aadhaar and whether it is linked to the PAN, the identity and address proofs and when they run out, whether the client is a
politically exposed person, for an entity the structure and its beneficial owners, and any event since that asks for a refresh.
An Aadhaar number is never stored or shown: the intermediary may not keep it (KYC Master Circular para 15), so a 12-digit number is
refused and only the last four are kept.

From those the state of each party is worked out, never set:

    not_on_file   no record. It does not mean no KYC was done.
    blocked       the KRA's status is On Hold or Rejected: the client may not transact until it is Registered or Validated
    overdue       the re-KYC date has passed for the party's risk band, or an identity or address proof has expired
    due           the date falls within 60 days, the KRA holds it Registered only (documents not verified at source), a refresh
                  trigger came after the KYC, or a recorded fact is short of what the circular asks
    verified      none of those
    not_assessed  the record cannot be dated, has no risk band to give the clock, or is an entity whose owners are not recorded

How often a KYC is refreshed is the firm's. SEBI's KYC Master Circular (para 29) says to update periodically by risk category and
sets no limit; the platform's default, high every 2 years, medium every 8 and low every 10, is the period the RBI's KYC Master
Direction sets for banks, borrowed and assumed, and the firm may set its own in `settings["kyc_review_months"]`. A politically
exposed person is never below high. The band is the one recorded on the KYC record, else the firm's latest AML rating of the
account (`aml_risk`); with neither the clock is not assessed, never defaulted to low.

For an entity the beneficial-owner test is SEBI's AML Master Circular as read in the research: more than 10% of a company or
partnership, more than 15% of an unincorporated body, and for a trust a beneficiary with 10% or more and always the author,
trustee and protector. CRS controlling persons (more than 25% of a company) are a different test and are listed apart, never one field.

An overdue or blocked KYC appears in the onboarding board's KYC queue, fails the activation gate and holds the household's book: a
book-moving approval (`runtime.decide`) and a direct order (`order_guard`) are refused, naming the KYC and who records the new one
(B-273). A due one or none on file warns and stops nothing. Nothing here files with a regulator or tells the client. This is not a statement that the firm's KYC is compliant.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.aurea_core.reviews import add_months
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Household, LegalEntity, Person
from app.models.kyc import KycRecord
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE})
KRA_STATUSES = {"validated": "Validated", "registered": "Registered", "on_hold": "On Hold", "rejected": "Rejected"}
KRA_MEANING = {
    "validated": "Every attribute was verified against official databases, and the record is portable.",
    "registered": "The documents could not be verified at source: the next intermediary must repeat the KYC.",
    "on_hold": "The client may not transact until the status becomes Registered or Validated.",
    "rejected": "The client must restart KYC.",
}
MODES = {"ipv": "In-person verification", "video_kyc": "Video KYC", "aadhaar_ekyc": "Aadhaar e-KYC", "digilocker": "DigiLocker"}
NEEDS_IPV = ("ipv",)                    # the other modes are themselves the verification: IPV is waived (paras 54-61)
BANDS = ("low", "medium", "high")
DEFAULT_REVIEW_MONTHS = {"high": 24, "medium": 96, "low": 120}
REVIEW_SOURCE = ("Assumed: SEBI's KYC Master Circular para 29 asks for periodic updating by risk category and sets no limit. The default is the "
                 "period the RBI's KYC Master Direction sets for banks (high 2 years, medium 8, low 10); the firm may set its own.")
DUE_SOON_DAYS = 60
STRUCTURES = {"company": "Company", "partnership": "Partnership", "unincorporated": "Unincorporated body or association",
              "trust": "Trust", "huf": "HUF"}
OWNER_ROLES = {"shareholder": "Shareholder", "partner": "Partner", "member": "Member", "beneficiary": "Beneficiary",
               "author": "Author of the trust", "trustee": "Trustee", "protector": "Protector", "karta": "Karta", "other": "Other"}
TRUST_ALWAYS = ("author", "trustee", "protector")
TRIGGERS = {"address_change": "Change of address", "name_change": "Change of name", "ownership_change": "Change in ownership or control",
            "adverse_media": "Adverse media", "pep_change": "Change in political exposure", "unusual_activity": "Unusual activity",
            "risk_band_change": "Change in risk band", "kra_notice": "Notice from the KRA"}
ID_KINDS = {"passport": "Passport", "driving_licence": "Driving licence", "voter_id": "Voter ID", "aadhaar_proof": "Proof of possession of Aadhaar",
            "nrega_card": "NREGA job card", "npr_letter": "NPR letter"}
ADDRESS_KINDS = {**ID_KINDS, "utility_bill": "Utility bill (a deemed document)", "bank_statement": "Bank statement (a deemed document)"}
DEEMED = ("utility_bill", "bank_statement")
SCALAR = ("ckyc_number", "kra_name", "kra_status", "kyc_completed_on", "verification_mode", "ipv_on", "aadhaar_last4", "aadhaar_xml_dated",
          "pan_aadhaar_linked", "id_proof_kind", "id_proof_valid_to", "address_proof_kind", "address_proof_dated", "address_proof_valid_to",
          "risk_band", "pep", "structure")
LABELS = {
    "ckyc_number": "CKYC number", "kra_name": "KRA", "kra_status": "KRA status", "kyc_completed_on": "KYC done on",
    "verification_mode": "Verified by", "ipv_on": "In-person verification on", "aadhaar_last4": "Aadhaar, last four",
    "aadhaar_xml_dated": "Aadhaar XML or QR dated", "pan_aadhaar_linked": "PAN linked to Aadhaar", "id_proof_kind": "Identity proof",
    "id_proof_valid_to": "Identity proof valid to", "address_proof_kind": "Address proof", "address_proof_dated": "Address proof dated",
    "address_proof_valid_to": "Address proof valid to", "risk_band": "Risk band", "pep": "Politically exposed", "structure": "Structure",
    "beneficial_owners": "Beneficial owners",
}
DATE_FIELDS = ("kyc_completed_on", "ipv_on", "aadhaar_xml_dated", "id_proof_valid_to", "address_proof_dated", "address_proof_valid_to")
AADHAAR_NUMBER = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
CITATION = "SEBI Master Circular on KYC (12 Oct 2023) as amended; the research report section 3.3. Real KRA and CKYC lookups are not connected."


class KycError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def mask_aadhaar(last4: str | None) -> str | None:
    return ("•" * 8 + last4) if last4 else None


def review_months(firm: Firm | None) -> dict:
    """The refresh period by band: the firm's own where it set one, else the platform's assumed default."""
    own = ((firm.settings or {}).get("kyc_review_months") if firm else None) or {}
    out = {}
    for band in BANDS:
        try:
            months = int(own.get(band)) if own.get(band) else None
        except (TypeError, ValueError):
            months = None
        out[band] = {"months": months or DEFAULT_REVIEW_MONTHS[band], "source": "the firm's setting" if months else "assumed default"}
    return out


# ── Reading one record ──────────────────────────────────────────────────────────────────────────────────────────
def _day(value) -> date | None:
    if value in (None, ""):
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def beneficial_owners(structure: str | None, owners: list[dict]) -> dict:
    """Who the recorded owners make beneficial owners, and who are CRS controlling persons: two tests, never one field."""
    if not structure:
        return {"beneficial": [], "controlling": [], "threshold": None, "needs": ["structure"]}
    if structure == "huf":
        k = [o["name"] for o in owners if o.get("role") == "karta"]
        return {"beneficial": k, "controlling": [], "threshold": None, "needs": [] if k else ["the karta"],
                "rule": "The karta acts and signs; coparceners are not beneficial owners by share."}
    if structure in ("company", "partnership"):
        t = law_figures.value("in_kyc_bo_company")
        rule = f"More than {t * 100:g}% of the capital or profits, or control by other means."
        bo = [o["name"] for o in owners if float(o.get("share") or 0) / 100 > t]
    elif structure == "unincorporated":
        t = law_figures.value("in_kyc_bo_unincorporated")
        rule = f"More than {t * 100:g}% of the property or capital or profits."
        bo = [o["name"] for o in owners if float(o.get("share") or 0) / 100 > t]
    else:
        t = law_figures.value("in_kyc_bo_trust")
        rule = f"A beneficiary with {t * 100:g}% or more of the interest, and always the author, trustee and protector."
        bo = [o["name"] for o in owners if o.get("role") in TRUST_ALWAYS or (o.get("role") == "beneficiary" and float(o.get("share") or 0) / 100 >= t)]
    crs = []
    if structure == "company":
        c = law_figures.value("in_kyc_crs_company")
        crs = [o["name"] for o in owners if float(o.get("share") or 0) / 100 > c]
    elif structure == "partnership":
        crs = [o["name"] for o in owners if float(o.get("share") or 0) > 15]
    elif structure == "trust":
        crs = [o["name"] for o in owners if o.get("role") in (*TRUST_ALWAYS, "beneficiary")]
    return {"beneficial": bo, "controlling": crs, "threshold": t, "rule": rule, "needs": [] if owners else ["the owners, from the shareholding or the deed"]}


def assess(r: KycRecord | None, *, today: date, firm: Firm | None, aml_band: str | None = None, is_entity: bool = False) -> dict:
    """The state of one party from its latest record. Pure."""
    if r is None:
        return {"state": "not_on_file", "message": "No KYC record on file. That does not mean none was done.", "issues": [], "needs": [],
                "due_on": None, "band": None}
    issues: list[str] = []
    needs: list[str] = []
    band = r.risk_band or aml_band
    band_from = "the KYC record" if r.risk_band else ("the firm's AML rating" if aml_band else None)
    if r.pep:
        if band != "high":
            band_from = "political exposure (never below high)"
        band = "high"
    done = _day(r.kyc_completed_on)
    months = review_months(firm)
    due_on = add_months(done, months[band]["months"]) if done and band else None
    triggers = [t for t in (r.triggers or []) if done and _day(t.get("on")) and _day(t["on"]) > done]

    if not r.kra_status:
        needs.append("KRA status")
    if not r.ckyc_number:
        needs.append("CKYC number")
    if not done:
        needs.append("the day the KYC was done")
    if not band:
        needs.append("risk band")
    if r.aadhaar_last4 is None and r.pan_aadhaar_linked is None:
        needs.append("Aadhaar and PAN linkage")
    if r.verification_mode is None:
        needs.append("how it was verified (in person, video, Aadhaar or DigiLocker)")

    # Defects: a recorded fact short of what the circular asks.
    if r.pan_aadhaar_linked is False:
        issues.append("PAN is not linked to Aadhaar: the KRA validates PAN with its linkage")
    if r.verification_mode in NEEDS_IPV and r.ipv_on is None:
        issues.append("In-person verification is the mode, with no date recorded for it")
    xml = _day(r.aadhaar_xml_dated)
    limit = int(law_figures.value("in_kyc_aadhaar_xml_days"))
    if xml and done and (done - xml).days > limit:
        issues.append(f"The Aadhaar XML or QR was {(done - xml).days} days old at KYC; it must be no older than {limit}")
    addr = _day(r.address_proof_dated)
    deemed_months = int(law_figures.value("in_kyc_address_proof_months"))
    if r.address_proof_kind in DEEMED and addr and done and add_months(addr, deemed_months) < done:
        issues.append(f"A deemed address proof was older than {deemed_months} months at KYC; an updated official document is needed")
    if r.kra_status == "registered":
        issues.append("Registered with the KRA: the documents were not verified at source")
    for t in triggers:
        issues.append(f"{TRIGGERS.get(t['kind'], t['kind'])} on {t['on']}, after the KYC: refresh it")

    expired = []
    for label, field in (("Identity proof", "id_proof_valid_to"), ("Address proof", "address_proof_valid_to")):
        d = _day(getattr(r, field))
        if d and d < today:
            expired.append(f"{label} expired on {d.isoformat()}")

    ubo = None
    if is_entity or r.structure:
        owners = list(r.beneficial_owners or [])
        ubo = beneficial_owners(r.structure, owners)
        needs += [f"for the entity: {n}" for n in ubo["needs"]]
        for o in owners:
            if o["name"] in ubo["beneficial"] and o.get("kyc_on_file") is False:
                issues.append(f"Beneficial owner {o['name']} has no KYC on file")

    if r.kra_status in ("on_hold", "rejected"):
        state, message = "blocked", KRA_MEANING[r.kra_status]
    elif expired or (due_on and due_on < today):
        state = "overdue"
        message = "; ".join(expired + ([f"Re-KYC was due {due_on.isoformat()}"] if due_on and due_on < today else []))
    elif (due_on and (due_on - today).days <= DUE_SOON_DAYS) or issues:
        state = "due"
        message = "; ".join(([f"Re-KYC falls due {due_on.isoformat()}"] if due_on and (due_on - today).days <= DUE_SOON_DAYS else []) + issues)
    elif not done or not band:
        state, message = "not_assessed", "Cannot be dated or given a refresh clock: " + ", ".join(n for n in needs if n in ("the day the KYC was done", "risk band"))
    elif ubo and ubo["needs"]:
        # An entity's KYC is not verified until the people behind it are recorded: not assessed, never read as complete.
        state, message = "not_assessed", "Who owns it is not recorded: " + ", ".join(ubo["needs"])
    else:
        state, message = "verified", f"Re-KYC falls due {due_on.isoformat()}."
    return {"state": state, "message": message, "issues": issues, "expired": expired, "needs": needs, "due_on": due_on.isoformat() if due_on else None,
            "days": (due_on - today).days if due_on else None, "band": band, "band_from": band_from,
            "review_months": months[band]["months"] if band else None, "owners": ubo,
            "mode_label": MODES.get(r.verification_mode or "")}


def view(r: KycRecord | None, name: str, *, today: date, firm: Firm | None, aml_band: str | None = None, is_entity: bool = False,
         key: dict | None = None) -> dict:
    a = assess(r, today=today, firm=firm, aml_band=aml_band, is_entity=is_entity)
    base = {"name": name, **(key or {}), **a, "recorded": r is not None}
    if r is None:
        return base
    return {**base, "ckyc_number": r.ckyc_number, "kra_name": r.kra_name, "kra_status": r.kra_status,
            "kra_label": KRA_STATUSES.get(r.kra_status or ""), "kyc_completed_on": r.kyc_completed_on.isoformat() if r.kyc_completed_on else None,
            "verification_mode": r.verification_mode, "ipv_on": r.ipv_on.isoformat() if r.ipv_on else None,
            "aadhaar": mask_aadhaar(r.aadhaar_last4), "pan_aadhaar_linked": r.pan_aadhaar_linked,
            "id_proof": {"kind": ID_KINDS.get(r.id_proof_kind or "", r.id_proof_kind), "valid_to": r.id_proof_valid_to.isoformat() if r.id_proof_valid_to else None},
            "address_proof": {"kind": ADDRESS_KINDS.get(r.address_proof_kind or "", r.address_proof_kind),
                              "valid_to": r.address_proof_valid_to.isoformat() if r.address_proof_valid_to else None,
                              "dated": r.address_proof_dated.isoformat() if r.address_proof_dated else None},
            "pep": r.pep, "structure": r.structure, "structure_label": STRUCTURES.get(r.structure or ""),
            "beneficial_owners": list(r.beneficial_owners or []), "triggers": list(r.triggers or []),
            "document": r.document, "recorded_by": r.recorded_by, "recorded_on": r.created_at.date().isoformat() if r.created_at else None}


# ── Reading the book ────────────────────────────────────────────────────────────────────────────────────────────
def _key(r: KycRecord) -> tuple:
    return ("person", r.person_id) if r.person_id else ("entity", r.entity_id) if r.entity_id else ("case", r.case_id, r.party_name.lower())


async def latest(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    rows = (await session.execute(select(KycRecord).where(KycRecord.firm_id == firm_id).order_by(KycRecord.created_at))).scalars().all()
    out: dict = {}
    for r in rows:
        out[_key(r)] = r
    return out


async def _case_bands(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    from app.models.aml_risk import AmlRiskRating

    out: dict = {}
    for r in (await session.execute(select(AmlRiskRating).where(AmlRiskRating.firm_id == firm_id).order_by(AmlRiskRating.created_at))).scalars().all():
        out[r.case_id] = r.band
    return out


async def _case_of_holder(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """The onboarding case that created each person or entity, so its AML band can stand for the KYC clock."""
    out: dict = {}
    for c in (await session.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm_id))).scalars().all():
        for k in ("person_id", "entity_id"):
            if (c.materialized or {}).get(k):
                out[uuid.UUID(str((c.materialized or {})[k]))] = c.id
    return out


async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    """Every client and entity with its KYC state, worst first, and who has none on file."""
    if not applies(firm):
        return {"applies": False, "parties": []}
    today = firm_time.today(firm)
    records = await latest(session, firm.id)
    bands, case_of = await _case_bands(session, firm.id), await _case_of_holder(session, firm.id)
    pq = select(Person).where(Person.firm_id == firm.id, Person.deceased_on.is_(None))
    eq = select(LegalEntity).join(Household, Household.id == LegalEntity.household_id).where(Household.firm_id == firm.id)
    if household_id:
        pq, eq = pq.where(Person.household_id == household_id), eq.where(LegalEntity.household_id == household_id)
    parties = []
    for p in (await session.execute(pq.order_by(Person.full_name))).scalars().all():
        parties.append(view(records.get(("person", p.id)), p.full_name, today=today, firm=firm, aml_band=bands.get(case_of.get(p.id)),
                            key={"person_id": str(p.id), "kind": "individual"}))
    for e in (await session.execute(eq.order_by(LegalEntity.name))).scalars().all():
        parties.append(view(records.get(("entity", e.id)), e.name, today=today, firm=firm, aml_band=bands.get(case_of.get(e.id)), is_entity=True,
                            key={"entity_id": str(e.id), "kind": str(e.entity_type)}))
    if not household_id:
        for k, r in records.items():
            if k[0] == "case":
                parties.append(view(r, r.party_name, today=today, firm=firm, is_entity=bool(r.structure), key={"case_id": str(r.case_id), "kind": "case"}))
    order = {"overdue": 0, "blocked": 1, "due": 2, "not_assessed": 3, "not_on_file": 4, "verified": 5}
    parties.sort(key=lambda x: (order[x["state"]], x["name"]))
    counts = {s: sum(1 for p in parties if p["state"] == s) for s in order}
    return {"applies": True, "as_of": today.isoformat(), "parties": parties, "counts": counts,
            "policy": {"review_months": review_months(firm), "source": REVIEW_SOURCE}, "citation": CITATION,
            "not_connected": "Real KRA and CKYC lookups are not connected: a record is what a person read from the document or the KRA's acknowledgement.",
            "not_compliance": "KYC as recorded here; it does not make the firm compliant."}


# ── Recording ───────────────────────────────────────────────────────────────────────────────────────────────────
def _clean(field: str, value, today: date):
    if value in (None, ""):
        return None
    if field == "ckyc_number":
        digits = re.sub(r"[\s-]", "", str(value))
        if not digits.isdigit() or len(digits) != 14:
            raise KycError("A CKYC number is the 14 digits the registry sent (the KIN).")
        return digits
    if field == "kra_status":
        v = str(value).strip().lower().replace(" ", "_")
        if v not in KRA_STATUSES:
            raise KycError(f"The KRA status is one of {', '.join(KRA_STATUSES)}.")
        return v
    if field == "verification_mode":
        v = str(value).strip().lower().replace(" ", "_")
        if v not in MODES:
            raise KycError(f"The verification is one of {', '.join(MODES)}.")
        return v
    if field == "risk_band":
        v = str(value).strip().lower()
        if v not in BANDS:
            raise KycError("The risk band is low, medium or high.")
        return v
    if field == "structure":
        v = str(value).strip().lower()
        if v not in STRUCTURES:
            raise KycError(f"The structure is one of {', '.join(STRUCTURES)}.")
        return v
    if field == "aadhaar_last4":
        text = str(value).strip()
        if AADHAAR_NUMBER.search(text) or len(re.sub(r"\D", "", text)) > 4:
            raise KycError("An Aadhaar number is never stored. Give only its last four digits.")
        if not re.fullmatch(r"\d{4}", text):
            raise KycError("The last four digits of the Aadhaar, as four digits.")
        return text
    if field in ("pan_aadhaar_linked", "pep"):
        if isinstance(value, bool):
            return value
        v = str(value).strip().lower()
        if v in ("true", "yes", "y", "1"):
            return True
        if v in ("false", "no", "n", "0"):
            return False
        raise KycError(f"{LABELS[field]} is yes or no.")
    if field in ("id_proof_kind",):
        v = str(value).strip().lower().replace(" ", "_")
        if v not in ID_KINDS:
            raise KycError(f"The identity proof is one of {', '.join(ID_KINDS)}.")
        return v
    if field == "address_proof_kind":
        v = str(value).strip().lower().replace(" ", "_")
        if v not in ADDRESS_KINDS:
            raise KycError(f"The address proof is one of {', '.join(ADDRESS_KINDS)}.")
        return v
    if field in DATE_FIELDS:
        try:
            d = _day(value)
        except ValueError:
            raise KycError(f"{LABELS[field]} is a date, YYYY-MM-DD.")
        if field in ("kyc_completed_on", "ipv_on", "aadhaar_xml_dated", "address_proof_dated") and d > today:
            raise KycError(f"{LABELS[field]} cannot be in the future.")
        return d
    return str(value).strip() or None


def _owners(value) -> list[dict] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        out = []
        for part in value.split(";"):
            bits = [b.strip() for b in part.split(",")]
            if len(bits) >= 2 and bits[0]:
                out.append({"name": bits[0], "share": bits[1] or None, "role": bits[2] if len(bits) > 2 and bits[2] else "other",
                            "kyc_on_file": (bits[3].lower() in ("yes", "true", "y", "1")) if len(bits) > 3 and bits[3] else None})
        value = out
    clean = []
    for o in value:
        if not isinstance(o, dict) or not str(o.get("name") or "").strip():
            raise KycError("Each owner has a name, a share, a role and whether their KYC is on file: 'Asha Rao, 40, shareholder, yes; ...'.")
        try:
            share = None if o.get("share") in (None, "") else float(o["share"])
        except (TypeError, ValueError):
            raise KycError(f"{o['name']}'s share is a percentage.")
        if share is not None and not 0 <= share <= 100:
            raise KycError(f"{o['name']}'s share is between 0 and 100.")
        role = str(o.get("role") or "other").strip().lower()
        if role not in OWNER_ROLES:
            raise KycError(f"An owner's role is one of {', '.join(OWNER_ROLES)}.")
        kyc = o.get("kyc_on_file")
        clean.append({"name": " ".join(str(o["name"]).split()), "share": share, "role": role, "kyc_on_file": kyc if isinstance(kyc, bool) or kyc is None else str(kyc).lower() in ("yes", "true", "y", "1")})
    return clean


async def prepare(session: AsyncSession, firm: Firm, *, person_id=None, entity_id=None, case_id=None, party_name: str | None = None,
                  document: str | None = None, beneficial_owners_text=None, trigger: str | None = None, trigger_on=None, **facts) -> dict:
    if not applies(firm):
        raise KycError("KYC is recorded for an Indian firm's clients.")
    today = firm_time.today(firm)
    ids = [x for x in (person_id, entity_id) if x]
    if len(ids) > 1:
        raise KycError("Say whose KYC it is: one person, one entity, or a case with the party's name.")
    holder, name = None, " ".join((party_name or "").split())
    try:
        if person_id:
            holder = await session.get(Person, uuid.UUID(str(person_id)))
        elif entity_id:
            holder = await session.get(LegalEntity, uuid.UUID(str(entity_id)))
    except ValueError:
        raise KycError("Give the client's id, from the client's page.")
    if ids:
        household = await session.get(Household, holder.household_id) if holder is not None and holder.household_id else None
        if holder is None or household is None or household.firm_id != firm.id:
            raise KycError("No such client or entity at this firm.")
        name = holder.full_name if isinstance(holder, Person) else holder.name
    case = None
    if case_id:
        try:
            case = await session.get(OnboardingCase, uuid.UUID(str(case_id)))
        except ValueError:
            raise KycError("Give the case's id, from the onboarding board.")
        if case is None or case.firm_id != firm.id:
            raise KycError("No such onboarding case at this firm.")
    if not ids and not (case and len(name) >= 3):
        raise KycError("Say whose KYC it is: a client or entity, or an onboarding case and the party's name.")
    if len((document or "").strip()) < 4:
        raise KycError("Say where it is on file: the KRA's acknowledgement, the CKYC letter or the client's document.")
    for text in (document, party_name, trigger):
        if text and AADHAAR_NUMBER.search(str(text)):
            raise KycError("An Aadhaar number is never stored, not even in a note. Leave it out; the last four digits go in their own field.")
    given = {}
    for f in SCALAR:
        if f in facts and facts[f] not in (None, ""):
            given[f] = _clean(f, facts[f], today)
    owners = _owners(beneficial_owners_text if beneficial_owners_text is not None else facts.get("beneficial_owners"))
    trig = None
    if trigger:
        kind = str(trigger).strip().lower().replace(" ", "_")
        if kind not in TRIGGERS:
            raise KycError(f"A refresh trigger is one of {', '.join(TRIGGERS)}.")
        on = _clean("kyc_completed_on", trigger_on or today, today)
        trig = {"kind": kind, "on": on.isoformat()}
    records = await latest(session, firm.id)
    key = ("person", holder.id) if isinstance(holder, Person) else ("entity", holder.id) if isinstance(holder, LegalEntity) \
        else ("case", case.id if case else None, name.lower())
    before = records.get(key)
    merged = {f: given.get(f, getattr(before, f, None) if before else None) for f in SCALAR}
    merged_owners = owners if owners is not None else list(before.beneficial_owners or []) if before else []
    merged_triggers = list(before.triggers or []) if before else []
    if trig:
        merged_triggers.append(trig)
    if merged["aadhaar_xml_dated"] and merged["kyc_completed_on"] and merged["aadhaar_xml_dated"] > merged["kyc_completed_on"]:
        raise KycError("The Aadhaar XML cannot be dated after the KYC it was used for.")
    if not given and owners is None and not trig:
        raise KycError("Give at least one fact the document states.")
    changes = []
    for f in (*SCALAR, "beneficial_owners"):
        old = getattr(before, f, None) if before else None
        new = merged_owners if f == "beneficial_owners" else merged[f]
        if f == "beneficial_owners":
            old = list(old or [])
        if old != new and (new not in (None, [], "")):
            changes.append({"field": f, "label": LABELS[f], "before": old if old not in (None, []) else None, "after": new})
    if trig:
        changes.append({"field": "trigger", "label": "Refresh trigger", "before": None, "after": f"{TRIGGERS[trig['kind']]} on {trig['on']}"})
    if not changes:
        raise KycError("Nothing changes: the record already says that.")
    row = KycRecord(firm_id=firm.id, person_id=holder.id if isinstance(holder, Person) else None,
                    entity_id=holder.id if isinstance(holder, LegalEntity) else None, case_id=case.id if case else (before.case_id if before else None),
                    party_name=name, beneficial_owners=merged_owners, triggers=merged_triggers, document=document.strip(), **merged)
    band = (await _case_bands(session, firm.id)).get((await _case_of_holder(session, firm.id)).get(holder.id)) if holder is not None else None
    state = assess(row, today=today, firm=firm, aml_band=band, is_entity=isinstance(holder, LegalEntity) or bool(merged["structure"]))
    return {"row": row, "name": name, "changes": changes, "state": state, "before": before, "holder": holder}


async def record(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare(session, firm, **terms)
    row: KycRecord = p["row"]
    row.recorded_by = by
    row.note = (note or "").strip() or None
    row.created_at = utcnow()
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="kyc_recorded", actor=by, content={
        "party": row.party_name, "kra_status": row.kra_status, "ckyc_recorded": bool(row.ckyc_number), "aadhaar": mask_aadhaar(row.aadhaar_last4),
        "kyc_completed_on": row.kyc_completed_on.isoformat() if row.kyc_completed_on else None, "document": row.document,
        "state": p["state"]["state"]})
    household = None
    holder = p["holder"]
    if holder is not None:
        household = holder.household_id
    return await overview(session, firm, household_id=household)


# ── The onboarding board and the activation gate ────────────────────────────────────────────────────────────────
async def _records_for_case(session: AsyncSession, firm_id: uuid.UUID, case: OnboardingCase) -> list[KycRecord]:
    records = await latest(session, firm_id)
    mat = case.materialized or {}
    wanted = set()
    for k, kind in (("person_id", "person"), ("entity_id", "entity")):
        if mat.get(k):
            wanted.add((kind, uuid.UUID(str(mat[k]))))
    return [r for k, r in records.items() if k in wanted or (k[0] == "case" and k[1] == case.id) or (r.case_id == case.id)]


async def case_status(session: AsyncSession, firm: Firm, case: OnboardingCase) -> dict | None:
    """The KYC of a case's parties: what the activation gate and the board's KYC queue read. None outside India."""
    if not applies(firm):
        return None
    today = firm_time.today(firm)
    rows = await _records_for_case(session, firm.id, case)
    band = (await _case_bands(session, firm.id)).get(case.id)
    views = [view(r, r.party_name, today=today, firm=firm, aml_band=band, is_entity=bool(r.structure)) for r in rows]
    by = lambda s: [v for v in views if v["state"] == s]  # noqa: E731
    return {"applies": True, "records": len(views), "overdue": by("overdue"), "blocked": by("blocked"), "due": by("due"), "parties": views}


def queue_lines(status: dict | None) -> list[str]:
    """What the board's KYC queue says for a case: overdue, blocked and due, each with its reason."""
    if not status:
        return []
    return ([f"KYC overdue, {v['name']}: {v['message']}" for v in status["overdue"]]
            + [f"KYC blocked, {v['name']}: KRA status {v.get('kra_label') or 'held'}. {v['message']}" for v in status["blocked"]]
            + [f"KYC due, {v['name']}: {v['message']}" for v in status["due"]])


def gate(status: dict | None) -> dict | None:
    """The activation gate's state and words for a case, or None where KYC is not read (outside India)."""
    if not status:
        return None
    bad = status["overdue"] + status["blocked"]
    if bad:
        return {"state": "fail", "detail": "; ".join(f"{v['name']}: {v['message']}" for v in bad)}
    if not status["records"]:
        return {"state": "warn", "detail": "No KYC record on file for this account's parties. Not on file does not mean not done: record it from the KRA's acknowledgement."}
    if status["due"]:
        return {"state": "warn", "detail": "; ".join(f"{v['name']}: {v['message']}" for v in status["due"])}
    return {"state": "pass", "detail": f"{status['records']} party(ies) with KYC on file and current."}


# ── At order time (B-273) ────────────────────────────────────────────────────────────────────────────────────────

async def household_refusal(session: AsyncSession, firm: Firm | None, household_id: uuid.UUID | None) -> str | None:
    """Why this household's book may not move, or None. An overdue or blocked KYC stops it, the way the pre-advice gate does; a KYC
    that is only due, or not on file, warns elsewhere and stops nothing, because not on file does not mean not done. A sale is
    stopped too: a blocked KRA record means the client may not transact. Dismissing a proposal stays open."""
    if firm is None or household_id is None or not applies(firm):
        return None
    stopped = [p for p in (await overview(session, firm, household_id=household_id))["parties"] if p["state"] in ("overdue", "blocked")]
    if not stopped:
        return None
    said = "; ".join(f"{p['name']}'s KYC is {p['state']} ({p['message']})" for p in stopped)
    return (f"{said}. Orders and approvals that move this household's book are held until an adviser, paraplanner, operations or compliance "
            f"records the refreshed KYC from the KRA's acknowledgement or the client's document (record_kyc) ({CITATION}).")


async def order_refusal(session: AsyncSession, firm: Firm | None, mandate) -> str | None:
    """For a direct order or a proposal's orders on this mandate."""
    from app.aurea_core import wash_sale

    if firm is None or not applies(firm):
        return None
    return await household_refusal(session, firm, await wash_sale.household_of(session, mandate))


async def refusal(session: AsyncSession, firm: Firm | None, rec) -> str | None:
    """Why a book-moving proposal may not be approved yet, or None."""
    from app.core import decision_rights
    from app.models.graph import Mandate

    if firm is None or not applies(firm) or not decision_rights.moves_the_book(rec.agent_key):
        return None
    if rec.subject_type != "mandate" or not rec.subject_id:
        return None
    mandate = await session.get(Mandate, rec.subject_id)
    return await order_refusal(session, firm, mandate) if mandate is not None else None
