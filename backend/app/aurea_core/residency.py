"""A client's residence, as a person recorded it, and what follows from it in India (India row 7).

Two tests answer two questions, and neither is worked out from where a client lives, banks or travels.

  Tax residence   the Income-tax Act's days test, worked from the days a person recorded for the tax year and the years
                  before it: resident, resident but not ordinarily resident, or non-resident. A fact the test needs and
                  nobody recorded is named, and the answer is "not assessed", never a guess.
  FEMA status     whether the client is a non-resident Indian or an overseas citizen of India, recorded from the
                  passport, visa or OCI card. It has no days test, and it can differ from tax residence.

Beside them, as facts with their source and never advice: which accounts an NRI holds (NRE, NRO, FCNR), the portfolio
investment limits and the NRO remittance limit from `law_figures`, how tax is taken at source on a non-resident's
gains, and what the client's own FATCA and CRS declaration says. Treaty relief is not modelled. A record is added and
never edited; a new one carries over what the last said and changes what the person gives, so the latest stands. Not on
file does not mean resident, and the statute and RBI directions were not re-read in this build: a qualified person checks
each before a client relies on it.

The Income-tax Act 2025 is in force from 1 April 2026 and carries the residence test under a section number this build
did not read, so each figure cites the 1961 Act's section and says so.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets, tax_india
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Household, Person
from app.models.residency import ResidencyRecord
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
FEMA_STATUSES = {"nri": "Non-resident Indian", "oci": "Overseas Citizen of India", "resident": "Resident in India",
                 "foreign_national": "Foreign national"}
NON_RESIDENT_FEMA = ("nri", "oci")
STATUS_TEXT = {"resident_ordinary": "Resident and ordinarily resident", "resident_rnor": "Resident, not ordinarily resident",
               "resident": "Resident, ordinariness not assessed", "non_resident": "Non-resident", "not_assessed": "Not assessed"}
US_INDICIA = {"us_citizen_or_born": "US citizenship or birth", "us_address": "US residence or mailing address",
              "us_phone": "US telephone number", "us_standing_instruction": "Standing instruction to a US account",
              "us_power_of_attorney": "Power of attorney to a person with a US address", "us_hold_mail": "'In care of' or hold-mail address in the US"}
# What each account an NRI holds is, from the RBI's FEMA (Deposit) Regulations; laid out, not advised on.
ACCOUNTS = {
    "NRE": {"label": "NRE account", "currency": "INR", "repatriable": "Principal and interest may be sent abroad",
            "holds": "Money sent in from abroad"},
    "NRO": {"label": "NRO account", "currency": "INR", "repatriable": "Up to the NRO limit a financial year, after tax",
            "holds": "Income earned in India"},
    "FCNR": {"label": "FCNR(B) deposit", "currency": "Foreign currency", "repatriable": "Principal and interest may be sent abroad",
             "holds": "Money sent in from abroad, as a foreign currency term deposit"},
}
ACCOUNTS_SOURCE = "RBI FEMA (Deposit) Regulations 2016, as read in the research report; not re-read in this build, counsel to confirm"
TDS_SOURCE = ("Income-tax Act 2025 (tax deducted on a non-resident's capital gains) and the research report, section 3.2: "
              "deducted at the gain's own rate, with surcharge and cess")
YEAR_FIELDS = ("days_in_india", "days_prior_four_years", "days_prior_seven_years", "nonresident_years_of_prior_ten",
               "left_for_employment_abroad", "india_income", "taxed_elsewhere")
PERSON_FIELDS = ("indian_citizen_or_pio", "fema_status", "country_of_residence", "accounts", "pis_permission",
                 "tax_residencies", "us_person", "us_indicia", "self_certified_on")
FIELD_LABELS = {
    "days_in_india": "Days in India in the year", "days_prior_four_years": "Days in India in the four years before",
    "days_prior_seven_years": "Days in India in the seven years before",
    "nonresident_years_of_prior_ten": "Years non-resident of the ten before",
    "left_for_employment_abroad": "Left India for employment abroad this year", "india_income": "Indian income, other than foreign sources",
    "taxed_elsewhere": "Liable to tax in another country", "indian_citizen_or_pio": "Indian citizen or person of Indian origin",
    "fema_status": "FEMA status", "country_of_residence": "Country of residence", "accounts": "NRE, NRO and FCNR accounts held",
    "pis_permission": "Portfolio Investment Scheme permission", "tax_residencies": "Countries of tax residence",
    "us_person": "US person", "us_indicia": "US indicia", "self_certified_on": "FATCA and CRS self-certification dated",
}


class ResidencyError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _num(key: str, today: date) -> float:
    return law_figures.value(key, today)


def _int(value, label: str, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ResidencyError(f"{label} is a whole number of days or years.")
    if n < 0 or n > high:
        raise ResidencyError(f"{label} is between 0 and {high}.")
    return n


def _bool(value, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in ("true", "yes", "y", "1"):
        return True
    if str(value).strip().lower() in ("false", "no", "n", "0"):
        return False
    raise ResidencyError(f"{label} is yes or no.")


def _day(value, label: str, today: date) -> date:
    if isinstance(value, date):
        day = value
    else:
        try:
            day = date.fromisoformat(str(value)[:10])
        except ValueError:
            raise ResidencyError(f"{label} is a date, YYYY-MM-DD.")
    if day > today:
        raise ResidencyError(f"{label} cannot be in the future.")
    return day


def _words(value) -> list[str]:
    if isinstance(value, str):
        value = [v for v in value.replace(";", ",").split(",")]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _facts(row: ResidencyRecord | None) -> dict:
    if row is None:
        return {}
    out = {k: getattr(row, k) for k in (*YEAR_FIELDS, *PERSON_FIELDS)}
    out["india_income"] = float(row.india_income) if row.india_income is not None else None
    return out


async def records(session: AsyncSession, firm_id: uuid.UUID, person_id: uuid.UUID) -> list[ResidencyRecord]:
    """Every record for the person, newest first."""
    return list((await session.execute(select(ResidencyRecord).where(
        ResidencyRecord.firm_id == firm_id, ResidencyRecord.person_id == person_id
    ).order_by(ResidencyRecord.created_at.desc()))).scalars().all())


def standing(rows: list[ResidencyRecord], tax_year: str | None = None) -> tuple[dict, ResidencyRecord | None]:
    """What stands for a tax year: the person's own facts from the latest record, and that year's test facts from the
    latest record for that year. With no year given, the latest record's year."""
    if not rows:
        return {}, None
    latest = rows[0]
    year = tax_year or latest.tax_year
    in_year = next((r for r in rows if r.tax_year == year), None)
    facts = {k: v for k, v in _facts(latest).items() if k in PERSON_FIELDS}
    facts.update({k: v for k, v in _facts(in_year).items() if k in YEAR_FIELDS} if in_year else {k: None for k in YEAR_FIELDS})
    facts["tax_year"] = year
    return facts, (in_year or latest)


def tax_status(f: dict, today: date) -> dict:
    """The residence test from the facts recorded. A fact the answer turns on and nobody recorded is named in `needs`."""
    steps: list[str] = []
    needs: list[str] = []
    days = f.get("days_in_india")
    keys = ["in_res_days_year", "in_res_days_short", "in_res_days_prior_four", "in_res_days_citizen_away", "in_res_days_high_income",
            "in_res_income_line", "in_res_rnor_days_prior_seven", "in_res_rnor_years_nonresident"]

    def done(status: str, ordinarily: bool | None = None) -> dict:
        return {"status": status, "label": STATUS_TEXT[status], "ordinarily_resident": ordinarily, "steps": steps, "needs": needs,
                "tax_year": f.get("tax_year"), "law": law_figures.cited(keys, today),
                "note": "Worked from the days a person recorded. A qualified person checks it before a client relies on it."}

    if days is None:
        needs.append(FIELD_LABELS["days_in_india"])
        return done("not_assessed")
    full, short, prior_need = _num("in_res_days_year", today), _num("in_res_days_short", today), _num("in_res_days_prior_four", today)
    line = _num("in_res_income_line", today)
    resident, deemed = False, False
    if days >= full:
        resident = True
        steps.append(f"{days} days in India in the year is {full:g} or more: resident")
    else:
        citizen = f.get("indian_citizen_or_pio")
        income, left = f.get("india_income"), f.get("left_for_employment_abroad")

        def need(key: str) -> None:
            if FIELD_LABELS[key] not in needs:
                needs.append(FIELD_LABELS[key])

        if citizen is None:
            need("indian_citizen_or_pio")
        # The short test: 60 days, or more for a citizen or person of Indian origin who is abroad.
        threshold: float | None = short
        if days < short:
            pass            # under 60 days no short test can apply, whoever they are
        elif citizen:
            if left:
                threshold = _num("in_res_days_citizen_away", today)
            elif income is None:
                threshold = None
                need("india_income")
            elif income > line:
                threshold = _num("in_res_days_high_income", today)
                if left is None:
                    threshold = None
                    need("left_for_employment_abroad")
            else:
                threshold = _num("in_res_days_citizen_away", today)
        elif citizen is None:
            threshold = None
        if days < short:
            steps.append(f"{days} days in the year is under {short:g}: the short test does not apply")
        elif threshold is not None and days < threshold:
            steps.append(f"{days} days in the year is under {threshold:g}: the short test does not apply")
        elif threshold is not None:
            prior = f.get("days_prior_four_years")
            if prior is None:
                need("days_prior_four_years")
            elif prior >= prior_need:
                resident = True
                steps.append(f"{days} days in the year is {threshold:g} or more, and {prior} days in the four years before is "
                             f"{prior_need:g} or more: resident")
            else:
                steps.append(f"{prior} days in the four years before is under {prior_need:g}: the short test does not make the client resident")
        # Deemed residence: a citizen with high Indian income who is liable to tax in no other country.
        if not resident and citizen:
            if income is None:
                need("india_income")
            elif income > line:
                if f.get("taxed_elsewhere") is None:
                    need("taxed_elsewhere")
                elif not f["taxed_elsewhere"]:
                    resident = deemed = True
                    steps.append(f"A citizen with Indian income over {markets.money(line, 'INR')} who is liable to tax nowhere else is "
                                 "deemed resident, and not ordinarily resident")
    if needs and not resident:
        return done("not_assessed")
    if not resident:
        steps.append("No test makes the client resident: non-resident")
        return done("non_resident")
    if deemed:
        return done("resident_rnor", False)
    years, seven = f.get("nonresident_years_of_prior_ten"), f.get("days_prior_seven_years")
    need_years, need_seven = _num("in_res_rnor_years_nonresident", today), _num("in_res_rnor_days_prior_seven", today)
    if years is not None and years >= need_years:
        steps.append(f"Non-resident in {years} of the ten years before is {need_years:g} or more: not ordinarily resident")
        return done("resident_rnor", False)
    if seven is not None and seven <= need_seven:
        steps.append(f"{seven} days in India in the seven years before is {need_seven:g} or fewer: not ordinarily resident")
        return done("resident_rnor", False)
    if years is not None and seven is not None:
        steps.append("Neither not-ordinarily-resident condition holds: ordinarily resident")
        return done("resident_ordinary", True)
    for key, value in (("nonresident_years_of_prior_ten", years), ("days_prior_seven_years", seven)):
        if value is None:
            needs.append(FIELD_LABELS[key])
    return done("resident")


def fatca_crs(f: dict, today: date) -> dict:
    """What the client's own FATCA and CRS declaration says, and what it leaves open."""
    flags: list[dict] = []
    indicia = [i for i in (f.get("us_indicia") or []) if i in US_INDICIA]
    certified = f.get("self_certified_on")
    if f.get("us_person"):
        flags.append({"key": "us_person", "tone": "warn", "text": "US person declared: reportable under FATCA"})
    elif indicia and not certified:
        flags.append({"key": "us_indicia", "tone": "warn", "text": f"{len(indicia)} US indicia on file and no self-certification"})
    elif indicia:
        flags.append({"key": "us_indicia_cleared", "tone": "ok", "text": "US indicia answered by self-certification"})
    others = [c for c in (f.get("tax_residencies") or []) if c.strip().lower() not in ("india", "in")]
    if others:
        flags.append({"key": "crs", "tone": "info", "text": f"Tax resident in {', '.join(others)}: reportable under CRS"})
    nri = f.get("fema_status") in NON_RESIDENT_FEMA
    declared = bool(f.get("tax_residencies"))
    if nri and not declared:
        flags.append({"key": "crs_unknown", "tone": "warn", "text": "Countries of tax residence not declared"})
    return {"self_certified_on": certified.isoformat() if certified else None, "on_file": certified is not None,
            "us_person": f.get("us_person"), "indicia": [{"key": i, "label": US_INDICIA[i]} for i in indicia],
            "tax_residencies": list(f.get("tax_residencies") or []), "flags": flags,
            "source": "Income-tax Rules 2026, rules 238-240 (FATCA and CRS), as read in the research report"}


def fema_facts(f: dict, today: date) -> dict | None:
    """The accounts, limits and conditions that go with a non-resident's holdings. None for anyone else."""
    if f.get("fema_status") not in NON_RESIDENT_FEMA:
        return None
    held = {str(a).upper() for a in (f.get("accounts") or [])}
    keys = ["in_nro_repatriation", "in_pis_individual_limit", "in_pis_aggregate_limit"]
    return {
        "status": f["fema_status"], "label": FEMA_STATUSES[f["fema_status"]], "country": f.get("country_of_residence"),
        "accounts": [{"kind": k, **v, "recorded": k in held} for k, v in ACCOUNTS.items()],
        "accounts_source": ACCOUNTS_SOURCE,
        "pis": {"recorded": bool(f.get("pis_permission")), "reference": f.get("pis_permission"),
                "note": "Whether the Portfolio Investment Scheme is still needed to trade listed shares was not settled in research: "
                        "the letter is shown when recorded and nothing here requires it"},
        "limits": [{"label": fg["label"], "value": fg["value"], "unit": fg["unit"]} for fg in law_figures.cited(keys, today)],
        "oci_note": "An Overseas Citizen of India is treated like an NRI for these investments",
        "law": law_figures.cited(keys, today),
    }


def tax_treatment(f: dict, status: dict) -> dict | None:
    """How tax is taken from a non-resident's gains, laid out. None where the client is not one."""
    if status["status"] != "non_resident" and f.get("fema_status") not in NON_RESIDENT_FEMA:
        return None
    return {"tds": [{"label": "Listed equity and equity funds held 12 months or less", "rate": tax_india.RATE_ST_EQUITY},
                    {"label": "Listed equity and equity funds held longer", "rate": tax_india.RATE_LT}],
            "plus": "Surcharge and 4% cess", "source": TDS_SOURCE,
            "not_modelled": ["Treaty relief (DTAA) is not modelled",
                             "Whether the Rs 1.25 lakh exemption is allowed at source or only by refund on the return is open (research report 6.2)",
                             "Refunds and the non-resident's own return are not modelled"],
            "counsel": "Estimates for a qualified person to check"}


async def person_view(session: AsyncSession, firm: Firm, person: Person, *, today: date | None = None, tax_year: str | None = None) -> dict:
    """One person's residence for a tax year (this one by default), from what stands."""
    today = today or firm_time.today(firm)
    rows = await records(session, firm.id, person.id)
    year = tax_year or markets.tax_year(markets.MARKETS["IN"], today)["label"]
    f, row = standing(rows, year)
    base = {"person_id": str(person.id), "name": person.full_name, "household_id": str(person.household_id) if person.household_id else None}
    if row is None:
        status = tax_status({"tax_year": year}, today)
        return {**base, "recorded": False, "tax": status, "fema": None, "fatca_crs": None, "treatment": None,
                "message": "No residence on file. That does not mean resident."}
    status = tax_status(f, today)
    fema = fema_facts(f, today)
    differ = bool(f.get("fema_status") in NON_RESIDENT_FEMA and status["status"] in ("resident_ordinary", "resident_rnor", "resident")) or \
        bool(f.get("fema_status") == "resident" and status["status"] == "non_resident")
    return {**base, "recorded": True, "tax": status, "fema": fema, "fema_status": f.get("fema_status"),
            "fema_label": FEMA_STATUSES.get(f.get("fema_status") or ""), "fatca_crs": fatca_crs(f, today),
            "treatment": tax_treatment(f, status), "differs": differ,
            "facts": {FIELD_LABELS[k]: f.get(k) for k in YEAR_FIELDS if f.get(k) is not None},
            "document": rows[0].document, "recorded_by": rows[0].recorded_by,
            "recorded_on": firm_time.today(firm, rows[0].created_at).isoformat(), "history": len(rows)}


async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise ValueError("Household not found.")
    people = (await session.execute(select(Person).where(Person.household_id == household_id, Person.firm_id == firm.id)
                                    .order_by(Person.full_name))).scalars().all()
    today = firm_time.today(firm)
    views = [await person_view(session, firm, p, today=today) for p in people]
    return {"household_id": str(household_id), "household": household.name, "people": views,
            "any_non_resident": any(v["recorded"] and (v["tax"]["status"] == "non_resident" or v.get("fema_status") in NON_RESIDENT_FEMA) for v in views)}


def _is_non_resident(v: dict) -> bool:
    return v["recorded"] and (v["tax"]["status"] == "non_resident" or v.get("fema_status") in NON_RESIDENT_FEMA)


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every household with a residence on file, and how many people have none."""
    if not applies(firm):
        return {"applies": False, "households": [], "counts": {}}
    today = firm_time.today(firm)
    people = (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.deceased_on.is_(None)))).scalars().all()
    recorded_ids = {pid for (pid,) in (await session.execute(select(ResidencyRecord.person_id).where(
        ResidencyRecord.firm_id == firm.id))).all()}
    by_household: dict[uuid.UUID, list[Person]] = {}
    for p in people:
        if p.id in recorded_ids and p.household_id:
            by_household.setdefault(p.household_id, []).append(p)
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    out = []
    for hid, plist in sorted(by_household.items(), key=lambda kv: names.get(kv[0], "")):
        views = [await person_view(session, firm, p, today=today) for p in sorted(plist, key=lambda p: p.full_name)]
        out.append({"household_id": str(hid), "household": names.get(hid), "people": views,
                    "non_resident": sum(1 for v in views if _is_non_resident(v)),
                    "self_certification_missing": [v["name"] for v in views if _is_non_resident(v) and v["fatca_crs"] and not v["fatca_crs"]["on_file"]]})
    return {"applies": True, "as_of": today.isoformat(), "households": out,
            "counts": {"recorded": len(recorded_ids & {p.id for p in people}), "not_on_file": sum(1 for p in people if p.id not in recorded_ids),
                       "non_resident": sum(h["non_resident"] for h in out)},
            "not_on_file_note": "A person with no residence on file is not assessed. That does not mean resident."}


async def prepare(session: AsyncSession, firm: Firm, *, person_id, document: str | None, tax_year: str | None = None,
                  **given) -> dict:
    """What recording would write: the standing facts with what the person gave laid over them. Writes nothing."""
    if not applies(firm):
        raise ResidencyError("Residence is recorded for an Indian firm.")
    try:
        pid = person_id if isinstance(person_id, uuid.UUID) else uuid.UUID(str(person_id))
    except ValueError:
        raise ResidencyError("Give the person's id, from the client's page.")
    person = await session.get(Person, pid)
    if person is None or person.firm_id != firm.id:
        raise ResidencyError("That person is not a client of this firm.")
    today = firm_time.today(firm)
    year = (tax_year or "").strip() or markets.tax_year(markets.MARKETS["IN"], today)["label"]
    if len(year) != 7 or year[4] != "-" or not year[:4].isdigit() or int(year[5:]) != (int(year[:4]) + 1) % 100:
        raise ResidencyError("A tax year is written 2026-27.")
    if len((document or "").strip()) < 4:
        raise ResidencyError("Say where it is on file: the passport, visa or OCI card, travel record, return or signed form.")
    new: dict = {}
    for key, value in given.items():
        if value is None or value == "":
            continue
        if key not in YEAR_FIELDS and key not in PERSON_FIELDS:
            raise ResidencyError(f"{key} is not a residence fact. One of {', '.join(YEAR_FIELDS + PERSON_FIELDS)}.")
        label = FIELD_LABELS[key]
        if key == "days_in_india":
            new[key] = _int(value, label, 366)
        elif key == "days_prior_four_years":
            new[key] = _int(value, label, 1461)
        elif key == "days_prior_seven_years":
            new[key] = _int(value, label, 2557)
        elif key == "nonresident_years_of_prior_ten":
            new[key] = _int(value, label, 10)
        elif key == "india_income":
            try:
                new[key] = float(value)
            except (TypeError, ValueError):
                raise ResidencyError(f"{label} is an amount in rupees.")
            if new[key] < 0:
                raise ResidencyError(f"{label} cannot be negative.")
        elif key in ("left_for_employment_abroad", "taxed_elsewhere", "indian_citizen_or_pio", "us_person"):
            new[key] = _bool(value, label)
        elif key == "fema_status":
            if str(value).strip().lower() not in FEMA_STATUSES:
                raise ResidencyError(f"A FEMA status is one of {', '.join(FEMA_STATUSES)}.")
            new[key] = str(value).strip().lower()
        elif key == "accounts":
            held = [a.upper() for a in _words(value)]
            if any(a not in ACCOUNTS for a in held):
                raise ResidencyError(f"An account is one of {', '.join(ACCOUNTS)}.")
            new[key] = sorted(set(held))
        elif key == "us_indicia":
            found = _words(value)
            if any(i not in US_INDICIA for i in found):
                raise ResidencyError(f"A US indicium is one of {', '.join(US_INDICIA)}.")
            new[key] = found
        elif key == "tax_residencies":
            new[key] = _words(value)
        elif key == "self_certified_on":
            new[key] = _day(value, label, today)
        else:
            new[key] = str(value).strip()
    if not new:
        raise ResidencyError("Give at least one fact to record.")
    rows = await records(session, firm.id, pid)
    before, _ = standing(rows, year)
    after = {k: v for k, v in before.items() if k != "tax_year"}
    after.update(new)
    changes = [{"field": k, "label": FIELD_LABELS[k], "before": before.get(k), "after": v} for k, v in new.items() if before.get(k) != v]
    if not changes:
        raise ResidencyError("Nothing changes: those facts are already on file.")
    after["tax_year"] = year
    return {"person": person, "tax_year": year, "facts": after, "changes": changes, "document": document.strip(),
            "status": tax_status(after, today)}


async def record(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare(session, firm, **terms)
    f = p["facts"]
    row = ResidencyRecord(firm_id=firm.id, person_id=p["person"].id, tax_year=p["tax_year"], document=p["document"], recorded_by=by,
                          note=(note or "").strip() or None, created_at=utcnow(),
                          **{k: f.get(k) for k in (*YEAR_FIELDS, *PERSON_FIELDS)})
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="residency_recorded", actor=by, content={
        "person_id": str(p["person"].id), "tax_year": p["tax_year"], "document": p["document"],
        "changes": [{"field": c["field"], "after": str(c["after"])} for c in p["changes"]], "status": p["status"]["status"]})
    return await person_view(session, firm, p["person"], tax_year=p["tax_year"])


async def gate_check(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict | None:
    """For the adviser's gate: a non-resident or overseas citizen in the household needs their FATCA and CRS self-certification
    on file before advice. None where nobody in the household is recorded as one: not recorded is not resident."""
    if not applies(firm):
        return None
    view = await household_view(session, firm, household_id)
    non = [v for v in view["people"] if _is_non_resident(v)]
    if not non:
        return None
    missing = [v["name"] for v in non if not v["fatca_crs"]["on_file"]]
    return {"key": "fatca_crs", "label": "FATCA and CRS self-certification", "state": "fail" if missing else "pass",
            "detail": (f"{', '.join(missing)}: no self-certification on file" if missing else
                       "; ".join(f"{v['name']}: {v['fatca_crs']['self_certified_on']}" for v in non)),
            "source": "Income-tax Rules 2026, rules 238-240"}
