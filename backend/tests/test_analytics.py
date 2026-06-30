"""Unit tests for the analytics calculators (pure assumption maths, no DB)."""
from app.aurea_core.analytics import assumptions as A
from app.aurea_core.analytics.client_intelligence import _annuity_pv


def test_value_tiers():
    assert A.value_tier(3_000_000) == "Platinum"
    assert A.value_tier(800_000) == "Gold"
    assert A.value_tier(200_000) == "Silver"
    assert A.value_tier(50_000) == "Bronze"


def test_fee_and_tenure_lookups():
    assert A.fee_rate("private_wealth") == 0.0090
    assert A.fee_rate("unknown_segment") == 0.0090  # default
    assert A.tenure("for_purpose") == 20
    assert 0 < A.wallet_share_default("mass_affluent") <= 1


def test_clv_annuity_pv():
    # PV of a $1,000 annual fee over 10 years at 6% should be < undiscounted $10,000 and > 0.
    pv = _annuity_pv(1000, 10, 0.06)
    assert 0 < pv < 10_000
    # Zero discount → simple multiplication.
    assert _annuity_pv(1000, 10, 0.0) == 10_000


def test_wallet_share_implies_held_away():
    # If the firm holds 55% of total wealth, held-away is the remaining 45%.
    aum = 1_000_000
    share = A.wallet_share_default("private_wealth")  # 0.55
    total = aum / share
    held = total - aum
    assert round(aum / total, 2) == 0.55
    assert held > 0
