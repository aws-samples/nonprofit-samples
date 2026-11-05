# ========== DASHBOARD CLIENT ==========
# This file provides a single function: report_cost()
# Feature files call this after each Bedrock response to send cost data to the dashboard.
# If the dashboard is not running, this file prints a warning and returns — never raises.

import requests
from decimal import Decimal

import config


RESET_URL = config.DASHBOARD_URL.replace("/api/cost", "/api/reset")


def reset_dashboard() -> None:
    """POST to the dashboard reset endpoint to clear all session metrics.

    Called at the start of each feature file so the audience always sees a
    fresh $0.00 baseline regardless of which file ran before.
    Never raises — dashboard is optional.
    """
    try:
        requests.post(RESET_URL, timeout=1.0)
    except Exception:
        pass  # nosec B110 — dashboard is optional


def report_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: Decimal,
    naive_cost_usd: Decimal,
    request_label: str,
) -> None:
    """Send cost data for one completed Bedrock request to the dashboard.

    Parameters
    ----------
    model_name     : The Bedrock model ID used for this request (e.g., "amazon.nova-lite-v1:0")
    input_tokens   : Number of input tokens consumed
    output_tokens  : Number of output tokens generated
    cost_usd       : Exact optimized cost in USD as a Decimal value
    naive_cost_usd : The all-champagne baseline cost in USD as a Decimal value. This is
                     champagne pricing applied to the actual work performed for this
                     request. The dashboard computes savings as
                     max(0, naive_cost_usd - cost_usd).
    request_label  : Human-readable label, typically the feature filename (e.g., "02_router.py")

    This function never raises. If the dashboard is unreachable for any reason, it prints
    a warning to stdout and returns normally so that feature files continue running on stage.
    """

    # Build the JSON payload — serialize cost_usd as a string to preserve Decimal precision
    # (JSON does not support Decimal; float conversion would introduce rounding errors)
    payload = {
        "model_name":     model_name,
        "input_tokens":   input_tokens,
        "output_tokens":  output_tokens,
        "cost_usd":       str(cost_usd),        # Decimal -> string, e.g. "0.003142"
        "naive_cost_usd": str(naive_cost_usd),  # Decimal -> string, the all-champagne baseline
        "request_label":  request_label,
    }

    try:
        # Send cost data to the dashboard — timeout=1.0 prevents a slow dashboard
        # from blocking demo progress
        requests.post(config.DASHBOARD_URL, json=payload, timeout=1.0)

    except Exception:
        # The dashboard is optional — feature files run fine without it.
        # Any network error, timeout, or unexpected exception is caught here.
        print("Warning: dashboard not reachable, continuing without cost reporting...")
