"""Regulatory Countdown — jurisdiction-aware deadline intelligence.

US modules:
  1. Federal Estate Tax (post-sunset reality) — exemption halved to ~$7M/person from Jan 2026.
  2. Reg BI / DOL Fiduciary ongoing compliance — annual best-interest evidence review.

UK modules:
  1. IHT Pension Pot Inclusion (April 6, 2027) — unused pension pots enter the IHT estate.
  2. BPR/APR Cap (April 6, 2027) — business/agricultural property relief capped at £1M.
  3. FCA Targeted Support (expected 2027) — new regulated category between guidance and advice.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain, list_households

# ── US constants ──────────────────────────────────────────────────────────────
# TCJA exemption sunset occurred Jan 1, 2026 — exemption reverted to indexed ~$7M/person
_US_EXEMPTION_CURRENT = 13_610_000      # TCJA high exemption (expired Jan 1, 2026 — historical baseline)
_US_EXEMPTION_POST_SUNSET = 7_000_000   # approx 2026 indexed value (now in force)
_US_ESTATE_TAX_RATE = 0.40
_US_GIFT_EXCLUSION = 19_000             # per-donor-per-recipient annual exclusion (2025)
_US_SUNSET_DATE = date(2026, 1, 1)      # already past — exemption IS reduced

# ── UK constants ──────────────────────────────────────────────────────────────
_UK_NRB = 325_000                        # nil-rate band per person
_UK_RNRB = 175_000                       # residence nil-rate band per person (if residence to direct descendants)
_UK_IHT_RATE = 0.40
_UK_PENSION_DEADLINE = date(2027, 4, 6)  # start of 2027/28 UK tax year
_UK_RNRB_TAPER_THRESHOLD = 2_000_000    # RNRB tapers £1 per £2 above this
_UK_BPR_APR_DATE = date(2027, 4, 6)     # BPR/APR cap takes effect (Budget 2024)
_UK_BPR_CAP = 1_000_000                 # BPR/APR exemption cap — above this, 50% relief only
_UK_TARGETED_SUPPORT_DATE = date(2027, 1, 1)   # FCA Targeted Support expected legislation


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
    persons = brain.get("persons", [])
    adult_count = len([p for p in persons if not p.get("is_next_gen")])

    # Sunset occurred Jan 1, 2026 — always in the past from today
    urgency = "critical"  # exemption IS reduced — planning is urgent now

    actions = []
    if figures["affected"]:
        actions.append({
            "priority": 1,
            "action": "Annual gift exclusion — maximise each year",
            "detail": (
                f"Each donor can give ${_US_GIFT_EXCLUSION:,}/year per recipient tax-free (2025). "
                "Fund irrevocable trusts or direct gifts to children, grandchildren, and charities. "
                "With gift-splitting, a married couple effectively gives $38,000/recipient/year."
            ),
            "estimated_benefit": f"${_US_GIFT_EXCLUSION * adult_count * 4:,}+/year reduction in taxable estate.",
        })
        actions.append({
            "priority": 2,
            "action": "Spousal Lifetime Access Trust (SLAT)",
            "detail": (
                "A SLAT allows one spouse to transfer assets irrevocably out of the estate "
                "while the other spouse retains indirect access through the trustee. "
                "Established now under the reduced exemption — assets inside the trust are frozen out of future estate growth."
            ),
            "estimated_benefit": f"Up to ${_US_EXEMPTION_POST_SUNSET/1e6:.0f}M per person sheltered per donor.",
        })
        actions.append({
            "priority": 3,
            "action": "Grantor Retained Annuity Trust (GRAT)",
            "detail": (
                "Transfer appreciating assets into a short-term GRAT. Appreciation above the §7520 "
                "hurdle rate (currently ~5%) passes to heirs completely estate-tax-free. "
                "Works best with assets likely to appreciate significantly (private equity, concentrated positions)."
            ),
            "estimated_benefit": "Appreciation above the hurdle rate exits the estate with zero estate or gift tax.",
        })
        actions.append({
            "priority": 4,
            "action": "Charitable strategies — DAF, CRT, CLT",
            "detail": (
                "Donor-Advised Funds (DAFs): immediate charitable deduction, assets leave the estate at once. "
                "Charitable Remainder Trusts (CRT): income stream to donor/heir, remainder to charity estate-tax-free. "
                "Charitable Lead Trusts (CLT): income to charity first, remainder to heirs at reduced gift-tax value."
            ),
            "estimated_benefit": "Removes appreciated assets from estate while generating income or charitable deduction.",
        })
        if figures["retirement_accounts"] > 0:
            actions.append({
                "priority": 5,
                "action": "Roth conversion — reduce IRA estate exposure",
                "detail": (
                    "Traditional IRA/401(k) balances inflate the taxable estate AND require RMDs. "
                    "Roth conversions: pay income tax now (at potentially lower rates), eliminate future RMDs, "
                    "and leave Roth assets income-tax-free to heirs."
                ),
                "estimated_benefit": f"${figures['retirement_accounts']:,.0f} IRA/401(k) estate exposure addressable.",
            })

    return {
        "type": "us_estate_tax_sunset",
        "title": "US Estate Tax — Reduced Exemption in Force",
        "subtitle": f"TCJA sunset effective Jan 2026 — exemption now ~${_US_EXEMPTION_POST_SUNSET/1e6:.0f}M/person",
        "deadline": _US_SUNSET_DATE.isoformat(),
        "days_remaining": 0,
        "urgency": urgency,
        "summary": (
            f"The TCJA estate tax exemption sunset occurred on Jan 1, 2026. "
            f"The federal exemption has reverted to approximately ${_US_EXEMPTION_POST_SUNSET/1e6:.0f}M/person "
            f"(down from $13.61M). This household's estimated gross estate of ${figures['gross_estate']/1e6:.1f}M "
            + ("exceeds the reduced threshold — estate planning actions are needed now."
               if figures["affected"] else f"remains below the ${_US_EXEMPTION_POST_SUNSET/1e6:.0f}M threshold — continue monitoring estate growth.")
        ),
        "figures": figures,
        "actions": actions,
        "currency": "USD",
        "regulation": "Tax Cuts and Jobs Act 2017 — §2010 sunset; post-sunset rules effective Jan 1, 2026",
        "legislative_note": (
            "Congressional action to restore the higher exemption remains possible but uncertain. "
            "Planning under the current reduced exemption is the prudent approach."
        ),
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


# ── UK BPR/APR Cap ───────────────────────────────────────────────────────────

def _uk_bpr_apr_cap(brain: dict) -> dict:
    """Business Property Relief / Agricultural Property Relief cap from April 2027 (Budget 2024)."""
    days = _days_until(_UK_BPR_APR_DATE)
    urgency = "critical" if days < 300 else ("high" if days < 500 else "medium")

    persons = brain.get("persons", [])
    totals = brain.get("totals", {})
    by_class = totals.get("by_asset_class", {})

    # Alternatives as proxy for private business / agricultural assets
    alternatives = by_class.get("alternatives", 0) or 0
    portfolio = totals.get("total_value", 0) or 0

    # Estimate BPR/APR-qualifying assets (alternatives as a rough proxy)
    est_qualifying = alternatives
    cap_exposure = max(0, est_qualifying - _UK_BPR_CAP)
    iht_additional = cap_exposure * 0.20  # 50% relief → 20% effective IHT on excess

    actions = []
    if est_qualifying > _UK_BPR_CAP:
        actions.append({
            "priority": 1,
            "action": "Review BPR/APR eligibility and cap exposure",
            "detail": (
                f"From April 2027, BPR/APR is capped at £{_UK_BPR_CAP:,}. "
                f"Qualifying assets above this threshold receive only 50% relief (effective IHT rate: 20%). "
                "Identify all business and agricultural assets; quantify exposure above the cap; "
                "consider restructuring to maximise per-person caps (each individual has their own £1M allowance)."
            ),
            "estimated_benefit": f"~£{iht_additional:,.0f} additional IHT avoidable with proactive restructuring.",
        })
        actions.append({
            "priority": 2,
            "action": "Lifetime gifts of qualifying assets before April 2027",
            "detail": (
                "Gifts of BPR/APR-qualifying assets made before April 2027 may still benefit from full (100%) relief. "
                "Outright gifts to family members or into a discretionary trust could lock in the pre-cap treatment "
                "if the donor survives 2 years (for BPR) or 7 years (PET)."
            ),
            "estimated_benefit": "Could shelter qualifying assets under current unlimited BPR/APR before the cap takes effect.",
        })
        actions.append({
            "priority": 3,
            "action": "Whole-of-life insurance to cover residual IHT",
            "detail": (
                "A whole-of-life policy written in a discretionary trust can fund the residual IHT liability "
                "arising from assets above the £1M cap, preserving the business or farm intact for successors."
            ),
            "estimated_benefit": f"Covers projected £{iht_additional:,.0f} IHT without forcing an asset sale.",
        })
    else:
        actions.append({
            "priority": 1,
            "action": "Confirm BPR/APR-qualifying asset inventory",
            "detail": (
                f"With an estimated £{est_qualifying:,.0f} in alternatives/business assets, "
                f"the £{_UK_BPR_CAP:,} cap is not breached. "
                "However, confirm which assets qualify for BPR (2-year minimum holding, actively traded or private business) "
                "vs APR (agricultural land, minimum 2 years owner-occupied or 7 years tenanted). "
                "Ensure the necessary holding periods are met before April 2027."
            ),
            "estimated_benefit": "Maintains full BPR/APR on qualifying assets — no additional IHT exposure.",
        })

    return {
        "type": "uk_bpr_apr_cap",
        "title": "UK BPR/APR Reform — £1M Relief Cap",
        "subtitle": "Business & agricultural property relief capped at £1M from April 2027",
        "deadline": _UK_BPR_APR_DATE.isoformat(),
        "days_remaining": days,
        "urgency": urgency,
        "summary": (
            f"From 6 April 2027 (Budget 2024), Business Property Relief and Agricultural Property Relief "
            f"are capped at £{_UK_BPR_CAP:,} per individual. Assets above this threshold receive only 50% relief "
            f"(effective 20% IHT). "
            + (f"This household has approximately £{est_qualifying:,.0f} in business/alternative assets — "
               f"£{cap_exposure:,.0f} is above the cap, creating an estimated £{iht_additional:,.0f} additional IHT exposure."
               if est_qualifying > _UK_BPR_CAP else
               f"Current business/alternative assets (£{est_qualifying:,.0f}) appear within the £{_UK_BPR_CAP:,} cap.")
        ),
        "figures": {
            "estimated_qualifying_assets": round(est_qualifying, 2),
            "cap": _UK_BPR_CAP,
            "cap_exposure": round(cap_exposure, 2),
            "estimated_additional_iht": round(iht_additional, 2),
            "affected": est_qualifying > _UK_BPR_CAP,
        },
        "actions": actions,
        "currency": "GBP",
        "regulation": "Autumn Budget 2024 — BPR/APR cap effective April 6, 2027",
    }


# ── UK FCA Targeted Support ───────────────────────────────────────────────────

def _uk_fca_targeted_support(brain: dict) -> dict:
    """FCA Targeted Support — new regulatory category between guidance and regulated advice."""
    days = _days_until(_UK_TARGETED_SUPPORT_DATE)
    urgency = "medium" if days > 400 else "high"

    totals = brain.get("totals", {})
    total_value = totals.get("total_value", 0) or 0
    persons = brain.get("persons", [])

    actions = [
        {
            "priority": 1,
            "action": "Map current service offering to the new Targeted Support tier",
            "detail": (
                "The FCA Targeted Support regime (expected 2026/27) will create a new category "
                "between generic guidance (unregulated) and full regulated advice. "
                "Firms will be able to offer more tailored, client-specific guidance "
                "without the full suitability assessment burden — but within defined parameters. "
                "Map which of your current conversations qualify and which services can be repositioned."
            ),
            "estimated_benefit": "Expands addressable market to mass-affluent clients previously underserved by full advice costs.",
        },
        {
            "priority": 2,
            "action": "Prepare data infrastructure for evidencing Targeted Support decisions",
            "detail": (
                "Targeted Support will likely require firms to evidence which standardised scenarios "
                "were used and why they were appropriate for each client. "
                "Ensure your CRM and advice systems can capture and export this trail at scale. "
                "Platforms that cannot evidence compliance will be unable to use the new category."
            ),
            "estimated_benefit": "Operational readiness reduces compliance risk and time-to-market when legislation lands.",
        },
        {
            "priority": 3,
            "action": "Monitor FCA consultation and draft rules",
            "detail": (
                "The FCA published CP23/24 (Consumer Investments) and the Advice Guidance Boundary Review. "
                "A further consultation on detailed Targeted Support rules is expected in 2026. "
                "Assign responsibility for tracking developments and updating your advice framework accordingly."
            ),
            "estimated_benefit": "First-mover advantage when the regime goes live.",
        },
    ]

    return {
        "type": "uk_fca_targeted_support",
        "title": "FCA Targeted Support — New Advice Category",
        "subtitle": "Regulatory framework expected 2026/27 — prepare now",
        "deadline": _UK_TARGETED_SUPPORT_DATE.isoformat(),
        "days_remaining": days,
        "urgency": urgency,
        "summary": (
            "The FCA Advice Guidance Boundary Review is expected to introduce a new 'Targeted Support' category, "
            "allowing firms to offer more tailored, client-specific guidance without a full regulated advice process. "
            "This is a significant commercial and compliance opportunity — firms that adapt early will gain "
            "access to underserved mass-affluent clients while managing regulatory risk proactively."
        ),
        "figures": {
            "total_portfolio_value": total_value,
            "client_count": len(persons),
        },
        "actions": actions,
        "currency": "GBP",
        "regulation": "FCA Advice Guidance Boundary Review (2023–2026) — CP23/24 and forthcoming consultation",
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
        result["analyses"].append(_uk_bpr_apr_cap(brain))
        result["analyses"].append(_uk_fca_targeted_support(brain))
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
