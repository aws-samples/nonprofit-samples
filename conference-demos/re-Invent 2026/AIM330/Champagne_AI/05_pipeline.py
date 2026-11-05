"""Full pipeline.

This is everything working together. Each request starts on the beer model with
a cached prefix. If the model isn't confident in its answer, we escalate to the
champagne model, but only when there's still budget for it. Once the budget is
gone, requests stay on the beer model.
"""

import threading
from decimal import Decimal

import config
import dashboard_client
from helpers import (
    load_prompts, setup_bedrock_client, compute_request_cost,
    call_bedrock_with_retry, build_system_with_cache_checkpoint, parse_cascade_response,
)
from parallel import run_threaded_workload

all_prompts = load_prompts()
bedrock_client = setup_bedrock_client()
dashboard_client.reset_dashboard()

# ── Budget state ──────────────────────────────────────────────────────────────

pipeline_user_budget = config.DEFAULT_USER_TOKEN_BUDGET
pipeline_org_budget  = config.DEFAULT_ORG_TOKEN_BUDGET
_budget_lock = threading.Lock()

CONFIDENCE_THRESHOLD = 0.8

CASCADE_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's request, then rate your own "
    "confidence in the answer on a scale of 0.0 to 1.0. A score of 1.0 means you "
    "are certain the answer is complete and correct. A score below 0.8 means the "
    "request is ambiguous, requires nuanced judgment, or you are unsure about some "
    "aspect of your response.\n\n"
    "You MUST respond in valid JSON with exactly two keys:\n"
    '  {"answer": "<your full response here>", "confidence": <float 0.0-1.0>}\n\n'
    "Do not include anything outside the JSON object."
)


def decrement_budget(tokens_consumed: int) -> None:
    """Subtract consumed tokens from pipeline budgets."""
    global pipeline_user_budget, pipeline_org_budget
    pipeline_user_budget = max(0, pipeline_user_budget - tokens_consumed)
    pipeline_org_budget  = max(0, pipeline_org_budget  - tokens_consumed)


# ── Per-request logic ─────────────────────────────────────────────────────────


def call_bedrock(prompt_entry):
    """Cascading + prompt caching + budget degradation."""
    prompt_id    = prompt_entry["id"]
    instructions = prompt_entry["instructions"]
    prompt_text  = prompt_entry["text"]

    total_cost = Decimal("0")
    system_blocks = build_system_with_cache_checkpoint(instructions)
    # Append cascade system prompt after the cachePoint
    system_blocks.append({"text": CASCADE_SYSTEM_PROMPT})
    messages = [{"role": "user", "content": [{"text": prompt_text}]}]

    # Beer model attempt (with cached system prefix)
    beer_response = call_bedrock_with_retry(bedrock_client, config.BEER_MODEL_ID, messages, system=system_blocks)

    if beer_response is None:
        print(f"[{prompt_id}] Skipped — beer model call failed.")
        return {"id": prompt_id, "skipped": True}

    beer_in  = beer_response["usage"]["inputTokens"]
    beer_out = beer_response["usage"]["outputTokens"]
    total_cost += compute_request_cost(beer_in, beer_out, config.BEER_INPUT_COST_PER_1K_TOKENS, config.BEER_OUTPUT_COST_PER_1K_TOKENS)

    with _budget_lock:
        decrement_budget(beer_in + beer_out)

    raw_text = beer_response["output"]["message"]["content"][0]["text"]
    answer, confidence = parse_cascade_response(raw_text)

    # Decision: accept, escalate, or degrade
    escalated = False
    degraded = False
    champ_in = champ_out = None

    with _budget_lock:
        budget_available = pipeline_user_budget > 0

    if confidence < CONFIDENCE_THRESHOLD:
        if budget_available:
            champ_messages = [{"role": "user", "content": [{"text": instructions + "\n\n" + prompt_text}]}]
            champ_response = call_bedrock_with_retry(
                bedrock_client, config.CHAMPAGNE_MODEL_ID, champ_messages,
                inference_config={"maxTokens": 1024},
            )
            if champ_response is not None:
                escalated = True
                champ_in  = champ_response["usage"]["inputTokens"]
                champ_out = champ_response["usage"]["outputTokens"]
                total_cost += compute_request_cost(champ_in, champ_out, config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS)
                with _budget_lock:
                    decrement_budget(champ_in + champ_out)
        else:
            degraded = True

    # Naive baseline = champagne pricing on the actual work performed for this request.
    # Escalated: the real champagne stage tokens. Resolved (beer only) and DEGRADED
    # (low confidence but no budget to escalate, so the request stayed on beer):
    # champagne pricing on the beer tokens.
    if escalated:
        naive_cost = compute_request_cost(champ_in, champ_out, config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS)
    else:
        naive_cost = compute_request_cost(beer_in, beer_out, config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS)
    savings = max(Decimal("0"), naive_cost - total_cost)

    if escalated:
        status = f"ESCALATED (conf={confidence:.2f})"
    elif degraded:
        status = f"DEGRADED (conf={confidence:.2f})"
    else:
        status = f"RESOLVED (conf={confidence:.2f})"

    print(f"[{prompt_id}] {status}  |  Cost: ${total_cost:.6f}  |  Savings: ${savings:.6f}")
    dashboard_client.report_cost(
        model_name=config.CHAMPAGNE_MODEL_ID if escalated else config.BEER_MODEL_ID,
        input_tokens=beer_in, output_tokens=beer_out,
        cost_usd=total_cost, naive_cost_usd=naive_cost, request_label="05_pipeline.py",
    )
    return {"id": prompt_id, "cost": total_cost, "naive_cost": naive_cost, "escalated": escalated, "degraded": degraded, "confidence": confidence, "skipped": False}


if __name__ == "__main__":
    print("=" * 60)
    print("FULL PIPELINE — cascading + prompt caching + budget degradation")
    print("=" * 60)

    results = run_threaded_workload(call_bedrock, all_prompts, threads=10)

    completed = [r for r in results if not r.get("skipped")]
    escalated = sum(1 for r in completed if r["escalated"])
    degraded  = sum(1 for r in completed if r["degraded"])
    resolved  = len(completed) - escalated - degraded

    total_cost = sum(r["cost"] for r in completed)
    total_naive = sum(r["naive_cost"] for r in completed)
    # Sum per-request clamped savings so this matches the dashboard, which floors
    # each request's savings at $0 before accumulating.
    total_savings = sum(max(Decimal("0"), r["naive_cost"] - r["cost"]) for r in completed)
    savings_pct = (total_savings / total_naive * 100) if total_naive > 0 else 0

    print("\n" + "=" * 60)
    print(f"PIPELINE RESULTS")
    print(f"  Resolved: {resolved}  |  Escalated: {escalated}  |  Degraded: {degraded}")
    print(f"  Optimized: ${total_cost:.6f}  |  Naive: ${total_naive:.6f}  |  Savings: {savings_pct:.1f}%")
    print(f"  Budget: {pipeline_user_budget:,} App / {pipeline_org_budget:,} org")
    print("=" * 60)

    if savings_pct >= config.MIN_SAVINGS_PERCENT:
        print(f"\n  *** {savings_pct:.1f}% savings vs. all-champagne ***")
