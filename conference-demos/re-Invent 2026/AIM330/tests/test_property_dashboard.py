# tests/test_property_dashboard.py
# Property 14: Spend color classification
# Feature: champagne-ai, Property 14: Spend color classification
# Validates: Requirements 7.7

import sys
import os
from decimal import Decimal

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard


@settings(max_examples=100)
@given(
    spend=st.decimals(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
)
def test_spend_zero_always_yields_normal(spend):
    """spend == 0 always yields 'normal'.

    Tag: # Feature: champagne-ai, Property 14: Spend color classification
    """
    color = dashboard.get_spend_color(Decimal("0"))
    assert color == "normal"


@settings(max_examples=200)
@given(
    spend=st.decimals(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    warning_threshold=st.decimals(
        min_value="0.01", max_value="49", allow_nan=False, allow_infinity=False
    ),
    delta=st.decimals(
        min_value="0.01", max_value="10", allow_nan=False, allow_infinity=False
    ),
)
def test_spend_color_classification_with_custom_thresholds(spend, warning_threshold, delta):
    """Property 14: color is correct for any valid warning/danger threshold pair.

    Tag: # Feature: champagne-ai, Property 14: Spend color classification
    """
    import config

    danger_threshold = warning_threshold + delta

    original_warning = config.WARNING_SPEND_USD
    original_danger  = config.DANGER_SPEND_USD

    config.WARNING_SPEND_USD = warning_threshold
    config.DANGER_SPEND_USD  = danger_threshold

    try:
        color = dashboard.get_spend_color(spend)

        if spend >= danger_threshold:
            assert color == "red", (
                f"Expected 'red' for spend={spend} >= danger={danger_threshold}, got '{color}'"
            )
        elif spend >= warning_threshold:
            assert color == "yellow", (
                f"Expected 'yellow' for spend={spend} >= warning={warning_threshold}, got '{color}'"
            )
        else:
            assert color == "normal", (
                f"Expected 'normal' for spend={spend} < warning={warning_threshold}, got '{color}'"
            )

    finally:
        config.WARNING_SPEND_USD = original_warning
        config.DANGER_SPEND_USD  = original_danger


@settings(max_examples=100)
@given(
    spend=st.decimals(min_value=0, max_value="0.9999", allow_nan=False, allow_infinity=False),
)
def test_below_warning_threshold_is_normal(spend):
    """spend strictly below WARNING_SPEND_USD yields 'normal'."""
    import config
    assume(spend < config.WARNING_SPEND_USD)
    color = dashboard.get_spend_color(spend)
    assert color == "normal"


@settings(max_examples=100)
@given(
    spend=st.decimals(min_value="1.00", max_value="4.99", allow_nan=False, allow_infinity=False),
)
def test_between_warning_and_danger_is_yellow(spend):
    """spend in [WARNING, DANGER) yields 'yellow'."""
    import config
    assume(spend >= config.WARNING_SPEND_USD)
    assume(spend < config.DANGER_SPEND_USD)
    color = dashboard.get_spend_color(spend)
    assert color == "yellow"


@settings(max_examples=100)
@given(
    spend=st.decimals(min_value="5.00", max_value="1000", allow_nan=False, allow_infinity=False),
)
def test_at_or_above_danger_is_red(spend):
    """spend >= DANGER_SPEND_USD yields 'red'."""
    import config
    assume(spend >= config.DANGER_SPEND_USD)
    color = dashboard.get_spend_color(spend)
    assert color == "red"
