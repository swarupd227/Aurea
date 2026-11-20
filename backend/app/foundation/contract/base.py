"""What every record in the contract carries: where it came from, its id there, when it was read, and the contract
version it was written to. A contract change is a new version; an adapter says which versions it speaks."""
from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

CONTRACT_VERSION = "1"


class Record(BaseModel):
    """One record of a domain. ``external_id`` is the source's own id; a reference to another record (``*_ref``) is
    that record's ``external_id`` in the same source."""

    model_config = ConfigDict(extra="forbid")

    # The entity this record is, as ``external_ref.entity_type`` names it, and its domain. Set on each subclass.
    entity: ClassVar[str] = ""
    domain: ClassVar[str] = ""

    source: str
    external_id: str
    as_of: datetime | None = None
    contract_version: str = CONTRACT_VERSION
