# tests/test_property_report_cost.py
# Property 11: report_cost() never raises regardless of dashboard state
# Feature: champagne-ai, Property 11: report_cost() never raises regardless of dashboard state
# Validates: Requirements 10.13

import sys
import os
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
import requests as req

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard_client


# ── Property 11: report_cost() never raises ────────────────────────────────────

@settings(max_examples=100)
@given(
    dashboard_available=st.booleans(),
    model_name=st.text(min_size=1, max_size=100),
    input_tokens=st.integers(0, 1_000_000),
    output_tokens=st.integers(0, 1_000_000),
    cost_str=st.decimals(
        min_value=0,
        max_value=1000,
        allow_nan=False,
        allow_infinity=False,
    ),
    naive_cost_str=st.decimals(
        min_value=0,
        max_value=1000,
        allow_nan=False,
        allow_infinity=False,
    ),
    request_label=st.text(min_size=0, max_size=200),
)
def test_report_cost_never_raises(
    dashboard_available,
    model_name,
    input_tokens,
    output_tokens,
    cost_str,
    naive_cost_str,
    request_label,
):
    """Property 11: report_cost() never raises regardless of dashboard state or input values.

    Tag: # Feature: champagne-ai, Property 11: report_cost() never raises regardless of dashboard state
    """
    cost_usd = Decimal(str(cost_str))
    naive_cost_usd = Decimal(str(naive_cost_str))

    if dashboard_available:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post = MagicMock(return_value=mock_response)
    else:
        mock_post = MagicMock(
            side_effect=req.exceptions.ConnectionError("Simulated connection refused")
        )

    with patch("dashboard_client.requests.post", mock_post):
        try:
            result = dashboard_client.report_cost(
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                naive_cost_usd=naive_cost_usd,
                request_label=request_label,
            )
            assert result is None
        except Exception as exc:
            pytest.fail(
                f"report_cost() raised an unexpected exception: {type(exc).__name__}: {exc}"
            )


@settings(max_examples=100)
@given(
    exception_type=st.sampled_from([
        req.exceptions.ConnectionError,
        req.exceptions.Timeout,
        req.exceptions.HTTPError,
        req.exceptions.RequestException,
        OSError,
        RuntimeError,
        Exception,
    ]),
    model_name=st.text(min_size=1, max_size=50),
    input_tokens=st.integers(0, 100_000),
    output_tokens=st.integers(0, 100_000),
)
def test_report_cost_never_raises_on_varied_exceptions(
    exception_type,
    model_name,
    input_tokens,
    output_tokens,
):
    """report_cost() swallows any exception type from requests.post."""
    with patch(
        "dashboard_client.requests.post",
        side_effect=exception_type("Simulated failure"),
    ):
        try:
            result = dashboard_client.report_cost(
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=Decimal("0.001"),
                naive_cost_usd=Decimal("0.005"),
                request_label="test",
            )
            assert result is None
        except Exception as exc:
            pytest.fail(
                f"report_cost() raised {type(exc).__name__} for exception type "
                f"{exception_type.__name__}: {exc}"
            )
