# tests/test_04_budget.py
# Unit tests for 04_budget.py — Budget Manager + Kill Switch
# Validates: Requirements 4.2, 4.3, 4.4, 4.5, 4.6, 4.12

import sys
import os
from decimal import Decimal
from unittest.mock import patch
import importlib.util

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_budget_module():
    """Load 04_budget.py as a module without executing main code."""
    spec = importlib.util.spec_from_file_location(
        "budget",
        os.path.join(PROJECT_ROOT, "04_budget.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BUDGET = load_budget_module()


def reset_budget_state():
    """Reset budget module state to defaults before each test."""
    import config
    _BUDGET.user_token_budget         = config.DEFAULT_USER_TOKEN_BUDGET
    _BUDGET.org_token_budget          = config.DEFAULT_ORG_TOKEN_BUDGET
    _BUDGET.alert_has_fired           = False
    _BUDGET.KILL_SWITCH_ACTIVE        = False
    _BUDGET.initial_user_token_budget = config.DEFAULT_USER_TOKEN_BUDGET
    _BUDGET.initial_org_token_budget  = config.DEFAULT_ORG_TOKEN_BUDGET


# ── check_budget ──────────────────────────────────────────────────────────────

class TestCheckBudget:

    def setup_method(self):
        reset_budget_state()

    def test_returns_true_when_budget_available(self):
        result = _BUDGET.check_budget(tokens_needed=100)
        assert result is True

    def test_returns_false_when_user_budget_exhausted(self):
        _BUDGET.user_token_budget = 0
        result = _BUDGET.check_budget(tokens_needed=1)
        assert result is False

    def test_returns_false_when_org_budget_exhausted(self):
        _BUDGET.org_token_budget = 0
        result = _BUDGET.check_budget(tokens_needed=1)
        assert result is False

    def test_returns_false_when_both_exhausted(self):
        _BUDGET.user_token_budget = 0
        _BUDGET.org_token_budget  = 0
        result = _BUDGET.check_budget(tokens_needed=1)
        assert result is False

    def test_returns_true_when_tokens_needed_is_zero(self):
        result = _BUDGET.check_budget(tokens_needed=0)
        assert result is True


# ── decrement_budget ──────────────────────────────────────────────────────────

class TestDecrementBudget:

    def setup_method(self):
        reset_budget_state()

    def test_decrements_both_balances(self):
        import config
        initial_user = _BUDGET.user_token_budget
        initial_org  = _BUDGET.org_token_budget

        _BUDGET.decrement_budget(tokens_consumed=1000)

        assert _BUDGET.user_token_budget == initial_user - 1000
        assert _BUDGET.org_token_budget  == initial_org  - 1000

    def test_clamps_user_balance_at_zero(self):
        _BUDGET.user_token_budget = 500
        _BUDGET.decrement_budget(tokens_consumed=1000)
        assert _BUDGET.user_token_budget == 0

    def test_clamps_org_balance_at_zero(self):
        _BUDGET.org_token_budget = 200
        _BUDGET.decrement_budget(tokens_consumed=500)
        assert _BUDGET.org_token_budget == 0

    def test_balance_never_goes_negative(self):
        _BUDGET.user_token_budget = 100
        _BUDGET.org_token_budget  = 100
        _BUDGET.decrement_budget(tokens_consumed=999_999_999)
        assert _BUDGET.user_token_budget >= 0
        assert _BUDGET.org_token_budget  >= 0


# ── alert mechanism ───────────────────────────────────────────────────────────

class TestBudgetAlert:

    def setup_method(self):
        reset_budget_state()

    def test_alert_fires_when_threshold_crossed(self, capsys):
        _BUDGET.user_token_budget         = 100
        _BUDGET.org_token_budget          = 100
        _BUDGET.initial_user_token_budget = 100
        _BUDGET.initial_org_token_budget  = 100

        _BUDGET.decrement_budget(tokens_consumed=85)

        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_alert_flag_set_after_threshold_crossed(self):
        _BUDGET.user_token_budget         = 1000
        _BUDGET.org_token_budget          = 1000
        _BUDGET.initial_user_token_budget = 1000
        _BUDGET.initial_org_token_budget  = 1000

        _BUDGET.decrement_budget(tokens_consumed=850)
        assert _BUDGET.alert_has_fired is True

    def test_alert_not_fired_below_threshold(self):
        _BUDGET.user_token_budget         = 1000
        _BUDGET.org_token_budget          = 1000
        _BUDGET.initial_user_token_budget = 1000
        _BUDGET.initial_org_token_budget  = 1000

        _BUDGET.decrement_budget(tokens_consumed=500)  # 50% < 80% threshold

        assert _BUDGET.alert_has_fired is False

    def test_alert_rearms_after_reset(self):
        _BUDGET.user_token_budget         = 100
        _BUDGET.org_token_budget          = 100
        _BUDGET.initial_user_token_budget = 100
        _BUDGET.initial_org_token_budget  = 100

        _BUDGET.decrement_budget(tokens_consumed=85)
        assert _BUDGET.alert_has_fired is True

        _BUDGET.reset_budget()
        assert _BUDGET.alert_has_fired is False

        # Set budget again for second trigger
        _BUDGET.user_token_budget         = 100
        _BUDGET.org_token_budget          = 100
        _BUDGET.initial_user_token_budget = 100
        _BUDGET.initial_org_token_budget  = 100

        _BUDGET.decrement_budget(tokens_consumed=85)
        assert _BUDGET.alert_has_fired is True


# ── reset_budget ──────────────────────────────────────────────────────────────

class TestResetBudget:

    def setup_method(self):
        reset_budget_state()

    def test_reset_restores_user_budget(self):
        import config
        _BUDGET.user_token_budget = 0
        _BUDGET.reset_budget()
        assert _BUDGET.user_token_budget == config.DEFAULT_USER_TOKEN_BUDGET

    def test_reset_restores_org_budget(self):
        import config
        _BUDGET.org_token_budget = 0
        _BUDGET.reset_budget()
        assert _BUDGET.org_token_budget == config.DEFAULT_ORG_TOKEN_BUDGET

    def test_reset_clears_alert_flag(self):
        _BUDGET.alert_has_fired = True
        _BUDGET.reset_budget()
        assert _BUDGET.alert_has_fired is False


# ── select_model_with_kill_switch ─────────────────────────────────────────────

class TestKillSwitch:

    def setup_method(self):
        reset_budget_state()

    def test_kill_switch_inactive_uses_claude_for_all_tasks(self):
        """When kill switch is off and budget available, all tasks use Claude."""
        import config
        _BUDGET.KILL_SWITCH_ACTIVE = False
        _BUDGET.user_token_budget = config.DEFAULT_USER_TOKEN_BUDGET

        for task_type in ["classification", "extraction", "summarization", "complex_reasoning"]:
            result = _BUDGET.select_model_with_kill_switch(task_type)
            assert result == config.CHAMPAGNE_MODEL_ID, (
                f"Expected Sonnet when kill switch is off for '{task_type}', got '{result}'"
            )

    def test_budget_exhausted_degrades_to_beer(self):
        """When user budget hits zero, degrade to beer model instead of rejecting."""
        import config
        _BUDGET.KILL_SWITCH_ACTIVE = False
        _BUDGET.user_token_budget = 0

        for task_type in ["classification", "extraction", "summarization", "complex_reasoning"]:
            result = _BUDGET.select_model_with_kill_switch(task_type)
            assert result == config.BEER_MODEL_ID, (
                f"Expected beer model when budget exhausted for '{task_type}', got '{result}'"
            )

    def test_kill_switch_active_forces_nova_lite(self):
        import config
        _BUDGET.KILL_SWITCH_ACTIVE = True

        for task_type in ["classification", "extraction", "summarization", "complex_reasoning"]:
            result = _BUDGET.select_model_with_kill_switch(task_type)
            assert result == config.BEER_MODEL_ID, (
                f"Kill switch active: expected beer model for '{task_type}', got '{result}'"
            )

    def test_kill_switch_active_overrides_complex_reasoning(self):
        import config
        _BUDGET.KILL_SWITCH_ACTIVE = True
        result = _BUDGET.select_model_with_kill_switch("complex_reasoning")
        assert result == config.BEER_MODEL_ID


# ── Per-page dashboard consistency (Req 7.1) ──────────────────────────────────
# The dashboard computes per-request savings as max(0, naive - cost) on the
# Decimal values it parses from the reported strings. This helper mirrors that
# contract exactly so we can assert the page's printed savings equals what the
# dashboard accumulates for the same logical request.

def dashboard_request_savings(reported_cost, reported_naive):
    """Reproduce dashboard.post_cost's per-request savings after the JSON round-trip."""
    cost_usd   = Decimal(str(reported_cost))
    naive_cost = Decimal(str(reported_naive))
    return max(Decimal("0"), naive_cost - cost_usd)


class TestBudgetDashboardConsistency:
    """Req 7.1: budget-page printed savings equals the dashboard's."""

    def setup_method(self):
        reset_budget_state()

    def test_degraded_beer_request_matches_dashboard(self):
        """Budget exhausted -> beer model; naive = champagne on the call's tokens."""
        import config
        # Force degradation to the beer model.
        _BUDGET.user_token_budget = 0
        response = {
            "output": {"message": {"content": [{"text": "answer"}]}},
            "usage": {"inputTokens": 300, "outputTokens": 100},
        }

        calls = []
        with patch.object(_BUDGET, "call_bedrock_with_retry", return_value=response), \
             patch.object(_BUDGET.dashboard_client, "report_cost",
                          side_effect=lambda **kw: calls.append(kw)):
            result = _BUDGET.call_bedrock({
                "id": "p01",
                "task_type": "complex_reasoning",
                "instructions": "Analyze.",
                "text": "Some request.",
            })

        assert result["skipped"] is False
        assert result["model"] == config.BEER_MODEL_ID
        assert len(calls) == 1
        reported = calls[0]
        # Naive is champagne pricing on the call's actual tokens.
        assert reported["naive_cost_usd"] == _BUDGET.compute_request_cost(
            300, 100,
            config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        page_savings = max(Decimal("0"), reported["naive_cost_usd"] - result["cost"])
        dash_savings = dashboard_request_savings(reported["cost_usd"], reported["naive_cost_usd"])
        assert dash_savings == page_savings
        assert dash_savings > Decimal("0")
