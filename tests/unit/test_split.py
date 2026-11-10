"""Invariant 2: one pure split function (P2 D1) + request defaults (BACKEND_SCHEMA §8).

Properties (Hypothesis): sum == amount; never above a balance; only selected invoices;
oldest due first (no later invoice gets money while an earlier selected one still has
balance); deterministic. Plus the demo examples and every error code.
"""

from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.core.dates import offset, today_ny
from backend.core.split import OpenInvoice, SplitError, resolve_request, split_payment

D = today_ny()

# The §Seed invoices: INV-1038 (due D-18), INV-1042 (D+10), INV-1051 (D+25)
SEED = [
    OpenInvoice(id=1038, due_on=offset(D, -18), issued_on=offset(D, -48), balance_cents=350000),
    OpenInvoice(id=1042, due_on=offset(D, 10), issued_on=offset(D, -20), balance_cents=350000),
    OpenInvoice(id=1051, due_on=offset(D, 25), issued_on=offset(D, -5), balance_cents=142000),
]
ALL = [i.id for i in SEED]


def as_dict(split):
    return dict(split)


# ---------- examples ----------


def test_demo_5000_pays_oldest_first():
    assert split_payment(500000, SEED, ALL) == [(1038, 350000), (1042, 150000)]


def test_full_8420_pays_everything():
    assert split_payment(842000, SEED, ALL) == [(1038, 350000), (1042, 350000), (1051, 142000)]


def test_five_cents():
    assert split_payment(5, SEED, ALL) == [(1038, 5)]


def test_exact_balance_of_selection():
    assert split_payment(492000, SEED, [1042, 1051]) == [(1042, 350000), (1051, 142000)]


def test_only_selected_even_if_older_exists():
    assert split_payment(100000, SEED, [1051]) == [(1051, 100000)]


def test_input_order_does_not_matter():
    assert split_payment(500000, list(reversed(SEED)), [1051, 1042, 1038]) == [
        (1038, 350000),
        (1042, 150000),
    ]


def test_ties_on_due_date_go_to_issued_on_then_id():
    due = offset(D, 5)
    inv = [
        OpenInvoice(id=3, due_on=due, issued_on=offset(D, -10), balance_cents=100),
        OpenInvoice(id=2, due_on=due, issued_on=offset(D, -20), balance_cents=100),
        OpenInvoice(id=1, due_on=due, issued_on=offset(D, -10), balance_cents=100),
    ]
    assert split_payment(150, inv, [1, 2, 3]) == [(2, 100), (1, 50)]


def test_zero_balance_invoices_get_nothing():
    inv = [
        *SEED,
        OpenInvoice(id=9, due_on=offset(D, -90), issued_on=offset(D, -99), balance_cents=0),
    ]
    assert split_payment(100, inv, [9, 1038]) == [(1038, 100)]


# ---------- errors ----------


@pytest.mark.parametrize("amount", [0, -1, -350000])
def test_amount_not_positive(amount):
    with pytest.raises(SplitError) as e:
        split_payment(amount, SEED, ALL)
    assert e.value.code == "AMOUNT_NOT_POSITIVE"


def test_amount_exceeds_selected():
    with pytest.raises(SplitError) as e:
        split_payment(350001, SEED, [1038])
    assert e.value.code == "AMOUNT_EXCEEDS_SELECTED"


@pytest.mark.parametrize("selected", [[], [9999]])
def test_nothing_selected(selected):
    with pytest.raises(SplitError) as e:
        split_payment(100, SEED, selected)
    assert e.value.code == "NOTHING_SELECTED"


def test_split_error_is_a_value_error():
    assert issubclass(SplitError, ValueError)


@pytest.mark.parametrize("bad", [1.5, "100", True])
def test_amount_must_be_int_cents(bad):
    with pytest.raises(TypeError):
        split_payment(bad, SEED, ALL)  # type: ignore[arg-type]


# ---------- properties ----------

invoices_st = st.lists(
    st.builds(
        lambda i, due, issued, bal: OpenInvoice(
            id=i, due_on=offset(D, due), issued_on=offset(D, issued), balance_cents=bal
        ),
        st.integers(1, 10_000),
        st.integers(-120, 60),
        st.integers(-200, 0),
        st.integers(0, 10_000_000),
    ),
    min_size=1,
    max_size=8,
    unique_by=lambda inv: inv.id,
)


@st.composite
def cases(draw):
    invoices = draw(invoices_st)
    selected = draw(st.lists(st.sampled_from([i.id for i in invoices]), unique=True, min_size=1))
    total = sum(i.balance_cents for i in invoices if i.id in selected)
    if total == 0:
        amount = draw(st.integers(1, 100))
    else:
        amount = draw(st.integers(1, total))
    return invoices, selected, amount, total


def _ordered(invoices):
    return sorted(invoices, key=lambda i: (i.due_on, i.issued_on, i.id))


@settings(max_examples=400, deadline=None)
@given(cases())
def test_properties(case):
    invoices, selected, amount, total = case
    if total == 0:
        with pytest.raises(SplitError):
            split_payment(amount, invoices, selected)
        return
    out = split_payment(amount, invoices, selected)
    got = as_dict(out)
    by_id = {i.id: i for i in invoices}

    assert sum(got.values()) == amount  # sum
    assert all(isinstance(c, int) and c > 0 for c in got.values())  # int cents, no zeros
    assert all(got[k] <= by_id[k].balance_cents for k in got)  # cap
    assert set(got) <= set(selected)  # only selected
    # oldest first: once an invoice is not fully paid, no later selected invoice gets money
    chosen = [i for i in _ordered(invoices) if i.id in selected and i.balance_cents > 0]
    seen_partial = False
    for inv in chosen:
        paid = got.get(inv.id, 0)
        if seen_partial:
            assert paid == 0
        if paid < inv.balance_cents:
            seen_partial = True
    assert [k for k, _ in out] == [i.id for i in chosen if i.id in got]  # output order
    assert split_payment(amount, list(reversed(invoices)), list(reversed(selected))) == out


@settings(max_examples=200, deadline=None)
@given(cases(), st.integers(1, 1_000_000))
def test_over_selected_balance_always_rejected(case, extra):
    invoices, selected, _, total = case
    with pytest.raises(SplitError):
        split_payment(total + extra, invoices, selected)


# ---------- resolve_request (BACKEND_SCHEMA §8 defaults) ----------


def test_defaults_nothing_given_is_all_open_full_balance():
    assert resolve_request(SEED, None, None) == (842000, ALL)


def test_defaults_ids_only_is_their_full_balance():
    assert resolve_request(SEED, [1051, 1038], None) == (492000, [1051, 1038])


def test_defaults_amount_only_is_all_open():
    assert resolve_request(SEED, None, 500000) == (500000, ALL)


def test_defaults_both_given_are_used():
    assert resolve_request(SEED, [1042], 1000) == (1000, [1042])


def test_resolve_rejects_unknown_or_paid_invoice():
    paid = OpenInvoice(id=7, due_on=D, issued_on=D - timedelta(days=1), balance_cents=0)
    for ids in ([9999], [7]):
        with pytest.raises(SplitError) as e:
            resolve_request([*SEED, paid], ids, None)
        assert e.value.code == "INVOICE_NOT_OPEN"


def test_resolve_empty_list_is_nothing_selected():
    with pytest.raises(SplitError) as e:
        resolve_request(SEED, [], None)
    assert e.value.code == "NOTHING_SELECTED"


def test_resolve_all_paid_is_nothing_selected():
    paid = [i._replace(balance_cents=0) for i in SEED]
    with pytest.raises(SplitError) as e:
        resolve_request(paid, None, None)
    assert e.value.code == "NOTHING_SELECTED"
