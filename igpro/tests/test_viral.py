import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.viral import viral_label, viral_score  # noqa: E402


def test_formula_exact():
    # (5000 + 2*100 + 5*10) / 1000 = 5250/1000 = 5.25
    assert viral_score(5000, 100, 10, 1000) == 5.25


def test_formula_none_inputs_treated_as_zero():
    # عکس: بازدید ندارد (None/0)
    assert viral_score(None, 100, 10, 1000) == (200 + 50) / 1000


def test_zero_followers_returns_none():
    assert viral_score(5000, 100, 10, 0) is None
    assert viral_score(5000, 100, 10, None) is None


def test_invalid_inputs_return_none():
    assert viral_score("x", 1, 1, 10) is None
    assert viral_score(None, None, None, "abc") is None


def test_labels():
    assert "—" in viral_label(None)
    assert "فوق‌العاده" in viral_label(47.0)
    assert "بسیار" in viral_label(6.0)
    assert "ویروسی" in viral_label(2.0)
    assert "خوب" in viral_label(0.5)
    assert "متوسط" in viral_label(0.1)
