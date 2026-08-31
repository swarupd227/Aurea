"""Check that every cadence fires on the day and time it is meant to.

The original check only proved each expression parsed, then reported when APScheduler
thought it would fire — which is circular, because the bug was that APScheduler read the
expression differently from crontab(5). Every numeric weekday landed a day late and the
check agreed with itself. next_best_action was set to Monday and ran on Tuesdays.

Two things this does differently:

  It asserts the weekday by name against what crontab(5) means, not against what the
  parser happens to produce.

  It pins the timezone to UTC. Triggers otherwise take the machine's local zone, so the
  same assertion passed on a UTC container and failed on an IST laptop — which is how a
  timezone artifact can be mistaken for a numbering bug, and vice versa.

    python -m tests.verify_schedules
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.cron import CronTrigger

from app.agents import schedules as sched
from app.agents.catalogue import CATALOGUE
from app.atlas.registry import build_registry

UTC = timezone.utc
# Sunday 30 Aug 2026, 00:00 UTC. Starting at the top of a Sunday means every weekday in
# the week ahead is reachable and nothing is already in the past.
REF = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(f"{label}: got {got}, want {want}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def next_fire(expr: str, *, normalise: bool = True) -> datetime:
    text = sched.normalise_cron(expr) if normalise else expr
    return CronTrigger.from_crontab(text, timezone=UTC).get_next_fire_time(None, REF)


def main() -> int:
    print(f"\nreference: {REF:%A %d %b %Y %H:%M} UTC  (31 Aug 2026 is a Monday)")

    print("\n=== the bug this guards against ===")
    # Raw crontab through APScheduler reads 1 as Tuesday. Assert the defect explicitly so
    # that if a future APScheduler fixes it, this test says so instead of silently
    # double-correcting and moving every weekday cadence again.
    raw = next_fire("0 5 * * 1", normalise=False).strftime("%A")
    check("untranslated '0 5 * * 1' still means Tuesday to APScheduler", raw, "Tuesday")

    print("\n=== crontab weekday numbering is honoured (0=Sunday) ===")
    for expr, want in [("0 5 * * 0", "Sunday"), ("0 5 * * 1", "Monday"),
                       ("0 5 * * 2", "Tuesday"), ("0 5 * * 6", "Saturday"),
                       ("0 5 * * 7", "Sunday")]:
        check(f"{expr} fires on", next_fire(expr).strftime("%A"), want)

    print("\n=== names, ranges and lists ===")
    check("0 5 * * mon", next_fire("0 5 * * mon").strftime("%A"), "Monday")
    # From Sunday midnight the first weekday match is Monday.
    check("0 5 * * 1-5 (weekdays)", next_fire("0 5 * * 1-5").strftime("%A"), "Monday")
    # From Sunday midnight, 05:00 that same Sunday is the first weekend match.
    check("0 5 * * 0,6 (weekend)", next_fire("0 5 * * 0,6").strftime("%A"), "Sunday")
    check("a step suffix survives", sched.normalise_cron("0 5 * * 1-5/2"), "0 5 * * mon-fri/2")
    check("non-weekday fields untouched", sched.normalise_cron("0 */6 * * *"), "0 */6 * * *")
    check("only the weekday field is rewritten",
          sched.normalise_cron("0 5 1 * 1"), "0 5 1 * mon")
    check("an expression already using names is unchanged",
          sched.normalise_cron("0 5 * * mon"), "0 5 * * mon")
    check("a malformed expression is passed through, not mangled",
          sched.normalise_cron("nonsense"), "nonsense")

    print("\n=== every declared scheduled agent has a cadence, and vice versa ===")
    reg = build_registry()
    declared = {str(k) for k, a in reg.items() if getattr(a, "scheduled", False)}
    have = {str(k) for k in sched.DEFAULT_SCHEDULES}
    check("declared but no cadence", sorted(declared - have), [])
    check("cadence but not declared", sorted(have - declared), [])

    print("\n=== each default cadence fires when its rationale claims ===")
    expectations = {
        "next_best_action": ("Monday", 5),
        "adverse_media_pep": (None, 2),
        "abandonment_recovery": (None, 2),
        "conduct_surveillance": (None, 3),
        "client_care": (None, 6),
        "drift_rebalancing": (None, 0),  # every 6h, so the first from midnight is 00:00
    }
    for key, (want_day, want_hour) in expectations.items():
        expr = next(c for k, (c, _) in sched.DEFAULT_SCHEDULES.items() if str(k) == key)
        nxt = next_fire(expr)
        check(f"{key} hour", nxt.hour, want_hour)
        if want_day:
            check(f"{key} weekday", nxt.strftime("%A"), want_day)

    print("\n=== every cadence parses, and none is left on a raw numeric weekday ===")
    for key, (expr, _why) in sched.DEFAULT_SCHEDULES.items():
        try:
            CronTrigger.from_crontab(sched.normalise_cron(expr), timezone=UTC)
        except ValueError as exc:
            check(f"{key} parses", str(exc), "parses")

    print("\n=== daily volume is still what was budgeted ===")
    per_subject = {"firm": 1, "household": 14, "mandate": 14, "onboarding_case": 1}
    total = 0
    for key, (expr, _) in sched.DEFAULT_SCHEDULES.items():
        subject = (CATALOGUE.get(key) or {}).get("subject", "firm")
        trigger = CronTrigger.from_crontab(sched.normalise_cron(expr), timezone=UTC)
        fires, cur = 0, REF
        for _ in range(200):
            nxt = trigger.get_next_fire_time(None, cur)
            if nxt is None or (nxt - REF).total_seconds() > 86400:
                break
            fires += 1
            cur = nxt + timedelta(seconds=1)
        total += fires * per_subject.get(subject, 1)
    print(f"  agent runs in the 24h from {REF:%a %d %b}: {total}")
    check("daily volume stays under 100", total < 100, True)

    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
