# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

from evidence_synthesis.agentcore_client import AgentCoreRuntimeClient
from evidence_synthesis.models import RunRequest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Invoke the IAM-authenticated AgentCore evidence-synthesis runtime."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime-arn")
    parser.add_argument("--qualifier", default="live")
    parser.add_argument("--session-id")
    parser.add_argument("--region", default="us-east-1")
    return parser


def main() -> int:
    args = _parser().parse_args()
    session = boto3.Session(region_name=args.region)
    runtime_arn = args.runtime_arn or session.client("ssm").get_parameter(
        Name="/evidence-synthesis/runtime-arn"
    )["Parameter"]["Value"]
    session_id = args.session_id or f"evidence-synthesis-{uuid.uuid4().hex}"
    request = RunRequest.model_validate_json(
        args.manifest.resolve().read_text(encoding="utf-8")
    )
    config = Config(
        retries={"total_max_attempts": 1, "mode": "adaptive"},
        connect_timeout=5,
        read_timeout=600,
        user_agent_appid="evidence-synthesis/0.1.0",
    )
    client = AgentCoreRuntimeClient(
        session.client("bedrock-agentcore", config=config)
    )
    state = client.invoke(
        request,
        runtime_arn=runtime_arn,
        qualifier=args.qualifier,
        session_id=session_id,
    )
    print(state.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
