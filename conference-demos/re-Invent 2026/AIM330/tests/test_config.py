"""
Unit tests for config.py.
Validates all required constants are present with correct types and values.

Feature: champagne-ai
"""
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
import pytest
import config


# ── Model identifiers ──────────────────────────────────────────────────────────

def test_beer_model_id_is_non_empty_string():
    assert isinstance(config.BEER_MODEL_ID, str)
    assert len(config.BEER_MODEL_ID) > 0


def test_champagne_model_id_is_non_empty_string():
    assert isinstance(config.CHAMPAGNE_MODEL_ID, str)
    assert len(config.CHAMPAGNE_MODEL_ID) > 0


# ── Routing table ──────────────────────────────────────────────────────────────

def test_routing_table_is_dict():
    assert isinstance(config.ROUTING_TABLE, dict)


def test_routing_table_has_exactly_four_task_type_keys():
    expected_keys = {"classification", "extraction", "summarization", "complex_reasoning"}
    assert set(config.ROUTING_TABLE.keys()) == expected_keys


def test_routing_table_values_are_known_model_ids():
    known_models = {config.BEER_MODEL_ID, config.CHAMPAGNE_MODEL_ID}
    for task_type, model_id in config.ROUTING_TABLE.items():
        assert model_id in known_models, (
            f"Task type '{task_type}' maps to unknown model '{model_id}'"
        )


def test_routing_table_cheap_tasks_use_beer():
    assert config.ROUTING_TABLE["classification"] == config.BEER_MODEL_ID
    assert config.ROUTING_TABLE["extraction"] == config.BEER_MODEL_ID
    assert config.ROUTING_TABLE["summarization"] == config.BEER_MODEL_ID


def test_routing_table_complex_reasoning_uses_champagne():
    assert config.ROUTING_TABLE["complex_reasoning"] == config.CHAMPAGNE_MODEL_ID


# ── Pricing constants ──────────────────────────────────────────────────────────

def test_champagne_input_cost_is_decimal():
    assert isinstance(config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, Decimal)


def test_champagne_input_cost_value():
    assert config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS == Decimal("0.005000")


def test_champagne_output_cost_is_decimal():
    assert isinstance(config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS, Decimal)


def test_champagne_output_cost_value():
    assert config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS == Decimal("0.025000")


def test_beer_input_cost_is_decimal():
    assert isinstance(config.BEER_INPUT_COST_PER_1K_TOKENS, Decimal)


def test_beer_input_cost_value():
    assert config.BEER_INPUT_COST_PER_1K_TOKENS == Decimal("0.000060")


def test_beer_output_cost_is_decimal():
    assert isinstance(config.BEER_OUTPUT_COST_PER_1K_TOKENS, Decimal)


def test_beer_output_cost_value():
    assert config.BEER_OUTPUT_COST_PER_1K_TOKENS == Decimal("0.000240")


# ── MODEL_COSTS dict ───────────────────────────────────────────────────────────

def test_model_costs_is_dict():
    assert isinstance(config.MODEL_COSTS, dict)


def test_model_costs_has_beer_entry():
    assert config.BEER_MODEL_ID in config.MODEL_COSTS


def test_model_costs_has_champagne_entry():
    assert config.CHAMPAGNE_MODEL_ID in config.MODEL_COSTS


def test_model_costs_beer_tuple_values():
    input_cost, output_cost = config.MODEL_COSTS[config.BEER_MODEL_ID]
    assert input_cost == config.BEER_INPUT_COST_PER_1K_TOKENS
    assert output_cost == config.BEER_OUTPUT_COST_PER_1K_TOKENS


def test_model_costs_champagne_tuple_values():
    input_cost, output_cost = config.MODEL_COSTS[config.CHAMPAGNE_MODEL_ID]
    assert input_cost == config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS
    assert output_cost == config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS


# ── Semantic cache settings ────────────────────────────────────────────────────

def test_similarity_threshold_is_float():
    assert isinstance(config.SIMILARITY_THRESHOLD, float)


def test_similarity_threshold_value():
    assert config.SIMILARITY_THRESHOLD == 0.75


def test_max_cache_entries_is_int():
    assert isinstance(config.MAX_CACHE_ENTRIES, int)


def test_max_cache_entries_value():
    assert config.MAX_CACHE_ENTRIES == 10_000


# ── Budget settings ────────────────────────────────────────────────────────────

def test_default_user_token_budget_is_int():
    assert isinstance(config.DEFAULT_USER_TOKEN_BUDGET, int)


def test_default_user_token_budget_value():
    assert config.DEFAULT_USER_TOKEN_BUDGET == 7_500


def test_default_org_token_budget_is_int():
    assert isinstance(config.DEFAULT_ORG_TOKEN_BUDGET, int)


def test_default_org_token_budget_value():
    assert config.DEFAULT_ORG_TOKEN_BUDGET == 500_000


def test_alert_threshold_percent_is_int():
    assert isinstance(config.ALERT_THRESHOLD_PERCENT, int)


def test_alert_threshold_percent_in_valid_range():
    assert 1 <= config.ALERT_THRESHOLD_PERCENT <= 99


def test_alert_threshold_percent_value():
    assert config.ALERT_THRESHOLD_PERCENT == 80


# ── Spend thresholds ───────────────────────────────────────────────────────────

def test_warning_spend_usd_is_decimal():
    assert isinstance(config.WARNING_SPEND_USD, Decimal)


def test_warning_spend_usd_value():
    assert config.WARNING_SPEND_USD == Decimal("1.00")


def test_danger_spend_usd_is_decimal():
    assert isinstance(config.DANGER_SPEND_USD, Decimal)


def test_danger_spend_usd_value():
    assert config.DANGER_SPEND_USD == Decimal("5.00")


def test_warning_less_than_danger():
    assert config.WARNING_SPEND_USD < config.DANGER_SPEND_USD


# ── Dashboard URL ──────────────────────────────────────────────────────────────

def test_dashboard_url_is_string():
    assert isinstance(config.DASHBOARD_URL, str)


def test_dashboard_url_value():
    assert config.DASHBOARD_URL == "http://localhost:8050/api/cost"


# ── Retry settings ─────────────────────────────────────────────────────────────

def test_max_retries_is_int():
    assert isinstance(config.MAX_RETRIES, int)


def test_max_retries_value():
    assert config.MAX_RETRIES == 3


def test_initial_backoff_ms_is_int():
    assert isinstance(config.INITIAL_BACKOFF_MS, int)


def test_initial_backoff_ms_value():
    assert config.INITIAL_BACKOFF_MS == 500


# ── Demo impact targets ────────────────────────────────────────────────────────

def test_min_savings_percent_is_int():
    assert isinstance(config.MIN_SAVINGS_PERCENT, int)


def test_min_savings_percent_value():
    assert config.MIN_SAVINGS_PERCENT == 70


def test_min_naive_cost_usd_is_decimal():
    assert isinstance(config.MIN_NAIVE_COST_USD, Decimal)


def test_min_naive_cost_usd_value():
    assert config.MIN_NAIVE_COST_USD == Decimal("1.00")


def test_min_daily_savings_usd_is_decimal():
    assert isinstance(config.MIN_DAILY_SAVINGS_USD, Decimal)


def test_min_daily_savings_usd_value():
    assert config.MIN_DAILY_SAVINGS_USD == Decimal("50.00")


# ── Sanity checks ──────────────────────────────────────────────────────────────

def test_beer_cheaper_than_champagne_input():
    assert config.BEER_INPUT_COST_PER_1K_TOKENS < config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS


def test_beer_cheaper_than_champagne_output():
    assert config.BEER_OUTPUT_COST_PER_1K_TOKENS < config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS


# ── Native prompt caching cost constants ───────────────────────────────────────

def test_champagne_cache_read_cost_is_decimal():
    assert isinstance(config.CHAMPAGNE_CACHE_READ_COST_PER_1K_TOKENS, Decimal)


def test_champagne_cache_read_cost_is_one_tenth_of_input():
    expected = config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS * Decimal("0.1")
    assert config.CHAMPAGNE_CACHE_READ_COST_PER_1K_TOKENS == expected


def test_champagne_cache_write_cost_is_decimal():
    assert isinstance(config.CHAMPAGNE_CACHE_WRITE_COST_PER_1K_TOKENS, Decimal)


def test_champagne_cache_write_cost_is_1_25x_input():
    expected = config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS * Decimal("1.25")
    assert config.CHAMPAGNE_CACHE_WRITE_COST_PER_1K_TOKENS == expected


def test_beer_cache_read_cost_is_decimal():
    assert isinstance(config.BEER_CACHE_READ_COST_PER_1K_TOKENS, Decimal)


def test_beer_cache_read_cost_is_one_tenth_of_input():
    expected = config.BEER_INPUT_COST_PER_1K_TOKENS * Decimal("0.1")
    assert config.BEER_CACHE_READ_COST_PER_1K_TOKENS == expected


def test_beer_cache_write_cost_is_decimal():
    assert isinstance(config.BEER_CACHE_WRITE_COST_PER_1K_TOKENS, Decimal)


def test_beer_cache_write_cost_is_1_25x_input():
    expected = config.BEER_INPUT_COST_PER_1K_TOKENS * Decimal("1.25")
    assert config.BEER_CACHE_WRITE_COST_PER_1K_TOKENS == expected


def test_cache_read_cheaper_than_standard_input_champagne():
    assert config.CHAMPAGNE_CACHE_READ_COST_PER_1K_TOKENS < config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS


def test_cache_read_cheaper_than_standard_input_beer():
    assert config.BEER_CACHE_READ_COST_PER_1K_TOKENS < config.BEER_INPUT_COST_PER_1K_TOKENS


# ── Daily workload assumption ──────────────────────────────────────────────────

def test_assumed_requests_per_day_is_int():
    assert isinstance(config.ASSUMED_REQUESTS_PER_DAY, int)


def test_assumed_requests_per_day_value():
    assert config.ASSUMED_REQUESTS_PER_DAY == 10_000


def test_assumed_requests_per_hour_is_removed():
    """The contradictory per-hour constant must no longer exist (Req 5.4)."""
    assert not hasattr(config, "ASSUMED_REQUESTS_PER_HOUR")


# ── Embedding pricing ──────────────────────────────────────────────────────────

def test_embedding_cost_per_1k_tokens_is_decimal():
    assert isinstance(config.EMBEDDING_COST_PER_1K_TOKENS, Decimal)


def test_embedding_cost_per_1k_tokens_value():
    assert config.EMBEDDING_COST_PER_1K_TOKENS == Decimal("0.00002")


# ── Nonprofit system prompt ────────────────────────────────────────────────────

def test_nonprofit_system_prompt_is_string():
    assert isinstance(config.NONPROFIT_SYSTEM_PROMPT, str)


def test_nonprofit_system_prompt_exceeds_minimum_cache_length():
    # System prompt must be long enough to exceed 1,024 tokens.
    # A rough heuristic: 1 token ~ 4 chars. 1,024 tokens ~ 4,096 chars minimum.
    assert len(config.NONPROFIT_SYSTEM_PROMPT) >= 4000
