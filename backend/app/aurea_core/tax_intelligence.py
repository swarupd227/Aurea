"""Tax Intelligence at Book Level — cross-book NZ tax optimisation engine.

Five modules:
  1. Loss-harvest scanner — unrealised losses across ALL mandates simultaneously
  2. PIE tax regime optimisation — fund-type selection based on investor PIR vs marginal rate
  3. KiwiSaver contribution optimisation — government top-up + employer match vs tax cost
  4. Bright-line property test tracker — flag clients approaching 2-yr or 10-yr thresholds
  5. Tax-efficient withdrawal sequencing — which accounts to draw first in decumulation
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain, list_households
from app.models.portfolio import Holding, Instrument, TaxLot

# ── NZ-specific constants ─────────────────────────────────────────────────────
_GOVT_TOPUP_MAX = 521.43          # NZ KiwiSaver government contribution cap
_GOVT_TOPUP_RATE = 0.50           # 50c per $1 own contribution
_GOVT_TOPUP_OWN_MIN = 1042.86     # own contribution required to earn full top-up
_BRIGHT_LINE_SHORT = 2            # years — family home / first property
_BRIGHT_LINE_LONG = 10            # years — 2nd+ property, investment property
_HARVEST_THRESHOLD = -5_000       # minimum loss to surface as an opportunity
_WASH_SALE_WINDOW = 30            # days — warn if same instrument bought recently
_KIWISAVER_RATES = [0.03, 0.04, 0.06, 0.08, 0.10]
_ASSUMED_EMPLOYER_MATCH = 0.03    # standard NZ employer minimum
_ASSUMED_GROSS_RETURN = 0.07      # annual return used for PIE saving estimate


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _load_lots(session: AsyncSession, brain: dict) -> list[dict]:
    """Return TaxLot rows enriched with current price, account and mandate context.

    Current price is derived from Holding.market_value / Holding.quantity — the same
    pre-cached value used by analytics/portfolio.py, set by revalue_firm(). This is
    authoritative and avoids stale Price-table reads.
    """
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

    # Derive current price per unit from holding's cached market_value (consistent with analytics).
    # market_value was set by revalue_firm() as quantity × latest_price.
    holding_unit_price: dict[uuid.UUID, float] = {}
    for h in holdings:
        qty = float(h.quantity or 0)
        mv = float(h.market_value or 0)
        if qty > 0:
            holding_unit_price[h.id] = mv / qty

    # Instruments recently purchased → wash-sale risk indicator
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


# ── Module 1: Loss-harvest scanner ───────────────────────────────────────────

def _scan_loss_harvest(lots: list[dict]) -> dict:
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


# ── Module 2: PIE tax regime optimisation ────────────────────────────────────

def _pie_optimisation(brain: dict) -> dict:
    persons = brain.get("persons", [])
    mandates = brain.get("mandates", [])
    accounts = brain.get("accounts", [])

    # Build a value map per mandate from accounts
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

            # Only flag if the mandate is currently direct (not already PIE)
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


# ── Module 3: KiwiSaver contribution optimisation ────────────────────────────

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
        current_net = None
        for rate in _KIWISAVER_RATES:
            own = annual_income * rate
            govt = min(_GOVT_TOPUP_MAX, own * _GOVT_TOPUP_RATE)
            emp = annual_income * _ASSUMED_EMPLOYER_MATCH  # capped by employer minimum
            # Tax cost of contribution (pre-tax salary sacrifice, so tax saving = marginal × contribution)
            # KiwiSaver in NZ is from after-tax pay → marginal tax has already been paid
            # Net benefit per dollar extra: employer match + govt top-up at current rate
            incremental_cost = annual_income * (rate - current_rate) * (1 - marginal) if rate > current_rate else 0
            net = govt + emp
            row = {
                "rate_pct": int(rate * 100),
                "own_contribution": round(own, 2),
                "employer_match": round(emp, 2),
                "govt_topup": round(govt, 2),
                "net_annual_benefit": round(net, 2),
            }
            rate_analysis.append(row)
            if rate == current_rate:
                current_net = net

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


# ── Module 4: Bright-line property test tracker ───────────────────────────────

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

            # First listed property uses 2-year test, subsequent use 10-year
            test_years = _BRIGHT_LINE_SHORT if idx == 0 else _BRIGHT_LINE_LONG
            test_days = test_years * 365
            days_held = (today - acquired).days
            days_remaining = test_days - days_held

            if days_remaining > 365:
                continue  # comfortably outside the warning window

            if days_remaining <= 0:
                status = "within_bright_line"
                action = (
                    f"Property has been held {days_held} days. "
                    f"Still within the {test_years}-year bright-line period — "
                    f"any sale is a taxable event. Do not sell without specialist tax advice."
                )
            else:
                months = round(days_remaining / 30.4, 1)
                status = "approaching"
                action = (
                    f"Approaching {test_years}-year bright-line in ~{months:.1f} months "
                    f"({acquired_str} → safe after "
                    f"{date.fromordinal(acquired.toordinal() + test_days).isoformat()}). "
                    f"If a sale is planned, consider waiting."
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


# ── Module 5: Tax-efficient withdrawal sequencing ────────────────────────────

def _withdrawal_sequencing(brain: dict) -> dict:
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
            priority = 4
            label = "4 — Draw last"
            rationale = "KiwiSaver benefits from PIR taxation capped at 28% and lock-in until 65. Preserve as long as possible."
        elif acct_type == "pie":
            priority = 3
            label = "3 — Third"
            rationale = "PIE funds benefit from capped PIR taxation. Preserve after drawing direct holdings."
        elif m.get("mandate_type") in ("advisory", "execution_only"):
            priority = 1
            label = "1 — Draw first"
            rationale = "Direct taxable holdings — draw first to realise losses, then rebuy in PIE wrapper."
        else:
            priority = 2
            label = "2 — Second"
            rationale = "Discretionary mandates — draw after direct advisory holdings, before PIE/KiwiSaver."

        sequences.append({
            "mandate_name": m["name"],
            "mandate_type": m.get("mandate_type"),
            "account_type": acct_type,
            "value": round(value, 2),
            "withdrawal_priority": priority,
            "priority_label": label,
            "rationale": rationale,
        })

    sequences.sort(key=lambda x: x["withdrawal_priority"])
    return {
        "sequences": sequences,
        "count": len(sequences),
        "guidance": (
            "Draw down accounts in priority order shown to minimise lifetime tax. "
            "Realise losses in direct accounts first, then rebuy in PIE to lock in future gains at PIR."
        ),
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def for_household(session: AsyncSession, household_id: uuid.UUID) -> dict | None:
    brain = await household_brain(session, household_id)
    if not brain:
        return None

    lots = await _load_lots(session, brain)

    loss_harvest = _scan_loss_harvest(lots)
    pie_opt = _pie_optimisation(brain)
    ks_opt = _kiwisaver_optimisation(brain)
    bright = _bright_line_check(brain)
    withdrawal = _withdrawal_sequencing(brain)

    return {
        "household_id": str(household_id),
        "household_name": brain["household"]["name"],
        "total_portfolio_value": brain["totals"]["total_value"],
        "loss_harvest": loss_harvest,
        "pie_optimisation": pie_opt,
        "kiwisaver": ks_opt,
        "bright_line": bright,
        "withdrawal_sequencing": withdrawal,
        "summary": {
            "total_flags": (
                loss_harvest["count"]
                + pie_opt["count"]
                + ks_opt["count"]
                + bright["count"]
            ),
            "harvestable_loss": loss_harvest["total_harvestable_loss"],
            "estimated_tax_saving": round(
                loss_harvest["estimated_tax_saving_at_top_pir"]
                + pie_opt["total_annual_saving"],
                2,
            ),
            "bright_line_flags": bright["count"],
        },
        "generated_at": date.today().isoformat(),
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    households = await list_households(session, firm_id)

    all_harvest: list[dict] = []
    all_pie: list[dict] = []
    all_ks: list[dict] = []
    all_bright: list[dict] = []

    for h in households:
        hh_id = uuid.UUID(h["id"])
        result = await for_household(session, hh_id)
        if not result:
            continue

        hh_ctx = {"household_id": h["id"], "household_name": h["name"]}
        for opp in result["loss_harvest"]["opportunities"]:
            all_harvest.append({**opp, **hh_ctx})
        for flag in result["pie_optimisation"]["flags"]:
            all_pie.append({**flag, **hh_ctx})
        for rec in result["kiwisaver"]["recommendations"]:
            all_ks.append({**rec, **hh_ctx})
        for flag in result["bright_line"]["flags"]:
            all_bright.append({**flag, **hh_ctx})

    all_harvest.sort(key=lambda x: x["unrealised_loss"])
    all_bright.sort(key=lambda x: x["days_until_safe"])

    return {
        "total_households_scanned": len(households),
        "loss_harvest": {
            "opportunities": all_harvest,
            "count": len(all_harvest),
            "total_harvestable_loss": round(sum(o["unrealised_loss"] for o in all_harvest), 2),
            "estimated_tax_saving": round(sum(abs(o["unrealised_loss"]) * 0.28 for o in all_harvest), 2),
        },
        "pie_optimisation": {
            "flags": all_pie,
            "count": len(all_pie),
            "total_annual_saving": round(sum(f["estimated_annual_saving"] for f in all_pie), 2),
        },
        "kiwisaver": {
            "recommendations": all_ks,
            "count": len(all_ks),
        },
        "bright_line": {
            "flags": all_bright,
            "count": len(all_bright),
        },
        "generated_at": date.today().isoformat(),
    }
