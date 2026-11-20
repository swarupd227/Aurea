"""What an adapter is: a conduit connector that speaks the contract for the domains it serves.

An adapter declares what it can supply, so a module needing a capability its owner lacks reads "not assessed":

    tax_lots            bool      lots under each holding, which every gain on a sale is worked from
    transactions_days   int|None  how many days of transactions it holds; None for all of them
    events              bool      whether it pushes changes, or is only pulled

``pull`` reads a domain from the system (a mock fixture, or the system itself when the connector is live) and returns
contract records; ``send`` delivers a command (a CRM activity) to it. Neither writes Astra's tables: the sync does,
through external_ref, with every difference that is not the source moving on reported as a break.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.foundation.contract import CONTRACT_VERSION, Record


@dataclass
class Pulled:
    """What one pull read: the records, in import order, and anything it could not map (counted, never guessed)."""

    records: list[Record] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    mode: str = "mock"   # mock | live | native


class AdapterError(Exception):
    """The system refused or could not be reached. The message names what failed, never a credential."""


class Adapter:
    key: str = ""
    name: str = ""
    speaks: tuple[str, ...] = (CONTRACT_VERSION,)
    domains: frozenset[str] = frozenset()
    capabilities: dict[str, Any] = {}
    # The command kinds it takes ("crm.activity.create"); a domain it owns with no command kind refuses every write.
    commands: frozenset[str] = frozenset()

    def serves(self, domain: str) -> bool:
        return domain in self.domains

    async def pull(self, session, firm, connector, domain: str) -> Pulled:   # pragma: no cover - interface
        raise NotImplementedError

    async def send(self, session, firm, connector, command) -> None:   # pragma: no cover - interface
        raise AdapterError(f"{self.name} takes no commands.")
