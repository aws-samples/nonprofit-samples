"""Model routing.

A classifier looks at each request and picks the model for it: cheap tasks go
to the beer model, and complex ones go to the champagne model. The classifier
itself is a cheap beer call, and its cost is folded into the request.
"""

import threading
from decimal import Decimal

import config
import dashboard_client
from helpers import load_prompts, setup_bedrock_client, compute_request_cost, call_bedrock_with_retry
from parallel import run_threaded_workload

all_prompts = load_prompts()
bedrock_client = setup_bedrock_client()
dashboard_client.reset_dashboard()

# ── Classifier ────────────────────────────────────────────────────────────────

CLASSIFIER_TIMEOUT_SECONDS = 5.0
VALID_TASK_TYPES = {"classification", "extraction", "summarization", "complex_reasoning"}

CLASSIFY_SYSTEM_PROMPT = (
    "You are a task classifier. Classify the user request as exactly one of: "
    "classification, extraction, summarization, complex_reasoning. "
    "Reply with only the label, nothing else."
)


def classify_request(bedrock_client, prompt_text: str):
    """Classify a prompt into one of four task types using the beer model.

    Returns a (task_type, classifier_cost) tuple. The classifier is a beer
    converse call; its usage is priced at beer rates and folded into the
    logical request's cost as pure optimization overhead. On timeout or
    failure the request falls back to complex_reasoning and the classifier
    cost is $0 so a failed classification never inflates the reported cost.
    """
    result_container = [None]
    usage_container = [None]

    def run_classifier():
        try:
            snippet = prompt_text[:300]
            messages = [{"role": "user", "content": [{"text": CLASSIFY_SYSTEM_PROMPT + "\n\nRequest: " + snippet}]}]
            response = bedrock_client.converse(modelId=config.BEER_MODEL_ID, messages=messages)
            usage_container[0] = response["usage"]
            result_container[0] = response["output"]["message"]["content"][0]["text"].strip().lower()
        except Exception:
            pass  # nosec B110

    t = threading.Thread(target=run_classifier, daemon=True)
    t.start()
    t.join(timeout=CLASSIFIER_TIMEOUT_SECONDS)

    # Price the classifier call at beer rates. A timed-out or failed
    # classification contributes $0 classifier cost.
    classifier_cost = Decimal("0")
    if usage_container[0] is not None:
        classifier_cost = compute_request_cost(
            usage_container[0]["inputTokens"], usage_container[0]["outputTokens"],
            config.BEER_INPUT_COST_PER_1K_TOKENS, config.BEER_OUTPUT_COST_PER_1K_TOKENS,
        )

    if t.is_alive() or result_container[0] is None:
        return "complex_reasoning", classifier_cost
    if result_container[0] not in VALID_TASK_TYPES:
        return "complex_reasoning", classifier_cost
    return result_container[0], classifier_cost


def select_model_for_task(task_type: str) -> str:
    """Return the Bedrock model ID for the given task type."""
    return config.ROUTING_TABLE.get(task_type, config.CHAMPAGNE_MODEL_ID)


# ── Per-request logic ─────────────────────────────────────────────────────────


def call_bedrock(prompt_entry):
    """Classify, route, call Bedrock, report cost."""
    prompt_id   = prompt_entry["id"]
    prompt_text = prompt_entry["instructions"] + "\n\n" + prompt_entry["text"]

    task_type, classifier_cost = classify_request(bedrock_client, prompt_text)
    selected_model = select_model_for_task(task_type)

    messages = [{"role": "user", "content": [{"text": prompt_text}]}]
    response = call_bedrock_with_retry(bedrock_client, selected_model, messages)

    if response is None:
        print(f"[{prompt_id}] Skipped — Bedrock call failed.")
        return {"id": prompt_id, "skipped": True}

    input_tokens  = response["usage"]["inputTokens"]
    output_tokens = response["usage"]["outputTokens"]

    actual_input_cost, actual_output_cost = config.MODEL_COSTS[selected_model]
    # Optimized cost folds the classifier's beer-priced overhead into the
    # single reported cost for this logical request.
    routed_cost = compute_request_cost(input_tokens, output_tokens, actual_input_cost, actual_output_cost)
    cost = routed_cost + classifier_cost
    # Naive baseline is champagne pricing on the routed call's tokens only.
    # The classifier is optimization overhead and belongs only on the cost side.
    naive_cost = compute_request_cost(
        input_tokens, output_tokens,
        config.CHAMPAGNE_INPUT_COST_PER_1K_TOKENS, config.CHAMPAGNE_OUTPUT_COST_PER_1K_TOKENS,
    )
    savings = naive_cost - cost

    print(f"[{prompt_id}] {task_type} -> {selected_model}  |  Cost: ${cost:.6f}  |  Savings: ${savings:.6f}")
    dashboard_client.report_cost(
        model_name=selected_model, input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=cost, naive_cost_usd=naive_cost, request_label="02_router.py",
    )
    return {"id": prompt_id, "cost": cost, "naive_cost": naive_cost, "skipped": False}


if __name__ == "__main__":
    print("=" * 60)
    print("Model routing: classifying each request and sending it to the right model")
    print("=" * 60)

    results = run_threaded_workload(call_bedrock, all_prompts, threads=10)

    completed = [r for r in results if not r["skipped"]]
    total_cost = sum(r["cost"] for r in completed)
    total_naive = sum(r["naive_cost"] for r in completed)
    savings_pct = ((total_naive - total_cost) / total_naive * 100) if total_naive > 0 else 0

    print("\n" + "=" * 60)
    print(f"ROUTING SUMMARY")
    print(f"  Optimized: ${total_cost:.6f}  |  Naive: ${total_naive:.6f}  |  Savings: {savings_pct:.1f}%")
    print("=" * 60)
