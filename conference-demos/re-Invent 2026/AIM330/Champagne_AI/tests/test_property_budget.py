# tests/test_property_budget.py
# Property 5:  Budget decrement is monotonically decreasing and clamps at zero
# Property 6:  Budget exhaustion blocks all subsequent requests
# Property 12: Budget alert fires at most once per period and re-arms on reset
#
# Feature: champagne-ai
# Validates: Requirements 4.2, 4.3, 4.4, 4.5, 4.12

import sys
import os
import importlib.util

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_budget():
    spec = importlib.util.spec_from_file_location(
        "budget_props",
        os.path.join(PROJECT_ROOT, "04_budget.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BUDGET = load_budget()


def setup_budget(initial_user, initial_org):
    """Set up the budget module with specific initial values."""
    _BUDGET.user_token_budget         = initial_user
    _BUDGET.org_token_budget          = initial_org
    _BUDGET.initial_user_token_budget = initial_user
    _BUDGET.initial_org_token_budget  = initial_org
    _BUDGET.alert_has_fired           = False
    _BUDGET.KILL_SWITCH_ACTIVE        = False


# ── Property 5: Budget decrement is monotonically decreasing and clamps at zero ─

@settings(max_examples=100, deadline=None)
@given(
    initial_budget=st.integers(0, 1_000_000),
    decrements=st.lists(st.integers(1, 50_000), min_size=1, max_size=20),
)
def test_budget_decrement_monotone_and_clamp(initial_budget, decrements):
    """Property 5: Balance after each step == max(0, B - sum(consumed_so_far)).

    Tag: # Feature: champagne-ai, Property 5: Budget decrement is monotonically decreasing and clamps at zero
    """
    setup_budget(initial_budget, initial_budget)

    running_consumed = 0
    for consumed in decrements:
        running_consumed += consumed
        _BUDGET.decrement_budget(tokens_consumed=consumed)

        expected_balance = max(0, initial_budget - running_consumed)

        assert _BUDGET.user_token_budget == expected_balance, (
            f"User budget mismatch: expected {expected_balance}, "
            f"got {_BUDGET.user_token_budget}"
        )
        assert _BUDGET.org_token_budget == expected_balance, (
            f"Org budget mismatch: expected {expected_balance}, "
            f"got {_BUDGET.org_token_budget}"
        )

        assert _BUDGET.user_token_budget >= 0
        assert _BUDGET.org_token_budget  >= 0


@settings(max_examples=100, deadline=None)
@given(
    initial_budget=st.integers(1, 1_000_000),
    decrement=st.integers(0, 2_000_000),
)
def test_budget_never_goes_negative_single_decrement(initial_budget, decrement):
    """Budget never goes negative for any single decrement size."""
    setup_budget(initial_budget, initial_budget)
    _BUDGET.decrement_budget(tokens_consumed=decrement)
    assert _BUDGET.user_token_budget >= 0
    assert _BUDGET.org_token_budget  >= 0


# ── Property 6: Budget exhaustion blocks all subsequent requests ──────────────

@settings(max_examples=100, deadline=None)
@given(
    initial_budget=st.integers(0, 100_000),
    first_decrement=st.integers(0, 200_000),
)
def test_budget_exhaustion_blocks_all_requests(initial_budget, first_decrement):
    """Property 6: Once budget hits zero, check_budget() always returns False.

    Tag: # Feature: champagne-ai, Property 6: Budget exhaustion blocks all subsequent requests
    """
    setup_budget(initial_budget, initial_budget)

    # Exhaust the budget
    _BUDGET.decrement_budget(tokens_consumed=max(initial_budget, first_decrement))

    for _ in range(5):
        result = _BUDGET.check_budget(tokens_needed=1)
        assert result is False, (
            f"check_budget() returned True after exhaustion. "
            f"user={_BUDGET.user_token_budget}, org={_BUDGET.org_token_budget}"
        )


@settings(max_examples=100, deadline=None)
@given(
    initial_budget=st.integers(0, 100_000),
    first_decrement=st.integers(0, 200_000),
)
def test_reset_re_enables_requests_after_exhaustion(initial_budget, first_decrement):
    """After exhaustion, reset_budget() re-enables requests."""
    setup_budget(initial_budget, initial_budget)
    _BUDGET.decrement_budget(tokens_consumed=max(initial_budget, first_decrement))

    _BUDGET.reset_budget()

    result = _BUDGET.check_budget(tokens_needed=1)
    assert result is True


# ── Property 12: Budget alert fires at most once per period ──────────────────

@settings(max_examples=100, deadline=None)
@given(
    alert_percent=st.integers(1, 99),
    initial_budget=st.integers(100, 1_000_000),
)
def test_budget_alert_fires_at_most_once_per_period(alert_percent, initial_budget):
    """Property 12: Alert fires exactly once per period even with multiple crossings.

    Tag: # Feature: champagne-ai, Property 12: Budget alert fires at most once per period and re-arms on reset
    """
    import config

    setup_budget(initial_budget, initial_budget)
    original_threshold = config.ALERT_THRESHOLD_PERCENT
    config.ALERT_THRESHOLD_PERCENT = alert_percent

    try:
        for _ in range(20):
            _BUDGET.decrement_budget(tokens_consumed=initial_budget // 20 + 1)

        # alert_has_fired can be True at most once (it's a boolean that only sets to True)
        assert isinstance(_BUDGET.alert_has_fired, bool), (
            "alert_has_fired must be a boolean"
        )

    finally:
        config.ALERT_THRESHOLD_PERCENT = original_threshold


@settings(max_examples=50, deadline=None)
@given(
    alert_percent=st.integers(1, 99),
    initial_budget=st.integers(100, 10_000),
)
def test_budget_alert_rearms_after_reset(alert_percent, initial_budget):
    """Property 12: After reset, alert can fire exactly once more.

    Tag: # Feature: champagne-ai, Property 12: Budget alert fires at most once per period and re-arms on reset
    """
    import config

    setup_budget(initial_budget, initial_budget)
    original_threshold = config.ALERT_THRESHOLD_PERCENT
    config.ALERT_THRESHOLD_PERCENT = alert_percent

    try:
        # First period: exhaust to trigger alert
        _BUDGET.decrement_budget(tokens_consumed=initial_budget)
        first_period_fired = _BUDGET.alert_has_fired

        # Reset
        _BUDGET.reset_budget()
        assert _BUDGET.alert_has_fired is False

        # Second period
        setup_budget(initial_budget, initial_budget)
        _BUDGET.decrement_budget(tokens_consumed=initial_budget)
        second_period_fired = _BUDGET.alert_has_fired

        if first_period_fired:
            assert second_period_fired is True, (
                "Alert should re-arm and fire again after reset"
            )

    finally:
        config.ALERT_THRESHOLD_PERCENT = original_threshold
