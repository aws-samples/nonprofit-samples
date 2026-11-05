# ========== NAIVE BASELINE ==========
# Every request goes straight to the champagne model, with no routing, caching,
# or budget in the way. This is the cost we compare everything else against.

from decimal import Decimal

import config
import dashboard_client
from helpers import load_prompts, setup_bedrock_client, compute_request_cost, call_bedrock_with_retry
from parallel import run_threaded_workload

all_prompts = load_prompts()
bedrock_client = setup_bedrock_client()
dashboard_client.reset_dashboard()


def call_bedrock(prompt_entry):
    """Send one prompt to the champagne model. Returns a result dict."""
    prompt_id   = prompt_entry["id"]
    prompt_text = prompt_entry["instructions"] + "\n\n" + prompt_entry["text"]
    messages = [{"role": "user", "content": [{"text": prompt_text}]}]

    response = call_bedrock_with_retry(bedrock_client, config.CHAMPAGNE_MODEL_ID, messages)

    if response is None:
        print(f"[{prompt_id}] Skipped — Bedrock call failed.")
        return {"id": prompt_id, "skipped": True}

    input_tokens  = response["usage"]["inputTokens"]
    output_tokens = response["usage"]["outputTokens"]
    cost = compute_request_cost(
        input_tokens, output_tokens,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS,
        config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )

    print(f"[{prompt_id}] Tokens: {input_tokens} in / {output_tokens} out  |  Cost: ${cost:.6f}")
    dashboard_client.report_cost(
        model_name=config.CHAMPAGNE_MODEL_ID,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=cost,
        naive_cost_usd=cost,  # savings $0 by design: no optimization, baseline == actual cost
        request_label="01_naive.py",
    )
    return {"id": prompt_id, "cost": cost, "skipped": False}


if __name__ == "__main__":
    print("=" * 60)
    print("Naive baseline: sending every prompt to the champagne model")
    print("=" * 60)

    results = run_threaded_workload(call_bedrock, all_prompts, threads=10)
    total_cost = sum(r["cost"] for r in results if not r["skipped"])

    print("\n" + "=" * 60)
    print(f"TOTAL COST (naive — all champagne): ${total_cost:.6f}")
    print("=" * 60)
