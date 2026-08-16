"""Onboarding operating metrics (L200 §4).

L200 lists what a practice actually manages onboarding by: cycle time at P50/P90 by
registration type, NIGO rate by root cause, first-pass yield, screening metrics, transfer
metrics, and SLA attainment. None of it existed.

Everything here is derived from timestamps the evidence already carries — cases have
created_at, disclosures delivered_at, parties screened_at, transfers initiated_at and
settled_at, custodian pushes custodian_push_at, and activation now stamps activated_at. No
separate metrics store, so the numbers cannot disagree with the records they describe.

Percentiles use nearest-rank on small samples: a practice onboarding a few dozen
households a quarter does not have the volume for interpolation to mean anything, and
nearest-rank always returns a value that actually occurred.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _days(a: datetime | None, b: datetime | None) -> float | None:
    """Days from a to b, or None if either end is missing."""
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 86400, 2)


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. Honest on small samples in a way interpolation is not."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(1, int(round(pct / 100 * len(ordered))))
    return round(ordered[min(k, len(ordered)) - 1], 2)


def _summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "mean": round(sum(values) / len(values), 2) if values else None,
    }


def compute(cases: list, *, parties_by_case: dict, disclosures_by_case: dict,
            transfers_by_case: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)

    open_cases = [c for c in cases if c.status not in ("approved", "rejected")]
    activated = [c for c in cases if c.status == "approved"]

    # ── Cycle time ───────────────────────────────────────────────────────────
    # Intake to activation, overall and split by registration type, since L200 is explicit
    # that a trust and an individual are not comparable.
    to_activation: list[float] = []
    by_reg: dict[str, list[float]] = {}
    for c in activated:
        d = _days(c.created_at, c.activated_at)
        if d is None:
            continue
        to_activation.append(d)
        by_reg.setdefault(c.registration_type or "unknown", []).append(d)

    # Activation to funds settled — the second half of the client's wait.
    to_funded: list[float] = []
    for c in cases:
        settled = [t.settled_at for t in transfers_by_case.get(c.id, []) if t.settled_at]
        if settled and c.activated_at:
            d = _days(c.activated_at, min(settled))
            if d is not None:
                to_funded.append(d)

    # ── First-pass yield ─────────────────────────────────────────────────────
    # Activated without ever being flagged not-in-good-order.
    clean = [c for c in activated if not c.nigo_flag]
    fpy = round(len(clean) / len(activated) * 100, 1) if activated else None

    # ── NIGO by root cause ───────────────────────────────────────────────────
    nigo_cases = [c for c in cases if c.nigo_flag]
    by_cause: dict[str, int] = {}
    for c in nigo_cases:
        by_cause[c.nigo_root_cause or "unclassified"] = by_cause.get(c.nigo_root_cause or "unclassified", 0) + 1
    scored = [c for c in cases if c.readiness_score is not None]

    # ── Screening ────────────────────────────────────────────────────────────
    all_parties = [p for ps in parties_by_case.values() for p in ps]
    screened = [p for p in all_parties if (p.screening_status or "not_screened") != "not_screened"]
    with_hits = [p for p in screened if p.screening_hits]
    # A hit dispositioned "clear" was a false positive. L200 notes real-world screening
    # runs 95%+ false positives, so tracking the rate is how a firm sizes its alert queue.
    false_positives = [p for p in with_hits if p.screening_status == "clear"]
    fp_rate = round(len(false_positives) / len(with_hits) * 100, 1) if with_hits else None
    edd_backlog = [c for c in open_cases if c.edd_status == "edd_pending"]
    edd_ages = [d for d in (_days(c.created_at, now) for c in edd_backlog) if d is not None]

    # ── Transfers ────────────────────────────────────────────────────────────
    all_transfers = [t for ts in transfers_by_case.values() for t in ts]
    settled_transfers = [t for t in all_transfers if t.status == "settled"]
    failed_transfers = [t for t in all_transfers if t.status == "failed"]
    in_flight = [t for t in all_transfers if t.status not in ("settled", "failed")]
    transfer_days = [
        d for d in (_days(t.initiated_at, t.settled_at) for t in settled_transfers)
        if d is not None
    ]
    reject_rate = (
        round(len(failed_transfers) / len(all_transfers) * 100, 1) if all_transfers else None
    )

    # ── SLA ──────────────────────────────────────────────────────────────────
    breached, at_risk = [], []
    for c in open_cases:
        elapsed = _days(c.created_at, now)
        if elapsed is None:
            continue
        sla = c.sla_days or 30
        if elapsed >= sla:
            breached.append(c)
        elif elapsed >= sla * 0.8:
            at_risk.append(c)

    # ── Ageing of the open book ──────────────────────────────────────────────
    open_ages = [d for d in (_days(c.created_at, now) for c in open_cases) if d is not None]

    return {
        "as_of": now.isoformat(),
        "counts": {
            "total": len(cases),
            "open": len(open_cases),
            "activated": len(activated),
        },
        "cycle_time_days": {
            "intake_to_activation": _summary(to_activation),
            "activation_to_funded": _summary(to_funded),
            "by_registration_type": {
                reg: _summary(v) for reg, v in sorted(by_reg.items())
            },
            "open_case_age": _summary(open_ages),
        },
        "quality": {
            "first_pass_yield_pct": fpy,
            "nigo_cases": len(nigo_cases),
            "nigo_by_root_cause": dict(sorted(by_cause.items(), key=lambda kv: -kv[1])),
            "mean_readiness": (
                round(sum(c.readiness_score for c in scored) / len(scored), 1) if scored else None
            ),
            "cases_scored": len(scored),
        },
        "screening": {
            "parties_total": len(all_parties),
            "parties_screened": len(screened),
            "coverage_pct": (
                round(len(screened) / len(all_parties) * 100, 1) if all_parties else None
            ),
            "parties_with_hits": len(with_hits),
            "false_positive_pct": fp_rate,
            "edd_backlog": len(edd_backlog),
            "edd_backlog_age_days": _summary(edd_ages),
        },
        "transfers": {
            "total": len(all_transfers),
            "settled": len(settled_transfers),
            "in_flight": len(in_flight),
            "reject_rate_pct": reject_rate,
            "days_to_settle": _summary(transfer_days),
        },
        "sla": {
            "breached": len(breached),
            "at_risk": len(at_risk),
            "on_track": len(open_cases) - len(breached) - len(at_risk),
            "attainment_pct": (
                round((len(open_cases) - len(breached)) / len(open_cases) * 100, 1)
                if open_cases else None
            ),
        },
    }


def blocker_frequency(gate_results: list[dict]) -> list[dict]:
    """Which gates block most often across the open book.

    Not named in L200, but it is the operational question the gate model makes answerable:
    if one control blocks most cases, that is where the process actually needs work.
    """
    counts: dict[str, dict] = {}
    for result in gate_results:
        for gate in result.get("gates", []):
            if not gate.get("blocks"):
                continue
            entry = counts.setdefault(gate["key"], {"key": gate["key"], "label": gate["label"], "count": 0})
            entry["count"] += 1
    return sorted(counts.values(), key=lambda e: -e["count"])
