# ========== SHARED HELPERS ==========
# Common utilities used across all demo files.
# Extracted here so the numbered files focus on strategy, not plumbing.

import json
import sys
import time
from decimal import Decimal

import boto3

import config


# ── Load prompts ──────────────────────────────────────────────────────────────

def load_prompts() -> list:
    """Load prompts from prompts.json. Exits on error."""
    try:
        with open("prompts.json", "r") as f:
            data = json.load(f)
        return data["prompts"]
    except FileNotFoundError:
        print("ERROR: prompts.json not found.")
        sys.exit(1)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"ERROR: prompts.json is malformed: {e}")
        sys.exit(1)


# ── Bedrock client setup ─────────────────────────────────────────────────────

def setup_bedrock_client():
    """Create and return a boto3 Bedrock runtime client."""
    return boto3.client("bedrock-runtime")


# ── Cost computation ──────────────────────────────────────────────────────────

def compute_request_cost(
    input_tokens: int,
    output_tokens: int,
    input_cost_per_1k: Decimal,
    output_cost_per_1k: Decimal,
) -> Decimal:
    """Return the exact USD cost for one Bedrock API call."""
    input_cost  = (Decimal(input_tokens)  / 1000) * input_cost_per_1k
    output_cost = (Decimal(output_tokens) / 1000) * output_cost_per_1k
    return input_cost + output_cost


# ── Bedrock call with retry ───────────────────────────────────────────────────

def call_bedrock_with_retry(client, model_id: str, messages: list,
                            system=None, inference_config=None):
    """Call Bedrock converse with exponential backoff retry.

    Args:
        client: boto3 bedrock-runtime client
        model_id: Bedrock model identifier
        messages: converse API messages list
        system: optional system blocks (for prompt caching)
        inference_config: optional inference config (e.g. maxTokens)

    Returns response dict on success, None after all retries exhausted.
    """
    retry_delays_seconds = [0.5, 1.0, 2.0]
    last_error = None

    for attempt_number in range(config.MAX_RETRIES + 1):
        try:
            kwargs = {"modelId": model_id, "messages": messages}
            if system:
                kwargs["system"] = system
            if inference_config:
                kwargs["inferenceConfig"] = inference_config
            return client.converse(**kwargs)
        except Exception as e:
            last_error = e
            if attempt_number < config.MAX_RETRIES:
                time.sleep(retry_delays_seconds[attempt_number])
            else:
                return None


# ── Prompt caching: build system blocks ───────────────────────────────────────

def build_system_with_cache_checkpoint(instructions: str) -> list:
    """Build cached system prefix: nonprofit context + instructions + cachePoint."""
    return [
        {"text": config.NONPROFIT_SYSTEM_PROMPT + "\n\n" + instructions},
        {"cachePoint": {"type": "default"}},
    ]


# ── Cascading: parse confidence response ─────────────────────────────────────

def parse_cascade_response(raw_text: str):
    """Parse JSON response with answer + confidence. Returns (answer, confidence)."""
    try:
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        data = json.loads(text)
        answer = data.get("answer", "")
        confidence = float(data.get("confidence", 0.0))
        return answer, confidence
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, 0.0
