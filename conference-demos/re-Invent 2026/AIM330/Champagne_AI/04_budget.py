# ========== BUDGET MANAGER ==========
# We track how many tokens each request uses. Once a user or the org runs out of
# budget, we don't turn anyone away. We just send their requests to the cheaper
# beer model until the budget resets.

import threading
from decimal import Decimal

import config
import dashboard_client
from helpers import load_prompts, setup_bedrock_client, compute_request_cost, call_bedrock_with_retry
from parallel import run_threaded_workload

all_prompts = load_prompts()
bedrock_client = setup_bedrock_client()
dashboard_client.reset_dashboard()

# ── Budget state ──────────────────────────────────────────────────────────────

user_token_budget = config.DEFAULT_USER_TOKEN_BUDGET
org_token_budget  = config.DEFAULT_ORG_TOKEN_BUDGET
initial_user_token_budget = config.DEFAULT_USER_TOKEN_BUDGET
initial_org_token_budget  = config.DEFAULT_ORG_TOKEN_BUDGET
alert_has_fired = False
KILL_SWITCH_ACTIVE = False

_budget_lock = threading.Lock()


def decrement_budget(tokens_consumed: int) -> None:
    """Subtract consumed tokens from both balances."""
    global user_token_budget, org_token_budget, alert_has_fired
    user_token_budget = max(0, user_token_budget - tokens_consumed)
    org_token_budget  = max(0, org_token_budget  - tokens_consumed)

    if not alert_has_fired:
        user_max = initial_user_token_budget if initial_user_token_budget > 0 else 1
        user_pct = (initial_user_token_budget - user_token_budget) / user_max * 100
        if user_pct >= config.ALERT_THRESHOLD_PERCENT:
            alert_has_fired = True
            print(f"  ALERT: Budget {config.ALERT_THRESHOLD_PERCENT}% consumed. User remaining: {user_token_budget:,}")


def check_budget(tokens_needed: int) -> bool:
    """Return True if both user and org budgets are above zero."""
    if KILL_SWITCH_ACTIVE:
        return True
    return user_token_budget > 0 and org_token_budget > 0


def reset_budget() -> None:
    """Restore budgets to defaults."""
    global user_token_budget, org_token_budget, alert_has_fired, initial_user_token_budget, initial_org_token_budget
    user_token_budget = config.DEFAULT_USER_TOKEN_BUDGET
    org_token_budget  = config.DEFAULT_ORG_TOKEN_BUDGET
    initial_user_token_budget = config.DEFAULT_USER_TOKEN_BUDGET
    initial_org_token_budget  = config.DEFAULT_ORG_TOKEN_BUDGET
    alert_has_fired = False


def select_model_with_kill_switch(task_type: str) -> str:
    """Select model: kill switch -> beer; budget exhausted -> beer; otherwise -> champagne."""
    if KILL_SWITCH_ACTIVE:
        return config.BEER_MODEL_ID
    if user_token_budget <= 0:
        return config.BEER_MODEL_ID
    return config.CHAMPAGNE_MODEL_ID


# ── Per-request logic ─────────────────────────────────────────────────────────


def call_bedrock(prompt_entry):
    """Check budget, select model (degrade if exhausted), call Bedrock."""
    prompt_id   = prompt_entry["id"]
    prompt_text = prompt_entry["instructions"] + "\n\n" + prompt_entry["text"]
    task_type = prompt_entry.get("task_type", "complex_reasoning")

    with _budget_lock:
        selected_model = select_model_with_kill_switch(task_type)

    messages = [{"role": "user", "content": [{"text": prompt_text}]}]
    response = call_bedrock_with_retry(bedrock_client, selected_model, messages)

    if response is None:
        print(f"[{prompt_id}] Skipped — Bedrock call failed.")
        return {"id": prompt_id, "skipped": True, "cost": Decimal("0")}

    input_tokens  = response["usage"]["inputTokens"]
    output_tokens = response["usage"]["outputTokens"]

    with _budget_lock:
        decrement_budget(input_tokens + output_tokens)

    actual_in, actual_out = config.MODEL_COSTS[selected_model]
    cost = compute_request_cost(input_tokens, output_tokens, actual_in, actual_out)

    # Naive baseline: champagne pricing applied to this call's actual tokens.
    naive_cost = compute_request_cost(
        input_tokens, output_tokens,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )

    print(f"[{prompt_id}] {selected_model}  |  Cost: ${cost:.6f}  |  App: {user_token_budget:,}  Org: {org_token_budget:,}")
    dashboard_client.report_cost(
        model_name=selected_model, input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=cost, naive_cost_usd=naive_cost, request_label="04_budget.py",
    )
    return {"id": prompt_id, "skipped": False, "cost": cost, "model": selected_model}


if __name__ == "__main__":
    print("=" * 60)
    print("BUDGET MANAGER — degrade to beer when budget exhausted")
    if KILL_SWITCH_ACTIVE:
        print("  *** KILL SWITCH ACTIVE ***")
    print("=" * 60)

    results = run_threaded_workload(call_bedrock, all_prompts, threads=10)

    completed = [r for r in results if not r.get("skipped")]
    degraded = sum(1 for r in completed if r.get("model") == config.BEER_MODEL_ID)
    champagne = sum(1 for r in completed if r.get("model") == config.CHAMPAGNE_MODEL_ID)
    total_cost = sum(r["cost"] for r in results)

    print("\n" + "=" * 60)
    print(f"BUDGET SUMMARY")
    print(f"  Champagne: {champagne}  |  Degraded to beer: {degraded}")
    print(f"  Total cost: ${total_cost:.6f}  |  Budget left: {user_token_budget:,} App / {org_token_budget:,} org")
    print("=" * 60)
