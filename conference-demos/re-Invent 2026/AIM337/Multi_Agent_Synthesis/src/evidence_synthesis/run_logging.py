# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import logging
import time


def log_run_event(
    logger: logging.Logger,
    run_id: str,
    stage: str,
    status: str,
    *,
    emit_metrics: bool = False,
    **metadata: object,
) -> None:
    """Log progress and usage without source text or model responses."""
    payload: dict[str, object] = {
        "event": "evidence_synthesis",
        "run_id": run_id,
        "stage": stage,
        "status": status,
        **metadata,
    }
    if emit_metrics:
        payload.update({
            "Runtime": "evidence-synthesis",
            "Runs": 1,
            "TotalTokens": metadata.get("total_tokens", 0),
            "VerificationCycles": metadata.get("verification_cycles", 0),
            "SafetyInterventions": metadata.get("safety_interventions", 0),
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": "EvidenceSynthesis",
                    "Dimensions": [["Runtime"]],
                    "Metrics": [
                        {"Name": "Runs", "Unit": "Count"},
                        {"Name": "TotalTokens", "Unit": "Count"},
                        {"Name": "VerificationCycles", "Unit": "Count"},
                        {"Name": "SafetyInterventions", "Unit": "Count"},
                    ],
                }],
            },
        })
    logger.info(json.dumps(payload, sort_keys=True))
