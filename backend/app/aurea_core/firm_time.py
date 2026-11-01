"""What day it is for a firm.

The server runs on UTC, so for most of a New Zealand morning its date is still yesterday. "Today",
"overdue" and "due Friday" are all said on the firm's calendar, so they are worked out here and not
from utcnow().date(). A moment in time (has this meeting's hour gone by?) needs none of this.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.db import utcnow
from app.models.tenant import Firm

_MOMENT = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?")

# A firm that needs another zone sets settings["timezone"]; US firms in particular span several.
JURISDICTION_ZONES = {
    "NZ": "Pacific/Auckland", "AU": "Australia/Sydney", "UK": "Europe/London", "GB": "Europe/London",
    "US": "America/New_York", "IN": "Asia/Kolkata",
}


def zone(firm: Firm | None) -> ZoneInfo:
    named = ((firm.settings or {}).get("timezone") if firm else None) or JURISDICTION_ZONES.get(
        str(firm.jurisdiction or "").upper() if firm else "")
    try:
        return ZoneInfo(named) if named else ZoneInfo("UTC")
    except (KeyError, ValueError):   # a zone name mistyped in settings is not a reason to fail a turn
        return ZoneInfo("UTC")


def today(firm: Firm | None, now: datetime | None = None) -> date:
    moment = now or utcnow()
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(zone(firm)).date()


def spoken(day: date) -> str:
    """A date with its weekday, so a wrong "Friday" can be seen before anyone confirms it."""
    return f"{day:%A} {day.day} {day:%B %Y}"


def on_firm_clock(value: Any, firm: Firm | None) -> Any:
    """A tool result as the model reads it: every moment in it restated in the firm's zone, with the
    offset, so the day it says is the firm's day. Stored results stay in UTC, and the browser shows
    those in the reader's zone. Only a whole string that is a moment changes, never a date alone or
    one inside a sentence. No offset means UTC, as `today` takes it."""
    tz = zone(firm)

    def moment(m: datetime) -> str:
        return (m if m.tzinfo else m.replace(tzinfo=timezone.utc)).astimezone(tz).isoformat(timespec="seconds")

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        if isinstance(v, datetime):
            return moment(v)
        if isinstance(v, str) and _MOMENT.fullmatch(v):
            try:
                return moment(datetime.fromisoformat(v.replace("Z", "+00:00")))
            except ValueError:
                return v
        return v

    return walk(value)
