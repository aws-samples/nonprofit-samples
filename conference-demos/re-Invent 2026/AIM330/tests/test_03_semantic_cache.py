# tests/test_03_semantic_cache.py
# Per-page dashboard-consistency tests for 03_semantic_cache.py.
# Validates: Requirements 7.1, 7.2, 7.3, 3.1, 3.2, 3.4
#
# The dashboard computes per-request savings as max(0, naive - cost) on the
# Decimal values it parses back from the reported strings. These tests assert
# the page's printed savings equals what the dashboard would accumulate for the
# same logical request, on BOTH the cache-hit and cache-miss branches.

import sys
import os
from decimal import Decimal
from unittest.mock import patch
import importlib.util

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_semantic_cache_module():
    """Load 03_semantic_cache.py as a module without executing main code."""
    spec = importlib.util.spec_from_file_location(
        "semantic_cache",
        os.path.join(PROJECT_ROOT, "03_semantic_cache.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SC = load_semantic_cache_module()


def dashboard_request_savings(reported_cost, reported_naive):
    """Reproduce dashboard.post_cost's per-request savings after the JSON round-trip."""
    cost_usd   = Decimal(str(reported_cost))
    naive_cost = Decimal(str(reported_naive))
    return max(Decimal("0"), naive_cost - cost_usd)


SAMPLE_PROMPT = {
    "id": "p01",
    "instructions": "Classify the urgency of this donor email.",
    "text": "Dear team, I would like to update my recurring gift.",
}


class TestSemanticCacheConfig:
    """Req 3.5: the embedding price constant lives in config, not the page."""

    def test_uses_config_embedding_constant(self):
        import config
        assert _SC.config.EMBEDDING_COST_PER_1K_TOKENS == config.EMBEDDING_COST_PER_1K_TOKENS
        assert isinstance(config.EMBEDDING_COST_PER_1K_TOKENS, Decimal)


class TestSemanticCacheDashboardConsistency:
    """Req 7.1: semantic-cache printed savings equals the dashboard's, hit and miss."""

    def test_cache_hit_matches_dashboard(self):
        """HIT: cost = embed only; naive = word-count champagne estimate on the prompt."""
        import config

        embed_tokens = 25
        embedding = np.ones(4, dtype=np.float64)
        hit_entry = {
            "embedding": embedding,
            "answer": "A previously cached answer with several words here.",
            "prompt_id": "p00",
        }

        calls = []
        with patch.object(_SC, "_embed_text", return_value=(embedding, embed_tokens)), \
             patch.object(_SC, "_find_cache_hit_or_reserve", return_value=(hit_entry, 0.97, None)), \
             patch.object(_SC.dashboard_client, "report_cost",
                          side_effect=lambda **kw: calls.append(kw)):
            result = _SC.call_bedrock(SAMPLE_PROMPT)

        assert result["skipped"] is False
        assert result["cache_hit"] is True
        assert len(calls) == 1
        reported = calls[0]

        # Cost is the embedding cost only.
        expected_embed_cost = (Decimal(embed_tokens) / 1000) * config.EMBEDDING_COST_PER_1K_TOKENS
        assert Decimal(str(reported["cost_usd"])) == expected_embed_cost

        # Naive is the champagne word-count estimate for the avoided generation.
        full_prompt = SAMPLE_PROMPT["instructions"] + "\n\n" + SAMPLE_PROMPT["text"]
        approx_in = int(len(full_prompt.split()) * 1.3)
        approx_out = max(1, len(hit_entry["answer"].split()))
        expected_naive = _SC.compute_request_cost(
            approx_in, approx_out,
            config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        assert reported["naive_cost_usd"] == expected_naive

        page_savings = max(Decimal("0"), result["naive_cost"] - result["cost"])
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == page_savings
        # A hit avoids the whole champagne call, so savings are strongly positive.
        assert dash_savings > Decimal("0")

    def test_cache_miss_matches_dashboard(self):
        """MISS: cost = embed + champagne; naive = champagne on champ tokens."""
        import config

        embed_tokens = 25
        embedding = np.ones(4, dtype=np.float64)
        placeholder = {"embedding": embedding, "answer": None, "prompt_id": None}
        champ_response = {
            "output": {"message": {"content": [{"text": "A fresh champagne answer."}]}},
            "usage": {"inputTokens": 320, "outputTokens": 150},
        }

        calls = []
        with patch.object(_SC, "_embed_text", return_value=(embedding, embed_tokens)), \
             patch.object(_SC, "_find_cache_hit_or_reserve", return_value=(None, 0.10, placeholder)), \
             patch.object(_SC, "call_bedrock_with_retry", return_value=champ_response), \
             patch.object(_SC.dashboard_client, "report_cost",
                          side_effect=lambda **kw: calls.append(kw)):
            result = _SC.call_bedrock(SAMPLE_PROMPT)

        assert result["skipped"] is False
        assert result["cache_hit"] is False
        assert len(calls) == 1
        reported = calls[0]

        embed_cost = (Decimal(embed_tokens) / 1000) * config.EMBEDDING_COST_PER_1K_TOKENS
        champ_cost = _SC.compute_request_cost(
            320, 150,
            config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        # Cost includes both embedding overhead and the champagne call.
        assert Decimal(str(reported["cost_usd"])) == embed_cost + champ_cost
        # Naive is champagne pricing on the champagne call's tokens (no embed overhead).
        assert reported["naive_cost_usd"] == champ_cost

        page_savings = max(Decimal("0"), result["naive_cost"] - result["cost"])
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == page_savings
        # A miss costs more than naive (embedding overhead), so savings clamp to $0.
        assert dash_savings == Decimal("0")
