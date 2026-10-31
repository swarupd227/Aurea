"""What day it is for a firm.

The server runs on UTC, so for most of a New Zealand morning its date is still yesterday. "Today",
"overdue" and "due Friday" are all said on the firm's calendar, so they are worked out here and not
from utcnow().date(). A moment in time (has this meeting's hour gone by?) needs none of this.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.core.db import utcnow
from app.models.tenant import Firm

# A firm that needs another zone sets settings["timezone"]; US firms in particular span several.
JURISDICTION_ZONES = {
    "NZ": "Pacific/Auckland", "AU": "Australia/Sydney", "UK": "Europe/London", "GB": "Europe/London",
    "US": "America/New_York",
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
