"""Which system owns each data domain of a firm, and what it can supply.

``firm.settings["foundation"]["sources"]`` maps a domain to its owner: "native" (Astra's own tables) or an adapter's
key, which is its connector's provider key (``crm.dynamics``). Unset is native, so a firm nobody configured reads
exactly as it always has. The rest of ``settings["foundation"]`` is the agent policy (app/core/foundation.py); the
two never mix.

An owner is the administrator's to change (``aurea_core/platform_admin.set_foundation_owner``), with a reason on the
audit log. A module that needs something the owner cannot supply (tax lots from a platform that keeps none) reads
"not assessed" through ``lacks``, never an estimate.
"""
from __future__ import annotations

from typing import Any

DOMAINS = ("parties", "accounts", "positions", "activity", "prices", "crm")
NATIVE = "native"
DOMAIN_LABEL = {"parties": "Parties", "accounts": "Accounts and mandates", "positions": "Positions",
                "activity": "Activity", "prices": "Prices", "crm": "CRM"}
# Which domain's owner supplies each capability.
CAPABILITY_DOMAIN = {"tax_lots": "positions", "transactions_days": "activity"}
CAPABILITY_LABEL = {"tax_lots": "tax lots", "transactions_days": "transaction history"}


def _stored(firm: Any) -> dict:
    return dict((((getattr(firm, "settings", None) or {}).get("foundation") or {}).get("sources")) or {})


def owners(firm: Any) -> dict[str, str]:
    """Each domain's owner. Native unless an administrator set another."""
    stored = _stored(firm)
    return {d: str(stored.get(d) or NATIVE) for d in DOMAINS}


def owner(firm: Any, domain: str) -> str:
    return owners(firm).get(domain, NATIVE)


def is_native(firm: Any, domain: str) -> bool:
    return owner(firm, domain) == NATIVE


def adapter_for(firm: Any, domain: str):
    """The adapter that owns a domain; None when the owner set has no adapter built here."""
    from app.foundation.adapters import get_adapter

    return get_adapter(owner(firm, domain))


def owner_name(key: str) -> str:
    from app.foundation.adapters import get_adapter

    adapter = get_adapter(key)
    return adapter.name if adapter else key


def capability(firm: Any, name: str) -> Any:
    """What the domain's owner declares for a capability; native supplies everything."""
    domain = CAPABILITY_DOMAIN[name]
    if is_native(firm, domain):
        return True if name == "tax_lots" else None
    adapter = adapter_for(firm, domain)
    return (adapter.capabilities or {}).get(name) if adapter else False


def lacks(firm: Any, name: str) -> str | None:
    """Why a reading that needs this capability is not assessed for this firm, or None when it can be.

    A native firm always can. ``transactions_days`` is a number of days (None: all history); only its absence
    (False) stops a reading, and the caller compares a window against the number itself."""
    domain = CAPABILITY_DOMAIN[name]
    if is_native(firm, domain):
        return None
    have = capability(firm, name)
    if have is False or (name == "tax_lots" and not have):
        return (f"Not assessed: {owner_name(owner(firm, domain))} supplies this firm's {DOMAIN_LABEL[domain].lower()} "
                f"and no {CAPABILITY_LABEL[name]}.")
    return None


def write_refusal(firm: Any, domain: str, what: str) -> str | None:
    """Why Astra may not record this here: the domain's owner holds it. None for a native domain."""
    if is_native(firm, domain):
        return None
    name = owner_name(owner(firm, domain))
    return (f"{name} owns this firm's {DOMAIN_LABEL[domain]}: {what} in {name}, and it reaches Astra on the next sync. "
            f"Astra does not write to a record {name} owns.")


def with_owner(settings: dict | None, domain: str, key: str) -> dict:
    """The firm's settings with one domain's owner set (native removes the entry). Returns a new dict to assign."""
    s = dict(settings or {})
    had = "foundation" in s
    f = dict(s.get("foundation") or {})
    sources = dict(f.get("sources") or {})
    if key == NATIVE:
        sources.pop(domain, None)
    else:
        sources[domain] = key
    if sources:
        f["sources"] = sources
    else:
        f.pop("sources", None)
    if f or had:
        s["foundation"] = f
    # (An empty key is not written where there was none: core/foundation.policy reads its absence.)
    return s
