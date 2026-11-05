# tests/test_02_router.py
# Unit tests for 02_router.py — Classifier + Model Router
# Validates: Requirements 2.2, 2.3, 2.7, 2.8

import sys
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch
import importlib.util

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_router_module():
    """Load 02_router.py as a module without executing main code."""
    spec = importlib.util.spec_from_file_location(
        "router",
        os.path.join(PROJECT_ROOT, "02_router.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ROUTER = load_router_module()


# ── select_model_for_task ─────────────────────────────────────────────────────

class TestSelectModelForTask:

    def test_classification_maps_to_beer(self):
        import config
        result = _ROUTER.select_model_for_task("classification")
        assert result == config.BEER_MODEL_ID

    def test_extraction_maps_to_beer(self):
        import config
        result = _ROUTER.select_model_for_task("extraction")
        assert result == config.BEER_MODEL_ID

    def test_summarization_maps_to_nova_lite(self):
        import config
        result = _ROUTER.select_model_for_task("summarization")
        assert result == config.BEER_MODEL_ID

    def test_complex_reasoning_maps_to_claude(self):
        import config
        result = _ROUTER.select_model_for_task("complex_reasoning")
        assert result == config.CHAMPAGNE_MODEL_ID

    def test_unknown_label_falls_back_to_claude(self):
        """Unknown task type must fall back to CHAMPAGNE_MODEL_ID (highest-cost tier)."""
        import config
        result = _ROUTER.select_model_for_task("unknown_task_type_xyz")
        assert result == config.CHAMPAGNE_MODEL_ID

    def test_empty_string_falls_back_to_claude(self):
        import config
        result = _ROUTER.select_model_for_task("")
        assert result == config.CHAMPAGNE_MODEL_ID


# ── classify_request ──────────────────────────────────────────────────────────

VALID_TASK_TYPES = {"classification", "extraction", "summarization", "complex_reasoning"}


class TestClassifyRequest:

    def test_returns_classification_label(self):
        """Classifier that returns 'classification' is passed through correctly.

        classify_request now returns a (task_type, classifier_cost) tuple.
        """
        mock_response = {
            "output": {"message": {"content": [{"text": "classification"}]}},
            "usage": {"inputTokens": 50, "outputTokens": 5},
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response

        task_type, classifier_cost = _ROUTER.classify_request(mock_client, "Classify this email.")
        assert task_type == "classification"
        assert isinstance(classifier_cost, Decimal)

    def test_returns_extraction_label(self):
        mock_response = {
            "output": {"message": {"content": [{"text": "extraction"}]}},
            "usage": {"inputTokens": 50, "outputTokens": 5},
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response

        task_type, classifier_cost = _ROUTER.classify_request(mock_client, "Extract dates from this.")
        assert task_type == "extraction"
        assert isinstance(classifier_cost, Decimal)

    def test_returns_summarization_label(self):
        mock_response = {
            "output": {"message": {"content": [{"text": "summarization"}]}},
            "usage": {"inputTokens": 50, "outputTokens": 5},
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response

        task_type, classifier_cost = _ROUTER.classify_request(mock_client, "Summarize this document.")
        assert task_type == "summarization"
        assert isinstance(classifier_cost, Decimal)

    def test_returns_complex_reasoning_label(self):
        mock_response = {
            "output": {"message": {"content": [{"text": "complex_reasoning"}]}},
            "usage": {"inputTokens": 50, "outputTokens": 5},
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response

        task_type, classifier_cost = _ROUTER.classify_request(mock_client, "Draft a major donor letter.")
        assert task_type == "complex_reasoning"
        assert isinstance(classifier_cost, Decimal)

    def test_unrecognised_label_falls_back_to_complex_reasoning(self):
        """If classifier returns an unrecognised label, fall back to complex_reasoning."""
        mock_response = {
            "output": {"message": {"content": [{"text": "unknown_garbage_label"}]}},
            "usage": {"inputTokens": 50, "outputTokens": 5},
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response

        task_type, classifier_cost = _ROUTER.classify_request(mock_client, "Some request.")
        assert task_type == "complex_reasoning"
        # A successful (but unrecognised) classifier call still incurred beer cost.
        assert isinstance(classifier_cost, Decimal)

    def test_classifier_cost_priced_at_beer_rates(self):
        """The returned classifier cost is the beer-priced cost of its usage."""
        import config
        mock_response = {
            "output": {"message": {"content": [{"text": "classification"}]}},
            "usage": {"inputTokens": 50, "outputTokens": 5},
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response

        _, classifier_cost = _ROUTER.classify_request(mock_client, "Classify this.")
        expected = _ROUTER.compute_request_cost(
            50, 5,
            config.BEER_INPUT_COST_PER_1K_TOKENS, config.BEER_OUTPUT_COST_PER_1K_TOKENS,
        )
        assert classifier_cost == expected

    def test_timeout_returns_complex_reasoning(self):
        """If classifier takes longer than CLASSIFIER_TIMEOUT_SECONDS, return complex_reasoning.

        A timed-out classification contributes $0 classifier cost so a failed
        classification never inflates the reported cost.
        """
        import time

        def slow_converse(**kwargs):
            # Sleep well past the timeout so the fallback fires reliably
            time.sleep(_ROUTER.CLASSIFIER_TIMEOUT_SECONDS + 1.0)
            return {
                "output": {"message": {"content": [{"text": "classification"}]}},
                "usage": {"inputTokens": 50, "outputTokens": 5},
            }

        mock_client = MagicMock()
        mock_client.converse.side_effect = slow_converse

        task_type, classifier_cost = _ROUTER.classify_request(mock_client, "Slow request.")
        assert task_type == "complex_reasoning"
        assert classifier_cost == Decimal("0")

    def test_classifier_uses_nova_lite_model(self):
        """Classifier must call Bedrock with BEER_MODEL_ID using correct boto3 param names."""
        import config

        called_kwargs = []
        mock_response = {
            "output": {"message": {"content": [{"text": "classification"}]}},
            "usage": {"inputTokens": 50, "outputTokens": 5},
        }

        def capture_converse(**kwargs):
            called_kwargs.append(kwargs)
            return mock_response

        mock_client = MagicMock()
        mock_client.converse.side_effect = capture_converse

        _ROUTER.classify_request(mock_client, "Classify this.")
        assert len(called_kwargs) == 1
        kw = called_kwargs[0]
        # boto3 uses lowercase 'modelId' and 'messages'
        assert "modelId" in kw, f"Must use 'modelId' (lowercase), got keys: {list(kw)}"
        assert kw["modelId"] == config.BEER_MODEL_ID


# ── compute_request_cost ──────────────────────────────────────────────────────

class TestComputeRequestCost:

    def test_correct_claude_pricing(self):
        import config
        result = _ROUTER.compute_request_cost(
            input_tokens=1000,
            output_tokens=1000,
            input_cost_per_1k=config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
            output_cost_per_1k=config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        assert result == Decimal("0.030000")

    def test_naive_baseline_always_uses_claude_pricing(self):
        """Champagne baseline cost is always higher than beer model actual cost."""
        import config
        nova_lite_actual = _ROUTER.compute_request_cost(
            input_tokens=1000,
            output_tokens=500,
            input_cost_per_1k=config.BEER_INPUT_COST_PER_1K_TOKENS,
            output_cost_per_1k=config.BEER_OUTPUT_COST_PER_1K_TOKENS,
        )
        claude_baseline = _ROUTER.compute_request_cost(
            input_tokens=1000,
            output_tokens=500,
            input_cost_per_1k=config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
            output_cost_per_1k=config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        assert claude_baseline > nova_lite_actual


# ── Per-page dashboard consistency (Req 7.1) ──────────────────────────────────
# The dashboard computes per-request savings as max(0, naive - cost) on the
# Decimal values it parses back from the reported strings. This helper mirrors
# that exact contract: serialize the page's reported Decimals the way
# dashboard_client does (str()), parse them the way dashboard.post_cost does
# (Decimal()), and apply the same clamp. Asserting the page's own printed
# savings equals this value proves the number on the page console matches what
# the dashboard would accumulate for the same logical request.

def dashboard_request_savings(reported_cost, reported_naive):
    """Reproduce dashboard.post_cost's per-request savings after the JSON round-trip."""
    cost_usd   = Decimal(str(reported_cost))
    naive_cost = Decimal(str(reported_naive))
    return max(Decimal("0"), naive_cost - cost_usd)


def capture_report(monkeypatch_target="dashboard_client"):
    """Return a (calls, patcher) pair that records report_cost kwargs."""
    calls = []

    def fake_report_cost(**kwargs):
        calls.append(kwargs)

    return calls, fake_report_cost


class TestRouterDashboardConsistency:
    """Req 7.1: the router page's printed savings equals the dashboard's."""

    def test_routed_request_savings_matches_dashboard(self):
        import config

        # Classifier resolves to a beer task; the routed call runs on the beer model.
        classifier_response = {
            "output": {"message": {"content": [{"text": "classification"}]}},
            "usage": {"inputTokens": 40, "outputTokens": 4},
        }
        routed_response = {
            "output": {"message": {"content": [{"text": "routed answer"}]}},
            "usage": {"inputTokens": 300, "outputTokens": 120},
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = classifier_response

        calls = []
        with patch.object(_ROUTER, "bedrock_client", mock_client), \
             patch.object(_ROUTER, "call_bedrock_with_retry", return_value=routed_response), \
             patch.object(_ROUTER.dashboard_client, "report_cost", side_effect=lambda **kw: calls.append(kw)):
            result = _ROUTER.call_bedrock({
                "id": "p01",
                "instructions": "Classify urgency.",
                "text": "Some email text.",
            })

        assert len(calls) == 1
        reported = calls[0]
        # The page's own printed per-request savings for this logical request.
        page_savings = result["naive_cost"] - result["cost"]
        # The dashboard's per-request savings from the reported payload.
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == max(Decimal("0"), page_savings)
        # Cost folds classifier overhead in; naive is champagne on routed tokens only.
        assert reported["naive_cost_usd"] == _ROUTER.compute_request_cost(
            300, 120,
            config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
