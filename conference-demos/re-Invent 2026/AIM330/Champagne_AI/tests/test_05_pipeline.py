"""Tests for 05_pipeline.py — Full optimized pipeline with cascading + caching + budget."""
import sys
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_pipeline_module():
    spec = importlib.util.spec_from_file_location(
        "pipeline", os.path.join(PROJECT_ROOT, "05_pipeline.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PIPELINE = load_pipeline_module()


def reset_pipeline_state():
    import config
    _PIPELINE.pipeline_user_budget = config.DEFAULT_USER_TOKEN_BUDGET
    _PIPELINE.pipeline_org_budget  = config.DEFAULT_ORG_TOKEN_BUDGET


SAMPLE_PROMPT = {
    "id": "p01",
    "task_type": "classification",
    "instructions": "Classify urgency as urgent, normal, or low_priority.",
    "text": "Dear team, I need help with my donation receipt.",
}


class TestParseResponse:

    def test_valid_json(self):
        answer, conf = _PIPELINE.parse_cascade_response('{"answer": "urgent", "confidence": 0.95}')
        assert answer == "urgent"
        assert conf == 0.95

    def test_invalid_json_returns_zero_confidence(self):
        answer, conf = _PIPELINE.parse_cascade_response("not json")
        assert answer is None
        assert conf == 0.0

    def test_code_fence_stripped(self):
        raw = '```json\n{"answer": "test", "confidence": 0.9}\n```'
        answer, conf = _PIPELINE.parse_cascade_response(raw)
        assert answer == "test"
        assert conf == 0.9


class TestHighConfidenceResolved:

    def setup_method(self):
        reset_pipeline_state()

    def test_high_confidence_does_not_escalate(self):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"answer": "urgent", "confidence": 0.95}'}]}},
            "usage": {"inputTokens": 100, "outputTokens": 20},
        }

        with patch.object(_PIPELINE, "bedrock_client", mock_client), \
             patch("dashboard_client.report_cost"):
            result = _PIPELINE.call_bedrock(SAMPLE_PROMPT)

        assert result["skipped"] is False
        assert result["escalated"] is False
        assert result["confidence"] == 0.95
        assert mock_client.converse.call_count == 1


class TestLowConfidenceEscalates:

    def setup_method(self):
        reset_pipeline_state()

    def test_low_confidence_calls_champagne(self):
        import config
        beer_response = {
            "output": {"message": {"content": [{"text": '{"answer": "unsure", "confidence": 0.4}'}]}},
            "usage": {"inputTokens": 100, "outputTokens": 20},
        }
        champagne_response = {
            "output": {"message": {"content": [{"text": "Full answer here."}]}},
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }

        mock_client = MagicMock()
        mock_client.converse.side_effect = [beer_response, champagne_response]

        with patch.object(_PIPELINE, "bedrock_client", mock_client), \
             patch("dashboard_client.report_cost"):
            result = _PIPELINE.call_bedrock(SAMPLE_PROMPT)

        assert result["escalated"] is True
        assert result["confidence"] == 0.4
        assert mock_client.converse.call_count == 2


class TestBudgetDegradation:

    def setup_method(self):
        reset_pipeline_state()

    def test_budget_exhausted_prevents_escalation(self):
        """When budget is zero, low confidence does NOT escalate — stays on beer."""
        _PIPELINE.pipeline_user_budget = 0

        beer_response = {
            "output": {"message": {"content": [{"text": '{"answer": "guess", "confidence": 0.3}'}]}},
            "usage": {"inputTokens": 100, "outputTokens": 20},
        }

        mock_client = MagicMock()
        mock_client.converse.return_value = beer_response

        with patch.object(_PIPELINE, "bedrock_client", mock_client), \
             patch("dashboard_client.report_cost"):
            result = _PIPELINE.call_bedrock(SAMPLE_PROMPT)

        assert result["degraded"] is True
        assert result["escalated"] is False
        # Only one call (beer), no champagne escalation
        assert mock_client.converse.call_count == 1


class TestBuildSystemWithCacheCheckpoint:

    def test_contains_cache_point(self):
        result = _PIPELINE.build_system_with_cache_checkpoint("Test instructions.")
        cache_blocks = [b for b in result if "cachePoint" in b]
        assert len(cache_blocks) == 1

    def test_contains_system_prompt_and_instructions(self):
        import config
        result = _PIPELINE.build_system_with_cache_checkpoint("Classify urgency.")
        text_blocks = [b for b in result if "text" in b]
        combined = " ".join(b["text"] for b in text_blocks)
        assert config.NONPROFIT_SYSTEM_PROMPT in combined
        assert "Classify urgency." in combined

    def test_contains_cascade_system_prompt(self):
        """The shared helper does NOT include cascade prompt (appended separately per file)."""
        result = _PIPELINE.build_system_with_cache_checkpoint("Test.")
        text_blocks = [b for b in result if "text" in b]
        combined = " ".join(b["text"] for b in text_blocks)
        # build_system_with_cache_checkpoint only has nonprofit + instructions
        assert "nonprofit" in combined.lower()


# ── Per-page dashboard consistency (Req 7.1) ──────────────────────────────────
# The dashboard computes per-request savings as max(0, naive - cost) on the
# Decimal values it parses from the reported strings. This helper mirrors that
# contract exactly so we can assert the page's printed savings equals what the
# dashboard accumulates. The pipeline must be consistent on BOTH the resolved
# and escalated branches (same semantics as 02_cascading.py).

def dashboard_request_savings(reported_cost, reported_naive):
    """Reproduce dashboard.post_cost's per-request savings after the JSON round-trip."""
    cost_usd   = Decimal(str(reported_cost))
    naive_cost = Decimal(str(reported_naive))
    return max(Decimal("0"), naive_cost - cost_usd)


class TestPipelineDashboardConsistency:
    """Req 7.1, 2.2, 2.4: pipeline printed savings equals dashboard, both branches."""

    def setup_method(self):
        reset_pipeline_state()

    def _run(self, converse_side_effect):
        mock_client = MagicMock()
        mock_client.converse.side_effect = converse_side_effect
        calls = []
        with patch.object(_PIPELINE, "bedrock_client", mock_client), \
             patch.object(_PIPELINE.dashboard_client, "report_cost",
                          side_effect=lambda **kw: calls.append(kw)):
            result = _PIPELINE.call_bedrock(SAMPLE_PROMPT)
        return result, calls

    def test_resolved_branch_matches_dashboard(self):
        """Resolved (beer only): naive = champagne on beer tokens; savings consistent."""
        import config
        beer_response = {
            "output": {"message": {"content": [{"text": '{"answer": "sure", "confidence": 0.95}'}]}},
            "usage": {"inputTokens": 200, "outputTokens": 60},
        }
        result, calls = self._run([beer_response])

        assert result["escalated"] is False
        assert result["degraded"] is False
        assert len(calls) == 1
        reported = calls[0]
        assert reported["naive_cost_usd"] == _PIPELINE.compute_request_cost(
            200, 60,
            config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        page_savings = max(Decimal("0"), result["naive_cost"] - result["cost"])
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == page_savings
        assert dash_savings > Decimal("0")

    def test_escalated_branch_matches_dashboard(self):
        """Escalated: cost = beer + champagne; naive = champagne on champ tokens."""
        import config
        beer_response = {
            "output": {"message": {"content": [{"text": '{"answer": "unsure", "confidence": 0.4}'}]}},
            "usage": {"inputTokens": 200, "outputTokens": 30},
        }
        champagne_response = {
            "output": {"message": {"content": [{"text": "Full champagne answer."}]}},
            "usage": {"inputTokens": 210, "outputTokens": 140},
        }
        result, calls = self._run([beer_response, champagne_response])

        assert result["escalated"] is True
        assert len(calls) == 1
        reported = calls[0]
        assert reported["naive_cost_usd"] == _PIPELINE.compute_request_cost(
            210, 140,
            config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        expected_cost = (
            _PIPELINE.compute_request_cost(200, 30, config.BEER_INPUT_COST_PER_1K_TOKENS, config.BEER_OUTPUT_COST_PER_1K_TOKENS)
            + _PIPELINE.compute_request_cost(210, 140, config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS)
        )
        assert Decimal(str(reported["cost_usd"])) == expected_cost
        page_savings = max(Decimal("0"), result["naive_cost"] - result["cost"])
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == page_savings
