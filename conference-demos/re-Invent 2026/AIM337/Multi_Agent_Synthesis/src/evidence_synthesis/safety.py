# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from evidence_synthesis.config import GuardrailSettings


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    allowed: bool
    text: str
    action: str = "NONE"
    reason: str | None = None


class SafetyViolation(RuntimeError):
    def __init__(self, reason: str, decision: SafetyDecision) -> None:
        super().__init__(reason)
        self.reason = reason
        self.decision = decision


class SafetyGate(Protocol):
    async def evaluate_input(self, text: str) -> SafetyDecision: ...

    async def evaluate_output(self, text: str) -> SafetyDecision: ...


class MissingGuardrailGate:
    async def evaluate_input(self, text: str) -> SafetyDecision:
        del text
        return SafetyDecision(
            allowed=False,
            text="A configured Bedrock Guardrail is required for runtime requests.",
            action="GUARDRAIL_NOT_CONFIGURED",
        )

    async def evaluate_output(self, text: str) -> SafetyDecision:
        return await self.evaluate_input(text)


class LocalSafetyGate:
    def __init__(self, denied_phrases: tuple[str, ...] | None = None) -> None:
        self._denied_phrases = denied_phrases or (
            "deny flood insurance",
            "deny insurance",
            "determine individual eligibility",
            "make an individual medical decision",
            "make an individual legal decision",
        )

    async def evaluate_input(self, text: str) -> SafetyDecision:
        lowered = text.lower()
        match = next((phrase for phrase in self._denied_phrases if phrase in lowered), None)
        if match:
            return SafetyDecision(
                allowed=False,
                text=(
                    "This system supports evidence synthesis and planning. It does not make "
                    "individual eligibility, insurance, legal, medical, or financial decisions."
                ),
                action="LOCAL_POLICY_INTERVENED",
                reason=f"Denied topic matched: {match}",
            )
        return SafetyDecision(allowed=True, text=text)

    async def evaluate_output(self, text: str) -> SafetyDecision:
        return SafetyDecision(allowed=True, text=text)


class BedrockGuardrailGate:
    def __init__(self, settings: GuardrailSettings) -> None:
        import boto3
        from botocore.config import Config

        self._settings = settings
        config = Config(
            retries={"total_max_attempts": 1},
            connect_timeout=5,
            read_timeout=15,
            user_agent_appid="evidence-synthesis/0.1.0",
        )
        self._client: Any = boto3.Session(region_name=settings.region).client(
            "bedrock-runtime", config=config
        )

    async def evaluate_input(self, text: str) -> SafetyDecision:
        return await asyncio.to_thread(self._apply, text, "INPUT")

    async def evaluate_output(self, text: str) -> SafetyDecision:
        return await asyncio.to_thread(self._apply, text, "OUTPUT")

    def _apply(self, text: str, source: str) -> SafetyDecision:
        # Overlap bounded requests so policy phrases are not split at a boundary.
        for start in range(0, max(1, len(text)), 9800):
            decision = self._apply_part(text[start:start + 10_000], source)
            if not decision.allowed:
                return decision
        return SafetyDecision(allowed=True, text=text)

    def _apply_part(self, text: str, source: str) -> SafetyDecision:
        response = self._client.apply_guardrail(
            guardrailIdentifier=self._settings.guardrail_id,
            guardrailVersion=self._settings.guardrail_version,
            source=source,
            content=[{"text": {"text": text, "qualifiers": ["guard_content"]}}],
            outputScope="INTERVENTIONS",
        )
        action = str(response.get("action", "NONE"))
        if action != "GUARDRAIL_INTERVENED":
            return SafetyDecision(allowed=True, text=text, action=action)
        # Return the guardrail's replacement text when it intervenes.
        outputs = response.get("outputs", [])
        replacement_value = (
            outputs[0].get("text")
            if outputs and isinstance(outputs[0], dict)
            else "The request was blocked by the configured Bedrock Guardrail."
        )
        replacement = (
            str(replacement_value)
            if replacement_value is not None
            else "The request was blocked by the configured Bedrock Guardrail."
        )
        return SafetyDecision(
            allowed=False,
            text=replacement,
            action=action,
            reason="Bedrock Guardrail intervened.",
        )
