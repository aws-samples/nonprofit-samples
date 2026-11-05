"""Tests for 02_cascading.py — model cascading with confidence threshold."""
import os
import sys
from decimal import Decimal
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestParseCascadeResponse:
    """Tests for parse_cascade_response."""

    def test_valid_json_returns_answer_and_confidence(self):
        from importlib import import_module
        _mod = import_module("02_cascading")
        answer, confidence = _mod.parse_cascade_response(
            '{"answer": "This is urgent.", "confidence": 0.95}'
        )
        assert answer == "This is urgent."
        assert confidence == 0.95

    def test_low_confidence_parsed_correctly(self):
        from importlib import import_module
        _mod = import_module("02_cascading")
        answer, confidence = _mod.parse_cascade_response(
            '{"answer": "Maybe billing related.", "confidence": 0.45}'
        )
        assert answer == "Maybe billing related."
        assert confidence == 0.45

    def test_markdown_code_fence_stripped(self):
        from importlib import import_module
        _mod = import_module("02_cascading")
        raw = '```json\n{"answer": "test", "confidence": 0.9}\n```'
        answer, confidence = _mod.parse_cascade_response(raw)
        assert answer == "test"
        assert confidence == 0.9

    def test_invalid_json_returns_none_and_zero(self):
        from importlib import import_module
        _mod = import_module("02_cascading")
        answer, confidence = _mod.parse_cascade_response("not json at all")
        assert answer is None
        assert confidence == 0.0

    def test_missing_confidence_key_returns_zero(self):
        from importlib import import_module
        _mod = import_module("02_cascading")
        answer, confidence = _mod.parse_cascade_response('{"answer": "hello"}')
        assert answer == "hello"
        assert confidence == 0.0


class TestCascadeLogic:
    """Tests for call_bedrock cascading behavior."""

    def test_high_confidence_does_not_escalate(self):
        from importlib import import_module
        _mod = import_module("02_cascading")

        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"answer": "urgent", "confidence": 0.95}'}]}},
            "usage": {"inputTokens": 100, "outputTokens": 20},
        }

        with patch.object(_mod, "bedrock_client", mock_client), \
             patch("dashboard_client.report_cost"):
            result = _mod.call_bedrock({
                "id": "p01",
                "task_type": "classification",
                "instructions": "Classify urgency.",
                "text": "Some email text.",
            })

        assert result["skipped"] is False
        assert result["escalated"] is False
        assert result["confidence"] == 0.95
        # Only one converse call (beer model, no escalation)
        assert mock_client.converse.call_count == 1

    def test_low_confidence_escalates_to_champagne(self):
        from importlib import import_module
        _mod = import_module("02_cascading")
        import config

        beer_response = {
            "output": {"message": {"content": [{"text": '{"answer": "unsure", "confidence": 0.4}'}]}},
            "usage": {"inputTokens": 100, "outputTokens": 20},
        }
        champagne_response = {
            "output": {"message": {"content": [{"text": "This is the champagne answer."}]}},
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }

        mock_client = MagicMock()
        mock_client.converse.side_effect = [beer_response, champagne_response]

        with patch.object(_mod, "bedrock_client", mock_client), \
             patch("dashboard_client.report_cost"):
            result = _mod.call_bedrock({
                "id": "p05",
                "task_type": "complex_reasoning",
                "instructions": "Analyze this.",
                "text": "Complex scenario.",
            })

        assert result["skipped"] is False
        assert result["escalated"] is True
        assert result["confidence"] == 0.4
        # Two converse calls: beer + champagne
        assert mock_client.converse.call_count == 2

    def test_unparseable_response_triggers_escalation(self):
        from importlib import import_module
        _mod = import_module("02_cascading")
        import config

        beer_response = {
            "output": {"message": {"content": [{"text": "I cannot format as JSON sorry"}]}},
            "usage": {"inputTokens": 100, "outputTokens": 30},
        }
        champagne_response = {
            "output": {"message": {"content": [{"text": "Proper answer here."}]}},
            "usage": {"inputTokens": 100, "outputTokens": 40},
        }

        mock_client = MagicMock()
        mock_client.converse.side_effect = [beer_response, champagne_response]

        with patch.object(_mod, "bedrock_client", mock_client), \
             patch("dashboard_client.report_cost"):
            result = _mod.call_bedrock({
                "id": "p09",
                "task_type": "complex_reasoning",
                "instructions": "Assess fit.",
                "text": "Grant criteria here.",
            })

        assert result["escalated"] is True
        assert result["confidence"] == 0.0


# ── Per-page dashboard consistency (Req 7.1) ──────────────────────────────────
# The dashboard computes per-request savings as max(0, naive - cost) on the
# Decimal values it parses from the reported strings. This helper mirrors that
# contract exactly so we can assert the page's printed savings equals what the
# dashboard accumulates for the same logical request. The cascade page must be
# consistent on BOTH the resolved and escalated branches.

def dashboard_request_savings(reported_cost, reported_naive):
    """Reproduce dashboard.post_cost's per-request savings after the JSON round-trip."""
    cost_usd   = Decimal(str(reported_cost))
    naive_cost = Decimal(str(reported_naive))
    return max(Decimal("0"), naive_cost - cost_usd)


class TestCascadeDashboardConsistency:
    """Req 7.1, 2.2, 2.3: cascade printed savings equals dashboard, both branches."""

    def _run(self, converse_side_effect):
        from importlib import import_module
        _mod = import_module("02_cascading")
        mock_client = MagicMock()
        mock_client.converse.side_effect = converse_side_effect

        calls = []
        with patch.object(_mod, "bedrock_client", mock_client), \
             patch.object(_mod.dashboard_client, "report_cost",
                          side_effect=lambda **kw: calls.append(kw)):
            result = _mod.call_bedrock({
                "id": "p01",
                "task_type": "complex_reasoning",
                "instructions": "Analyze this.",
                "text": "Some scenario.",
            })
        return _mod, result, calls

    def test_resolved_branch_matches_dashboard(self):
        """Resolved (beer only): naive = champagne on beer tokens; savings positive and consistent."""
        import config
        beer_response = {
            "output": {"message": {"content": [{"text": '{"answer": "sure", "confidence": 0.95}'}]}},
            "usage": {"inputTokens": 200, "outputTokens": 60},
        }
        _mod, result, calls = self._run([beer_response])

        assert result["escalated"] is False
        assert len(calls) == 1
        reported = calls[0]
        # Naive is champagne priced on the beer tokens.
        assert reported["naive_cost_usd"] == _mod.compute_request_cost(
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
        _mod, result, calls = self._run([beer_response, champagne_response])

        assert result["escalated"] is True
        assert len(calls) == 1
        reported = calls[0]
        # Naive is champagne priced on the champagne stage's actual tokens.
        assert reported["naive_cost_usd"] == _mod.compute_request_cost(
            210, 140,
            config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        # Cost sums both stages.
        expected_cost = (
            _mod.compute_request_cost(200, 30, config.BEER_INPUT_COST_PER_1K_TOKENS, config.BEER_OUTPUT_COST_PER_1K_TOKENS)
            + _mod.compute_request_cost(210, 140, config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS)
        )
        assert Decimal(str(reported["cost_usd"])) == expected_cost
        page_savings = max(Decimal("0"), result["naive_cost"] - result["cost"])
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == page_savings
