# tests/test_property_cost_accuracy.py
# Property 9: Cost calculation accuracy
# Feature: champagne-ai, Property 9: Cost calculation accuracy
# Validates: Requirements 1.4, 14.4, 14.5
#
# Tests that compute_request_cost() produces exact Decimal results matching
# the formula: (input_tokens / 1000) * input_cost + (output_tokens / 1000) * output_cost

import sys
import os
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


# ── Reference implementation of the formula (as specified in design.md) ────────

def compute_request_cost(
    input_tokens: int,
    output_tokens: int,
    input_cost_per_1k: Decimal,
    output_cost_per_1k: Decimal,
) -> Decimal:
    """Reference formula for cost calculation.

    Cost = (tokens / 1000) * price_per_1k for each of input and output.
    Uses Decimal arithmetic throughout to avoid floating-point drift.
    """
    input_cost  = (Decimal(input_tokens)  / 1000) * input_cost_per_1k
    output_cost = (Decimal(output_tokens) / 1000) * output_cost_per_1k
    return input_cost + output_cost


# ── Property 9: Cost calculation accuracy ─────────────────────────────────────

@settings(max_examples=100)
@given(
    input_tokens=st.integers(0, 1_000_000),
    output_tokens=st.integers(0, 1_000_000),
)
def test_compute_request_cost_matches_exact_formula_sonnet(input_tokens, output_tokens):
    """Property 9: compute_request_cost() equals the exact Decimal formula for Claude pricing."""
    # Tag: # Feature: champagne-ai, Property 9: Cost calculation accuracy
    input_cost_per_1k  = config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS
    output_cost_per_1k = config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS

    expected = (
        (Decimal(input_tokens)  / 1000) * input_cost_per_1k +
        (Decimal(output_tokens) / 1000) * output_cost_per_1k
    )

    actual = compute_request_cost(
        input_tokens,
        output_tokens,
        input_cost_per_1k,
        output_cost_per_1k,
    )

    assert actual == expected, (
        f"Cost mismatch for Sonnet: input={input_tokens}, output={output_tokens}, "
        f"expected={expected}, got={actual}"
    )


@settings(max_examples=100)
@given(
    input_tokens=st.integers(0, 1_000_000),
    output_tokens=st.integers(0, 1_000_000),
)
def test_compute_request_cost_matches_exact_formula_nova_lite(input_tokens, output_tokens):
    """Property 9: compute_request_cost() equals the exact Decimal formula for beer model pricing."""
    # Tag: # Feature: champagne-ai, Property 9: Cost calculation accuracy
    input_cost_per_1k  = config.BEER_INPUT_COST_PER_1K_TOKENS
    output_cost_per_1k = config.BEER_OUTPUT_COST_PER_1K_TOKENS

    expected = (
        (Decimal(input_tokens)  / 1000) * input_cost_per_1k +
        (Decimal(output_tokens) / 1000) * output_cost_per_1k
    )

    actual = compute_request_cost(
        input_tokens,
        output_tokens,
        input_cost_per_1k,
        output_cost_per_1k,
    )

    assert actual == expected, (
        f"Cost mismatch for beer model: input={input_tokens}, output={output_tokens}, "
        f"expected={expected}, got={actual}"
    )


@settings(max_examples=100)
@given(
    input_tokens=st.integers(0, 1_000_000),
    output_tokens=st.integers(0, 1_000_000),
)
def test_compute_request_cost_is_non_negative(input_tokens, output_tokens):
    """Cost is always >= 0 for any non-negative token counts and positive pricing."""
    result = compute_request_cost(
        input_tokens,
        output_tokens,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
        config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )
    assert result >= Decimal("0")


@settings(max_examples=100)
@given(
    input_tokens=st.integers(0, 1_000_000),
    output_tokens=st.integers(0, 1_000_000),
)
def test_compute_request_cost_returns_decimal_type(input_tokens, output_tokens):
    """compute_request_cost() must return a Decimal, never a float."""
    result = compute_request_cost(
        input_tokens,
        output_tokens,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
        config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )
    assert isinstance(result, Decimal), (
        f"Expected Decimal, got {type(result).__name__}"
    )


def test_compute_request_cost_zero_tokens():
    """Zero tokens should produce zero cost."""
    result = compute_request_cost(
        0,
        0,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
        config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )
    assert result == Decimal("0")


def test_compute_request_cost_known_values():
    """Spot-check: 1000 input + 1000 output at Claude pricing = 0.003 + 0.015 = 0.018."""
    result = compute_request_cost(
        1000,
        1000,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
        config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )
    assert result == Decimal("0.030000")


def test_compute_request_cost_known_values_beer():
    """Spot-check: 1000 input + 1000 output at beer model pricing = 0.00006 + 0.00024 = 0.00030."""
    result = compute_request_cost(
        1000,
        1000,
        config.BEER_INPUT_COST_PER_1K_TOKENS,
        config.BEER_OUTPUT_COST_PER_1K_TOKENS,
    )
    assert result == Decimal("0.000300")
