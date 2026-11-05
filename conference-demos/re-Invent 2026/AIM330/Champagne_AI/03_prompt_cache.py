"""Native prompt caching.

We put a cachePoint on the system prompt so Bedrock caches that prefix. You pay
a bit extra to write the cache the first time (1.25x), but every request after
reuses it and the cached input tokens cost 90% less.
"""

from decimal import Decimal

import config
import dashboard_client
from helpers import load_prompts, setup_bedrock_client, build_system_with_cache_checkpoint, call_bedrock_with_retry
from parallel import run_threaded_workload

all_prompts = load_prompts()
bedrock_client = setup_bedrock_client()
dashboard_client.reset_dashboard()


def compute_request_cost_with_cache(
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    input_cost_per_1k, output_cost_per_1k, cache_read_cost_per_1k, cache_write_cost_per_1k,
) -> Decimal:
    """Cost accounting that separates cached vs uncached input tokens."""
    return (
        (Decimal(input_tokens) / 1000) * input_cost_per_1k
        + (Decimal(output_tokens) / 1000) * output_cost_per_1k
        + (Decimal(cache_read_tokens) / 1000) * cache_read_cost_per_1k
        + (Decimal(cache_write_tokens) / 1000) * cache_write_cost_per_1k
    )


def call_bedrock(prompt_entry):
    """Send one prompt with cached system prefix. Returns a result dict."""
    prompt_id    = prompt_entry["id"]
    instructions = prompt_entry["instructions"]
    prompt_text  = prompt_entry["text"]

    system_blocks = build_system_with_cache_checkpoint(instructions)
    messages = [{"role": "user", "content": [{"text": prompt_text}]}]

    response = call_bedrock_with_retry(bedrock_client, config.CHAMPAGNE_MODEL_ID, messages, system=system_blocks)

    if response is None:
        print(f"[{prompt_id}] Skipped — Bedrock call failed.")
        return {"id": prompt_id, "skipped": True}

    usage = response["usage"]
    input_tokens       = usage.get("inputTokens", 0)
    output_tokens      = usage.get("outputTokens", 0)
    cache_read_tokens  = usage.get("cacheReadInputTokens", 0)
    cache_write_tokens = usage.get("cacheWriteInputTokens", 0)

    cost = compute_request_cost_with_cache(
        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        config.CHAMPAGNE_CACHE_READ_COST_PER_1K_TOKENS, config.CHAMPAGNE_CACHE_WRITE_COST_PER_1K_TOKENS,
    )

    all_input = input_tokens + cache_read_tokens + cache_write_tokens
    naive_cost = (Decimal(all_input) / 1000) * config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS + (Decimal(output_tokens) / 1000) * config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS
    savings = max(Decimal("0"), naive_cost - cost)

    status = f"CACHE HIT ({cache_read_tokens} tokens)" if cache_read_tokens > 0 else f"CACHE WRITE ({cache_write_tokens} tokens)"
    print(f"[{prompt_id}] {status}  |  Cost: ${cost:.6f}  |  Savings: ${savings:.6f}")

    dashboard_client.report_cost(
        model_name=config.CHAMPAGNE_MODEL_ID, input_tokens=all_input,
        output_tokens=output_tokens, cost_usd=cost, naive_cost_usd=naive_cost,
        request_label="03_prompt_cache.py",
    )
    return {"id": prompt_id, "cost": cost, "naive_cost": naive_cost, "cache_read_tokens": cache_read_tokens, "cache_write_tokens": cache_write_tokens, "skipped": False}


if __name__ == "__main__":
    print("=" * 60)
    print("Native prompt caching: cached input tokens cost 90% less")
    print(f"  Model: {config.CHAMPAGNE_MODEL_ID}")
    print("=" * 60)

    results = run_threaded_workload(call_bedrock, all_prompts, threads=10)

    completed = [r for r in results if not r["skipped"]]
    total_cost = sum(r["cost"] for r in completed)
    total_naive = sum(r["naive_cost"] for r in completed)
    total_reads = sum(r["cache_read_tokens"] for r in completed)
    total_writes = sum(r["cache_write_tokens"] for r in completed)
    savings_pct = ((total_naive - total_cost) / total_naive * 100) if total_naive > 0 else 0

    print("\n" + "=" * 60)
    print(f"PROMPT CACHING SUMMARY")
    print(f"  Cache reads: {total_reads:,} tokens  |  Cache writes: {total_writes:,} tokens")
    print(f"  Optimized: ${total_cost:.6f}  |  Naive: ${total_naive:.6f}  |  Savings: {savings_pct:.1f}%")
    print("=" * 60)
