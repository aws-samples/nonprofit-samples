"""Semantic cache.

Embed each prompt's variable content with Titan Embeddings V2. Before calling
champagne, check whether a semantically similar prompt has already been
answered. On a hit we return the stored answer and only pay for the embedding
call, skipping the champagne call entirely.

How it works:
  1. Embed only the prompt's *variable* text (not the shared instructions —
     see note below) with Titan Embeddings V2.
  2. Compare against every cached entry via cosine similarity.
  3. Above SIMILARITY_THRESHOLD -> cache HIT: return the stored answer, only
     pay for the embedding call.
  4. Below threshold -> cache MISS: call champagne, store the embedding and
     the answer for future lookups.
"""

# Why embed prompt_entry["text"] and not instructions + text:
#   In this demo, many prompts of the same task_type share byte-identical
#   instructions (e.g. every classification prompt uses the same triage
#   rubric). That shared block dominates the embedding and drowns out the
#   part that actually varies, pushing unrelated prompts (e.g. p01's estate
#   gift email vs p02's receipt request) to 0.8+ similarity purely because
#   they share instructions. Embedding text alone separates genuinely
#   different scenarios (~0.03-0.89 similarity) from true duplicates (~1.0),
#   which is what SIMILARITY_THRESHOLD needs to discriminate on.
#
# Why this beats naive:
#   - A cache hit skips the champagne call entirely (not just the input side
#     of it, unlike compression). Both input AND output cost disappear.
#   - Donor emails, grant letters, and survey recaps repeat in structure and
#     substance across a real caseload, which is exactly where semantic caching
#     helps and where literal prefix caching does not.
#   - Unlike literal prompt caching (03_prompt_cache.py), this catches prompts
#     that are reworded or reordered but mean the same thing, not just byte-
#     identical prefixes.

import json
import threading
import time
from decimal import Decimal

import numpy as np

import config
import dashboard_client
from helpers import load_prompts, setup_bedrock_client, compute_request_cost, call_bedrock_with_retry
from parallel import run_threaded_workload

all_prompts = load_prompts()
bedrock_client = setup_bedrock_client()
dashboard_client.reset_dashboard()

EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"

# Titan Embeddings V2 pricing lives in config.py alongside the champagne/beer
# rates (config.EMBEDDING_COST_PER_1K_TOKENS = $0.02 per 1M tokens).

# Measured against this demo's prompt set: distinct-but-similar-genre prompts
# (e.g. several different volunteer surveys) land as high as ~0.886 cosine
# similarity when embedding text alone; true/near-true duplicates land at
# ~1.0. config.SIMILARITY_THRESHOLD (0.75) sits inside that false-positive
# band. This file is set to 0.90, which clears the highest measured
# same-genre pair (p08 vs p19, ~0.886) with margin while staying looser
# than the 0.95 fully-safe cutoff.
CACHE_SIMILARITY_THRESHOLD = 0.90

# ── Cache state ────────────────────────────────────────────────────────────────
# Each entry: {"embedding": np.ndarray, "answer": str, "prompt_id": str}
_cache_lock = threading.Lock()
_cache_entries = []


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return cosine similarity between two embedding vectors."""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _embed_text(text: str):
    """Call Titan Embeddings V2. Returns (embedding, input_tokens) or (None, 0) on failure."""
    try:
        response = bedrock_client.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=json.dumps({"inputText": text}),
        )
        body = json.loads(response["body"].read())
        embedding = np.array(body["embedding"], dtype=np.float64)
        input_tokens = body.get("inputTextTokenCount", 0)
        return embedding, input_tokens
    except Exception:
        return None, 0


def _extract_answer_text(response: dict) -> str:
    """Return the first text block from a converse() response.

    Reasoning-capable models (e.g. Claude Opus with extended thinking) may
    prepend a reasoningContent block before the actual answer, so content[0]
    is not reliably the answer — scan for the first block with a "text" key.
    """
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            return block["text"]
    return ""


def _find_cache_hit_or_reserve(embedding: np.ndarray):
    """Atomically check for a cache hit; if it's a miss, reserve this slot immediately.

    Reserving on read (inserting a placeholder entry before the champagne call
    returns) closes the race that plain read-then-write threading has: two
    threads embedding near-duplicate prompts at the same time could otherwise
    both see a miss and both pay for a champagne call. The placeholder is
    filled in with the real answer once the champagne call completes.

    Returns (hit_entry, score, placeholder) where hit_entry is None on a miss
    and placeholder is the dict this call reserved and must fill in via
    _fill_cache_entry, or None on a hit.
    """
    with _cache_lock:
        best_entry = None
        best_score = 0.0
        for entry in _cache_entries:
            score = _cosine_similarity(embedding, entry["embedding"])
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is not None and best_score >= CACHE_SIMILARITY_THRESHOLD:
            return best_entry, best_score, None

        # Miss — reserve a placeholder slot now, while still holding the lock,
        # so no other thread can also miss against this same prompt.
        placeholder = {"embedding": embedding, "answer": None, "prompt_id": None}
        _cache_entries.append(placeholder)
        if len(_cache_entries) > config.MAX_CACHE_ENTRIES:
            _cache_entries.pop(0)
        return None, best_score, placeholder


def _fill_cache_entry(placeholder: dict, answer: str, prompt_id: str) -> None:
    """Fill in a reserved placeholder entry once the champagne call completes."""
    with _cache_lock:
        placeholder["answer"] = answer
        placeholder["prompt_id"] = prompt_id


def call_bedrock(prompt_entry):
    """Check the semantic cache before paying for a champagne call."""
    prompt_id     = prompt_entry["id"]
    instructions  = prompt_entry["instructions"]
    original_text = prompt_entry["text"]
    full_prompt   = instructions + "\n\n" + original_text

    # Embed only the variable text, not instructions — see module docstring.
    embedding, embed_tokens = _embed_text(original_text)
    if embedding is None:
        print(f"[{prompt_id}] Skipped — embedding call failed.")
        return {"id": prompt_id, "skipped": True}

    embed_cost = (Decimal(embed_tokens) / 1000) * config.EMBEDDING_COST_PER_1K_TOKENS

    hit_entry, score, placeholder = _find_cache_hit_or_reserve(embedding)

    # A hit against an entry whose answer hasn't landed yet (another thread's
    # in-flight miss) means we raced it. Wait briefly for it to fill in rather
    # than fabricating a champagne call for what is really the same request.
    if hit_entry is not None:
        wait_deadline = time.monotonic() + 30
        while hit_entry["answer"] is None and time.monotonic() < wait_deadline:
            time.sleep(0.05)

    if hit_entry is not None and hit_entry["answer"] is not None:
        total_cost = embed_cost

        # Naive baseline: what champagne would have charged on a fresh call for
        # this exact prompt. We skipped that call, so this is an approximation —
        # no champagne call actually occurred. We approximate input tokens from
        # word count (roughly 1.3 tokens/word for English prose) and use the
        # matched entry's actual output length as the output estimate, priced at
        # champagne rates. This estimated baseline is reported to the dashboard
        # as naive_cost_usd so the avoided generation is credited as savings.
        approx_in_tokens = int(len(full_prompt.split()) * 1.3)
        approx_out_tokens = max(1, len(hit_entry["answer"].split()))
        naive_cost = compute_request_cost(
            approx_in_tokens, approx_out_tokens,
            config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
        )
        savings = max(Decimal("0"), naive_cost - total_cost)

        print(
            f"[{prompt_id}] CACHE HIT (sim={score:.3f}, matched {hit_entry['prompt_id']})  |  "
            f"Cost: ${total_cost:.6f}  |  Savings: ${savings:.6f}"
        )
        dashboard_client.report_cost(
            model_name=EMBEDDING_MODEL_ID, input_tokens=embed_tokens, output_tokens=0,
            cost_usd=total_cost, naive_cost_usd=naive_cost, request_label="03_semantic_cache.py",
        )
        return {"id": prompt_id, "cost": total_cost, "naive_cost": naive_cost, "cache_hit": True, "skipped": False}

    # --- Cache miss (or a race we gave up waiting on): call champagne ---
    if placeholder is None:
        # We waited on someone else's placeholder and it never filled in
        # (their champagne call must have failed). Reserve our own slot now.
        _, score, placeholder = _find_cache_hit_or_reserve(embedding)

    messages = [{"role": "user", "content": [{"text": full_prompt}]}]
    response = call_bedrock_with_retry(
        bedrock_client, config.CHAMPAGNE_MODEL_ID, messages,
        inference_config={"maxTokens": 1024},
    )

    if response is None:
        print(f"[{prompt_id}] Skipped — champagne call failed.")
        return {"id": prompt_id, "skipped": True}

    champ_in  = response["usage"]["inputTokens"]
    champ_out = response["usage"]["outputTokens"]
    champ_cost = compute_request_cost(
        champ_in, champ_out,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )
    total_cost = embed_cost + champ_cost
    naive_cost = champ_cost  # naive pays the identical champagne call, no embedding overhead
    savings = max(Decimal("0"), naive_cost - total_cost)  # negative on a miss, clamped to 0

    answer = _extract_answer_text(response)
    _fill_cache_entry(placeholder, answer, prompt_id)

    print(
        f"[{prompt_id}] CACHE MISS (best sim={score:.3f})  |  "
        f"Cost: ${total_cost:.6f}  |  Savings: ${savings:.6f}"
    )
    dashboard_client.report_cost(
        model_name=config.CHAMPAGNE_MODEL_ID, input_tokens=champ_in, output_tokens=champ_out,
        cost_usd=total_cost, naive_cost_usd=naive_cost, request_label="03_semantic_cache.py",
    )
    return {"id": prompt_id, "cost": total_cost, "naive_cost": naive_cost, "cache_hit": False, "skipped": False}


if __name__ == "__main__":
    print("=" * 60)
    print("SEMANTIC CACHE — skip champagne entirely on a similarity hit")
    print(f"  Similarity threshold: {CACHE_SIMILARITY_THRESHOLD}")
    print("=" * 60)

    results = run_threaded_workload(call_bedrock, all_prompts, threads=10)

    completed = [r for r in results if not r["skipped"]]
    hits = sum(1 for r in completed if r["cache_hit"])
    misses = len(completed) - hits
    total_cost = sum(r["cost"] for r in completed)
    total_naive = sum(r["naive_cost"] for r in completed)
    # Accumulate per-request clamped savings (max(0, naive - cost)) so this
    # matches the dashboard, which floors each request's savings at $0.
    total_savings = sum(max(Decimal("0"), r["naive_cost"] - r["cost"]) for r in completed)
    savings_pct = (total_savings / total_naive * 100) if total_naive > 0 else 0

    print("\n" + "=" * 60)
    print(f"SEMANTIC CACHE SUMMARY")
    print(f"  Cache hits: {hits}  |  Cache misses: {misses}")
    print(f"  Optimized: ${total_cost:.6f}  |  Naive: ${total_naive:.6f}  |  Savings: {savings_pct:.1f}%")
    print("=" * 60)
