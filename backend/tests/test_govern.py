"""Unit tests for Protect & Govern — quality scoring & communications-surveillance logic."""


def _score(dismiss=0.0, rollback=0.0, high_flag=0.0, modify=0.0):
    s = 1.0 - dismiss * 0.5 - rollback * 0.6 - high_flag * 0.7 - modify * 0.1
    return max(0.0, min(1.0, s))


def test_quality_score_penalises_regression():
    # A healthy agent: all approved.
    assert _score() == 1.0
    # Heavy dismissal + rollback + flags should drop below the 0.5 'regressed' line.
    bad = _score(dismiss=0.5, rollback=0.3, high_flag=0.5)
    assert bad < 0.5


def test_grade_thresholds():
    def grade(score, decided):
        if decided < 3:
            return "unrated"
        if score >= 0.75:
            return "healthy"
        if score >= 0.5:
            return "watch"
        return "regressed"

    assert grade(0.9, 5) == "healthy"
    assert grade(0.6, 5) == "watch"
    assert grade(0.3, 5) == "regressed"
    assert grade(0.3, 2) == "unrated"  # too few samples to act


def test_comms_surveillance_detects_overpromising():
    banned = ["guarantee", "guaranteed", "risk-free", "riskless", "no risk", "will definitely",
              "can't lose", "cannot lose", "always outperform", "sure thing"]
    good = "your plan is built to withstand market dips; your adviser is watching it closely.".lower()
    bad = "this strategy is risk-free and will definitely outperform.".lower()
    assert not [b for b in banned if b in good]
    assert sorted({b for b in banned if b in bad}) == ["risk-free", "will definitely"]
