"""Tax Intelligence — jurisdiction-dispatched tax optimisation engine.

Jurisdiction routing:
  NZ  — 5 modules: loss-harvest, PIE regime, KiwiSaver, bright-line, withdrawal sequencing
  US  — 5 modules: CGT classification + NIIT, RMD planning, gift tracker, Roth conversion, withdrawal sequencing
  UK  — 6 modules: CGT annual exempt amount, ISA allowance, pension allowance, PET clock, dividend allowance, withdrawal sequencing
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain, list_households
from app.models.portfolio import Holding, Instrument, TaxLot

# ── NZ constants ──────────────────────────────────────────────────────────────
_GOVT_TOPUP_MAX = 521.43
_GOVT_TOPUP_RATE = 0.50
_GOVT_TOPUP_OWN_MIN = 1042.86
_BRIGHT_LINE_SHORT = 2
_BRIGHT_LINE_LONG = 10
_HARVEST_THRESHOLD = -5_000
_WASH_SALE_WINDOW = 30
_KIWISAVER_RATES = [0.03, 0.04, 0.06, 0.08, 0.10]
_ASSUMED_EMPLOYER_MATCH = 0.03
_ASSUMED_GROSS_RETURN = 0.07

# ── US constants ──────────────────────────────────────────────────────────────
_US_LTCG_RATE_ZERO_MFJ = 94_050       # 0% LTCG rate ceiling (married filing jointly, 2024)
_US_LTCG_RATE_15_MFJ = 583_750        # 15% LTCG rate ceiling (MFJ, 2024)
_US_NIIT_THRESHOLD_MFJ = 250_000      # NIIT ($200k single / $250k married)
_US_NIIT_THRESHOLD_SINGLE = 200_000
_US_NIIT_RATE = 0.038
_US_ANNUAL_GIFT_EXCLUSION = 19_000    # per donor per recipient (2025)
_US_RMD_FACTORS = {                   # IRS Uniform Lifetime Table (simplified)
    73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9,
    78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5,
    83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4,
    88: 13.7, 89: 12.9, 90: 12.2,
}
# 2025 tax brackets MFJ
_US_BRACKETS_MFJ = [
    (23_850, 0.10), (96_950, 0.12), (206_700, 0.22),
    (394_600, 0.24), (501_050, 0.32), (751_600, 0.35), (float("inf"), 0.37)
]
_US_BRACKETS_SINGLE = [
    (11_925, 0.10), (48_475, 0.12), (103_350, 0.22),
    (197_300, 0.24), (250_525, 0.32), (626_350, 0.35), (float("inf"), 0.37)
]

# ── UK constants ──────────────────────────────────────────────────────────────
_UK_CGT_EXEMPT = 3_000               # annual exempt amount 2024/25
_UK_CGT_RATE_BASIC = 0.18            # investments (basic rate taxpayer)
_UK_CGT_RATE_HIGHER = 0.24           # investments (higher / additional rate)
_UK_ISA_ALLOWANCE = 20_000           # per person per year
_UK_PENSION_AA = 60_000              # annual allowance 2024/25
_UK_PENSION_TAPER_THRESHOLD = 260_000  # adjusted income above this → taper
_UK_PENSION_MIN_AA = 10_000          # minimum tapered allowance
_UK_PENSION_LSA = 268_275            # Lump Sum Allowance (tax-free cash cap)
_UK_DIVIDEND_ALLOWANCE = 500         # 2024/25 annual dividend allowance
_UK_DIVIDEND_RATE_BASIC = 0.0875
_UK_DIVIDEND_RATE_HIGHER = 0.3375
_UK_ESTIMATED_EQUITY_YIELD = 0.025   # used to estimate dividend income from portfolio


def _age(dob: str | None) -> int | None:
    if not dob:
        return None
    try:
        y, m, d = map(int, dob.split("-"))
        today = date.today()
        return today.year - y - ((today.month, today.day) < (m, d))
    except Exception:
        return None


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _load_lots(session: AsyncSession, brain: dict) -> list[dict]:
    account_ids = [uuid.UUID(a["id"]) for a in brain.get("accounts", [])]
    if not account_ids:
        return []

    account_map = {a["id"]: a for a in brain.get("accounts", [])}
    mandate_map = {m["id"]: m for m in brain.get("mandates", [])}

    holdings = (
        await session.execute(select(Holding).where(Holding.account_id.in_(account_ids)))
    ).scalars().all()
    if not holdings:
        return []

    holding_ids = [h.id for h in holdings]
    holding_map = {h.id: h for h in holdings}

    lots = (
        await session.execute(select(TaxLot).where(TaxLot.holding_id.in_(holding_ids)))
    ).scalars().all()

    instrument_ids = list({h.instrument_id for h in holdings})
    instruments = (
        await session.execute(select(Instrument).where(Instrument.id.in_(instrument_ids)))
    ).scalars().all()
    instrument_map = {i.id: i for i in instruments}

    holding_unit_price: dict[uuid.UUID, float] = {}
    for h in holdings:
        qty = float(h.quantity or 0)
        mv = float(h.market_value or 0)
        if qty > 0:
            holding_unit_price[h.id] = mv / qty

    today = date.today()
    recent_instrument_ids: set[uuid.UUID] = set()
    for lot in lots:
        if (today - lot.acquired_on).days <= _WASH_SALE_WINDOW:
            h = holding_map.get(lot.holding_id)
            if h:
                recent_instrument_ids.add(h.instrument_id)

    result = []
    for lot in lots:
        h = holding_map.get(lot.holding_id)
        if not h:
            continue
        inst = instrument_map.get(h.instrument_id)
        current_price = holding_unit_price.get(h.id, float(lot.cost_per_unit))
        acc = account_map.get(str(h.account_id)) or {}
        mandate_id = acc.get("mandate_id")
        mandate = mandate_map.get(mandate_id) or {} if mandate_id else {}

        result.append({
            "lot_id": str(lot.id),
            "account_name": acc.get("name", ""),
            "mandate_id": mandate_id,
            "mandate_name": mandate.get("name", ""),
            "symbol": inst.symbol if inst else "",
            "instrument_name": inst.name if inst else "",
            "quantity": float(lot.quantity),
            "cost_per_unit": float(lot.cost_per_unit),
            "current_price": current_price,
            "acquired_on": lot.acquired_on.isoformat(),
            "holding_days": (today - lot.acquired_on).days,
            "wash_sale_risk": (
                h.instrument_id in recent_instrument_ids
                and (today - lot.acquired_on).days > _WASH_SALE_WINDOW
            ),
        })
    return result


# ════════════════════════════════════════════════════════════════════════════════
# NZ MODULES (existing — unchanged)
# ════════════════════════════════════════════════════════════════════════════════

def _scan_loss_harvest_nz(lots: list[dict]) -> dict:
    opportunities = []
    for lot in lots:
        unrealised = (lot["current_price"] - lot["cost_per_unit"]) * lot["quantity"]
        if unrealised < _HARVEST_THRESHOLD:
            opportunities.append({
                **lot,
                "unrealised_loss": round(unrealised, 2),
                "unrealised_loss_pct": round(
                    (lot["current_price"] - lot["cost_per_unit"]) / lot["cost_per_unit"] * 100, 1
                ) if lot["cost_per_unit"] else 0,
            })

    opportunities.sort(key=lambda x: x["unrealised_loss"])
    total = sum(o["unrealised_loss"] for o in opportunities)
    return {
        "opportunities": opportunities,
        "count": len(opportunities),
        "total_harvestable_loss": round(total, 2),
        "estimated_tax_saving_at_top_pir": round(abs(total) * 0.28, 2),
    }


def _pie_optimisation(brain: dict) -> dict:
    persons = brain.get("persons", [])
    mandates = brain.get("mandates", [])
    accounts = brain.get("accounts", [])

    mandate_value: dict[str, float] = {}
    for acc in accounts:
        mid = acc.get("mandate_id")
        if mid:
            mandate_value[mid] = mandate_value.get(mid, 0) + (acc.get("total_value") or 0)

    flags = []
    for person in persons:
        tax = (person.get("profile") or {}).get("tax") or {}
        marginal = tax.get("marginal_rate")
        pir = tax.get("pir", 0.28)
        if not marginal:
            continue

        person_mandates = [m for m in mandates if m.get("person_id") == person["id"]]
        for m in person_mandates:
            constraints = m.get("constraints") or {}
            acct_type = constraints.get("account_type", "direct")
            value = mandate_value.get(m["id"], 0)
            estimated_return = value * _ASSUMED_GROSS_RETURN
            saving = estimated_return * max(0.0, marginal - pir) if marginal > pir else 0.0

            if marginal > pir and acct_type == "direct" and value > 0:
                flags.append({
                    "person_name": person.get("preferred_name") or person["full_name"],
                    "mandate_name": m["name"],
                    "current_treatment": "direct",
                    "recommended_treatment": "PIE fund",
                    "marginal_rate_pct": round(marginal * 100, 1),
                    "pir_pct": round(pir * 100, 1),
                    "mandate_value": round(value, 2),
                    "estimated_annual_saving": round(saving, 2),
                    "action": (
                        f"Restructure into a PIE fund to cap tax at PIR "
                        f"({pir*100:.1f}%) instead of marginal rate ({marginal*100:.1f}%). "
                        f"Estimated annual saving: ${saving:,.0f}."
                    ),
                })

    return {
        "flags": flags,
        "count": len(flags),
        "total_annual_saving": round(sum(f["estimated_annual_saving"] for f in flags), 2),
    }


def _kiwisaver_optimisation(brain: dict) -> dict:
    persons = brain.get("persons", [])
    recommendations = []

    for person in persons:
        tax = (person.get("profile") or {}).get("tax") or {}
        marginal = tax.get("marginal_rate")
        current_rate = tax.get("kiwisaver_rate")
        annual_income = tax.get("annual_income")

        if not marginal or not current_rate or not annual_income:
            continue

        rate_analysis = []
        for rate in _KIWISAVER_RATES:
            own = annual_income * rate
            govt = min(_GOVT_TOPUP_MAX, own * _GOVT_TOPUP_RATE)
            emp = annual_income * _ASSUMED_EMPLOYER_MATCH
            net = govt + emp
            rate_analysis.append({
                "rate_pct": int(rate * 100),
                "own_contribution": round(own, 2),
                "employer_match": round(emp, 2),
                "govt_topup": round(govt, 2),
                "net_annual_benefit": round(net, 2),
            })

        best = max(rate_analysis, key=lambda x: x["net_annual_benefit"])
        current_own = annual_income * current_rate
        current_govt = min(_GOVT_TOPUP_MAX, current_own * _GOVT_TOPUP_RATE)

        recommendations.append({
            "person_name": person.get("preferred_name") or person["full_name"],
            "annual_income": annual_income,
            "current_rate_pct": int(current_rate * 100),
            "recommended_rate_pct": best["rate_pct"],
            "current_govt_topup": round(current_govt, 2),
            "max_govt_topup": _GOVT_TOPUP_MAX,
            "employer_match_annual": round(annual_income * _ASSUMED_EMPLOYER_MATCH, 2),
            "rate_analysis": rate_analysis,
            "action": (
                f"Increase KiwiSaver rate from {int(current_rate*100)}% to {best['rate_pct']}% "
                f"to capture full government top-up of ${_GOVT_TOPUP_MAX:,.2f}/yr + "
                f"employer match of ${annual_income * _ASSUMED_EMPLOYER_MATCH:,.0f}/yr."
            ) if best["rate_pct"] != int(current_rate * 100) else "KiwiSaver rate appears optimal.",
        })

    return {"recommendations": recommendations, "count": len(recommendations)}


def _bright_line_check(brain: dict) -> dict:
    persons = brain.get("persons", [])
    today = date.today()
    flags = []

    for person in persons:
        properties = (person.get("profile") or {}).get("properties") or []
        for idx, prop in enumerate(properties):
            acquired_str = prop.get("acquired_on")
            if not acquired_str:
                continue
            try:
                acquired = date.fromisoformat(acquired_str)
            except ValueError:
                continue

            test_years = _BRIGHT_LINE_SHORT if idx == 0 else _BRIGHT_LINE_LONG
            test_days = test_years * 365
            days_held = (today - acquired).days
            days_remaining = test_days - days_held

            if days_remaining > 365:
                continue

            if days_remaining <= 0:
                status = "within_bright_line"
                action = (
                    f"Property held {days_held} days — still within the {test_years}-year bright-line period. "
                    "Any sale is a taxable event. Do not sell without specialist tax advice."
                )
            else:
                months = round(days_remaining / 30.4, 1)
                status = "approaching"
                action = (
                    f"Approaching {test_years}-year bright-line in ~{months:.1f} months "
                    f"({acquired_str} → safe after "
                    f"{date.fromordinal(acquired.toordinal() + test_days).isoformat()}). "
                    "If a sale is planned, consider waiting."
                )

            flags.append({
                "person_name": person.get("preferred_name") or person["full_name"],
                "property_address": prop.get("address", f"Property {idx + 1}"),
                "property_value": prop.get("value"),
                "acquired_on": acquired_str,
                "days_held": days_held,
                "test_years": test_years,
                "days_until_safe": max(0, days_remaining),
                "months_until_safe": max(0.0, round(days_remaining / 30.4, 1)),
                "status": status,
                "action": action,
            })

    flags.sort(key=lambda x: x["days_until_safe"])
    return {"flags": flags, "count": len(flags)}


def _withdrawal_sequencing_nz(brain: dict) -> dict:
    mandates = brain.get("mandates", [])
    accounts = brain.get("accounts", [])

    mandate_value: dict[str, float] = {}
    for acc in accounts:
        mid = acc.get("mandate_id")
        if mid:
            mandate_value[mid] = mandate_value.get(mid, 0) + (acc.get("total_value") or 0)

    sequences = []
    for m in mandates:
        constraints = m.get("constraints") or {}
        acct_type = constraints.get("account_type", "direct")
        value = mandate_value.get(m["id"], 0)

        if acct_type == "kiwisaver":
            priority, label = 4, "4 — Draw last"
            rationale = "KiwiSaver: PIR-capped taxation, locked until 65. Preserve as long as possible."
        elif acct_type == "pie":
            priority, label = 3, "3 — Third"
            rationale = "PIE funds: PIR-capped taxation. Preserve after drawing direct holdings."
        elif m.get("mandate_type") in ("advisory", "execution_only"):
            priority, label = 1, "1 — Draw first"
            rationale = "Direct taxable holdings — draw first to realise losses, then rebuy in PIE wrapper."
        else:
            priority, label = 2, "2 — Second"
            rationale = "Discretionary — draw after direct advisory, before PIE/KiwiSaver."

        sequences.append({
            "mandate_name": m["name"], "mandate_type": m.get("mandate_type"),
            "account_type": acct_type, "value": round(value, 2),
            "withdrawal_priority": priority, "priority_label": label, "rationale": rationale,
        })

    sequences.sort(key=lambda x: x["withdrawal_priority"])
    return {
        "sequences": sequences, "count": len(sequences),
        "guidance": (
            "Draw down accounts in priority order to minimise lifetime tax. "
            "Realise losses in direct accounts first, then rebuy in PIE to lock future gains at PIR."
        ),
    }


def _nz_tax_report(brain: dict, lots: list[dict]) -> dict:
    loss_harvest = _scan_loss_harvest_nz(lots)
    pie_opt = _pie_optimisation(brain)
    ks_opt = _kiwisaver_optimisation(brain)
    bright = _bright_line_check(brain)
    withdrawal = _withdrawal_sequencing_nz(brain)

    return {
        "jurisdiction": "NZ",
        "currency": "NZD",
        "loss_harvest": loss_harvest,
        "pie_optimisation": pie_opt,
        "kiwisaver": ks_opt,
        "bright_line": bright,
        "withdrawal_sequencing": withdrawal,
        "summary": {
            "total_flags": loss_harvest["count"] + pie_opt["count"] + ks_opt["count"] + bright["count"],
            "harvestable_loss": loss_harvest["total_harvestable_loss"],
            "estimated_tax_saving": round(
                loss_harvest["estimated_tax_saving_at_top_pir"] + pie_opt["total_annual_saving"], 2
            ),
            "bright_line_flags": bright["count"],
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
# US MODULES
# ════════════════════════════════════════════════════════════════════════════════

def _us_cgt_analysis(lots: list[dict], brain: dict) -> dict:
    """CGT classification, NIIT threshold alerts, and wash-sale warnings."""
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]
    married = len(adults) >= 2
    niit_threshold = _US_NIIT_THRESHOLD_MFJ if married else _US_NIIT_THRESHOLD_SINGLE

    short_term_gains, short_term_losses = [], []
    long_term_gains, long_term_losses = [], []

    for lot in lots:
        gain = (lot["current_price"] - lot["cost_per_unit"]) * lot["quantity"]
        entry = {**lot, "unrealised_gain": round(gain, 2)}
        if lot["holding_days"] < 366:
            (short_term_gains if gain > 500 else short_term_losses if gain < -1_000 else []).append(entry)
        else:
            (long_term_gains if gain > 500 else long_term_losses if gain < -1_000 else []).append(entry)

    total_st_gains = sum(l["unrealised_gain"] for l in short_term_gains)
    total_lt_gains = sum(l["unrealised_gain"] for l in long_term_gains)
    total_harvestable = abs(sum(l["unrealised_gain"] for l in short_term_losses + long_term_losses))

    incomes = [p.get("profile", {}).get("tax", {}).get("annual_income", 0) or 0 for p in adults]
    total_income = sum(incomes)
    niit_exposed = total_income > niit_threshold

    # Estimated tax saving: converting ST → LT (assuming 22% vs 15% rate)
    potential_rate_saving = total_st_gains * 0.07  # 22% ordinary vs 15% LTCG

    return {
        "short_term_gains": sorted(short_term_gains, key=lambda x: -x["unrealised_gain"])[:8],
        "long_term_gains": sorted(long_term_gains, key=lambda x: -x["unrealised_gain"])[:8],
        "loss_lots": sorted(short_term_losses + long_term_losses, key=lambda x: x["unrealised_gain"])[:8],
        "total_short_term_gains": round(total_st_gains, 2),
        "total_long_term_gains": round(total_lt_gains, 2),
        "total_harvestable_losses": round(total_harvestable, 2),
        "married": married,
        "niit_threshold": niit_threshold,
        "estimated_income": total_income,
        "niit_exposed": niit_exposed,
        "niit_additional_tax": round(total_lt_gains * _US_NIIT_RATE, 2) if niit_exposed else 0,
        "potential_st_to_lt_saving": round(potential_rate_saving, 2),
        "wash_sale_violations": [l for l in lots if l.get("wash_sale_risk")],
        "st_conversion_action": (
            f"${total_st_gains:,.0f} in short-term gains will be taxed at ordinary income rates. "
            "Holding these positions for 12+ months converts them to long-term capital gains "
            f"(15–20% vs up to {adults[0].get('profile',{}).get('tax',{}).get('marginal_rate',0.37)*100:.0f}% ordinary). "
            f"Potential saving: ~${potential_rate_saving:,.0f}."
        ) if total_st_gains > 10_000 else None,
        "niit_action": (
            f"Income of ${total_income:,.0f} exceeds the ${niit_threshold:,} NIIT threshold. "
            f"Net investment income will incur an additional 3.8% NIIT. "
            "Consider tax-loss harvesting or deferring income to reduce exposure."
        ) if niit_exposed else None,
    }


def _us_rmd_planning(brain: dict) -> dict:
    """RMD alerts for persons aged 73+ and pre-planning for 65–72."""
    persons = brain.get("persons", [])
    alerts = []

    for person in persons:
        if person.get("is_next_gen"):
            continue
        tax = (person.get("profile") or {}).get("tax") or {}
        ira = tax.get("ira_value", 0) or 0
        roth = tax.get("roth_value", 0) or 0
        if not ira:
            continue

        age = _age(person.get("date_of_birth"))
        if not age:
            continue

        name = person.get("preferred_name") or person["full_name"]

        if age >= 73:
            factor = _US_RMD_FACTORS.get(age, max(1.0, 26.5 - (age - 73) * 0.9))
            rmd_amount = ira / factor
            alerts.append({
                "person_name": name, "age": age,
                "ira_balance": ira, "roth_balance": roth,
                "rmd_required": True,
                "estimated_rmd": round(rmd_amount, 2),
                "life_expectancy_factor": factor,
                "severity": "high",
                "action": (
                    f"RMD required: approximately ${rmd_amount:,.0f} must be withdrawn by December 31 "
                    f"(IRA balance ${ira:,.0f}, factor {factor}). "
                    "Failure incurs a 25% excise tax (reduced to 10% if corrected within 2 years via SECURE 2.0)."
                ),
            })
        elif age >= 65:
            years_to_rmd = 73 - age
            alerts.append({
                "person_name": name, "age": age,
                "ira_balance": ira, "roth_balance": roth,
                "rmd_required": False,
                "years_to_rmd": years_to_rmd,
                "severity": "medium",
                "action": (
                    f"RMD begins at age 73 ({years_to_rmd} year(s) away). "
                    f"IRA balance ${ira:,.0f}. "
                    "Consider Roth conversions in lower-income years now to reduce future RMD obligations."
                ),
            })

    return {
        "alerts": alerts, "count": len(alerts),
        "rmd_active": sum(1 for a in alerts if a.get("rmd_required")),
        "total_rmd_this_year": round(sum(a.get("estimated_rmd", 0) for a in alerts if a.get("rmd_required")), 2),
        "guidance": (
            "SECURE 2.0 (2022): RMD age raised to 73 (further raising to 75 in 2033). "
            "Roth IRAs are exempt from RMDs. Roth 401(k)s are RMD-free from 2024. "
            "Qualified charitable distributions (QCDs) up to $105,000/year satisfy RMDs tax-free."
        ),
    }


def _us_gift_tracker(brain: dict) -> dict:
    """Annual gift exclusion utilisation and 529 superfunding opportunity."""
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]
    heirs = [p for p in persons if p.get("is_next_gen")]
    married = len(adults) >= 2

    # Estimate maximum annual exclusion gifts
    external_recipients = 4  # conservative assumption: 4 outside beneficiaries
    total_recipients = len(heirs) + external_recipients
    max_annual = len(adults) * total_recipients * _US_ANNUAL_GIFT_EXCLUSION

    # Estate tax exemption (post-TCJA sunset, effective Jan 2026)
    post_sunset_exemption = 7_000_000 * (2 if married else 1)
    totals = brain.get("totals", {})
    gross_estate = totals.get("total_value", 0) or 0
    estate_exposed = gross_estate > post_sunset_exemption

    superfunding = None
    if heirs:
        superfunding = {
            "available": True,
            "max_per_recipient": _US_ANNUAL_GIFT_EXCLUSION * 5,
            "max_total": _US_ANNUAL_GIFT_EXCLUSION * 5 * (2 if married else 1) * len(heirs),
            "action": (
                f"529 superfunding: up to ${_US_ANNUAL_GIFT_EXCLUSION * 5:,}/beneficiary "
                + (f"(${_US_ANNUAL_GIFT_EXCLUSION * 10:,} with gift-splitting for a married couple) " if married else "")
                + "removes the full amount from the taxable estate immediately while treating it as 5 years of annual exclusion gifts."
            ),
        }

    return {
        "annual_exclusion_per_donor": _US_ANNUAL_GIFT_EXCLUSION,
        "donors": len(adults),
        "married": married,
        "gift_splitting_available": married,
        "effective_annual_per_recipient": _US_ANNUAL_GIFT_EXCLUSION * (2 if married else 1),
        "max_annual_exclusion_gifts": round(max_annual, 0),
        "estate_exposed": estate_exposed,
        "529_superfunding": superfunding,
        "action": (
            f"Annual exclusion: each donor may give ${_US_ANNUAL_GIFT_EXCLUSION:,}/recipient tax-free (2025). "
            + (f"With gift-splitting, a married couple can give ${_US_ANNUAL_GIFT_EXCLUSION * 2:,}/recipient — "
               f"effectively ${max_annual:,.0f} total across ~{total_recipients} recipients this year." if married else "")
            + (" Systematic gifting now reduces an estate exposed to the post-sunset exemption." if estate_exposed else "")
        ),
        "qualified_exclusions": {
            "tuition": "Direct payment to educational institutions: unlimited, not a taxable gift.",
            "medical": "Direct payment to medical providers: unlimited, not a taxable gift.",
        },
    }


def _us_roth_conversion(brain: dict) -> dict:
    """Identify Roth conversion opportunities in low-marginal-rate years."""
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]
    married = len(adults) >= 2
    brackets = _US_BRACKETS_MFJ if married else _US_BRACKETS_SINGLE

    candidates = []
    for person in adults:
        tax = (person.get("profile") or {}).get("tax") or {}
        ira = tax.get("ira_value", 0) or 0
        roth = tax.get("roth_value", 0) or 0
        income = tax.get("annual_income", 0) or 0
        if not ira:
            continue

        name = person.get("preferred_name") or person["full_name"]

        # Find marginal bracket and room remaining
        prev_threshold = 0
        current_rate = None
        room_in_bracket = 0
        for threshold, rate in brackets:
            if income < threshold:
                current_rate = rate
                room_in_bracket = threshold - income
                break
            prev_threshold = threshold

        if current_rate is None:
            current_rate = 0.37
            room_in_bracket = 0

        attractive = current_rate <= 0.24
        recommended_amount = round(min(ira, room_in_bracket), 2) if attractive else 0

        candidates.append({
            "person_name": name,
            "ira_balance": ira,
            "roth_balance": roth,
            "current_income": income,
            "marginal_rate_pct": round(current_rate * 100, 1),
            "room_in_current_bracket": round(room_in_bracket, 2),
            "recommended_conversion": recommended_amount,
            "attractive": attractive,
            "action": (
                f"Roth conversion opportunity: converting up to ${recommended_amount:,.0f} "
                f"at the current {current_rate*100:.0f}% marginal rate shelters that amount from "
                "future RMDs and higher tax rates. Roth grows and withdraws tax-free."
            ) if attractive else (
                f"Current marginal rate ({current_rate*100:.0f}%) is not favourable for conversion. "
                "Consider converting in a year where income drops below the 24% threshold (e.g. early retirement, business loss year)."
            ),
        })

    return {
        "candidates": candidates,
        "count": len(candidates),
        "total_ira_balance": round(sum(p.get("profile", {}).get("tax", {}).get("ira_value", 0) or 0 for p in adults), 2),
        "guidance": (
            "Optimal conversion window: years where marginal rate is ≤24% (MFJ income ≤$394,600). "
            "Convert enough to fill the current bracket without pushing into the next one. "
            "Best years: early retirement before RMDs, years with large business losses, "
            "or after a market correction (convert more shares at lower value)."
        ),
    }


def _us_withdrawal_seq(brain: dict) -> dict:
    """US-optimal withdrawal sequencing: taxable → Traditional → Roth."""
    mandates = brain.get("mandates", [])
    accounts = brain.get("accounts", [])
    persons = brain.get("persons", [])

    mandate_value: dict[str, float] = {}
    for acc in accounts:
        mid = acc.get("mandate_id")
        if mid:
            mandate_value[mid] = mandate_value.get(mid, 0) + (acc.get("total_value") or 0)

    sequences = []
    for m in mandates:
        constraints = m.get("constraints") or {}
        acct_type = constraints.get("account_type", "direct")
        value = mandate_value.get(m["id"], 0)

        if acct_type == "roth":
            priority, label = 4, "4 — Draw last"
            rationale = "Roth IRA/401(k): no RMDs, tax-free growth and withdrawals. Preserve to maximise tax-free compound growth and pass to heirs income-tax-free."
        elif acct_type in ("ira", "401k", "traditional"):
            priority, label = 3, "3 — Third"
            rationale = "Traditional IRA/401(k): tax-deferred, subject to RMDs from age 73. Draw after taxable to reduce future RMD burden."
        elif m.get("mandate_type") in ("advisory", "execution_only"):
            priority, label = 1, "1 — Draw first"
            rationale = "Taxable brokerage: draw first. Use losses to offset gains, manage short vs long-term classification, and benefit from step-up in basis at death."
        else:
            priority, label = 2, "2 — Second"
            rationale = "Discretionary taxable: draw before retirement accounts to defer tax-deferred and tax-free growth."

        sequences.append({
            "mandate_name": m["name"], "mandate_type": m.get("mandate_type"),
            "account_type": acct_type, "value": round(value, 2),
            "withdrawal_priority": priority, "priority_label": label, "rationale": rationale,
        })

    # Include retirement accounts not in mandate system
    for person in [p for p in persons if not p.get("is_next_gen")]:
        tax = (person.get("profile") or {}).get("tax") or {}
        name = person.get("preferred_name") or person["full_name"]
        ira = tax.get("ira_value", 0) or 0
        roth = tax.get("roth_value", 0) or 0
        if ira > 0:
            sequences.append({
                "mandate_name": f"{name} — Traditional IRA", "mandate_type": "retirement",
                "account_type": "ira", "value": ira,
                "withdrawal_priority": 3, "priority_label": "3 — Third",
                "rationale": "Traditional IRA: RMDs required from 73. Draw after taxable accounts to delay and minimise RMD impact.",
            })
        if roth > 0:
            sequences.append({
                "mandate_name": f"{name} — Roth IRA", "mandate_type": "retirement",
                "account_type": "roth", "value": roth,
                "withdrawal_priority": 4, "priority_label": "4 — Draw last",
                "rationale": "Roth IRA: no RMDs, permanent tax-free status. The longest-duration tax-advantaged asset — let it compound.",
            })

    sequences.sort(key=lambda x: x["withdrawal_priority"])
    return {
        "sequences": sequences, "count": len(sequences),
        "guidance": (
            "US optimal withdrawal order: (1) taxable accounts — use annual CGT exempt amounts and loss-harvesting; "
            "(2) Traditional IRA/401(k) — defer to manage RMD timing; (3) Roth — maximise tax-free compound growth. "
            "This order may be reversed in years where Roth conversion is attractive."
        ),
    }


def _us_tax_report(brain: dict, lots: list[dict]) -> dict:
    cgt = _us_cgt_analysis(lots, brain)
    rmd = _us_rmd_planning(brain)
    gift = _us_gift_tracker(brain)
    roth = _us_roth_conversion(brain)
    withdrawal = _us_withdrawal_seq(brain)

    total_flags = (
        (1 if cgt["total_short_term_gains"] > 10_000 else 0)
        + (1 if cgt["niit_exposed"] else 0)
        + len(cgt["wash_sale_violations"])
        + rmd["rmd_active"]
        + sum(1 for c in roth["candidates"] if c["attractive"])
    )

    return {
        "jurisdiction": "US",
        "currency": "USD",
        "cgt_analysis": cgt,
        "rmd_planning": rmd,
        "gift_tracker": gift,
        "roth_conversion": roth,
        "withdrawal_sequencing": withdrawal,
        "summary": {
            "total_flags": total_flags,
            "harvestable_loss": cgt["total_harvestable_losses"],
            "estimated_tax_saving": round(
                cgt["potential_st_to_lt_saving"]
                + cgt["total_harvestable_losses"] * 0.20,  # at 20% LTCG rate
                2,
            ),
            "rmd_required": rmd["rmd_active"],
            "roth_opportunity": sum(1 for c in roth["candidates"] if c["attractive"]),
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
# UK MODULES
# ════════════════════════════════════════════════════════════════════════════════

def _uk_cgt_allowance(lots: list[dict], brain: dict) -> dict:
    """CGT annual exempt amount utilisation, gain/loss inventory, and key rate changes."""
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]

    gains, losses = [], []
    for lot in lots:
        gain = (lot["current_price"] - lot["cost_per_unit"]) * lot["quantity"]
        entry = {**lot, "unrealised_gain": round(gain, 2)}
        if gain > 500:
            gains.append(entry)
        elif gain < -1_000:
            losses.append(entry)

    total_gains = sum(l["unrealised_gain"] for l in gains)
    total_losses = abs(sum(l["unrealised_gain"] for l in losses))

    total_exempt = _UK_CGT_EXEMPT * len(adults)
    net_gains_after_exempt = max(0, total_gains - total_exempt)
    estimated_cgt = net_gains_after_exempt * _UK_CGT_RATE_HIGHER
    flag = total_gains > total_exempt

    return {
        "annual_exempt_amount": _UK_CGT_EXEMPT,
        "total_exempt_available": total_exempt,
        "total_unrealised_gains": round(total_gains, 2),
        "total_harvestable_losses": round(total_losses, 2),
        "net_taxable_gains": round(net_gains_after_exempt, 2),
        "estimated_cgt_at_higher_rate": round(estimated_cgt, 2),
        "gain_lots": sorted(gains, key=lambda x: -x["unrealised_gain"])[:8],
        "loss_lots": sorted(losses, key=lambda x: x["unrealised_gain"])[:8],
        "flag": flag,
        "action": (
            f"Unrealised gains of £{total_gains:,.0f} exceed the £{total_exempt:,} combined annual "
            f"exempt amount ({len(adults)} × £{_UK_CGT_EXEMPT:,}). "
            + (f"£{total_losses:,.0f} in harvestable losses can offset gains before 5 April. " if total_losses > 500 else "")
            + f"Estimated CGT at higher rate if crystallised: £{estimated_cgt:,.0f}."
        ) if flag else (
            f"Unrealised gains (£{total_gains:,.0f}) are within the £{total_exempt:,} annual exempt amount — no immediate CGT exposure."
        ),
        "key_changes": (
            "Oct 2024 Budget changes: CGT rates on investments raised to 18% (basic) / 24% (higher/additional). "
            f"Annual exempt amount: £3,000 (down from £12,300 in 2022/23). "
            "Consider using the full £3,000/person allowance each tax year — it cannot be carried forward."
        ),
    }


def _uk_isa_allowance(brain: dict) -> dict:
    """ISA allowance utilisation and wrapper strategy."""
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]
    under18 = [p for p in persons if p.get("is_next_gen")]

    person_flags = []
    total_isa_value = 0.0

    for person in adults:
        tax = (person.get("profile") or {}).get("tax") or {}
        isa_value = tax.get("isa_value", 0) or 0
        total_isa_value += isa_value
        name = person.get("preferred_name") or person["full_name"]

        person_flags.append({
            "person_name": name,
            "isa_value": isa_value,
            "annual_allowance": _UK_ISA_ALLOWANCE,
            "action": (
                f"{name} holds £{isa_value:,.0f} in ISA. Ensure the £20,000 annual Stocks & Shares ISA "
                "allowance is maximised before 5 April — gains and income inside are tax-free for life."
            ),
        })

    return {
        "annual_allowance": _UK_ISA_ALLOWANCE,
        "persons": person_flags,
        "total_isa_value": round(total_isa_value, 2),
        "junior_isa": {
            "available": len(under18) > 0,
            "allowance": 9_000,
            "beneficiaries": [p.get("preferred_name") or p["full_name"] for p in under18],
        } if under18 else None,
        "guidance": (
            "ISA: up to £20,000/year per adult completely free from CGT, income tax, and dividend tax — forever. "
            "Stocks & Shares ISA is the primary vehicle for long-term investment growth. "
            "Unused annual allowance cannot be carried forward — it is strictly use-it-or-lose-it. "
            "Innovative Finance ISAs and Lifetime ISAs (LISA, up to age 40, up to £4,000/yr) are additional options."
        ),
    }


def _uk_pension_allowance(brain: dict) -> dict:
    """Pension annual allowance, taper for high earners, and carry-forward opportunity."""
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]

    person_flags = []
    for person in adults:
        tax = (person.get("profile") or {}).get("tax") or {}
        pension_pot = tax.get("pension_pot", 0) or 0
        income = tax.get("annual_income", 0) or 0
        name = person.get("preferred_name") or person["full_name"]

        if income > _UK_PENSION_TAPER_THRESHOLD:
            taper_reduction = min(_UK_PENSION_AA - _UK_PENSION_MIN_AA, (income - _UK_PENSION_TAPER_THRESHOLD) / 2)
            effective_aa = max(_UK_PENSION_MIN_AA, _UK_PENSION_AA - taper_reduction)
        else:
            effective_aa = _UK_PENSION_AA

        tapered = effective_aa < _UK_PENSION_AA

        person_flags.append({
            "person_name": name,
            "pension_pot": pension_pot,
            "annual_income": income,
            "standard_allowance": _UK_PENSION_AA,
            "effective_allowance": round(effective_aa),
            "tapered": tapered,
            "lump_sum_allowance": _UK_PENSION_LSA,
            "action": (
                f"⚠ Tapered allowance applies: income (£{income:,.0f}) exceeds £{_UK_PENSION_TAPER_THRESHOLD:,}. "
                f"Effective allowance reduced to £{effective_aa:,.0f}. "
                "Pension contributions above this trigger an annual allowance charge — seek specialist advice."
            ) if tapered else (
                f"{name} holds £{pension_pot:,.0f} in pension. Standard allowance £{_UK_PENSION_AA:,}/year. "
                "Consider carry-forward of unused allowance from prior 3 years for larger one-off contributions."
            ),
        })

    return {
        "annual_allowance": _UK_PENSION_AA,
        "persons": person_flags,
        "guidance": (
            "Pension annual allowance: £60,000/year (2024/25). Tax relief at marginal rate — "
            "a higher-rate taxpayer effectively contributes £60k for a net £36k cost. "
            "High earners (adjusted income >£260k): tapered allowance applies, reducing to £10k minimum. "
            "Carry-forward: unused allowance from the past 3 tax years can be added to this year's allowance. "
            "Lifetime Allowance abolished April 2024; replaced by Lump Sum Allowance (£268,275 tax-free cash). "
            "Post-death: pension pots outside estate until April 2027 — review drawdown strategy now."
        ),
    }


def _uk_pet_clock(brain: dict) -> dict:
    """PET 7-year clock, taper relief schedule, and annual exemptions."""
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]
    goals = brain.get("goals", [])
    legacy_goals = [g for g in goals if g.get("kind") == "legacy"]

    taper_schedule = [
        {"years_survived": y, "taper_pct": t, "iht_pct_payable": round((1 - t / 100) * 40, 1)}
        for y, t in [(0, 0), (3, 20), (4, 40), (5, 60), (6, 80), (7, 100)]
    ]

    return {
        "survival_period_years": 7,
        "taper_schedule": taper_schedule,
        "tracked_pets": [],
        "annual_exemption": {"amount": 3_000, "per_person": True,
                             "note": "£3,000/year per donor reduces the estate immediately (not a PET)."},
        "small_gift_exemption": {"amount": 250, "per_recipient": True,
                                 "note": "£250/person/year — unlimited recipients, no PET rules apply."},
        "wedding_gifts": {"child": 5_000, "grandchild": 2_500, "other": 1_000,
                          "note": "Wedding/civil partnership gifts: exempt up to these amounts per donor."},
        "planning_note": (
            "Potentially Exempt Transfers (PETs): any gift starts a 7-year survival clock. "
            "If the donor survives 7 years, the gift is fully exempt from IHT. "
            "Taper relief applies from year 3 — IHT payable reduces progressively. "
            "Gifts to a spouse or civil partner are always 100% exempt regardless of survival. "
            + (f"This household has {len(legacy_goals)} legacy goal(s) — "
               "consider documenting gifts to start the clock formally." if legacy_goals else
               "Consider adding legacy goals to model systematic gifting strategy.")
        ),
        "persons": len(adults),
    }


def _uk_dividend_allowance(brain: dict) -> dict:
    """Dividend allowance utilisation and ISA wrapper strategy."""
    persons = brain.get("persons", [])
    adults = [p for p in persons if not p.get("is_next_gen")]
    accounts = brain.get("accounts", [])

    # Estimate dividend income from portfolio (equity holdings at assumed yield)
    total_portfolio = sum(acc.get("total_value", 0) or 0 for acc in accounts)
    estimated_dividends = total_portfolio * 0.60 * _UK_ESTIMATED_EQUITY_YIELD  # 60% equity × 2.5% yield
    per_person = estimated_dividends / max(1, len(adults))

    total_allowance = _UK_DIVIDEND_ALLOWANCE * len(adults)
    flag = per_person > _UK_DIVIDEND_ALLOWANCE
    estimated_excess = max(0, estimated_dividends - total_allowance)
    estimated_tax = estimated_excess * _UK_DIVIDEND_RATE_HIGHER  # assume higher rate

    return {
        "annual_allowance": _UK_DIVIDEND_ALLOWANCE,
        "persons": len(adults),
        "total_allowance": total_allowance,
        "estimated_annual_dividends": round(estimated_dividends, 2),
        "estimated_per_person": round(per_person, 2),
        "flag": flag,
        "estimated_excess": round(estimated_excess, 2),
        "estimated_tax": round(estimated_tax, 2),
        "action": (
            f"Estimated dividend income of ~£{per_person:,.0f}/person likely exceeds the £{_UK_DIVIDEND_ALLOWANCE:,} allowance. "
            f"Approximately £{estimated_excess:,.0f} excess dividend income may attract tax at {_UK_DIVIDEND_RATE_HIGHER*100:.2f}%. "
            "Moving dividend-paying holdings into ISA wrappers eliminates this tax entirely."
        ) if flag else (
            f"Estimated dividend income (£{per_person:,.0f}/person) appears within the £{_UK_DIVIDEND_ALLOWANCE:,} annual allowance."
        ),
        "key_changes": (
            f"Dividend allowance: £{_UK_DIVIDEND_ALLOWANCE:,} (2024/25, down from £2,000 in 2023/24 and £5,000 in 2017/18). "
            "Rates: Basic 8.75%, Higher 33.75%, Additional 39.35%. Dividends within ISA: completely tax-free."
        ),
    }


def _uk_withdrawal_seq(brain: dict) -> dict:
    """UK-optimal withdrawal sequencing: GIA → SIPP → ISA."""
    mandates = brain.get("mandates", [])
    accounts = brain.get("accounts", [])
    persons = brain.get("persons", [])

    mandate_value: dict[str, float] = {}
    for acc in accounts:
        mid = acc.get("mandate_id")
        if mid:
            mandate_value[mid] = mandate_value.get(mid, 0) + (acc.get("total_value") or 0)

    sequences = []
    for m in mandates:
        constraints = m.get("constraints") or {}
        acct_type = constraints.get("account_type", "direct")
        value = mandate_value.get(m["id"], 0)

        if acct_type == "isa":
            priority, label = 3, "3 — Draw last"
            rationale = "ISA: completely tax-free growth and income. Preserve as the most tax-efficient wrapper — no CGT, no income tax ever."
        elif acct_type in ("sipp", "pension"):
            priority, label = 2, "2 — Second"
            rationale = f"SIPP/Pension: 25% tax-free cash (up to £{_UK_PENSION_LSA:,} LSA), remainder taxed as income. Draw before ISA; plan drawdown to stay in basic-rate band."
        else:
            priority, label = 1, "1 — Draw first"
            rationale = f"GIA (taxable): draw first to utilise the £{_UK_CGT_EXEMPT:,} annual CGT exempt amount and manage taxable gains systematically."

        sequences.append({
            "mandate_name": m["name"], "mandate_type": m.get("mandate_type"),
            "account_type": acct_type, "value": round(value, 2),
            "withdrawal_priority": priority, "priority_label": label, "rationale": rationale,
        })

    for person in [p for p in persons if not p.get("is_next_gen")]:
        tax = (person.get("profile") or {}).get("tax") or {}
        name = person.get("preferred_name") or person["full_name"]
        pension = tax.get("pension_pot", 0) or 0
        isa = tax.get("isa_value", 0) or 0
        if pension > 0:
            sequences.append({
                "mandate_name": f"{name} — Pension (SIPP)", "mandate_type": "pension",
                "account_type": "sipp", "value": pension,
                "withdrawal_priority": 2, "priority_label": "2 — Second",
                "rationale": f"Pension: 25% tax-free cash up to £{_UK_PENSION_LSA:,}, remainder as taxable income. Model drawdown to stay in the 20% basic-rate band where possible.",
            })
        if isa > 0:
            sequences.append({
                "mandate_name": f"{name} — ISA", "mandate_type": "isa",
                "account_type": "isa", "value": isa,
                "withdrawal_priority": 3, "priority_label": "3 — Draw last",
                "rationale": "ISA: the ultimate tax-efficient wrapper — no CGT, no income tax on dividends or interest. Preserve until all other sources are drawn down.",
            })

    sequences.sort(key=lambda x: x["withdrawal_priority"])
    return {
        "sequences": sequences, "count": len(sequences),
        "guidance": (
            "UK optimal withdrawal order: (1) GIA — utilise £3,000 CGT exempt amount, harvest losses before April 5; "
            "(2) SIPP/Pension — take 25% tax-free cash, manage income drawdown to stay in basic-rate band; "
            "(3) ISA — preserve the tax-free wrapper as long as possible for compound growth."
        ),
    }


def _uk_tax_report(brain: dict, lots: list[dict]) -> dict:
    cgt = _uk_cgt_allowance(lots, brain)
    isa = _uk_isa_allowance(brain)
    pension = _uk_pension_allowance(brain)
    pet = _uk_pet_clock(brain)
    div = _uk_dividend_allowance(brain)
    withdrawal = _uk_withdrawal_seq(brain)

    total_flags = (
        (1 if cgt["flag"] else 0)
        + (1 if div["flag"] else 0)
        + sum(1 for p in pension["persons"] if p["tapered"])
    )

    return {
        "jurisdiction": "UK",
        "currency": "GBP",
        "cgt_allowance": cgt,
        "isa_allowance": isa,
        "pension_allowance": pension,
        "pet_clock": pet,
        "dividend_allowance": div,
        "withdrawal_sequencing": withdrawal,
        "summary": {
            "total_flags": total_flags,
            "harvestable_loss": cgt["total_harvestable_losses"],
            "estimated_tax_saving": round(cgt["total_harvestable_losses"] * _UK_CGT_RATE_HIGHER, 2),
            "cgt_flag": cgt["flag"],
            "dividend_flag": div["flag"],
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════════

async def for_household(
    session: AsyncSession,
    household_id: uuid.UUID,
    firm_jurisdiction: str = "NZ",
) -> dict | None:
    brain = await household_brain(session, household_id)
    if not brain:
        return None

    jurisdiction = brain["household"]["values"].get("jurisdiction") or firm_jurisdiction
    lots = await _load_lots(session, brain)

    if jurisdiction == "US":
        result = _us_tax_report(brain, lots)
    elif jurisdiction == "UK":
        result = _uk_tax_report(brain, lots)
    else:
        result = _nz_tax_report(brain, lots)

    result.update({
        "household_id": str(household_id),
        "household_name": brain["household"]["name"],
        "total_portfolio_value": brain["totals"]["total_value"],
        "generated_at": date.today().isoformat(),
    })
    return result


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, firm_jurisdiction: str = "NZ") -> dict:
    households = await list_households(session, firm_id)

    # Per-jurisdiction aggregates
    nz_harvest: list[dict] = []
    nz_pie: list[dict] = []
    nz_ks: list[dict] = []
    nz_bright: list[dict] = []
    us_results: list[dict] = []
    uk_results: list[dict] = []

    for h in households:
        hh_id = uuid.UUID(h["id"])
        result = await for_household(session, hh_id, firm_jurisdiction)
        if not result:
            continue

        jur = result.get("jurisdiction", "NZ")
        hh_ctx = {"household_id": h["id"], "household_name": h["name"]}

        if jur == "NZ":
            for opp in result["loss_harvest"]["opportunities"]:
                nz_harvest.append({**opp, **hh_ctx})
            for flag in result["pie_optimisation"]["flags"]:
                nz_pie.append({**flag, **hh_ctx})
            for rec in result["kiwisaver"]["recommendations"]:
                nz_ks.append({**rec, **hh_ctx})
            for flag in result["bright_line"]["flags"]:
                nz_bright.append({**flag, **hh_ctx})
        elif jur == "US":
            us_results.append({**result, **hh_ctx})
        elif jur == "UK":
            uk_results.append({**result, **hh_ctx})

    nz_harvest.sort(key=lambda x: x["unrealised_loss"])
    nz_bright.sort(key=lambda x: x["days_until_safe"])

    return {
        "total_households_scanned": len(households),
        "jurisdictions": {
            "NZ": len([h for h in households]) - len(us_results) - len(uk_results),
            "US": len(us_results),
            "UK": len(uk_results),
        },
        # NZ aggregates (backward compat)
        "loss_harvest": {
            "opportunities": nz_harvest, "count": len(nz_harvest),
            "total_harvestable_loss": round(sum(o["unrealised_loss"] for o in nz_harvest), 2),
            "estimated_tax_saving": round(sum(abs(o["unrealised_loss"]) * 0.28 for o in nz_harvest), 2),
        },
        "pie_optimisation": {
            "flags": nz_pie, "count": len(nz_pie),
            "total_annual_saving": round(sum(f["estimated_annual_saving"] for f in nz_pie), 2),
        },
        "kiwisaver": {"recommendations": nz_ks, "count": len(nz_ks)},
        "bright_line": {"flags": nz_bright, "count": len(nz_bright)},
        # US / UK per-household summaries
        "us_households": [
            {
                "household_id": r["household_id"], "household_name": r["household_name"],
                "total_portfolio_value": r["total_portfolio_value"],
                "summary": r["summary"], "currency": "USD",
            }
            for r in us_results
        ],
        "uk_households": [
            {
                "household_id": r["household_id"], "household_name": r["household_name"],
                "total_portfolio_value": r["total_portfolio_value"],
                "summary": r["summary"], "currency": "GBP",
            }
            for r in uk_results
        ],
        "generated_at": date.today().isoformat(),
    }
