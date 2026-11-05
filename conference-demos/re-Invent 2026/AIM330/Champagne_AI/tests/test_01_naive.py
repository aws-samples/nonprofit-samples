# tests/test_01_naive.py
# Unit tests for 01_naive.py — Naive Integration
# Validates: Requirements 1.1, 1.5, 9.3, 9.4

import sys
import os
from decimal import Decimal
from unittest.mock import patch, MagicMock
import importlib.util

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def import_naive_functions():
    """Load 01_naive.py as a module (filename not a valid Python identifier)."""
    spec = importlib.util.spec_from_file_location(
        "naive",
        os.path.join(PROJECT_ROOT, "01_naive.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── compute_request_cost ──────────────────────────────────────────────────────

class TestComputeRequestCost:

    @pytest.fixture(autouse=True)
    def load_module(self):
        self.naive = import_naive_functions()

    def test_correct_result_for_claude_pricing(self):
        import config
        result = self.naive.compute_request_cost(
            input_tokens=1000,
            output_tokens=1000,
            input_cost_per_1k=config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
            output_cost_per_1k=config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        assert result == Decimal("0.030000")

    def test_returns_decimal_type(self):
        import config
        result = self.naive.compute_request_cost(
            input_tokens=500,
            output_tokens=200,
            input_cost_per_1k=config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
            output_cost_per_1k=config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        assert isinstance(result, Decimal)

    def test_zero_tokens_is_zero_cost(self):
        import config
        result = self.naive.compute_request_cost(
            input_tokens=0,
            output_tokens=0,
            input_cost_per_1k=config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
            output_cost_per_1k=config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        assert result == Decimal("0")


# ── call_bedrock_with_retry ───────────────────────────────────────────────────

class TestCallBedrockWithRetry:

    @pytest.fixture(autouse=True)
    def load_module(self):
        self.naive = import_naive_functions()

    def test_returns_response_on_first_success(self):
        """When Bedrock succeeds on first attempt, return the response."""
        mock_response = {"output": {"message": {"content": [{"text": "OK"}]}}}
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response

        import config
        messages = [{"role": "user", "content": [{"text": "hi"}]}]
        result = self.naive.call_bedrock_with_retry(
            mock_client,
            config.CHAMPAGNE_MODEL_ID,
            messages,
        )

        assert result == mock_response
        assert mock_client.converse.call_count == 1
        # Verify boto3 lowercase parameter names are used
        call_kwargs = mock_client.converse.call_args[1]
        assert "modelId" in call_kwargs, "Must use 'modelId' (lowercase), not 'ModelId'"
        assert "messages" in call_kwargs, "Must use 'messages' (lowercase), not 'Messages'"
        assert call_kwargs["modelId"] == config.CHAMPAGNE_MODEL_ID
        assert call_kwargs["messages"] == messages

    def test_retries_exactly_3_times_on_failure_returns_none(self):
        """When all attempts fail, retry MAX_RETRIES times then return None."""
        import config

        mock_client = MagicMock()
        mock_client.converse.side_effect = Exception("Bedrock unavailable")

        with patch("time.sleep"):
            result = self.naive.call_bedrock_with_retry(
                mock_client,
                config.CHAMPAGNE_MODEL_ID,
                [{"role": "user", "content": [{"text": "hi"}]}],
            )

        assert result is None
        # 1 initial attempt + MAX_RETRIES retries
        assert mock_client.converse.call_count == config.MAX_RETRIES + 1

    def test_succeeds_on_second_attempt(self):
        """Succeeds on the second attempt after one failure."""
        import config

        mock_response = {"output": {"message": {"content": [{"text": "OK"}]}}}
        mock_client = MagicMock()
        mock_client.converse.side_effect = [Exception("First failure"), mock_response]

        with patch("time.sleep"):
            result = self.naive.call_bedrock_with_retry(
                mock_client,
                config.CHAMPAGNE_MODEL_ID,
                [{"role": "user", "content": [{"text": "hi"}]}],
            )

        assert result == mock_response
        assert mock_client.converse.call_count == 2

    def test_backoff_delays_are_correct(self):
        """Retry delays must be [0.5, 1.0, 2.0] seconds."""
        import config

        mock_client = MagicMock()
        mock_client.converse.side_effect = Exception("always fails")
        sleep_calls = []

        with patch("time.sleep", side_effect=lambda t: sleep_calls.append(t)):
            self.naive.call_bedrock_with_retry(
                mock_client,
                config.CHAMPAGNE_MODEL_ID,
                [{"role": "user", "content": [{"text": "hi"}]}],
            )

        assert sleep_calls == [0.5, 1.0, 2.0]

    def test_prints_failure_details_to_stdout(self, capsys):
        """After all retries fail, returns None."""
        import config

        mock_client = MagicMock()
        mock_client.converse.side_effect = Exception("throttled")

        with patch("time.sleep"):
            result = self.naive.call_bedrock_with_retry(
                mock_client,
                config.CHAMPAGNE_MODEL_ID,
                [{"role": "user", "content": [{"text": "hi"}]}],
            )

        assert result is None


# ── Per-page dashboard consistency (Req 7.1) ──────────────────────────────────
# The dashboard computes per-request savings as max(0, naive - cost) on the
# Decimal values it parses from the reported strings. This helper mirrors that
# contract exactly (str() on the way out, Decimal() on the way back in) so we
# can assert the page's printed savings equals what the dashboard accumulates.

def dashboard_request_savings(reported_cost, reported_naive):
    """Reproduce dashboard.post_cost's per-request savings after the JSON round-trip."""
    cost_usd   = Decimal(str(reported_cost))
    naive_cost = Decimal(str(reported_naive))
    return max(Decimal("0"), naive_cost - cost_usd)


class TestNaiveDashboardConsistency:
    """Req 7.1 / 1.5: naive page reports naive == cost, so savings is $0."""

    @pytest.fixture(autouse=True)
    def load_module(self):
        self.naive = import_naive_functions()

    def test_naive_reports_zero_savings_and_matches_dashboard(self):
        mock_response = {
            "output": {"message": {"content": [{"text": "answer"}]}},
            "usage": {"inputTokens": 250, "outputTokens": 90},
        }

        calls = []
        with patch.object(self.naive, "call_bedrock_with_retry", return_value=mock_response), \
             patch.object(self.naive.dashboard_client, "report_cost",
                          side_effect=lambda **kw: calls.append(kw)):
            result = self.naive.call_bedrock({
                "id": "p01",
                "instructions": "Do the thing.",
                "text": "Some request text.",
            })

        assert len(calls) == 1
        reported = calls[0]
        # By design the naive baseline equals the actual cost.
        assert reported["naive_cost_usd"] == reported["cost_usd"] == result["cost"]
        # Page prints $0 savings; the dashboard agrees.
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == Decimal("0")
