"""Default run cadence for the agents that declare `scheduled = True`.

These are seeded into `AgentConfig.schedule_cron` / `schedule_enabled` and are editable
per firm in Admin — the worker reads the config, never this file, so a firm can change
cadence without a deploy. This is only the starting point.

Cadence is a product decision, not a technical default. Two things shaped these:

  Fan-out. Eight of the thirteen run per household. Putting all of them on a daily timer
  across a real book would generate far more recommendations than an adviser can read,
  and an ignored queue is worse than no queue.

  Rate of change. Drift moves with the market and is checked through the day. A 12-month
  reverse-churning threshold does not change between Tuesdays. Tax planning is seasonal.
  Matching cadence to how fast the underlying thing actually moves is what keeps the
  output worth reading.

Runs are staggered across hours and days of the month so the book is not swept by
everything at once.
"""
from __future__ import annotations

from app.models.enums import AgentKey

# agent -> (cron, why this cadence)
DEFAULT_SCHEDULES: dict[str, tuple[str, str]] = {
    # ── Through the day ──────────────────────────────────────────────────────
    AgentKey.DRIFT_REBALANCING: (
        "0 */6 * * *",
        "Drift moves with the market; four checks a day catches a band breach the same day.",
    ),

    # ── Daily, off-peak ──────────────────────────────────────────────────────
    AgentKey.ADVERSE_MEDIA_PEP: (
        "30 2 * * *",
        "L200: rescreening runs continuously against list updates. Sanctions lists change daily.",
    ),
    AgentKey.ABANDONMENT_RECOVERY: (
        "45 2 * * *",
        "Stalled applications age daily; catching one a day late costs a day of the SLA.",
    ),
    AgentKey.CONDUCT_SURVEILLANCE: (
        "0 3 * * *",
        "Supervises the recommendations made that day — surveillance that lags is not supervision.",
    ),
    AgentKey.CLIENT_CARE: (
        "0 6 * * *",
        "Volatility and at-risk signals are time-sensitive; before the adviser's day starts.",
    ),

    # ── Weekly ───────────────────────────────────────────────────────────────
    AgentKey.NEXT_BEST_ACTION: (
        "0 5 * * 1",
        "Sets up the adviser's week. Daily would re-surface the same actions before anyone acted.",
    ),

    # ── Monthly, staggered across the first week ─────────────────────────────
    AgentKey.TAX_INTELLIGENCE: (
        "0 4 1 * *",
        "Seasonal rather than continuous — harvesting and allowances are managed against year end.",
    ),
    AgentKey.REGULATORY_COUNTDOWN: (
        "0 4 2 * *",
        "Counts down to fixed statutory dates; a month's resolution is ample.",
    ),
    AgentKey.WALLET_SHARE_SCOUT: (
        "0 5 3 * *",
        "Held-away balances move slowly and the conversation is periodic, not reactive.",
    ),
    AgentKey.BEHAVIOURAL_FINANCE: (
        "0 5 4 * *",
        "Needs decision history to accumulate before a pattern means anything.",
    ),
    AgentKey.ESTATE_SUCCESSION: (
        "0 4 5 * *",
        "Governance and succession gaps change on the scale of months.",
    ),
    AgentKey.GLIDE_PATH: (
        "0 4 6 * *",
        "Step-down schedules are annual; monthly is already generous.",
    ),
    AgentKey.REVERSE_CHURNING: (
        "0 3 7 * *",
        "A 12-month inactivity threshold does not need checking more often than monthly.",
    ),
}


def default_for(agent_key) -> tuple[str, str] | None:
    return DEFAULT_SCHEDULES.get(agent_key)
