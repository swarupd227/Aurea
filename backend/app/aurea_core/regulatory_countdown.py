"""Regulatory Countdown — jurisdiction-aware deadline intelligence.

Two modules:
  1. US: Federal Estate Tax Exemption Sunset (Jan 1, 2026) — exemption halves unless Congress acts.
  2. UK: IHT Pension Pot Inclusion (April 6, 2027) — unused pension pots enter the IHT estate.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain, list_households

# ── US constants ──────────────────────────────────────────────────────────────
_US_EXEMPTION_CURRENT = 13_610_000      # per-person indexed exemption (2024)
_US_EXEMPTION_POST_SUNSET = 7_000_000   # approx 2026 value (reverting to 2018 $5M base, indexed)
_US_ESTATE_TAX_RATE = 0.40
_US_GIFT_EXCLUSION = 18_000             # per-donor-per-recipient annual exclusion (2024)
_US_SUNSET_DATE = date(2026, 1, 1)

# ── UK constants ──────────────────────────────────────────────────────────────
_UK_NRB = 325_000                        # nil-rate band per person
_UK_RNRB = 175_000                       # residence nil-rate band per person (if residence to direct descendants)
_UK_IHT_RATE = 0.40
_UK_PENSION_DEADLINE = date(2027, 4, 6)  # start of 2027/28 UK tax year
_UK_RNRB_TAPER_THRESHOLD = 2_000_000    # RNRB tapers £1 per £2 above this


def _days_until(deadline: date) -> int:
    return max(0, (deadline - date.today()).days)


# ── US Estate Tax Sunset ──────────────────────────────────────────────────────

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
    multiplier = 2 if married else 1

    current_exemption = _US_EXEMPTION_CURRENT * multiplier
    post_sunset_exemption = _US_EXEMPTION_POST_SUNSET * multiplier
    exposure_current = max(0, gross_estate - current_exemption)
    exposure_post_sunset = max(0, gross_estate - post_sunset_exemption)
    additional_tax_at_risk = max(0, exposure_post_sunset - exposure_current) * _US_ESTATE_TAX_RATE

    return {
        "gross_estate": gross_estate,
        "portfolio": portfolio,
        "held_away": held_away,
        "retirement_accounts": ira_total + roth_total,
        "real_estate": property_total,
        "married": married,
        "current_exemption": current_exemption,
        "post_sunset_exemption": post_sunset_exemption,
        "exposure_current": exposure_current,
        "exposure_post_sunset": exposure_post_sunset,
        "additional_tax_at_risk": additional_tax_at_risk,
        "affected": gross_estate > post_sunset_exemption,
    }


def _us_estate_sunset(brain: dict) -> dict:
    figures = _estimate_us_estate(brain)
    days = _days_until(_US_SUNSET_DATE)
    persons = brain.get("persons", [])
    adult_count = len([p for p in persons if not p.get("is_next_gen")])

    urgency = "critical" if days < 200 else ("high" if days < 400 else "medium")

    actions = []
    if figures["affected"]:
        actions.append({
            "priority": 1,
            "action": "Annual gift exclusion — act before Dec 31",
            "detail": (
                f"Each donor can give ${_US_GIFT_EXCLUSION:,}/year per recipient tax-free. "
                "Fund irrevocable trusts or direct gifts to children, grandchildren, charities before year-end."
            ),
            "estimated_benefit": f"${_US_GIFT_EXCLUSION * adult_count * 4:,}/year reduction in taxable estate.",
        })
        actions.append({
            "priority": 2,
            "action": "Spousal Lifetime Access Trust (SLAT)",
            "detail": (
                "Fund a SLAT now while the high exemption applies. Assets inside the trust are excluded "
                "from the estate yet the spouse retains beneficial access."
            ),
            "estimated_benefit": f"Up to ${_US_EXEMPTION_CURRENT/1e6:.1f}M sheltered if executed before Jan 1 2026.",
        })
        actions.append({
            "priority": 3,
            "action": "Grantor Retained Annuity Trust (GRAT)",
            "detail": (
                "Transfer appreciating assets into a short-term GRAT. Appreciation above the §7520 "
                "hurdle rate passes to heirs estate-tax-free."
            ),
            "estimated_benefit": "Appreciation beyond hurdle rate exits estate with no gift tax.",
        })
        if figures["retirement_accounts"] > 0:
            actions.append({
                "priority": 4,
                "action": "Roth conversion strategy",
                "detail": (
                    "Traditional IRA/401(k) balances are included in the gross estate and subject to "
                    "RMDs. Roth conversions reduce taxable IRA balances, avoid future RMDs, and pass "
                    "income-tax-free to heirs."
                ),
                "estimated_benefit": f"${figures['retirement_accounts']:,.0f} retirement account estate exposure addressable.",
            })

    return {
        "type": "us_estate_tax_sunset",
        "title": "US Estate Tax Exemption Sunset",
        "subtitle": "Federal exemption halves on Jan 1, 2026",
        "deadline": _US_SUNSET_DATE.isoformat(),
        "days_remaining": days,
        "urgency": urgency,
        "summary": (
            f"The TCJA estate tax exemption (${ _US_EXEMPTION_CURRENT/1e6:.1f}M/person) sunsets on "
            f"Jan 1 2026 unless Congress acts, reverting to ~${_US_EXEMPTION_POST_SUNSET/1e6:.0f}M. "
            f"This household's estimated gross estate of ${figures['gross_estate']/1e6:.1f}M "
            + ("exceeds the post-sunset threshold — immediate planning is required."
               if figures["affected"] else "falls within the post-sunset threshold — monitor legislation.")
        ),
        "figures": figures,
        "actions": actions,
        "currency": "USD",
        "regulation": "Tax Cuts and Jobs Act 2017 — §2010 sunset clause",
    }


# ── UK IHT Pension Inclusion ──────────────────────────────────────────────────

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

    nrb_total = _UK_NRB * (2 if married else 1)
    rnrb_claimable = _UK_RNRB * (2 if married else 1)
    if current_estate > _UK_RNRB_TAPER_THRESHOLD:
        taper = min(rnrb_claimable, max(0, (current_estate - _UK_RNRB_TAPER_THRESHOLD) // 2))
        rnrb_claimable = max(0, rnrb_claimable - int(taper))

    threshold = nrb_total + rnrb_claimable

    iht_current = max(0, current_estate - threshold) * _UK_IHT_RATE
    iht_post_2027 = max(0, post_2027_estate - threshold) * _UK_IHT_RATE
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


def _uk_iht_pension(brain: dict) -> dict:
    figures = _estimate_uk_iht(brain)
    days = _days_until(_UK_PENSION_DEADLINE)
    urgency = "critical" if days < 300 else ("high" if days < 500 else "medium")

    actions = []
    if figures["affected"]:
        actions.append({
            "priority": 1,
            "action": "Accelerate pension drawdown",
            "detail": (
                "Drawing down pension assets before April 2027 removes them from the IHT estate. "
                "Consider crystallising up to 25% tax-free cash (up to the Lump Sum Allowance) and "
                "reinvesting in ISAs or spending on lifestyle goals."
            ),
            "estimated_benefit": f"Up to £{figures['pension_total']:,.0f} removed from IHT estate.",
        })
        actions.append({
            "priority": 2,
            "action": "Whole-of-life policy in trust",
            "detail": (
                "A whole-of-life policy written in a discretionary trust pays the IHT liability on death, "
                "leaving the estate intact for beneficiaries."
            ),
            "estimated_benefit": f"Covers £{figures['iht_post_2027']:,.0f} projected IHT liability.",
        })
        actions.append({
            "priority": 3,
            "action": "Spousal bypass trust for pension death benefits",
            "detail": (
                "Pension death benefits nominated to a discretionary trust (rather than the spouse outright) "
                "prevent inclusion in the surviving spouse's estate — avoiding double IHT exposure."
            ),
            "estimated_benefit": "Eliminates pension entering the estate twice across successive deaths.",
        })
        actions.append({
            "priority": 4,
            "action": "PET gifting strategy",
            "detail": (
                "Gifts made now start the 7-year survival clock. Large cash gifts to children or a "
                "discretionary trust can reduce the estate before pension inclusion takes effect in 2027."
            ),
            "estimated_benefit": "Taper relief applies after 3 years; full IHT exemption after 7 years.",
        })

    if figures["lpa_missing"]:
        actions.append({
            "priority": 2 if not figures["affected"] else 5,
            "action": "Register Lasting Powers of Attorney",
            "detail": (
                f"{', '.join(figures['lpa_missing'])} {'has' if len(figures['lpa_missing']) == 1 else 'have'} "
                "no LPA registered. Without a Property & Finance LPA, family cannot manage finances if "
                "capacity is lost — pension decisions could be frozen at exactly the wrong moment."
            ),
            "estimated_benefit": "Critical governance — protects all planning actions if capacity is impaired.",
        })

    return {
        "type": "uk_iht_pension_inclusion",
        "title": "UK IHT: Pension Pots Enter Estate",
        "subtitle": "Finance Bill 2024 — effective April 6, 2027",
        "deadline": _UK_PENSION_DEADLINE.isoformat(),
        "days_remaining": days,
        "urgency": urgency,
        "summary": (
            f"From 6 April 2027, unused pension pots are included in the IHT estate under Finance Bill 2024. "
            f"This household's pension assets of £{figures['pension_total']:,.0f} will increase the IHT "
            f"liability by £{figures['additional_iht']:,.0f}."
            if figures["affected"] else
            "No pension assets are recorded in this household's profile. "
            "Add pension values to profile.tax.pension_pot to model the April 2027 IHT impact."
        ),
        "figures": figures,
        "actions": actions,
        "currency": "GBP",
        "regulation": "Finance Bill 2024 — IHT on pension assets from April 6, 2027",
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def for_household(
    session: AsyncSession, household_id: uuid.UUID, firm_jurisdiction: str = "NZ"
) -> dict | None:
    brain = await household_brain(session, str(household_id))
    if not brain:
        return None

    jurisdiction = brain["household"]["values"].get("jurisdiction") or firm_jurisdiction

    result: dict = {
        "household_id": str(household_id),
        "household_name": brain["household"]["name"],
        "jurisdiction": jurisdiction,
        "analyses": [],
    }

    if jurisdiction == "US":
        result["analyses"].append(_us_estate_sunset(brain))
    elif jurisdiction == "UK":
        result["analyses"].append(_uk_iht_pension(brain))
    else:
        result["message"] = (
            f"No critical upcoming regulatory deadlines are currently tracked for {jurisdiction} jurisdiction. "
            "US and UK countdown analyses are available for households with jurisdiction set to 'US' or 'UK'."
        )

    return result


async def for_firm(
    session: AsyncSession, firm_id: uuid.UUID, firm_jurisdiction: str = "NZ"
) -> list[dict]:
    households = await list_households(session, firm_id)
    results = []
    for hh in households:
        r = await for_household(session, uuid.UUID(hh["id"]), firm_jurisdiction)
        if r and r.get("analyses"):
            results.append(r)
    return results
