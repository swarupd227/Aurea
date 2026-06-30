"""Book-integration reconciliation (spec Table 8 — 'reconciles and maps client, account and
holding data from an acquired firm into the client brain … flags conflicts').

Pure functions: fuzzy-match inbound clients against existing households, map inbound securities
to the firm's instrument master, and detect holding conflicts. The agent wraps these and, on
operations approval, commits the accepted mappings as golden records."""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace("the ", "").replace(".", " ").split())


def name_similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    token = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return round(max(seq, token), 3)


@dataclass
class ReconResult:
    client_mappings: list[dict] = field(default_factory=list)
    security_mappings: list[dict] = field(default_factory=list)
    holding_conflicts: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def reconcile(
    *,
    inbound_clients: list[dict],
    existing_households: list[dict],
    inbound_securities: list[dict],
    master_symbols: dict[str, str],  # symbol -> name (the firm's instrument master)
    inbound_holdings: list[dict],
    existing_by_client_symbol: dict[str, float],  # "client|symbol" -> quantity in the brain
    match_threshold: float = 0.8,
) -> ReconResult:
    res = ReconResult()

    # 1. Client matching — merge into an existing household or create new.
    for c in inbound_clients:
        best, best_score = None, 0.0
        for h in existing_households:
            s = name_similarity(c["name"], h["name"])
            if s > best_score:
                best, best_score = h, s
        if best and best_score >= match_threshold:
            res.client_mappings.append({
                "inbound": c["name"], "action": "merge", "target_id": best["id"],
                "target_name": best["name"], "score": best_score,
            })
        else:
            res.client_mappings.append({
                "inbound": c["name"], "action": "create", "target_id": None,
                "target_name": None, "score": best_score,
            })

    # 2. Security mapping — map to the master or create a new instrument.
    master_lower = {k.lower(): k for k in master_symbols}
    for sec in inbound_securities:
        sym = sec["symbol"]
        if sym.lower() in master_lower:
            res.security_mappings.append({
                "inbound": sym, "action": "map", "target": master_lower[sym.lower()],
                "name": master_symbols[master_lower[sym.lower()]],
            })
        else:
            res.security_mappings.append({
                "inbound": sym, "action": "create", "target": sym, "name": sec.get("name", sym),
                "asset_class": sec.get("asset_class", "equity"),
            })

    # 3. Holding conflicts — same client+symbol present in the brain with a different quantity.
    for h in inbound_holdings:
        key = f"{_norm(h['client'])}|{h['symbol'].lower()}"
        existing_qty = existing_by_client_symbol.get(key)
        if existing_qty is not None and abs(existing_qty - h["quantity"]) > 1e-6:
            res.holding_conflicts.append({
                "client": h["client"], "symbol": h["symbol"],
                "inbound_quantity": h["quantity"], "existing_quantity": existing_qty,
                "delta": round(h["quantity"] - existing_qty, 4),
            })

    res.stats = {
        "clients": len(inbound_clients),
        "merges": sum(1 for m in res.client_mappings if m["action"] == "merge"),
        "new_clients": sum(1 for m in res.client_mappings if m["action"] == "create"),
        "securities": len(inbound_securities),
        "unmapped_securities": sum(1 for m in res.security_mappings if m["action"] == "create"),
        "holdings": len(inbound_holdings),
        "conflicts": len(res.holding_conflicts),
    }
    return res
