# tests/test_03a_prompt_cache.py
# Unit tests for 03a_prompt_cache.py -- Native Bedrock Prompt Caching
# Validates: cache checkpoint structure, cost computation with cache tokens,
# and correct reporting of cache hits vs writes.

import sys
import os
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch
import importlib.util

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_prompt_cache_module():
    """Load 03_prompt_cache.py as a module without executing main code."""
    spec = importlib.util.spec_from_file_location(
        "prompt_cache",
        os.path.join(PROJECT_ROOT, "03_prompt_cache.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PC = load_prompt_cache_module()


# -- build_system_with_cache_checkpoint ----------------------------------------

class TestBuildSystemWithCacheCheckpoint:

    def test_returns_list(self):
        result = _PC.build_system_with_cache_checkpoint("Test instructions.")
        assert isinstance(result, list)

    def test_contains_text_block(self):
        result = _PC.build_system_with_cache_checkpoint("Test instructions.")
        text_blocks = [b for b in result if "text" in b]
        assert len(text_blocks) >= 1

    def test_contains_cache_point_block(self):
        result = _PC.build_system_with_cache_checkpoint("Test instructions.")
        cache_blocks = [b for b in result if "cachePoint" in b]
        assert len(cache_blocks) == 1

    def test_cache_point_has_default_type(self):
        result = _PC.build_system_with_cache_checkpoint("Test instructions.")
        cache_blocks = [b for b in result if "cachePoint" in b]
        assert cache_blocks[0]["cachePoint"]["type"] == "default"

    def test_cache_point_comes_after_text(self):
        result = _PC.build_system_with_cache_checkpoint("Test instructions.")
        # cachePoint must be the last element (after all text content)
        assert "cachePoint" in result[-1]

    def test_text_block_contains_nonprofit_system_prompt_and_instructions(self):
        import config
        result = _PC.build_system_with_cache_checkpoint("Classify urgency.")
        text_blocks = [b for b in result if "text" in b]
        assert config.NONPROFIT_SYSTEM_PROMPT in text_blocks[0]["text"]
        assert "Classify urgency." in text_blocks[0]["text"]


# -- compute_request_cost_with_cache -------------------------------------------

class TestComputeRequestCostWithCache:

    def test_returns_decimal(self):
        result = _PC.compute_request_cost_with_cache(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            input_cost_per_1k=Decimal("0.003"),
            output_cost_per_1k=Decimal("0.015"),
            cache_read_cost_per_1k=Decimal("0.0003"),
            cache_write_cost_per_1k=Decimal("0.00375"),
        )
        assert isinstance(result, Decimal)

    def test_no_cache_tokens_equals_standard_cost(self):
        """When there are no cache tokens, cost should equal standard pricing."""
        result = _PC.compute_request_cost_with_cache(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            cache_write_tokens=0,
            input_cost_per_1k=Decimal("0.003"),
            output_cost_per_1k=Decimal("0.015"),
            cache_read_cost_per_1k=Decimal("0.0003"),
            cache_write_cost_per_1k=Decimal("0.00375"),
        )
        expected = Decimal("1000") / 1000 * Decimal("0.003") + Decimal("500") / 1000 * Decimal("0.015")
        assert result == expected

    def test_cache_read_tokens_reduce_cost(self):
        """Cache read tokens are charged at 0.1x, so total cost should be lower."""
        # All input from cache read
        cached_cost = _PC.compute_request_cost_with_cache(
            input_tokens=0,
            output_tokens=500,
            cache_read_tokens=1000,
            cache_write_tokens=0,
            input_cost_per_1k=Decimal("0.003"),
            output_cost_per_1k=Decimal("0.015"),
            cache_read_cost_per_1k=Decimal("0.0003"),
            cache_write_cost_per_1k=Decimal("0.00375"),
        )
        # Standard cost if those tokens were not cached
        standard_cost = _PC.compute_request_cost_with_cache(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            cache_write_tokens=0,
            input_cost_per_1k=Decimal("0.003"),
            output_cost_per_1k=Decimal("0.015"),
            cache_read_cost_per_1k=Decimal("0.0003"),
            cache_write_cost_per_1k=Decimal("0.00375"),
        )
        assert cached_cost < standard_cost

    def test_cache_write_tokens_cost_more_than_standard(self):
        """Cache write tokens are charged at 1.25x, more expensive than standard input."""
        write_cost = _PC.compute_request_cost_with_cache(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=1000,
            input_cost_per_1k=Decimal("0.003"),
            output_cost_per_1k=Decimal("0.015"),
            cache_read_cost_per_1k=Decimal("0.0003"),
            cache_write_cost_per_1k=Decimal("0.00375"),
        )
        standard_input_cost = Decimal("1000") / 1000 * Decimal("0.003")
        assert write_cost > standard_input_cost

    def test_mixed_cache_read_and_uncached_input(self):
        """A request with some cached and some uncached input tokens."""
        result = _PC.compute_request_cost_with_cache(
            input_tokens=200,        # uncached portion
            output_tokens=100,
            cache_read_tokens=800,   # cached portion
            cache_write_tokens=0,
            input_cost_per_1k=Decimal("0.003"),
            output_cost_per_1k=Decimal("0.015"),
            cache_read_cost_per_1k=Decimal("0.0003"),
            cache_write_cost_per_1k=Decimal("0.00375"),
        )
        expected = (
            Decimal("200") / 1000 * Decimal("0.003")    # uncached input
            + Decimal("100") / 1000 * Decimal("0.015")  # output
            + Decimal("800") / 1000 * Decimal("0.0003") # cache read
        )
        assert result == expected


# -- call_bedrock_with_retry ---------------------------------------------------

class TestCallBedrockWithRetry:

    def test_returns_response_on_success(self):
        mock_client = MagicMock()
        mock_client.converse.return_value = {"output": {"message": {"content": [{"text": "hi"}]}}}
        result = _PC.call_bedrock_with_retry(mock_client, "model-id", [], [])
        assert result == {"output": {"message": {"content": [{"text": "hi"}]}}}

    def test_returns_none_after_all_retries_fail(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = Exception("throttled")
        result = _PC.call_bedrock_with_retry(mock_client, "model-id", [], [])
        assert result is None

    def test_passes_system_parameter(self):
        """The system prompt with cachePoint must be passed to the converse call."""
        mock_client = MagicMock()
        mock_client.converse.return_value = {"output": {"message": {"content": [{"text": "ok"}]}}}

        system_blocks = [{"text": "system prompt"}, {"cachePoint": {"type": "default"}}]
        messages = [{"role": "user", "content": [{"text": "hello"}]}]

        _PC.call_bedrock_with_retry(mock_client, "model-id", messages, system_blocks)

        call_kwargs = mock_client.converse.call_args[1] if mock_client.converse.call_args[1] else {}
        # Check that system was passed as a keyword argument
        assert "system" in call_kwargs


# -- Per-page dashboard consistency (Req 7.1) ----------------------------------
# The dashboard computes per-request savings as max(0, naive - cost) on the
# Decimal values it parses from the reported strings. This helper mirrors that
# contract exactly so we can assert the page's printed savings equals what the
# dashboard accumulates for the same logical request.

def dashboard_request_savings(reported_cost, reported_naive):
    """Reproduce dashboard.post_cost's per-request savings after the JSON round-trip."""
    cost_usd   = Decimal(str(reported_cost))
    naive_cost = Decimal(str(reported_naive))
    return max(Decimal("0"), naive_cost - cost_usd)


class TestPromptCacheDashboardConsistency:
    """Req 7.1: prompt-cache printed savings equals the dashboard's."""

    def test_cache_hit_request_matches_dashboard(self):
        """On a cache read, naive = champagne on all_input + output; savings consistent."""
        import config
        response = {
            "output": {"message": {"content": [{"text": "answer"}]}},
            "usage": {
                "inputTokens": 40,
                "outputTokens": 120,
                "cacheReadInputTokens": 1500,
                "cacheWriteInputTokens": 0,
            },
        }

        calls = []
        with patch.object(_PC, "call_bedrock_with_retry", return_value=response), \
             patch.object(_PC.dashboard_client, "report_cost",
                          side_effect=lambda **kw: calls.append(kw)):
            result = _PC.call_bedrock({
                "id": "p01",
                "instructions": "Classify urgency.",
                "text": "Some email text.",
            })

        assert len(calls) == 1
        reported = calls[0]
        all_input = 40 + 1500 + 0
        expected_naive = (
            (Decimal(all_input) / 1000) * config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS
            + (Decimal(120) / 1000) * config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS
        )
        assert reported["naive_cost_usd"] == expected_naive
        page_savings = max(Decimal("0"), result["naive_cost"] - result["cost"])
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == page_savings
        assert dash_savings > Decimal("0")
