# Agent Harness for Mission Advisors

Demo code for **AIM333 — The Agent Harness: Unifying Fragmented Tools for Mission AI Advisors** (AWS re:Invent).

Shows one reusable Amazon Bedrock AgentCore Harness and Gateway serving two different mission-driven advisory domains, synthetic data only:

- **`financial_advisor/`** — a credit union financial advisor helping members plan for retirement and home buying (tools: `member-lookup`, `rate-engine`, `advisor-library`)
- **`benefits_navigator/`** — a nonprofit benefits navigator screening households for food-assistance and health-coverage eligibility (tools: `household-lookup`, `eligibility-engine`, `program-library`)

The core idea: swapping domains is a configuration change (S3 skill URI + Gateway tool allowlist), not an infrastructure rebuild. Both domains run on the same Harness, Gateway, AgentCore Policy (Cedar) authorization, Bedrock Guardrail, Memory, and Observability setup.

## Structure

Each domain folder is self-contained:

```
<domain>/
  config.py          # domain config (gateway targets, model, skill S3 prefix)
  aws_helpers.py      # shared AWS resource helpers
  deploy.py           # provisions Lambda targets + registers them on the shared Gateway
  cleanup.py          # tears down domain-specific resources
  test_invoke.py      # invokes the Harness with this domain's skill + tool allowlist
  lambda/              # synthetic tool Lambdas + their tool schemas
  skills/<domain>/SKILL.md   # the domain workflow skill, loaded from S3 at invoke time
  config/<domain>.json       # domain workflow config
```

`financial_advisor/` additionally has `cedar_deny_check.py` (direct-to-Gateway Cedar allow/deny proof, bypassing the model) and `tests/` (unit tests for the Lambda handlers).

## Status

Synthetic data only. This sample is for AWS re:Invent 2026 session AIM333, for educational use only.

> **For demonstration purposes only.** This sample is intended to illustrate the Agent Harness pattern and is not production-ready as-is. It requires additional security review and hardening (e.g. secrets management, input validation, rate limiting, monitoring) before any production deployment.

## License

This project is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
