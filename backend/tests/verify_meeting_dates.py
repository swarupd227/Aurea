"""Seeded meetings sit around today, and a meeting whose date has passed is never upcoming.

    .venv\\Scripts\\python.exe -m tests.verify_meeting_dates
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from app.models.engagement import Meeting
from seed.meeting_dates_backfill import CHEN_REVIEW, NOW, SEEDED_FROM, new_date

PASS: list[str] = []
FAIL: list[str] = []


def check(label, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def main() -> int:
    now = datetime.now(timezone.utc)
    patel = "Goals & cashflow check-in — The Patel Household"
    upcoming = Meeting(title=patel, status="scheduled", scheduled_at=SEEDED_FROM + timedelta(days=11))
    held = Meeting(title="Annual strategy review — The Chen Family", status="completed",
                   scheduled_at=SEEDED_FROM - timedelta(days=46))
    redated = Meeting(title=patel, status="scheduled", scheduled_at=SEEDED_FROM + timedelta(days=12))
    chen = Meeting(title=CHEN_REVIEW, status="prepped", scheduled_at=datetime(2026, 7, 9, 10, tzinfo=timezone.utc))
    chen_held = Meeting(title=CHEN_REVIEW, status="completed", scheduled_at=chen.scheduled_at)
    unscheduled = Meeting(title=patel, status="scheduled", scheduled_at=None)

    print("\n=== a meeting not held whose date has gone by ===")
    check("is past its date", upcoming.date_passed(now), True)
    check("a held meeting is not", held.date_passed(now), False)
    check("nor one with no date", unscheduled.date_passed(now), False)

    print("\n=== the backfill moves seeded meetings to the same offset from today ===")
    moved = new_date(upcoming, now)
    check("an upcoming one is upcoming again", (moved == NOW + timedelta(days=11), moved > now), (True, True))
    check("a held one stays in the past", new_date(held, now), NOW - timedelta(days=46))
    check("one someone re-dated is left alone", new_date(redated, now), None)
    chen_moved = new_date(chen, now)
    check("the Chen review, never held, is three days out", (chen_moved > now, chen_moved.hour), (True, 10))
    check("but not once it was held", new_date(chen_held, now), None)
    upcoming.scheduled_at = moved
    check("a second run moves nothing", new_date(upcoming, now), None)
    check("and the moved meeting is not past its date", upcoming.date_passed(now), False)

    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
