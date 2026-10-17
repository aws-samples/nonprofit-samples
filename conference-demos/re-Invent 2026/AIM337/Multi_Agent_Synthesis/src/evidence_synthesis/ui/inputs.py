# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from evidence_synthesis.manifest_generator import (
    SUPPORTED_MEDIA_TYPES,
    generate_from_folder,
    generate_local_from_folder,
)
from evidence_synthesis.models import RunRequest

MAX_SOURCE_FILES = 20
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    description: str
    folder: Path


@dataclass(frozen=True)
class UploadedSource:
    name: str
    data: bytes


SCENARIOS = {
    "harris-county": Scenario(
        key="harris-county",
        label="Harris County climate risk",
        description="Real NOAA, FEMA, environmental-justice, and Landsat data.",
        folder=PROJECT_ROOT / "examples" / "harris-county-climate-risk",
    ),
    "coastal-resilience": Scenario(
        key="coastal-resilience",
        label="Coastal resilience",
        description="Compact fixture with deliberately conflicting evidence.",
        folder=PROJECT_ROOT / "examples" / "coastal-resilience",
    ),
}


def load_scenario(key: str) -> tuple[Scenario, RunRequest]:
    scenario = SCENARIOS[key]
    request = RunRequest.model_validate_json(
        (scenario.folder / "manifest.json").read_text(encoding="utf-8")
    )
    return scenario, request


def content_fingerprint(folder: Path | None, uploads: list[UploadedSource]) -> str:
    entries = [
        (upload.name, hashlib.sha256(upload.data).hexdigest()) for upload in uploads
    ]
    if folder is not None:
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if (
                not path.is_file()
                or any(part.startswith(".") for part in relative.parts)
                or path.suffix.lower() not in SUPPORTED_MEDIA_TYPES
            ):
                continue
            with path.open("rb") as source:
                entries.append((
                    relative.as_posix(), hashlib.file_digest(source, "sha256").hexdigest(),
                ))
    return hashlib.sha256(json.dumps(sorted(entries)).encode()).hexdigest()


def write_uploaded_sources(
    uploads: list[UploadedSource],
    destination: Path,
) -> None:
    if not uploads:
        raise ValueError("Upload at least one supported source file")
    if len(uploads) > MAX_SOURCE_FILES + 1:
        raise ValueError(f"Upload at most {MAX_SOURCE_FILES} source files")

    total_bytes = sum(len(upload.data) for upload in uploads)
    if total_bytes > MAX_TOTAL_BYTES:
        raise ValueError("Uploaded files exceed the 100 MB total limit")

    normalized_names: set[str] = set()
    source_count = 0
    for upload in uploads:
        relative_path = _safe_relative_path(upload.name)
        normalized_key = relative_path.casefold()
        if normalized_key in normalized_names:
            raise ValueError(f"Duplicate uploaded path: {relative_path}")
        normalized_names.add(normalized_key)

        is_metadata = relative_path == "source-metadata.json"
        suffix = PurePosixPath(relative_path).suffix.lower()
        if not is_metadata and suffix not in SUPPORTED_MEDIA_TYPES:
            raise ValueError(f"Unsupported uploaded file type: {relative_path}")
        if not is_metadata:
            source_count += 1
            if len(upload.data) > MAX_FILE_BYTES:
                raise ValueError(f"Uploaded file exceeds 25 MB: {relative_path}")

        path = destination / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(upload.data)

    if source_count == 0:
        raise ValueError("Upload at least one supported source file")
    if source_count > MAX_SOURCE_FILES:
        raise ValueError(f"Upload at most {MAX_SOURCE_FILES} source files")

    metadata_path = destination / "source-metadata.json"
    if metadata_path.exists():
        value = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("source-metadata.json must contain a JSON object")


def local_request(
    folder: Path,
    *,
    question: str,
    max_evidence: int,
) -> RunRequest:
    return generate_local_from_folder(
        folder=folder,
        question=question,
        max_evidence=max_evidence,
    )


def agentcore_request(
    folder: Path,
    *,
    question: str,
    max_evidence: int,
    bucket: str,
    prefix: str,
    s3_client: object,
) -> RunRequest:
    return generate_from_folder(
        folder=folder,
        question=question,
        bucket=bucket,
        prefix=prefix,
        s3_client=s3_client,
        max_evidence=max_evidence,
    )


def _safe_relative_path(value: str) -> str:
    raw = value.replace("\\", "/")
    if raw.startswith("/") or (
        len(raw) >= 3 and raw[0].isalpha() and raw[1:3] == ":/"
    ):
        raise ValueError(f"Unsafe uploaded path: {value}")
    normalized = raw.strip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.startswith(".") for part in path.parts)
    ):
        raise ValueError(f"Unsafe uploaded path: {value}")
    return path.as_posix()
