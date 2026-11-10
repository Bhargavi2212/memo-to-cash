"""Invariant 1: money is integer cents; parsing and PayPal formatting never use floats."""

import pytest

from backend.core.money import fmt_paypal, to_cents


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("1150", 115000),
        ("1150.5", 115050),
        ("1150.50", 115050),
        ("0.05", 5),
        ("0", 0),
        ("2350", 235000),
    ],
)
def test_to_cents_parses_dollar_strings(text, cents):
    assert to_cents(text) == cents
    assert isinstance(to_cents(text), int)


@pytest.mark.parametrize("text", ["1.001", "-1", "1e3", "", " ", "1,150", "$5", "1.", ".5", "abc"])
def test_to_cents_rejects_bad_strings(text):
    with pytest.raises(ValueError):
        to_cents(text)


@pytest.mark.parametrize("value", [1150.5, 1150, None, True])
def test_to_cents_rejects_non_strings(value):
    with pytest.raises(TypeError):
        to_cents(value)  # type: ignore[arg-type]


def test_to_cents_strips_surrounding_spaces():
    assert to_cents(" 800 ") == 80000


@pytest.mark.parametrize(
    ("cents", "text"),
    [
        (115000, "1150.00"),
        (5, "0.05"),
        (0, "0.00"),
        (842000, "8420.00"),
        (-2000, "-20.00"),
        (-5, "-0.05"),
    ],
)
def test_fmt_paypal(cents, text):
    assert fmt_paypal(cents) == text


@pytest.mark.parametrize("value", [1150.0, "115000", True])
def test_fmt_paypal_rejects_non_int(value):
    with pytest.raises(TypeError):
        fmt_paypal(value)  # type: ignore[arg-type]


def test_round_trip():
    for cents in (1, 99, 100, 123456789):
        assert to_cents(fmt_paypal(cents)) == cents
