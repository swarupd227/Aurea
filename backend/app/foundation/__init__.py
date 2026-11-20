"""Astra over a Foundation layer (docs/03_Architecture/foundation-layer.md; backlog B-161 to B-166).

Astra keeps its own tables as a local copy of what a firm's established products own, fed through a versioned contract
(``contract/``) by swappable adapters (``adapters/``). Nothing that reads client data changes.

  - ``owners``: which system owns each domain, from ``firm.settings["foundation"]["sources"]``; native when unset, so
    an existing firm is exactly as it was. Also what the owning adapter can supply (its capabilities).
  - ``mapping``: Astra's tables to the contract and back, entity by entity.
  - ``sync``: an adapter's records upserted by (source, external_id) through ``external_ref``; a difference with a
    record changed here is a break, reported with its evidence and never overwritten in either direction.
  - ``commands``: what Astra asks a Foundation system to record leaves as a command; the copy changes when the
    system's own record comes back through a sync.
  - ``sources``: the read the Connectors page and ``read_foundation_sources`` show.
"""
