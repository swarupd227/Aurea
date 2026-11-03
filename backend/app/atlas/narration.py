"""A run narrated into a conversation: the card that is published when it starts and settled when it
finishes, and the sentence the agent says when it is done.

Pure functions over plain dicts, so the card can be stored on a thread message (`card_data`),
folded forward as the run's beats arrive, and read back by the browser without any of it knowing
about the database. The shape is the `agent_run` card in the workspace's ArtifactRenderer.

Sentences live only in the agent's message (CLAUDE.md rule 5): the card carries figures, chips
and the beats of the run, nothing explanatory.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

CARD_KIND = "agent_run"

# A run that was started and never settled - the server went away mid-run - is shown as
# interrupted rather than left spinning for ever. Generous: a run takes seconds.
STALE_AFTER = timedelta(minutes=15)

_TRACKED = ("sense", "think", "check")


def new_card(preview: dict[str, Any]) -> dict[str, Any]:
    """The card as it is first published: running, with no beats yet."""
    return {
        "status": "running",
        "agent_key": preview["agent_key"],
        "agent_name": preview["agent_name"],
        "subject_label": preview.get("subject_label"),
        "tier": preview["autonomy"]["tier"],
        "tier_label": preview["autonomy"]["label"],
        "held_to_proposal": preview["autonomy"]["held_to_proposal"],
        "beats": [],
        "run_id": None,
        "flags": 0,
        "proposals": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "error": None,
    }


def fold(card: dict[str, Any], event: dict[str, Any]) -> bool:
    """Fold one streamed run event into the card. True when the card changed."""
    phase = event.get("phase")
    if phase == "start":
        card["run_id"] = event.get("run_id")
        if event.get("tier"):
            card["tier"] = str(event["tier"])
        return True
    if phase in _TRACKED:
        status = event.get("status")
        if status == "start":
            card["beats"].append({"phase": phase, "label": event.get("label"), "state": "running", "detail": None})
            return True
        if status == "done":
            for beat in reversed(card["beats"]):
                if beat["phase"] == phase and beat["state"] == "running":
                    beat["state"] = "done"
                    beat["detail"] = event.get("detail")
                    break
            if phase == "check":
                card["flags"] = int(event.get("flags") or 0)
            return True
    return False


def settle(card: dict[str, Any], *, status: str, proposals: list[dict[str, Any]] | None = None,
           error: str | None = None) -> dict[str, Any]:
    """Close the card. `status` is one of proposed | acted | nothing | failed."""
    card["status"] = status
    card["proposals"] = proposals or []
    card["error"] = error
    card["finished_at"] = datetime.now(timezone.utc).isoformat()
    for beat in card["beats"]:
        if beat["state"] == "running":
            beat["state"] = "failed" if status == "failed" else "done"
    return card


def rebalance_figures(payload: dict[str, Any]) -> dict[str, Any] | None:
    """What a rebalancing proposal would do, as the card shows it: each class's weight now, its target and after the
    orders, the orders, the gain against the budget and the cash before and after. Worked out from the proposal's own
    payload; a class's weight after is its value now plus what is bought less what is sold, over the same total."""
    current, target = payload.get("current_weights") or {}, payload.get("target_weights") or {}
    orders = payload.get("order_set") or []
    total = float((payload.get("summary") or {}).get("total_value") or 0)
    if not total or not (current or target):
        return None
    flow: dict[str, float] = {}
    for o in orders:
        value = float(o.get("est_value") or 0) * (1 if o.get("side") == "buy" else -1)
        cls = o.get("asset_class") or "other"
        flow[cls] = flow.get(cls, 0.0) + value
        flow["cash"] = flow.get("cash", 0.0) - value
    classes = sorted(set(current) | set(target) | set(flow), key=lambda c: (c == "cash", -float(target.get(c, 0))))
    rows = [{"class": c, "now": round(float(current.get(c, 0)), 4), "target": round(float(target.get(c, 0)), 4) if c in target else None,
             "after": round((float(current.get(c, 0)) * total + flow.get(c, 0.0)) / total, 4)} for c in classes]
    cash_now = float(current.get("cash", 0)) * total
    s = payload.get("summary") or {}
    return {
        "currency": payload.get("currency"), "total_value": round(total, 2),
        "max_drift": s.get("max_drift"), "band": s.get("drift_band"), "turnover": s.get("turnover"),
        "classes": rows,
        "orders": [{"side": o.get("side"), "symbol": o.get("symbol"), "quantity": o.get("quantity"),
                    "value": round(float(o.get("est_value") or 0), 2), "gain": o.get("est_realised_gain")} for o in orders],
        "gain": payload.get("estimated_realised_gain"), "budget": payload.get("cgt_budget"),
        "harvested": payload.get("harvested_losses") or 0,
        "cash": {"now": round(cash_now, 2), "after": round(cash_now + flow.get("cash", 0.0), 2)},
        "measured_against": "IPS" if payload.get("ips") else "model",
    }


def proposal_row(rec) -> dict[str, Any]:
    """A proposal as the run card lists it, with what the card needs to show what it would do."""
    row: dict[str, Any] = {"id": str(rec.id), "title": rec.title, "priority": rec.priority, "confidence": rec.confidence,
                           "status": str(rec.status), "agent_key": str(rec.agent_key)}
    payload = rec.modified_payload or rec.payload or {}
    if str(rec.agent_key) == "drift_rebalancing":
        row["rebalance"] = rebalance_figures(payload)
    if payload.get("brief"):
        row["brief"] = payload["brief"]
    return row


def as_served(card: dict[str, Any] | None) -> dict[str, Any] | None:
    """The card as the browser should see it: a run that began long ago and never settled is
    reported as interrupted, not as still running."""
    if not card or card.get("status") != "running":
        return card
    try:
        started = datetime.fromisoformat(str(card.get("started_at")))
    except ValueError:
        return card
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - started > STALE_AFTER:
        return {**card, "status": "interrupted"}
    return card


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " or " + names[-1]


def deciders_phrase(agent_key: str) -> str:
    """Who may approve what this agent proposes, in words - from decision_rights."""
    from app.core import decision_rights as dr

    if dr.moves_the_book(agent_key):
        return "an adviser or the portfolio team"
    if dr.moves_client_cash(agent_key):
        return "operations or a branch leader"
    return "a member of staff"


def sentence(card: dict[str, Any], *, rationale: str = "") -> str:
    """What the agent says when its run settles. Built only from the settled card, which was
    itself built from rows the run wrote."""
    name = card["agent_name"]
    where = f" for {card['subject_label']}" if card.get("subject_label") else " across the book"
    status = card["status"]
    if status == "failed":
        return f"{name} could not complete the run{where}: {card.get('error') or 'it stopped without a reason'}."
    proposals = card.get("proposals") or []
    if status == "nothing":
        return f"{name} checked{where} and found nothing to put forward."

    waiting = [p for p in proposals if p["status"] == "proposed"]
    carried_out = [p for p in proposals if p["status"] == "executed"]
    bits = []
    if waiting:
        n = len(waiting)
        bits.append(f"{n} proposal{'s' if n != 1 else ''} waiting for {deciders_phrase(card['agent_key'])} to decide")
    if carried_out:
        n = len(carried_out)
        bits.append(f"{n} carried out on its own at {card['tier_label']}, open to review")
    other = len(proposals) - len(waiting) - len(carried_out)
    if other:
        bits.append(f"{other} recorded without a decision")
    head = f"{name} put forward {len(proposals)} item{'s' if len(proposals) != 1 else ''}{where}: " + "; ".join(bits) + "."
    if proposals:
        head += f" The first is “{proposals[0]['title']}”."
    if card.get("held_to_proposal") and waiting:
        head += " It stops at a proposal: nothing has been traded or billed."
    if card.get("flags"):
        head += f" Surveillance raised {card['flags']} flag{'s' if card['flags'] != 1 else ''} on it."
    text = head
    if rationale.strip():
        text += "\n\n" + whole_sentences(rationale.strip(), 900)
    return text


def whole_sentences(text: str, limit: int) -> str:
    """At most `limit` characters, ending at the last full sentence, so a reply never stops mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("? "), cut.rfind("! "))
    return cut[:end + 1] if end > limit // 3 else cut[:cut.rfind(" ")].rstrip(",;:") + "…"


def follow_ups(card: dict[str, Any]) -> list[str]:
    """Suggestions that follow from how the run settled - each a sentence a tool can answer."""
    if card["status"] == "failed":
        return ["Which agents can I run?"]
    if card["status"] == "nothing":
        return ["Which agents can I run?", "What proposals are waiting for my decision?"]
    out = ["What proposals are waiting for my decision?"]
    if card.get("subject_label"):
        out.append(f"How has {card['subject_label']} performed against its benchmark?")
    out.append("Which agents can I run?")
    return out
