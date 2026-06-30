"""Documented analytics assumptions (fee/cost/tenure/held-away schedules).

These are transparent, firm-tunable defaults — the analytics equivalent of the capital-market
assumptions in the planning engine. A firm would confirm these in a discovery / data-readiness
assessment; here they give the practice & client analytics realistic, reconcilable inputs."""
from __future__ import annotations

# Annual advisory fee as a fraction of AUM, by client segment.
FEE_SCHEDULE = {
    "private_wealth": 0.0090,
    "mass_affluent": 0.0110,
    "for_purpose": 0.0060,
    "institutional": 0.0045,
    "next_gen": 0.0080,
}

# Annual cost-to-serve model.
COST_BASE_PER_CLIENT = 1800.0       # relationship overhead
COST_PER_ACCOUNT = 250.0            # operations / custody admin
COST_ALTERNATIVES_SURCHARGE = 400.0  # extra cost where the book holds alternatives

# Expected client tenure (years) for lifetime-value, by segment.
TENURE_YEARS = {
    "private_wealth": 14, "mass_affluent": 9, "for_purpose": 20,
    "institutional": 12, "next_gen": 25,
}
CLV_DISCOUNT_RATE = 0.06

# Share of a client's TOTAL wealth that the firm typically holds, by segment
# (used to estimate held-away assets and wallet-share when no real feed value is present).
WALLET_SHARE_DEFAULT = {
    "private_wealth": 0.55, "mass_affluent": 0.70, "for_purpose": 0.85,
    "institutional": 0.60, "next_gen": 0.40,
}

# Segmentation tiers by AUM (NZD).
def value_tier(aum: float) -> str:
    if aum >= 2_000_000:
        return "Platinum"
    if aum >= 500_000:
        return "Gold"
    if aum >= 100_000:
        return "Silver"
    return "Bronze"


def fee_rate(segment: str) -> float:
    return FEE_SCHEDULE.get(segment, 0.0090)


def tenure(segment: str) -> int:
    return TENURE_YEARS.get(segment, 10)


def wallet_share_default(segment: str) -> float:
    return WALLET_SHARE_DEFAULT.get(segment, 0.6)
