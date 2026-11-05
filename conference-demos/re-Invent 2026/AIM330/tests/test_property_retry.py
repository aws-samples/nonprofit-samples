# tests/test_property_retry.py
# Property 13: Retry count and backoff correctness
# Feature: champagne-ai, Property 13: Retry count and backoff correctness
# Validates: Requirements 9.3, 9.4

import sys
import os
from unittest.mock import MagicMock, patch
import importlib.util

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED_DELAYS = [0.5, 1.0, 2.0]  # 500ms, 1s, 2s


def load_naive_module():
    """Load 01_naive.py as a module without executing main code."""
    spec = importlib.util.spec_from_file_location(
        "naive_retry",
        os.path.join(PROJECT_ROOT, "01_naive.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level to avoid repeated I/O per hypothesis example
_NAIVE = load_naive_module()
call_bedrock_with_retry = _NAIVE.call_bedrock_with_retry


@settings(max_examples=100, deadline=None)
@given(failure_count=st.integers(1, 3))
def test_total_attempt_count_equals_failures_plus_one(failure_count):
    """Property 13: When Bedrock fails k times then succeeds, total call count = k + 1.

    Tag: # Feature: champagne-ai, Property 13: Retry count and backoff correctness
    """
    import config
    mock_response = {"output": {"message": {"content": [{"text": "ok"}]}}}

    call_results = [Exception("transient error")] * failure_count + [mock_response]
    mock_client = MagicMock()
    mock_client.converse.side_effect = call_results

    with patch("time.sleep"):
        result = call_bedrock_with_retry(
            mock_client,
            config.CHAMPAGNE_MODEL_ID,
            [{"role": "user", "content": [{"text": "test"}]}],
        )

    assert result == mock_response
    assert mock_client.converse.call_count == failure_count + 1


@settings(max_examples=100, deadline=None)
@given(failure_count=st.integers(1, 3))
def test_backoff_delay_sequence_matches_spec(failure_count):
    """Property 13: Retry delays are exactly [0.5, 1.0, 2.0] for up to 3 failures.

    Tag: # Feature: champagne-ai, Property 13: Retry count and backoff correctness
    """
    import config
    mock_response = {"output": {"message": {"content": [{"text": "ok"}]}}}

    call_results = [Exception("fail")] * failure_count + [mock_response]
    mock_client = MagicMock()
    mock_client.converse.side_effect = call_results

    sleep_calls = []
    with patch("time.sleep", side_effect=lambda t: sleep_calls.append(t)):
        call_bedrock_with_retry(
            mock_client,
            config.CHAMPAGNE_MODEL_ID,
            [{"role": "user", "content": [{"text": "test"}]}],
        )

    assert sleep_calls == EXPECTED_DELAYS[:failure_count], (
        f"For {failure_count} failures: expected delays {EXPECTED_DELAYS[:failure_count]}, "
        f"got {sleep_calls}"
    )


@settings(max_examples=50, deadline=None)
@given(failure_count=st.integers(1, 3))
def test_all_failures_returns_none_after_max_retries(failure_count):
    """When all MAX_RETRIES+1 attempts fail, return None."""
    import config

    mock_client = MagicMock()
    mock_client.converse.side_effect = Exception("always fails")

    with patch("time.sleep"):
        result = call_bedrock_with_retry(
            mock_client,
            config.CHAMPAGNE_MODEL_ID,
            [{"role": "user", "content": [{"text": "test"}]}],
        )

    assert result is None
    assert mock_client.converse.call_count == config.MAX_RETRIES + 1


def test_no_sleep_on_first_success():
    """When Bedrock succeeds on first attempt, no sleep is called."""
    import config
    mock_response = {"output": {"message": {"content": [{"text": "ok"}]}}}
    mock_client = MagicMock()
    mock_client.converse.return_value = mock_response

    sleep_calls = []
    with patch("time.sleep", side_effect=lambda t: sleep_calls.append(t)):
        call_bedrock_with_retry(
            mock_client,
            config.CHAMPAGNE_MODEL_ID,
            [{"role": "user", "content": [{"text": "test"}]}],
        )

    assert sleep_calls == []
    assert mock_client.converse.call_count == 1
