"""Regulatory Countdown — the dated rule changes a household's market faces, and what each does to its figures.

US: the federal estate exemption. The TCJA sunset this used to count down to (1 Jan 2026) never took effect -
P.L. 119-21 set the exclusion instead - so it is reported as expired, and the exposure is worked against the
exclusion in force.

UK: unused pension funds enter the IHT estate (6 Apr 2027); the BPR/APR 100% relief allowance (in force from
6 Apr 2026).

Every figure fixed in law comes from aurea_core/law_figures.py with its source and as-of date, and the analysis
lists the ones it used (B-120). A date that has passed is in force or expired, never a countdown at zero. What the
platform does not hold - which assets qualify for BPR, whether a spouse's unused exemption was elected - is named as
not assessed or assumed, never estimated from something else. A firm with no market gets nothing (B-121).
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.aurea_core.graph import household_brain, list_households
from app.models.tenant import Firm

L = law_figures


def _timing(key: str, today: date) -> dict:
    """When a dated change falls, read from its law figure: a countdown only while it is still to come."""
    fig = L.get(key)
    day = fig.applies_from or fig.applies_to
    status = fig.status(today)
    if status == "upcoming":
        days = (fig.applies_from - today).days
        urgency = "critical" if days < 300 else ("high" if days < 500 else "medium")
        return {"deadline": day.isoformat(), "status": status, "days_remaining": days, "urgency": urgency}
    if fig.applies_from is None and fig.applies_to is not None:   # an event that was due and did not happen
        day = date.fromordinal(fig.applies_to.toordinal() + 1)
    return {"deadline": day.isoformat(), "status": status, "days_remaining": 0, "urgency": status}


# ── US federal estate exemption ───────────────────────────────────────────────

def _estimate_us_estate(brain: dict) -> dict:
    totals = brain.get("totals", {})
    portfolio = totals.get("total_value", 0) or 0
    persons = brain.get("persons", [])

    held_away = sum(p.get("profile", {}).get("held_away", 0) or 0 for p in persons)
    ira_total = sum(p.get("profile", {}).get("tax", {}).get("ira_value", 0) or 0 for p in persons)
    roth_total = sum(p.get("profile", {}).get("tax", {}).get("roth_value", 0) or 0 for p in persons)
    property_total = sum(
        prop.get("value", 0) or 0
        for p in persons
        for prop in p.get("profile", {}).get("properties", [])
    )
    gross_estate = portfolio + held_away + ira_total + roth_total + property_total

    adults = [p for p in persons if not p.get("is_next_gen")]
    married = len(adults) >= 2
    per_person = L.value("us_basic_exclusion")
    exemption = per_person * (2 if married else 1)
    exposure = max(0, gross_estate - exemption)

    return {
        "gross_estate": gross_estate,
        "portfolio": portfolio,
        "held_away": held_away,
        "retirement_accounts": ira_total + roth_total,
        "real_estate": property_total,
        "married": married,
        "exemption_per_person": per_person,
        "exemption": exemption,
        "exposure": exposure,
        "estimated_tax": round(exposure * L.value("us_estate_tax_rate"), 2),
        "affected": gross_estate > exemption,
    }


def _us_estate_exemption(brain: dict, today: date) -> dict:
    figures = _estimate_us_estate(brain)
    gift = L.value("us_annual_gift_exclusion")

    actions = []
    if figures["affected"]:
        actions.append({
            "priority": 1,
            "action": "Annual gift exclusion — use it each year",
            "detail": (
                f"Each donor can give ${gift:,.0f} a year per recipient free of gift tax; with gift-splitting a "
                f"married couple gives ${gift * 2:,.0f} per recipient. Recipients are not on file here."
            ),
            "estimated_benefit": f"${gift:,.0f} per donor, per recipient, per year out of the taxable estate.",
        })
        actions.append({
            "priority": 2,
            "action": "Spousal Lifetime Access Trust (SLAT)",
            "detail": (
                "A SLAT moves assets irrevocably out of the estate while the other spouse keeps indirect access "
                "through the trustee; growth inside the trust is outside the estate."
            ),
            "estimated_benefit": f"Uses up to ${figures['exemption_per_person']:,.0f} of exclusion per donor.",
        })
        actions.append({
            "priority": 3,
            "action": "Grantor Retained Annuity Trust (GRAT)",
            "detail": (
                "Appreciation above the IRC §7520 rate on assets in a short-term GRAT passes to heirs free of "
                "estate and gift tax. The §7520 rate is published monthly by the IRS and is not held here."
            ),
            "estimated_benefit": "Appreciation above the §7520 rate leaves the estate.",
        })
        actions.append({
            "priority": 4,
            "action": "Charitable strategies — DAF, CRT, CLT",
            "detail": (
                "A donor-advised fund, charitable remainder trust or charitable lead trust moves appreciated "
                "assets out of the estate with an income stream or a charitable deduction."
            ),
            "estimated_benefit": "Removes appreciated assets from the estate.",
        })
        if figures["retirement_accounts"] > 0:
            actions.append({
                "priority": 5,
                "action": "Roth conversion — IRA balances in the estate",
                "detail": "Traditional IRA/401(k) balances count in the estate and carry RMDs; a conversion pays the income tax now.",
                "estimated_benefit": f"${figures['retirement_accounts']:,.0f} of IRA/401(k) in the estate.",
            })

    return {
        "type": "us_estate_tax_sunset",
        "title": "US estate tax — exemption in force",
        "subtitle": "TCJA sunset superseded by P.L. 119-21",
        **_timing("us_tcja_sunset", today),
        "summary": (
            f"Gross estate on file ${figures['gross_estate']:,.0f} against an exclusion of "
            f"${figures['exemption']:,.0f}: exposure ${figures['exposure']:,.0f}."
        ),
        "figures": figures,
        "actions": actions,
        "currency": "USD",
        "regulation": "IRC §2010(c)(3) as amended by P.L. 119-21 §70106",
        "law": L.cited(("us_tcja_sunset", "us_basic_exclusion", "us_estate_tax_rate", "us_annual_gift_exclusion"), today),
        "assumed": (["a couple's exclusion is doubled: the survivor elects portability"] if figures["married"] else [])
                   + ["the estate is what is on file: managed, held away, retirement accounts and property"],
    }


# ── UK IHT: pensions in the estate ────────────────────────────────────────────

def _estimate_uk_iht(brain: dict) -> dict:
    totals = brain.get("totals", {})
    portfolio = totals.get("total_value", 0) or 0
    persons = brain.get("persons", [])

    pension_total = sum(p.get("profile", {}).get("tax", {}).get("pension_pot", 0) or 0 for p in persons)
    held_away = sum(p.get("profile", {}).get("held_away", 0) or 0 for p in persons)
    isa_total = sum(p.get("profile", {}).get("tax", {}).get("isa_value", 0) or 0 for p in persons)

    current_estate = portfolio + held_away
    post_2027_estate = current_estate + pension_total

    adults = [p for p in persons if not p.get("is_next_gen")]
    married = len(adults) >= 2

    nrb_total = L.value("uk_nrb") * (2 if married else 1)
    rnrb_claimable = L.value("uk_rnrb") * (2 if married else 1)
    taper_at = L.value("uk_rnrb_taper")
    if current_estate > taper_at:
        taper = min(rnrb_claimable, max(0, (current_estate - taper_at) // 2))
        rnrb_claimable = max(0, rnrb_claimable - int(taper))

    threshold = nrb_total + rnrb_claimable
    rate = L.value("uk_iht_rate")
    iht_current = max(0, current_estate - threshold) * rate
    iht_post_2027 = max(0, post_2027_estate - threshold) * rate
    additional_iht = max(0, iht_post_2027 - iht_current)

    lpa_missing = [
        p.get("preferred_name") or p["full_name"].split()[0]
        for p in adults
        if (p.get("profile", {}).get("tax", {}).get("lpa_status") or "none") == "none"
    ]

    return {
        "current_estate": current_estate,
        "portfolio": portfolio,
        "pension_total": pension_total,
        "isa_total": isa_total,
        "post_2027_estate": post_2027_estate,
        "married": married,
        "nrb_total": nrb_total,
        "rnrb_claimable": rnrb_claimable,
        "threshold": threshold,
        "iht_current": iht_current,
        "iht_post_2027": iht_post_2027,
        "additional_iht": additional_iht,
        "affected": pension_total > 0 and additional_iht > 0,
        "lpa_missing": lpa_missing,
    }


def _uk_iht_pension(brain: dict, today: date) -> dict:
    figures = _estimate_uk_iht(brain)

    actions = []
    if figures["affected"]:
        actions.append({
            "priority": 1,
            "action": "Pension drawdown before the change",
            "detail": "Pension funds drawn before the change leave the IHT estate; tax-free cash is capped by the Lump Sum Allowance.",
            "estimated_benefit": f"Up to £{figures['pension_total']:,.0f} out of the IHT estate.",
        })
        actions.append({
            "priority": 2,
            "action": "Whole-of-life policy in trust",
            "detail": "A whole-of-life policy written in a discretionary trust pays the IHT on death.",
            "estimated_benefit": f"Covers £{figures['iht_post_2027']:,.0f} of IHT on the figures on file.",
        })
        actions.append({
            "priority": 3,
            "action": "Pension death benefits to a bypass trust",
            "detail": "Nominating a discretionary trust rather than the spouse outright keeps the pension out of the survivor's estate.",
            "estimated_benefit": "Avoids the pension entering the estate twice.",
        })
        actions.append({
            "priority": 4,
            "action": "Potentially exempt transfers",
            "detail": "A gift starts the 7-year clock; taper relief applies after 3 years.",
            "estimated_benefit": "Fully exempt after 7 years.",
        })

    if figures["lpa_missing"]:
        actions.append({
            "priority": 2 if not figures["affected"] else 5,
            "action": "Register Lasting Powers of Attorney",
            "detail": f"No LPA on file for {', '.join(figures['lpa_missing'])}.",
            "estimated_benefit": "Someone can act on the pension if capacity is lost.",
        })

    return {
        "type": "uk_iht_pension_inclusion",
        "title": "UK IHT: Pension Pots Enter Estate",
        "subtitle": "From 6 April 2027",
        **_timing("uk_pension_iht", today),
        "summary": (
            f"Pension funds on file £{figures['pension_total']:,.0f}; IHT £{figures['iht_current']:,.0f} now and "
            f"£{figures['iht_post_2027']:,.0f} once they are in the estate."
            if figures["affected"] else "No pension funds on file (profile.tax.pension_pot)."
        ),
        "figures": figures,
        "actions": actions,
        "currency": "GBP",
        "regulation": "Pensions in the IHT estate from 6 April 2027 (Autumn Budget 2024)",
        "law": L.cited(("uk_pension_iht", "uk_nrb", "uk_rnrb", "uk_rnrb_taper", "uk_iht_rate"), today),
        "assumed": (["a couple claims both nil-rate bands and both residence bands"] if figures["married"] else [])
                   + ["the residence band is claimed: a home passes to direct descendants"],
    }


# ── UK BPR/APR allowance ──────────────────────────────────────────────────────

def _uk_bpr_apr_cap(brain: dict, today: date) -> dict:
    """The BPR/APR 100% relief allowance. Which assets qualify is a person's judgement from the business and its
    paperwork and is not on file, so nothing is estimated: the analysis names the allowance and is not assessed. It
    used to read the household's alternatives as qualifying assets, which they are not."""
    return {
        "type": "uk_bpr_apr_cap",
        "title": "UK BPR/APR — 100% Relief Allowance",
        "subtitle": "£1M per person from 6 April 2026",
        **_timing("uk_bpr_apr_cap", today),
        "summary": "BPR/APR-qualifying assets are not on file, so the allowance is not assessed for this household.",
        "figures": {
            "estimated_qualifying_assets": None,
            "cap": L.value("uk_bpr_apr_cap"),
            "cap_exposure": None,
            "estimated_additional_iht": None,
            "affected": None,
            "assessed": False,
        },
        "actions": [{
            "priority": 1,
            "action": "Record which assets qualify for BPR or APR",
            "detail": "Business and agricultural property, its holding period and its value, from the business's own paperwork.",
            "estimated_benefit": "Lets the allowance be measured.",
        }],
        "currency": "GBP",
        "regulation": "BPR/APR 100% relief allowance (Autumn Budget 2024)",
        "law": L.cited(("uk_bpr_apr_cap",), today),
        "assumed": [],
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def for_household(
    session: AsyncSession, household_id: uuid.UUID, firm_jurisdiction: str | None,
    *, firm_id: uuid.UUID,
) -> dict | None:
    brain = await household_brain(session, uuid.UUID(str(household_id)), firm_id=firm_id)
    if not brain:
        return None
    today = firm_time.today(await session.get(Firm, firm_id))

    jurisdiction = markets.code((brain["household"]["values"] or {}).get("jurisdiction") or firm_jurisdiction)
    supported = markets.get(jurisdiction) is not None

    result: dict = {
        "household_id": str(household_id),
        "household_name": brain["household"]["name"],
        "jurisdiction": jurisdiction,
        "supported": supported,
        "as_of": today.isoformat(),
        "analyses": [],
    }

    if jurisdiction == "US":
        result["analyses"].append(_us_estate_exemption(brain, today))
    elif jurisdiction == "UK":
        result["analyses"].append(_uk_iht_pension(brain, today))
        result["analyses"].append(_uk_bpr_apr_cap(brain, today))
    elif not supported:
        result["message"] = f"{jurisdiction} has no market on this platform, so no rule change is tracked."
    else:
        result["message"] = f"No dated rule change is tracked for {jurisdiction}."

    return result


async def for_firm(
    session: AsyncSession, firm_id: uuid.UUID, firm_jurisdiction: str | None
) -> list[dict]:
    households = await list_households(session, firm_id)
    results = []
    for hh in households:
        r = await for_household(session, uuid.UUID(hh["id"]), firm_jurisdiction, firm_id=firm_id)
        if r and r.get("analyses"):
            results.append(r)
    return results
