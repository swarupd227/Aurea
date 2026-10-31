"""Investment policy monitoring: a mandate's book read against the IPS its client accepted (L100 §5.5).

The IPS is the client's own terms, so it outranks the house model: where one is in force the rebalancer
works to its targets and band, and the model only names the instruments to buy. Where none is on file the
mandate is measured against its model as before, and says so. No IPS on file does not mean no IPS: the
firm may hold a signed one that nobody has recorded here.

Only a person records one, from the document, because the limits in it are the client's and cannot be
worked out. The one exception is onboarding, where a person has already accepted the proposed model and
constraints: those become the first version. Terms are added and never edited.

Reading the book against the terms reports and changes nothing. A breach is put to a person by the IPS
monitor (`agents/ips_monitoring.py`), who fixes it or accepts it. An accepted breach is an exception: it
lasts a limited time, holds only as far as the breach stood, and falls away with a new version of the IPS.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.aurea_core.risk_profile import CAPACITY_EQUITY_MAX, on_file
from app.aurea_core.valuation import latest_prices
from app.models.enums import AssetClass
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding, Instrument, InvestmentPolicy, IpsException, ModelPortfolio, TargetAllocation
from app.models.tenant import Firm
from app.provenance import ledger

CLASSES = tuple(c.value for c in AssetClass)
MAX_BAND = 0.25
EXCEPTION_DAYS = 90
# How much further a breach may go before an exception stops covering it.
WORSE_BY = 0.01
SHOWN_VERSIONS = 5
ORDER = ("breach", "excepted", "within", "not_assessed", "no_ips")


class IpsError(ValueError):
    """Terms that cannot be recorded, with the reason."""


def exclusions_of(mandate: Mandate) -> list[str]:
    """What the mandate excludes. Onboarding writes these to its constraints and every other path to
    its suitability, so both are read."""
    both = [*((mandate.suitability or {}).get("values_exclusions") or []),
            *((mandate.constraints or {}).get("values_exclusions") or [])]
    return list(dict.fromkeys(str(x).strip() for x in both if str(x).strip()))


def restricted_by(inst: Instrument | None, restricted) -> bool:
    """Whether a list of symbols and values flags rules this instrument out."""
    if inst is None:
        return False
    flags = (inst.values_tags or {}).get("flags") or []
    return inst.symbol in restricted or any(flag in restricted for flag in flags)


async def in_force(session: AsyncSession, mandate_id: uuid.UUID) -> InvestmentPolicy | None:
    return (await session.execute(
        select(InvestmentPolicy).where(InvestmentPolicy.mandate_id == mandate_id)
        .order_by(InvestmentPolicy.version.desc()).limit(1)
    )).scalars().first()


def stamp(policy: InvestmentPolicy | None) -> dict | None:
    """What a proposal carries, so approving it can tell whether the IPS has changed since."""
    return {"id": str(policy.id), "version": policy.version, "accepted_on": policy.accepted_on.isoformat()} if policy else None


async def moved_on(session: AsyncSession, mandate: Mandate, stamped: dict | None) -> InvestmentPolicy | None:
    """The IPS now in force, if it is not the one a proposal was measured against."""
    now = await in_force(session, mandate.id)
    return now if (str(now.id) if now else None) != (stamped or {}).get("id") else None


async def model_terms(session: AsyncSession, model_id: uuid.UUID | None) -> tuple[dict[str, float], float | None, str | None]:
    """A model's targets by class as the rebalancer sums them, its band and its name."""
    model = await session.get(ModelPortfolio, model_id) if model_id else None
    if model is None:
        return {}, None, None
    targets: dict[str, float] = {}
    for row in (await session.execute(select(TargetAllocation).where(TargetAllocation.model_id == model.id))).scalars().all():
        targets[str(row.asset_class)] = targets.get(str(row.asset_class), 0.0) + float(row.target_weight)
    return targets, float(model.drift_band), model.name


def _terms_view(p: InvestmentPolicy) -> dict:
    return {
        "id": str(p.id), "version": p.version, "accepted_on": p.accepted_on.isoformat(),
        "targets": {c: float(w) for c, w in (p.targets or {}).items()}, "drift_band": float(p.drift_band),
        "max_position": float(p.max_position) if p.max_position is not None else None,
        "min_cash": float(p.min_cash) if p.min_cash is not None else None,
        "restricted": list(p.restricted or []), "profile": dict(p.profile or {}), "document": p.document,
        "reason": p.reason, "source": p.source, "recorded_by": p.recorded_by, "recorded_by_role": p.recorded_by_role,
    }


def findings_of(terms: dict, *, weights: dict[str, float], positions: list[dict], profile: dict) -> list[dict]:
    """Every term the book is outside, each with how far. `positions` are one row per instrument:
    {symbol, weight, restricted}. Cash sits outside the band, as it does in the rebalancer, and answers
    only to its floor. Pure, and the same test the rebalancer uses: a band is breached only past its edge."""
    band, found = terms["drift_band"], []
    for cls in sorted((set(terms["targets"]) | set(weights)) - {"cash"}):
        target, weight = terms["targets"].get(cls, 0.0), weights.get(cls, 0.0)
        if abs(weight - target) > band:
            found.append({"term": f"allocation:{cls}", "kind": "allocation", "asset_class": cls, "weight": round(weight, 4),
                          "target": round(target, 4), "low": round(max(target - band, 0.0), 4), "high": round(target + band, 4),
                          "excess": round(abs(weight - target) - band, 5)})
    limit = terms.get("max_position")
    for p in positions:
        if limit is not None and p["weight"] > limit:
            found.append({"term": f"position:{p['symbol']}", "kind": "position", "symbol": p["symbol"],
                          "weight": round(p["weight"], 4), "limit": limit, "excess": round(p["weight"] - limit, 5)})
        if p["restricted"]:
            found.append({"term": f"restricted:{p['symbol']}", "kind": "restricted", "symbol": p["symbol"],
                          "weight": round(p["weight"], 4), "excess": round(p["weight"], 5)})
    floor, cash = terms.get("min_cash"), weights.get("cash", 0.0)
    if floor is not None and cash < floor:
        found.append({"term": "cash", "kind": "cash", "weight": round(cash, 4), "limit": floor, "excess": round(floor - cash, 5)})
    accepted = terms.get("profile") or {}
    if any(accepted.get(k) != profile.get(k) for k in ("risk_profile", "capacity_for_loss")):
        found.append({"term": "profile", "kind": "profile", "was": accepted, "now": profile, "excess": 0.0})
    return found


def _split(found: list[dict], exceptions: dict[str, IpsException]) -> tuple[list[dict], list[dict]]:
    """Findings still open, and those an exception covers. One that has grown past what was accepted is open."""
    open_, covered = [], []
    for f in found:
        row = exceptions.get(f["term"])
        if row is not None and f["excess"] <= float(row.excess) + WORSE_BY:
            covered.append({**f, "until": row.until.isoformat(), "reason": row.reason, "accepted_by": row.accepted_by})
        else:
            open_.append(f)
    return open_, covered


async def _exceptions(session: AsyncSession, policy: InvestmentPolicy | None, today: date) -> dict[str, IpsException]:
    """The exceptions running against this version of the IPS, latest last."""
    if policy is None:
        return {}
    rows = (await session.execute(
        select(IpsException).where(IpsException.policy_id == policy.id, IpsException.withdrawn_by.is_(None),
                                   IpsException.until >= today).order_by(IpsException.created_at)
    )).scalars().all()
    return {r.term: r for r in rows}


async def _book(session: AsyncSession, mandate: Mandate, restricted: list[str], prices: dict) -> dict:
    """The mandate's holdings and cash as weights, on the values the rebalancer reads."""
    accounts = (await session.execute(select(Account).where(Account.mandate_id == mandate.id))).scalars().all()
    by_class: dict[str, float] = {}
    by_instrument: dict[uuid.UUID, float] = {}
    total = 0.0
    for acc in accounts:
        cash = float(acc.cash_balance or 0)
        total += cash
        by_class["cash"] = by_class.get("cash", 0.0) + cash
        for h in (await session.execute(select(Holding).where(Holding.account_id == acc.id))).scalars().all():
            by_instrument[h.instrument_id] = by_instrument.get(h.instrument_id, 0.0) + float(h.market_value or 0)
    instruments = {i.id: i for i in (await session.execute(
        select(Instrument).where(Instrument.id.in_(list(by_instrument))))).scalars().all()} if by_instrument else {}
    positions, synthetic = [], False
    for instrument_id, value in by_instrument.items():
        inst = instruments.get(instrument_id)
        cls = str(inst.asset_class) if inst else "multi_asset"
        by_class[cls] = by_class.get(cls, 0.0) + value
        total += value
        price = prices.get(instrument_id)
        synthetic = synthetic or (price is not None and not price.is_real)
        positions.append({"symbol": inst.symbol if inst else "?", "value": value, "restricted": restricted_by(inst, restricted)})
    if total <= 0:
        return {"total": 0.0, "weights": {}, "positions": [], "synthetic": False}
    return {"total": total, "weights": {c: v / total for c, v in by_class.items()}, "synthetic": synthetic,
            "positions": [{**p, "weight": p["value"] / total} for p in positions]}


async def _view(session: AsyncSession, mandate: Mandate, household: Household | None, prices: dict, today: date) -> dict:
    policy = await in_force(session, mandate.id)
    targets, band, model_name = await model_terms(session, mandate.model_portfolio_id)
    terms = _terms_view(policy) if policy else None
    book = await _book(session, mandate, terms["restricted"] if terms else [], prices)
    row = {
        "mandate_id": str(mandate.id), "mandate": mandate.name, "model": model_name,
        "household_id": str(household.id) if household else None, "household": household.name if household else None,
        "measured_against": "ips" if terms else "model" if targets else None,
        "ips": terms, "weights": {c: round(w, 4) for c, w in book["weights"].items()},
        "targets": terms["targets"] if terms else targets, "drift_band": terms["drift_band"] if terms else band,
        "price_source": "synthetic" if book["synthetic"] else "market", "findings": [], "excepted": [],
        "model_drift": None,
    }
    if terms is None:
        row["status"] = "no_ips"
        if targets and book["total"] > 0:
            drift = max((abs(book["weights"].get(c, 0.0) - targets.get(c, 0.0))
                         for c in (set(targets) | set(book["weights"])) - {"cash"}), default=0.0)
            row["model_drift"] = {"max_drift": round(drift, 4), "band": band, "outside": drift > band}
        return row
    if book["total"] <= 0:
        row["status"] = "not_assessed"
        return row
    profile, capacity = on_file(mandate.suitability)
    found = findings_of(terms, weights=book["weights"], positions=book["positions"],
                        profile={"risk_profile": profile, "capacity_for_loss": capacity})
    row["findings"], row["excepted"] = _split(found, await _exceptions(session, policy, today))
    row["status"] = "breach" if row["findings"] else "excepted" if row["excepted"] else "within"
    return row


async def _owners(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, Household]:
    households = {h.id: h for h in (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    owners: dict[uuid.UUID, Household] = {}
    for model in (Person, LegalEntity):
        for owner, household_id in (await session.execute(
                select(model.id, model.household_id).where(model.household_id.in_(list(households))))).all():
            if household_id in households:
                owners[owner] = households[household_id]
    return owners


async def _rows(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID | None = None) -> list[dict]:
    firm = await session.get(Firm, firm_id)
    owners = await _owners(session, firm_id)
    today, prices = firm_time.today(firm), await latest_prices(session, firm_id)
    rows = []
    for m in (await session.execute(
        select(Mandate).where(Mandate.firm_id == firm_id, Mandate.is_active.is_(True)).order_by(Mandate.name)
    )).scalars().all():
        household = owners.get(m.person_id) or owners.get(m.entity_id)
        if household_id is None or (household is not None and household.id == household_id):
            rows.append(await _view(session, m, household, prices, today))
    rows.sort(key=lambda r: (ORDER.index(r["status"]), r["mandate"]))
    return rows


def _summary(rows: list[dict]) -> dict:
    def count(status: str) -> int:
        return sum(1 for r in rows if r["status"] == status)

    return {
        "mandates": len(rows), "breach": count("breach"), "excepted": count("excepted"), "within": count("within"),
        "not_assessed": count("not_assessed"), "no_ips": count("no_ips"),
        "outside_model_band": sum(1 for r in rows if (r["model_drift"] or {}).get("outside")),
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every active mandate, those in breach first."""
    firm = await session.get(Firm, firm_id)
    rows = await _rows(session, firm_id)
    return {"mandates": rows, "summary": _summary(rows), "classes": list(CLASSES), "exception_days": EXCEPTION_DAYS,
            "as_of": firm_time.today(firm).isoformat()}


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    rows = await _rows(session, firm_id, household_id)
    return {"household_id": str(household.id), "household": household.name, "mandates": rows, "summary": _summary(rows)}


async def for_mandate(session: AsyncSession, mandate_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    mandate = await session.get(Mandate, mandate_id)
    if mandate is None or mandate.firm_id != firm_id:
        return None
    owners = await _owners(session, firm_id)
    firm = await session.get(Firm, firm_id)
    view = await _view(session, mandate, owners.get(mandate.person_id) or owners.get(mandate.entity_id),
                       await latest_prices(session, firm_id), firm_time.today(firm))
    versions = (await session.execute(
        select(InvestmentPolicy).where(InvestmentPolicy.mandate_id == mandate_id)
        .order_by(InvestmentPolicy.version.desc()).limit(SHOWN_VERSIONS))).scalars().all()
    return {**view, "history": [_terms_view(v) for v in versions]}


def _share(value, name: str) -> float | None:
    """A percentage held as a fraction, or None when left out."""
    if value is None or value == "":
        return None
    try:
        share = float(value)
    except (TypeError, ValueError):
        raise IpsError(f"{name} must be a number.")
    if not 0 <= share <= 1:
        raise IpsError(f"{name} must be between 0% and 100%.")
    return share


def _targets(given: dict) -> dict[str, float]:
    targets = {}
    for cls, weight in given.items():
        name = str(cls).strip().lower()
        if name not in CLASSES:
            raise IpsError(f"Unknown asset class: {cls}. Choose from {', '.join(CLASSES)}.")
        share = _share(weight, name) or 0.0
        if share > 0:
            targets[name] = round(targets.get(name, 0.0) + share, 6)
    if abs(sum(targets.values()) - 1.0) > 0.001:
        raise IpsError(f"Targets must add up to 100%, and these add up to {sum(targets.values()):.1%}.")
    return targets


async def prepare_record(session: AsyncSession, mandate_id: uuid.UUID, *, firm_id: uuid.UUID, accepted_on: date | None,
                         document: str | None, targets: dict | None = None, drift_band=None, max_position=None,
                         min_cash=None, restricted: list | None = None, reason: str | None = None) -> dict:
    """Everything recording would refuse, refused without writing, so a Confirm card that would only
    fail is not shown. Left out, the targets and band are the model's and the restrictions the mandate's
    exclusions; the limits have no default, because only the document can say what they are."""
    mandate = await session.get(Mandate, mandate_id)
    if mandate is None or mandate.firm_id != firm_id or not mandate.is_active:
        raise IpsError("Mandate not found.")
    firm = await session.get(Firm, firm_id)
    if accepted_on is None:
        raise IpsError("Give the date the client accepted this IPS.")
    if accepted_on > firm_time.today(firm):
        raise IpsError("An IPS cannot be recorded before it has been accepted.")
    named = (document or "").strip()
    if len(named) < 3:
        raise IpsError("Say which document these terms come from.")

    was = await in_force(session, mandate_id)
    why = (reason or "").strip() or None
    if was and accepted_on < was.accepted_on:
        raise IpsError(f"Version {was.version} was accepted on {was.accepted_on.isoformat()}, so a later version cannot predate it.")
    if was and (why is None or len(why) < 3):
        raise IpsError(f"This replaces version {was.version}. Give the reason.")

    model_targets, model_band, _ = await model_terms(session, mandate.model_portfolio_id)
    if targets is None and not model_targets:
        raise IpsError("This mandate has no model to take targets from. Give the targets.")
    weights = _targets(targets if targets is not None else model_targets)
    band = _share(drift_band, "The drift band")
    band = band if band is not None else model_band
    if band is None or not 0 < band <= MAX_BAND:
        raise IpsError(f"The drift band must be above 0% and at most {MAX_BAND:.0%}.")
    limit, floor = _share(max_position, "The position limit"), _share(min_cash, "The cash floor")
    if limit == 0:
        raise IpsError("A position limit of 0% would rule out every holding.")
    barred = list(dict.fromkeys(str(x).strip() for x in restricted if str(x).strip())) if restricted is not None else exclusions_of(mandate)

    # An equity target over the client's capacity breaches on the next rebalance, and a breach pauses
    # the rebalancer for the whole firm.
    profile, capacity = on_file(mandate.suitability)
    ceiling = CAPACITY_EQUITY_MAX.get(str((mandate.suitability or {}).get("capacity_for_loss") or "").strip().lower())
    if ceiling is not None and weights.get("equity", 0.0) > ceiling:
        raise IpsError(f"An equity target of {weights['equity']:.0%} is above the {ceiling:.0%} ceiling for {capacity} capacity.")

    ready = {"mandate": mandate, "accepted_on": accepted_on, "document": named, "targets": weights, "drift_band": band,
             "max_position": limit, "min_cash": floor, "restricted": barred, "reason": why, "was": was,
             "version": (was.version if was else 0) + 1, "profile": {"risk_profile": profile, "capacity_for_loss": capacity}}
    if was and all((_terms_view(was)[k]) == ready[k] for k in ("targets", "drift_band", "max_position", "min_cash", "restricted")):
        raise IpsError(f"These are the terms of version {was.version}.")
    return ready


def _row(ready: dict, *, firm_id: uuid.UUID, by: str, role: str, source: str) -> InvestmentPolicy:
    return InvestmentPolicy(
        firm_id=firm_id, mandate_id=ready["mandate"].id, version=ready["version"], accepted_on=ready["accepted_on"],
        targets=ready["targets"], drift_band=ready["drift_band"], max_position=ready["max_position"],
        min_cash=ready["min_cash"], restricted=ready["restricted"], profile=ready["profile"], document=ready["document"],
        reason=ready["reason"], source=source, recorded_by=by, recorded_by_role=role)


async def record(session: AsyncSession, mandate_id: uuid.UUID, *, firm_id: uuid.UUID, accepted_on: date | None,
                 document: str | None, by: str, role: str, targets: dict | None = None, drift_band=None,
                 max_position=None, min_cash=None, restricted: list | None = None, reason: str | None = None) -> dict:
    ready = await prepare_record(session, mandate_id, firm_id=firm_id, accepted_on=accepted_on, document=document,
                                 targets=targets, drift_band=drift_band, max_position=max_position, min_cash=min_cash,
                                 restricted=restricted, reason=reason)
    policy = _row(ready, firm_id=firm_id, by=by, role=str(role), source="recorded")
    session.add(policy)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="ips_recorded", actor=by, content={
        "policy_id": str(policy.id), "mandate_id": str(mandate_id), "version": policy.version,
        "accepted_on": policy.accepted_on.isoformat(), "targets": policy.targets, "drift_band": ready["drift_band"],
        "max_position": ready["max_position"], "min_cash": ready["min_cash"], "restricted": ready["restricted"],
        "document": ready["document"], "reason": ready["reason"],
    })
    return {**await for_mandate(session, mandate_id, firm_id=firm_id), "version": policy.version}


async def carry_from_onboarding(session: AsyncSession, mandate: Mandate, *, accepted_on: date, by: str | None,
                                document: str) -> InvestmentPolicy | None:
    """The first version of a mandate whose client accepted its IPS at onboarding: what was accepted is
    the proposed model and constraints. None with no model, or if one is already on file."""
    if await in_force(session, mandate.id) is not None:
        return None
    targets, band, _ = await model_terms(session, mandate.model_portfolio_id)
    if not targets:
        return None
    profile, capacity = on_file(mandate.suitability)
    policy = InvestmentPolicy(
        firm_id=mandate.firm_id, mandate_id=mandate.id, version=1, accepted_on=accepted_on, targets=targets,
        drift_band=band, restricted=exclusions_of(mandate), profile={"risk_profile": profile, "capacity_for_loss": capacity},
        document=document, source="onboarding", recorded_by=by or "unknown", recorded_by_role="onboarding")
    session.add(policy)
    await session.flush()
    return policy


async def accept(session: AsyncSession, mandate: Mandate, *, firm: Firm, terms: list[str], reason: str, by: str,
                 recommendation_id: uuid.UUID | None) -> dict:
    """Accept breaches that still stand, as they stand now and for a limited time. What has moved on
    since the proposal was drafted is left out and reported, so nothing is accepted that nobody saw."""
    policy = await in_force(session, mandate.id)
    view = await for_mandate(session, mandate.id, firm_id=firm.id)
    standing = {f["term"]: f for f in view["findings"]}
    until = firm_time.today(firm) + timedelta(days=EXCEPTION_DAYS)
    done, gone = [], []
    for term in terms:
        found = standing.get(term)
        if found is None or policy is None:
            gone.append(term)
            continue
        session.add(IpsException(firm_id=firm.id, mandate_id=mandate.id, policy_id=policy.id, term=term,
                                 excess=found["excess"], until=until, reason=reason, accepted_by=by,
                                 recommendation_id=recommendation_id))
        done.append(term)
    await session.flush()
    if done:
        await ledger.append_entry(session, firm_id=firm.id, event_type="ips_exception_accepted", actor=by, content={
            "mandate_id": str(mandate.id), "policy_id": str(policy.id), "version": policy.version, "terms": done,
            "until": until.isoformat(), "reason": reason, "recommendation_id": str(recommendation_id) if recommendation_id else None,
        })
    return {"accepted": done, "moved_on": gone, "until": until.isoformat()}


async def withdraw(session: AsyncSession, recommendation_id: uuid.UUID, *, firm_id: uuid.UUID, by: str) -> int:
    """Withdraw the exceptions a proposal accepted. They stay on record as withdrawn."""
    rows = (await session.execute(
        select(IpsException).where(IpsException.firm_id == firm_id, IpsException.recommendation_id == recommendation_id,
                                   IpsException.withdrawn_by.is_(None)))).scalars().all()
    for row in rows:
        row.withdrawn_by = by
    await session.flush()
    if rows:
        await ledger.append_entry(session, firm_id=firm_id, event_type="ips_exception_withdrawn", actor=by, content={
            "mandate_id": str(rows[0].mandate_id), "terms": [r.term for r in rows],
            "recommendation_id": str(recommendation_id),
        })
    return len(rows)
