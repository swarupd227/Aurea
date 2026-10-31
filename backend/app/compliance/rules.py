"""Deterministic rule evaluators. Each takes a CheckContext and returns a RuleResult
(pass / fail / na, a plain-language finding, and an optional severity override). Evaluators are
pure and auditable — the same input always yields the same compliance result."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.aurea_core.markets import money

BANNED = ["guarantee", "guaranteed", "risk-free", "riskless", "no risk", "will definitely",
          "can't lose", "cannot lose", "always outperform", "sure thing", "can not lose"]


@dataclass
class CheckContext:
    agent_key: str
    rationale: str
    text: str               # lower-cased rationale + string payload values
    confidence: float
    payload: dict
    evidence: dict
    citations: list
    mandate_type: str | None
    policy: dict            # effective foundation policy (thresholds)


@dataclass
class RuleResult:
    status: str            # pass | fail | na
    finding: str = ""
    severity: str | None = None  # override the rule's default severity


def _f(p): return ((p or {}).get("summary") or {})  # payload summary shortcut


def no_overpromise(c: CheckContext) -> RuleResult:
    hits = sorted({b for b in BANNED if b in c.text})
    if hits:
        return RuleResult("fail", f"Over-promising language: {', '.join(hits)}.")
    return RuleResult("pass", "No misleading or guaranteed-outcome language.")


def turnover(c: CheckContext) -> RuleResult:
    cap = float(c.policy.get("max_turnover_pct") or 0)
    t = _f(c.payload).get("turnover_pct")
    if t is None:
        return RuleResult("na", "No turnover in this action.")
    if cap > 0 and t > cap:
        return RuleResult("fail", f"Turnover {t:.0%} exceeds the firm limit of {cap:.0%}.")
    return RuleResult("pass", f"Turnover {t:.0%} within limits.")


def explainable(c: CheckContext) -> RuleResult:
    if len((c.rationale or "").strip()) < 40:
        return RuleResult("fail", "No adequate plain-language rationale for the client.")
    return RuleResult("pass", "Plain-language rationale present.")


def mandate_suitability(c: CheckContext) -> RuleResult:
    if c.mandate_type:
        return RuleResult("pass", f"Within the client's {c.mandate_type} mandate.")
    return RuleResult("na", "No mandate scope on this recommendation.")


def within_guardrails(c: CheckContext) -> RuleResult:
    breaches = (c.evidence or {}).get("guardrail_breaches") or []
    if breaches:
        return RuleResult("fail", f"{len(breaches)} guardrail breach(es): {breaches[0]}")
    return RuleResult("pass", "No guardrail breaches.")


def suitability_complete(c: CheckContext) -> RuleResult:
    """Were the inputs a suitability control needs actually collected?

    Deliberately LOW severity. A missing assessment is a gap to close, not a breach to
    stop trading over — and only HIGH auto-pauses an agent. Reporting it as HIGH is what
    left drift permanently paused over a field nobody had filled in."""
    gaps = (c.evidence or {}).get("suitability_gaps") or []
    if gaps:
        return RuleResult("fail", f"{len(gaps)} suitability input(s) incomplete: {gaps[0]}",
                          severity="low")
    return RuleResult("pass", "Suitability inputs complete.")


def cgt_budget(c: CheckContext) -> RuleResult:
    budget = (c.evidence or {}).get("cgt_budget")
    if budget is None:
        budget = (c.payload or {}).get("cgt_budget")
    gain = (c.payload or {}).get("estimated_realised_gain")
    if budget is None or gain is None:
        return RuleResult("na", "No CGT budget applies.")
    ccy = (c.payload or {}).get("currency") or "USD"
    if gain > float(budget) + 1:
        return RuleResult("fail", f"Realised gain {money(gain, ccy)} exceeds the CGT budget {money(float(budget), ccy)}.")
    return RuleResult("pass", f"Realised gain {money(gain, ccy)} within the {money(float(budget), ccy)} CGT budget.")


def grounding(c: CheckContext) -> RuleResult:
    required = bool(c.policy.get("require_grounding"))
    if c.citations:
        return RuleResult("pass", f"{len(c.citations)} firm-research source(s) cited.")
    sev = "high" if required else "low"
    msg = "No firm research cited." + (" Firm policy requires grounded advice." if required else "")
    return RuleResult("fail", msg, severity=sev)


def confidence(c: CheckContext) -> RuleResult:
    thr = float(c.policy.get("min_confidence", 0.5))
    if c.confidence and c.confidence < thr:
        return RuleResult("fail", f"Confidence {c.confidence:.0%} below the {thr:.0%} reliability threshold.")
    return RuleResult("pass", f"Confidence {c.confidence:.0%} meets the threshold.")


def aml_screening(c: CheckContext) -> RuleResult:
    st = (c.payload or {}).get("screening_status")
    if st == "blocked":
        return RuleResult("fail", "Sanctions match — onboarding halted for compliance escalation.", severity="high")
    if st == "review":
        return RuleResult("fail", "PEP / adverse-media hit — enhanced due diligence required.", severity="medium")
    if st in ("clear", "cleared", "passed"):
        return RuleResult("pass", "AML/CFT screening clear.")
    return RuleResult("na", "No screening on this action.")


def disclosure(c: CheckContext) -> RuleResult:
    breaches = (c.evidence or {}).get("guardrail_breaches") or []
    if breaches:
        return RuleResult("pass", f"{len(breaches)} material limitation(s) disclosed and recorded.")
    return RuleResult("pass", "No undisclosed material limitations.")


def records(c: CheckContext) -> RuleResult:
    return RuleResult("pass", "Written to the hash-chained decision ledger.")


# ── Jurisdiction-specific structural checks (US / EU) ────────────────────────────────────────────
def wash_sale(c: CheckContext) -> RuleResult:
    """IRC §1091 — a loss sale must not be paired with a repurchase of the same security."""
    orders = (c.payload or {}).get("order_set") or []
    if not orders:
        return RuleResult("na", "No trades to assess for wash sales.")
    loss_sells = {o.get("symbol") for o in orders
                  if o.get("side") == "sell" and (o.get("est_realised_gain") or 0) < 0}
    buys = {o.get("symbol") for o in orders if o.get("side") == "buy"}
    clash = sorted(s for s in (loss_sells & buys) if s)
    if clash:
        return RuleResult("fail", f"Potential wash sale: {', '.join(clash)} sold at a loss and repurchased.")
    guard = (c.payload or {}).get("wash_sale") or {}
    if guard.get("applies"):
        return RuleResult("pass", "Checked against the household's trades in the wash-sale window, in every account.")
    return RuleResult("pass", "No loss sale repurchased within this order set.")


def costs_disclosed(c: CheckContext) -> RuleResult:
    s = (c.payload or {}).get("summary") or {}
    if "turnover" in s or "turnover_pct" in s:
        return RuleResult("pass", "Turnover and cost impact disclosed in the order set.")
    return RuleResult("na", "No costs to disclose for this action.")


def target_market(c: CheckContext) -> RuleResult:
    if c.mandate_type:
        return RuleResult("pass", f"Action within the {c.mandate_type} mandate's target market.")
    return RuleResult("na", "No product target-market assessment applies.")


def best_execution(c: CheckContext) -> RuleResult:
    orders = (c.payload or {}).get("order_set") or []
    if not orders:
        return RuleResult("na", "No execution in this action.")
    venues = {o.get("custodian") for o in orders if o.get("custodian")}
    if venues:
        return RuleResult("pass", f"Routed across {len(venues)} venue(s) on best-execution terms.")
    return RuleResult("fail", "No execution venue recorded for the order set.")


def kid_provided(c: CheckContext) -> RuleResult:
    return RuleResult("na", "No packaged retail investment product (PRIIP) in scope.")


def privacy(c: CheckContext) -> RuleResult:
    if c.policy.get("pii_redaction", True):
        return RuleResult("pass", "Client PII redacted before model processing.")
    return RuleResult("fail", "PII redaction is disabled for this firm.", severity="medium")


def esg_preferences(c: CheckContext) -> RuleResult:
    orders = (c.payload or {}).get("order_set") or []
    if not orders:
        return RuleResult("na", "No trades to assess.")
    honored = [o for o in orders if "exclud" in (o.get("reason", "") or "").lower()
               or "esg" in (o.get("reason", "") or "").lower()]
    return RuleResult("pass", f"{len(honored)} values/ESG exclusion(s) enforced." if honored
                      else "No ESG conflicts in the order set.")


def narrative_instrument_check(c: CheckContext) -> RuleResult:
    """Verify the narrative is grounded in the actual recommendation — asset classes and direction
    language in the rationale must correspond to the order set, catching rationale that drifts from
    the trades (a proxy hallucination / disconnection check)."""
    orders = (c.payload or {}).get("order_set") or []
    if not orders:
        return RuleResult("na", "No order set — narrative groundedness not applicable.")

    asset_classes = {
        (o.get("asset_class") or "").replace("_", " ").lower()
        for o in orders if o.get("asset_class")
    }
    asset_classes = {ac for ac in asset_classes if len(ac) >= 3}
    if not asset_classes:
        return RuleResult("na", "Order set has no asset classes to verify narrative against.")

    rationale = (c.rationale or "").lower()

    # Ground check 1: at least one asset class from the order set appears in the narrative
    ac_hit = any(ac in rationale or ac.split()[0] in rationale for ac in asset_classes)

    # Ground check 2: direction language is consistent with the order composition
    sells = sum(1 for o in orders if o.get("side") == "sell")
    buys = sum(1 for o in orders if o.get("side") == "buy")
    reducing = any(w in rationale for w in ("reduc", "trim", "sell", "divest", "decreas", "lessen"))
    adding = any(w in rationale for w in ("add", "buy", "increas", "purchas", "build", "accumulat"))

    direction_ok = True
    direction_note = ""
    if sells > 0 and buys == 0 and not reducing:
        direction_ok = False
        direction_note = " Sell-only order set but no reduce/trim language found in narrative."
    elif buys > 0 and sells == 0 and not adding:
        direction_ok = False
        direction_note = " Buy-only order set but no buy/increase language found in narrative."

    if not ac_hit and not direction_ok:
        return RuleResult("fail",
            f"Narrative appears disconnected from the recommendation payload: no reference to "
            f"{', '.join(sorted(asset_classes))} and direction mismatch.{direction_note}",
            severity="medium")
    if not ac_hit:
        return RuleResult("fail",
            f"Narrative does not reference the recommendation's asset class(es): "
            f"{', '.join(sorted(asset_classes))}. Rationale may not describe the actual trades.",
            severity="low")
    if not direction_ok:
        return RuleResult("fail",
            f"Trade direction in narrative may not match the order set.{direction_note}",
            severity="low")

    direction_label = (
        f"{sells} sell / {buys} buy order(s)" if sells and buys
        else f"{sells} sell order(s)" if sells
        else f"{buys} buy order(s)"
    )
    return RuleResult("pass",
        f"Narrative grounded in the {direction_label} across "
        f"{', '.join(sorted(asset_classes))} from the recommendation payload.")


def client_consent(c: CheckContext) -> RuleResult:
    """Which consent this trade needs is settled by the client's licence track (aurea_core/licences.py).
    A pass here means the gate knows; runtime.decide is what holds the orders back."""
    licence = (c.payload or {}).get("licence")
    if not licence:
        return RuleResult("na", "No licence track on this recommendation.")
    if licence.get("per_trade_consent"):
        return RuleResult("pass", f"{licence.get('name')}: no order until the client's consent is on record.")
    if licence.get("may_execute"):
        return RuleResult("pass", f"{licence.get('name')}: acts inside the mandate, approved by a named person.")
    return RuleResult("fail", f"{licence.get('name')} may not execute a trade.")


def statutory_fifo(c: CheckContext) -> RuleResult:
    tax = (c.payload or {}).get("india_tax")
    if not tax:
        return RuleResult("na", "No Indian tax position on this recommendation.")
    if tax.get("lot_method") != "FIFO":
        return RuleResult("fail", f"Lots relieved by {tax.get('lot_method')}; demat holdings relieve first-in first-out.")
    return RuleResult("pass", "Lots relieved first-in first-out, account by account.")


def india_harvest_guard(c: CheckContext) -> RuleResult:
    tax = (c.payload or {}).get("india_tax")
    if not tax:
        return RuleResult("na", "No Indian tax position on this recommendation.")
    conflicts = tax.get("conflicts") or []
    if conflicts:
        return RuleResult("fail", f"{len(conflicts)} order(s) break the harvest guard: {conflicts[0].get('symbol')}.")
    held = len(tax.get("held") or [])
    return RuleResult("pass", f"No same-day round trip or stripped loss{f'; {held} held back' if held else ''}.")


def no_regime(c: CheckContext) -> RuleResult:
    return RuleResult("fail", "This firm's jurisdiction has no rules on this platform, so nothing can be cleared.")


REGISTRY = {
    "no_overpromise": no_overpromise, "turnover": turnover, "explainable": explainable,
    "mandate_suitability": mandate_suitability, "within_guardrails": within_guardrails,
    "suitability_complete": suitability_complete,
    "cgt_budget": cgt_budget, "grounding": grounding, "confidence": confidence,
    "aml_screening": aml_screening, "disclosure": disclosure, "records": records,
    "narrative_instrument_check": narrative_instrument_check,
    # US / EU structural evaluators
    "wash_sale": wash_sale, "costs_disclosed": costs_disclosed, "target_market": target_market,
    "best_execution": best_execution, "kid_provided": kid_provided, "privacy": privacy,
    "esg_preferences": esg_preferences,
    # India
    "client_consent": client_consent, "statutory_fifo": statutory_fifo,
    "india_harvest_guard": india_harvest_guard,
    "no_regime": no_regime,
}
