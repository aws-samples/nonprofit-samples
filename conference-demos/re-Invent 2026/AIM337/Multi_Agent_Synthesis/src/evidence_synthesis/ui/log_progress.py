# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from evidence_synthesis.models import AgentRole

_LOGGER_NAME = "evidence_synthesis.orchestrator"
_ROLE_NAMES = tuple(role.value for role in AgentRole)
_STAGES = {
    **{role: role for role in _ROLE_NAMES},
    "analyst-retry": "analyst",
    "partial-synthesis": "synthesizer",
}
_STATUS_ORDER = {"started": 0, "failed": 1, "timed_out": 1, "completed": 2}
_FORMATTED_LOG = re.compile(
    r"^\d{4}-\d{2}-\d{2} .*? INFO \[evidence_synthesis\.orchestrator\] .*? - (\{.*\})$"
)


@dataclass(frozen=True)
class StageLog:
    stage: str
    status: str
    timestamp_ms: int
    attempt: int

    @property
    def order(self) -> tuple[int, int, int]:
        return self.timestamp_ms, self.attempt, _STATUS_ORDER[self.status]

    @property
    def label(self) -> str:
        if self.status == "started":
            label = {
                "analyst-retry": "Revising claims",
                "partial-synthesis": "Preparing partial answer",
            }.get(self.stage, "Running")
            return f"{label} · attempt {self.attempt}" if self.attempt > 1 else label
        if self.status == "completed":
            return "Revision complete" if self.stage == "analyst-retry" else "Complete"
        return "Timed out" if self.status == "timed_out" else "Failed"


class RunLogReader:
    """Keep the latest logged state of each role, independent of artifact delivery."""

    def __init__(self, run_id: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Log polling requires a 32-character hexadecimal run ID.")
        self.run_id = run_id
        self._latest: dict[str, StageLog] = {}
        self._lock = threading.Lock()

    def record(self, payload: dict[str, Any], timestamp_ms: int) -> None:
        if payload.get("event") != "evidence_synthesis" or payload.get("run_id") != self.run_id:
            return
        stage = payload.get("stage")
        status = payload.get("status")
        attempt = payload.get("attempt", 1)
        if not isinstance(stage, str) or not isinstance(status, str):
            return
        if stage not in _STAGES or status not in _STATUS_ORDER:
            return
        if type(attempt) is not int or attempt < 1:
            return
        event = StageLog(stage, status, timestamp_ms, attempt)
        role = _STAGES[stage]
        with self._lock:
            previous = self._latest.get(role)
            # A late message should not undo a newer status.
            if previous is None or event.order > previous.order:
                self._latest[role] = event

    def statuses(self) -> dict[str, str]:
        with self._lock:
            return {
                role: self._latest[role].label if role in self._latest else "Waiting for logs"
                for role in _ROLE_NAMES
            }

    def poll(self) -> None:
        pass

    def close(self) -> None:
        pass


class CloudWatchLogReader(RunLogReader):
    def __init__(self, client: Any, log_group: str, run_id: str) -> None:
        super().__init__(run_id)
        self._client = client
        self._log_group = log_group
        self._start_ms = int((time.time() - 10) * 1000)

    def poll(self) -> None:
        # Keep the time window fixed so delayed logs can still arrive.
        request: dict[str, Any] = {
            "logGroupName": self._log_group,
            "startTime": self._start_ms,
            "filterPattern": f'"{self.run_id}" "{_LOGGER_NAME}"',
        }
        while True:
            response = self._client.filter_log_events(**request)
            for event in response.get("events", []):
                payload = self._payload(str(event["message"]))
                if payload is not None:
                    self.record(payload, int(event["timestamp"]))
            token = response.get("nextToken")
            if not token or token == request.get("nextToken"):
                break
            request["nextToken"] = token

    @staticmethod
    def _payload(message: str) -> dict[str, Any] | None:
        try:
            if message.startswith("{"):
                envelope = json.loads(message)
                if not isinstance(envelope, dict):
                    return None
                if envelope.get("scope", {}).get("name") != _LOGGER_NAME:
                    return None
                body = envelope.get("body")
                value = json.loads(body) if isinstance(body, str) else body
            elif message.startswith(f"INFO:{_LOGGER_NAME}:"):
                value = json.loads(message.split(":", 2)[2])
            else:
                match = _FORMATTED_LOG.fullmatch(message)
                if not match:
                    return None
                value = json.loads(match[1])
        except (ValueError, TypeError, AttributeError):
            return None
        return value if isinstance(value, dict) else None


class _RunLogHandler(logging.Handler):
    def __init__(self, reader: RunLogReader) -> None:
        super().__init__(logging.INFO)
        self._reader = reader

    def emit(self, record: logging.LogRecord) -> None:
        if record.name != _LOGGER_NAME:
            return
        try:
            payload = json.loads(record.getMessage())
            if isinstance(payload, dict):
                self._reader.record(payload, int(record.created * 1000))
        except (ValueError, TypeError):
            return


class LocalLogReader(RunLogReader):
    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self._logger = logging.getLogger(_LOGGER_NAME)
        self._handler = _RunLogHandler(self)
        if self._logger.getEffectiveLevel() > logging.INFO:
            self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)

    def close(self) -> None:
        self._logger.removeHandler(self._handler)
        self._handler.close()
