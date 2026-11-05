# tests/test_property_router.py
# Property tests for 02_router.py
#
# Property 1:  Classifier always returns a valid task type (including timeout fallback)
# Property 2:  Model Router respects routing table, kill switch, and unknown-label fallback
# Property 10: Naive baseline always uses Claude pricing for savings calculation
#
# Feature: champagne-ai
# Validates: Requirements 2.1, 2.2, 2.3, 2.7, 2.8, 4.6, 5.1-5.5, 14.6

import sys
import os
from decimal import Decimal
from unittest.mock import MagicMock
import importlib.util
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VALID_TASK_TYPES = {"classification", "extraction", "summarization", "complex_reasoning"}


def load_router():
    spec = importlib.util.spec_from_file_location(
        "router_props",
        os.path.join(PROJECT_ROOT, "02_router.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ROUTER = load_router()


# ── Property 1: Classifier always returns a valid task type ──────────────────

@settings(max_examples=100, deadline=None)
@given(request_text=st.text(min_size=1, max_size=500))
def test_classify_request_always_returns_valid_task_type(request_text):
    """Property 1: For any non-empty request text, classify_request returns a valid label.

    Tag: # Feature: champagne-ai, Property 1: Classifier always returns a valid task type
    """
    labels = ["classification", "extraction", "summarization", "complex_reasoning",
              "unknown_junk", "", "CLASSIFICATION", "Classification"]
    label = labels[len(request_text) % len(labels)]

    mock_response = {
        "output": {"message": {"content": [{"text": label}]}},
        "usage": {"inputTokens": 10, "outputTokens": 2},
    }
    mock_client = MagicMock()
    mock_client.converse.return_value = mock_response

    task_type, classifier_cost = _ROUTER.classify_request(mock_client, request_text)

    assert task_type in VALID_TASK_TYPES, (
        f"classify_request returned invalid label '{task_type}' for input label '{label}'"
    )
    assert isinstance(classifier_cost, Decimal) and classifier_cost >= 0, (
        f"classifier_cost must be a non-negative Decimal, got {classifier_cost!r}"
    )


# ── Property 1 (timeout): Classifier timeout returns complex_reasoning ────────

@settings(max_examples=10, deadline=None)
@given(extra_ms=st.integers(100, 500))
def test_classify_request_timeout_returns_complex_reasoning(extra_ms):
    """Property 1 (timeout): When classifier delay exceeds CLASSIFIER_TIMEOUT_SECONDS, returns complex_reasoning.

    Uses a delay of (timeout + extra_ms) so the test is always correct regardless of
    what value CLASSIFIER_TIMEOUT_SECONDS is set to.

    Tag: # Feature: champagne-ai, Property 1 (timeout): Classifier timeout returns complex_reasoning
    """
    # Always delay longer than the actual configured timeout
    delay_seconds = _ROUTER.CLASSIFIER_TIMEOUT_SECONDS + (extra_ms / 1000.0)

    def slow_converse(**kwargs):
        time.sleep(delay_seconds)
        return {
            "output": {"message": {"content": [{"text": "classification"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 2},
        }

    mock_client = MagicMock()
    mock_client.converse.side_effect = slow_converse

    task_type, classifier_cost = _ROUTER.classify_request(mock_client, "Test prompt.")

    assert task_type == "complex_reasoning", (
        f"Expected complex_reasoning for delay={delay_seconds:.2f}s "
        f"(timeout={_ROUTER.CLASSIFIER_TIMEOUT_SECONDS}s), got '{task_type}'"
    )
    # A timed-out classification must contribute $0 classifier cost so a failed
    # classification never inflates the reported cost.
    assert classifier_cost == Decimal("0"), (
        f"Expected $0 classifier cost on timeout, got {classifier_cost}"
    )


# ── Property 2: Model Router routing table, kill switch, and fallback ─────────

KNOWN_TASK_TYPES = list(VALID_TASK_TYPES) + ["unknown_xyz", "garbage", "", "123"]


@settings(max_examples=200, deadline=None)
@given(task_label=st.sampled_from(KNOWN_TASK_TYPES))
def test_select_model_always_returns_known_model_id(task_label):
    """Property 2: select_model_for_task always returns a known Bedrock model ID.

    Tag: # Feature: champagne-ai, Property 2: Model Router respects routing table, kill switch, and unknown-label fallback
    """
    import config

    result = _ROUTER.select_model_for_task(task_label)

    known_model_ids = {config.BEER_MODEL_ID, config.CHAMPAGNE_MODEL_ID}
    assert result in known_model_ids, (
        f"select_model_for_task('{task_label}') returned unknown model '{result}'"
    )


@settings(max_examples=100, deadline=None)
@given(task_label=st.text(min_size=1, max_size=50))
def test_select_model_unknown_label_falls_back_to_claude(task_label):
    """Property 2: Any label not in ROUTING_TABLE falls back to CHAMPAGNE_MODEL_ID.

    Tag: # Feature: champagne-ai, Property 2: Model Router respects routing table, kill switch, and unknown-label fallback
    """
    import config

    if task_label in config.ROUTING_TABLE:
        return  # Skip known labels

    result = _ROUTER.select_model_for_task(task_label)
    assert result == config.CHAMPAGNE_MODEL_ID, (
        f"Unknown label '{task_label}' should fall back to Sonnet, got '{result}'"
    )


@settings(max_examples=100, deadline=None)
@given(
    task_label=st.sampled_from(["classification", "extraction", "summarization"])
)
def test_select_model_cheap_tasks_use_nova_lite(task_label):
    """Property 2: classification, extraction, summarization route to the beer model.

    Tag: # Feature: champagne-ai, Property 2: Model Router respects routing table
    """
    import config
    result = _ROUTER.select_model_for_task(task_label)
    assert result == config.BEER_MODEL_ID


@settings(max_examples=10, deadline=None)
@given(task_label=st.just("complex_reasoning"))
def test_select_model_complex_reasoning_uses_claude(task_label):
    """Property 2: complex_reasoning always routes to the champagne model.

    Tag: # Feature: champagne-ai, Property 2: Model Router respects routing table
    """
    import config
    result = _ROUTER.select_model_for_task(task_label)
    assert result == config.CHAMPAGNE_MODEL_ID


# ── Property 10: Naive baseline always uses Claude pricing ───────────────────

@settings(max_examples=100, deadline=None)
@given(
    actual_model=st.sampled_from([config.BEER_MODEL_ID, config.CHAMPAGNE_MODEL_ID]),
    input_tokens=st.integers(0, 500_000),
    output_tokens=st.integers(0, 500_000),
)
def test_naive_baseline_always_uses_claude_pricing(actual_model, input_tokens, output_tokens):
    """Property 10: Naive baseline always uses Claude pricing for savings calculation.

    Tag: # Feature: champagne-ai, Property 10: Naive baseline always uses Claude pricing
    """
    import config

    actual_input_cost, actual_output_cost = config.MODEL_COSTS[actual_model]
    actual_cost = _ROUTER.compute_request_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cost_per_1k=actual_input_cost,
        output_cost_per_1k=actual_output_cost,
    )

    naive_baseline = _ROUTER.compute_request_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cost_per_1k=config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
        output_cost_per_1k=config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )

    assert naive_baseline >= actual_cost, (
        f"Naive baseline ({naive_baseline}) must be >= actual cost ({actual_cost}) "
        f"for model {actual_model}"
    )

    if actual_model == config.BEER_MODEL_ID and input_tokens + output_tokens > 0:
        assert naive_baseline > actual_cost, (
            f"Champagne baseline must be strictly greater than beer model cost for non-zero tokens"
        )
