"""Model cascading.

We try the cheaper beer model first. If it isn't confident in its answer
(below 0.8), we send the same request to the champagne model and use that
answer instead.
"""

from decimal import Decimal

import config
import dashboard_client
from helpers import (
    load_prompts, setup_bedrock_client, compute_request_cost,
    call_bedrock_with_retry, parse_cascade_response,
)
from parallel import run_threaded_workload

all_prompts = load_prompts()
bedrock_client = setup_bedrock_client()
dashboard_client.reset_dashboard()

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


def call_bedrock(prompt_entry):
    """Try beer first; escalate to champagne if confidence < threshold."""
    prompt_id   = prompt_entry["id"]
    prompt_text = prompt_entry["instructions"] + "\n\n" + prompt_entry["text"]

    total_cost = Decimal("0")
    messages = [{"role": "user", "content": [{"text": prompt_text}]}]
    system = [{"text": CASCADE_SYSTEM_PROMPT}]

    # Beer model attempt
    beer_response = call_bedrock_with_retry(bedrock_client, config.BEER_MODEL_ID, messages, system=system)

    if beer_response is None:
        print(f"[{prompt_id}] Skipped — beer model call failed.")
        return {"id": prompt_id, "skipped": True}

    beer_in  = beer_response["usage"]["inputTokens"]
    beer_out = beer_response["usage"]["outputTokens"]
    total_cost += compute_request_cost(beer_in, beer_out, config.BEER_INPUT_COST_PER_1K_TOKENS, config.BEER_OUTPUT_COST_PER_1K_TOKENS)

    raw_text = beer_response["output"]["message"]["content"][0]["text"]
    answer, confidence = parse_cascade_response(raw_text)

    escalated = False
    champ_in = champ_out = None
    if confidence < CONFIDENCE_THRESHOLD:
        champ_messages = [{"role": "user", "content": [{"text": prompt_text}]}]
        champ_response = call_bedrock_with_retry(
            bedrock_client, config.CHAMPAGNE_MODEL_ID, champ_messages,
            inference_config={"maxTokens": 1024},
        )
        if champ_response is not None:
            escalated = True
            champ_in  = champ_response["usage"]["inputTokens"]
            champ_out = champ_response["usage"]["outputTokens"]
            total_cost += compute_request_cost(champ_in, champ_out, config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS)

    # Naive baseline = champagne pricing on the actual work performed for this request.
    # Escalated: the real champagne stage tokens. Resolved (beer only, incl. a failed
    # escalation that stayed on beer): champagne pricing on the beer tokens.
    if escalated:
        naive_cost = compute_request_cost(champ_in, champ_out, config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS)
    else:
        naive_cost = compute_request_cost(beer_in, beer_out, config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS)
    savings = max(Decimal("0"), naive_cost - total_cost)
    status = f"ESCALATED (conf={confidence:.2f})" if escalated else f"RESOLVED (conf={confidence:.2f})"

    print(f"[{prompt_id}] {status}  |  Cost: ${total_cost:.6f}  |  Savings: ${savings:.6f}")
    dashboard_client.report_cost(
        model_name=config.CHAMPAGNE_MODEL_ID if escalated else config.BEER_MODEL_ID,
        input_tokens=beer_in, output_tokens=beer_out,
        cost_usd=total_cost, naive_cost_usd=naive_cost, request_label="02_cascading.py",
    )
    return {"id": prompt_id, "cost": total_cost, "naive_cost": naive_cost, "escalated": escalated, "confidence": confidence, "skipped": False}


if __name__ == "__main__":
    print("=" * 60)
    print("Model cascading: trying the beer model first, escalating to champagne when confidence is low")
    print("=" * 60)

    results = run_threaded_workload(call_bedrock, all_prompts, threads=10)

    completed = [r for r in results if not r["skipped"]]
    escalated_count = sum(1 for r in completed if r["escalated"])
    total_cost = sum(r["cost"] for r in completed)
    total_naive = sum(r["naive_cost"] for r in completed)
    # Sum per-request clamped savings so this matches the dashboard, which floors
    # each request's savings at $0 before accumulating.
    total_savings = sum(max(Decimal("0"), r["naive_cost"] - r["cost"]) for r in completed)
    savings_pct = (total_savings / total_naive * 100) if total_naive > 0 else 0

    print("\n" + "=" * 60)
    print(f"CASCADING SUMMARY")
    print(f"  Resolved: {len(completed) - escalated_count}  |  Escalated: {escalated_count}")
    print(f"  Optimized: ${total_cost:.6f}  |  Naive: ${total_naive:.6f}  |  Savings: {savings_pct:.1f}%")
    print("=" * 60)
