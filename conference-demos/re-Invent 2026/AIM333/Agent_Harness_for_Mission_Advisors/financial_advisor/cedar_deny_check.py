# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Stage 3 Cedar deny-path check — direct-to-Gateway, bypassing the Harness and model.

Sends a SigV4-signed MCP tools/call straight to the Gateway's /mcp endpoint, so
the result isolates the Gateway's Cedar policy enforcement alone — not the
model's own judgment or the skill's instructions. This is the honest way to
prove Cedar is real: the model is not in the loop at all.

Runs two calls against rate-engine___rate_engine, printed as a plain-English
narrative rather than raw JSON-RPC/HTTP, so the result can be read aloud
on stage without translating error codes live:
  - ALLOW: purpose="retirement-savings" (an in-domain value a Cedar policy
    permits) -> prints the synthetic rate the Gateway returned.
  - DENY:  purpose="debt-consolidation" (out of the permitted set) -> prints
    that Cedar blocked the call before it ever reached the Lambda.

Read-only in effect: rate-engine is a synthetic lookup Lambda; the denied call
never even reaches it. Nothing is created, modified, or deleted.

Usage:
    python cedar_deny_check.py            # plain narrative output
    python cedar_deny_check.py --verbose  # also print the raw JSON-RPC payload/error

Requires AWS credentials for the demo account (the same creds used by deploy.py
/ test_invoke.py) and the shared Gateway to be deployed and in ENFORCE mode.
"""

import json
import sys

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

import config

# The Gateway tool being exercised and the arguments for each case. The tool
# name is the Gateway tool name "{target}___{tool}" (gateway-tool-naming.md).
TOOL_NAME = "rate-engine___rate_engine"
ALLOW_ARGS = {"purpose": "retirement-savings"}   # permitted by RateEngineScope Cedar policy
DENY_ARGS = {"purpose": "debt-consolidation"}     # not in the permitted set -> denied by default

# SigV4 signing service name for AgentCore data-plane (InvokeGateway).
SIGNING_SERVICE = "bedrock-agentcore"


def resolve_gateway_url() -> str:
    control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)
    for page in control.get_paginator("list_gateways").paginate():
        for gw in page.get("items", []):
            if gw.get("name") == config.GATEWAY_NAME:
                detail = control.get_gateway(gatewayIdentifier=gw["gatewayId"])
                url = detail.get("gatewayUrl")
                if not url:
                    sys.exit("Gateway has no gatewayUrl; is it READY?")
                return url
    sys.exit(f"No gateway named {config.GATEWAY_NAME!r} found. Run deploy.py first.")


def signed_tools_call(gateway_url: str, tool_name: str, arguments: dict) -> requests.Response:
    """SigV4-sign and POST a single MCP tools/call to the Gateway /mcp endpoint."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
    )
    headers = {
        "Content-Type": "application/json",
        # MCP streamable-http endpoints expect the client to accept both.
        "Accept": "application/json, text/event-stream",
    }

    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        sys.exit("No AWS credentials found. Configure creds for the demo account first.")

    aws_req = AWSRequest(method="POST", url=gateway_url, data=body, headers=headers)
    SigV4Auth(credentials.get_frozen_credentials(), SIGNING_SERVICE, config.AWS_REGION).add_auth(aws_req)

    return requests.post(gateway_url, data=body, headers=dict(aws_req.headers), timeout=30)


def _parse_payload(resp: requests.Response) -> dict | None:
    """Parse the JSON-RPC envelope out of a streamable-http response.

    May arrive as an SSE "data:" line or as a plain JSON body; handle both.
    """
    text = resp.text.strip()
    if text.startswith("data:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    pass
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _rate_from_content(content: list) -> str | None:
    """Dig the synthetic rate payload out of a successful tool result's content blocks."""
    for block in content or []:
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if "annual_percentage_yield" in data:
            return f'{data["annual_percentage_yield"]}% APY \u2014 {data.get("product", "retirement product")}'
        if "annual_percentage_rate" in data:
            return f'{data["annual_percentage_rate"]}% APR \u2014 {data.get("product", "mortgage product")}'
    return None


def _print_call_detail(verbose: bool, tool_name: str, arguments: dict) -> None:
    """Show exactly what's being called, so --verbose answers "what request did that send?"."""
    if verbose:
        print(f"     Calling: {tool_name}  arguments={json.dumps(arguments)}")


def run_allow_case(gateway_url: str, verbose: bool) -> None:
    print("Scenario: the same member-101 retirement conversation from the plan demo.")
    print(f'Advisor asks for a retirement rate (purpose="{ALLOW_ARGS["purpose"]}")...')
    _print_call_detail(verbose, TOOL_NAME, ALLOW_ARGS)
    resp = signed_tools_call(gateway_url, TOOL_NAME, ALLOW_ARGS)
    payload = _parse_payload(resp)

    if payload and "error" not in payload:
        content = payload.get("result", {}).get("content")
        rate = _rate_from_content(content) if content else None
        if rate:
            print(f"  -> ALLOWED. Gateway returned: {rate}")
        else:
            print("  -> ALLOWED. Gateway returned a result.")
    else:
        print("  -> Unexpected: this in-policy call was NOT allowed. Check Cedar policy / Gateway state.")
    if verbose:
        print(f"     Raw response: {json.dumps(payload)[:400]}")
    print()


def run_deny_case(gateway_url: str, verbose: bool) -> None:
    print("Scenario: the advisor now tries to redirect that same lookup toward debt")
    print("consolidation \u2014 a purpose this Gateway was never configured to allow.")
    print(f'Advisor asks for a debt-consolidation rate (purpose="{DENY_ARGS["purpose"]}")...')
    print("  This purpose is outside what the RateEngineScope Cedar policy permits")
    print("  (retirement-savings and home-purchase only).")
    _print_call_detail(verbose, TOOL_NAME, DENY_ARGS)
    resp = signed_tools_call(gateway_url, TOOL_NAME, DENY_ARGS)
    payload = _parse_payload(resp)

    if payload and "error" in payload:
        print("  -> BLOCKED by Cedar at the Gateway. The tool call never reached the")
        print('     Lambda; the Gateway\'s policy engine denied it before it ran.')
        if verbose:
            err = payload["error"]
            print(f"     Cedar's message: {err.get('message')} (code {err.get('code')})")
    else:
        print("  -> Unexpected: this out-of-policy call was NOT blocked. Check Cedar policy state.")
    if verbose and payload and "error" not in payload:
        print(f"     Raw response: {json.dumps(payload)[:400]}")
    print()


def main() -> None:
    verbose = "--verbose" in sys.argv
    gateway_url = resolve_gateway_url()
    print(f"Gateway /mcp endpoint: {gateway_url}\n")
    print("Same tool, same Gateway, two requests. The only difference is the")
    print("purpose argument. No model is involved \u2014 this talks straight to the")
    print("Gateway, so the result is Cedar's policy decision alone.\n")

    run_allow_case(gateway_url, verbose)
    run_deny_case(gateway_url, verbose)

    print("Cedar enforces this by default: a request is denied unless a policy")
    print("explicitly allows it. Nothing had to be written to block debt-consolidation")
    print("\u2014 it was never permitted in the first place.")


if __name__ == "__main__":
    main()
