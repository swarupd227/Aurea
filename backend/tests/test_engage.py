"""Unit tests for the Advise & Engage / Manage & Optimise engines — pure, no DB."""
from app.agents._signals import book_signals, goal_projections, life_events
from app.agents.meeting_companion import _parse_amount, MeetingCompanionAgent
from app.agents.client_care import _age


def _brain(**over):
    base = {
        "household": {"id": "h1", "name": "Test Family", "segment": "private_wealth",
                      "values": {"themes": ["clean energy"]}},
        "persons": [
            {"id": "p1", "full_name": "Older Person", "preferred_name": "Older",
             "is_next_gen": False, "profile": {"life_stage": "pre-retirement"}, "date_of_birth": "1960-01-01"},
            {"id": "p2", "full_name": "Young Heir", "preferred_name": "Young",
             "is_next_gen": True, "profile": {}, "date_of_birth": "1998-01-01"},
        ],
        "goals": [{"name": "Retirement", "kind": "retirement", "target_amount": 5_000_000,
                   "assumptions": {"years": 5, "funding_share": 1.0}}],
        "accounts": [{"positions": [
            {"instrument": "AAPL", "asset_class": "equity", "market_value": 700000,
             "unrealised_gain": -2000, "name": "Apple"},
            {"instrument": "AGG", "asset_class": "fixed_income", "market_value": 100000,
             "unrealised_gain": 1000, "name": "Bonds"},
        ]}],
        "totals": {"total_value": 1000000, "by_asset_class": {"equity": 700000, "fixed_income": 100000, "cash": 200000}, "data_confidence": 0.95},
    }
    base.update(over)
    return base


def test_book_signals_detects_concentration_loss_idlecash_heir():
    sigs = {s["kind"] for s in book_signals(_brain())}
    assert "concentration" in sigs       # AAPL 700k / 900k = 78%
    assert "loss_harvest" in sigs        # AAPL at a loss
    assert "idle_cash" in sigs           # cash 100k / 900k > 15%
    assert "intergenerational" in sigs   # next-gen heir present


def test_goal_projection_flags_unreachable_goal():
    g = goal_projections(_brain())[0]
    assert g["on_track"] is False        # $900k can't reach $5m in 5y
    assert 0.0 <= g["probability"] <= 1.0


def test_life_events_surfaces_stage_and_nextgen():
    events = life_events(_brain())
    assert any("pre retirement" in e or "pre-retirement" in e for e in events)
    assert any("next-generation" in e for e in events)


def test_parse_amount_handles_k_and_commas():
    assert _parse_amount("about $150k") == 150_000
    assert _parse_amount("$1,250,000 transfer") == 1_250_000
    assert _parse_amount("no figure here") is None


def test_age_computation():
    assert _age("1960-01-01") >= 60
    assert _age(None) is None
