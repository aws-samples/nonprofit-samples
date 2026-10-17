# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from evidence_synthesis.models import RunRequest

SUPPORTED_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
}
EXCLUDED_FILENAMES = frozenset({"manifest.json", "source-metadata.json"})
SOURCE_METADATA_FIELDS = frozenset(
    {"source_id", "title", "methodology", "metadata"}
)


@dataclass(frozen=True)
class SourceMetadata:
    source_id: str | None = None
    title: str | None = None
    methodology: str = "unspecified"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DiscoveredSource:
    relative_path: str
    uri: str
    media_type: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a validated evidence-synthesis manifest from a local folder "
            "or an existing S3 prefix."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--folder",
        type=Path,
        help="Recursively upload supported files from this local folder.",
    )
    source.add_argument(
        "--s3-prefix",
        help="Discover supported files under an existing s3://bucket/prefix/.",
    )
    parser.add_argument("--question", required=True)
    parser.add_argument(
        "--metadata",
        type=Path,
        help=(
            "Optional JSON mapping of relative paths to source_id, title, "
            "methodology, and metadata."
        ),
    )
    parser.add_argument(
        "--bucket",
        help=(
            "Destination bucket for --folder. Defaults to the deployed input bucket "
            "from SSM."
        ),
    )
    parser.add_argument(
        "--prefix",
        help=(
            "Destination prefix for --folder. Defaults to sources/<folder-name>."
        ),
    )
    parser.add_argument("--max-evidence", type=int, default=20)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--region", default="us-east-1")
    return parser


def _normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Invalid relative metadata path: {value}")
    return path.as_posix()


def _load_source_metadata(path: Path | None) -> dict[str, SourceMetadata]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Source metadata must be a JSON object keyed by relative path")

    result: dict[str, SourceMetadata] = {}
    for raw_path, raw_entry in value.items():
        if not isinstance(raw_path, str) or not isinstance(raw_entry, dict):
            raise ValueError("Each source metadata entry must map a path to an object")
        unknown = set(raw_entry) - SOURCE_METADATA_FIELDS
        if unknown:
            rendered = ", ".join(sorted(unknown))
            raise ValueError(f"Unsupported metadata fields for {raw_path}: {rendered}")

        relative_path = _normalize_relative_path(raw_path)
        if relative_path in result:
            raise ValueError(f"Duplicate source metadata path: {relative_path}")

        source_id = raw_entry.get("source_id")
        title = raw_entry.get("title")
        methodology = raw_entry.get("methodology", "unspecified")
        metadata = raw_entry.get("metadata", {})
        if source_id is not None and not isinstance(source_id, str):
            raise ValueError(f"source_id for {raw_path} must be a string")
        if title is not None and not isinstance(title, str):
            raise ValueError(f"title for {raw_path} must be a string")
        if not isinstance(methodology, str):
            raise ValueError(f"methodology for {raw_path} must be a string")
        if not isinstance(metadata, dict):
            raise ValueError(f"metadata for {raw_path} must be an object")
        result[relative_path] = SourceMetadata(
            source_id=source_id,
            title=title,
            methodology=methodology,
            metadata=metadata,
        )
    return result


def _is_hidden(relative_path: str) -> bool:
    return any(part.startswith(".") for part in PurePosixPath(relative_path).parts)


def _is_supported(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    return (
        not _is_hidden(relative_path)
        and path.name.lower() not in EXCLUDED_FILENAMES
        and path.suffix.lower() in SUPPORTED_MEDIA_TYPES
    )


def _source_id(relative_path: str) -> str:
    without_suffix = str(PurePosixPath(relative_path).with_suffix(""))
    slug = re.sub(r"[^a-z0-9]+", "-", without_suffix.lower()).strip("-")
    return slug or "source"


def _unique_source_ids(
    sources: list[DiscoveredSource],
    metadata: dict[str, SourceMetadata],
) -> dict[str, str]:
    candidates: dict[str, list[str]] = {}
    for source in sources:
        configured = metadata.get(source.relative_path)
        candidate = configured.source_id if configured and configured.source_id else _source_id(
            source.relative_path
        )
        candidates.setdefault(candidate, []).append(source.relative_path)

    resolved: dict[str, str] = {}
    for candidate, paths in candidates.items():
        if len(paths) == 1:
            resolved[paths[0]] = candidate
            continue
        for relative_path in paths:
            digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:8]
            resolved[relative_path] = f"{candidate}-{digest}"
    return resolved


def _default_title(relative_path: str) -> str:
    stem = PurePosixPath(relative_path).stem
    return re.sub(r"[-_]+", " ", stem).strip().title()


def _build_request(
    *,
    question: str,
    discovered: list[DiscoveredSource],
    metadata: dict[str, SourceMetadata],
    max_evidence: int,
) -> RunRequest:
    if not discovered:
        supported = ", ".join(sorted(SUPPORTED_MEDIA_TYPES))
        raise ValueError(f"No supported source files found; expected one of: {supported}")

    discovered_paths = {source.relative_path for source in discovered}
    unknown_metadata = set(metadata) - discovered_paths
    if unknown_metadata:
        rendered = ", ".join(sorted(unknown_metadata))
        raise ValueError(f"Metadata references files that were not discovered: {rendered}")

    source_ids = _unique_source_ids(discovered, metadata)
    sources: list[dict[str, Any]] = []
    for source in sorted(discovered, key=lambda item: item.relative_path):
        configured = metadata.get(source.relative_path, SourceMetadata())
        sources.append(
            {
                "source_id": source_ids[source.relative_path],
                "uri": source.uri,
                "title": configured.title or _default_title(source.relative_path),
                "methodology": configured.methodology,
                "media_type": source.media_type,
                "metadata": configured.metadata or {},
            }
        )
    return RunRequest.model_validate(
        {
            "question": question,
            "sources": sources,
            "max_evidence": max_evidence,
        }
    )


def _validate_metadata_paths(
    discovered_paths: set[str],
    metadata: dict[str, SourceMetadata],
) -> None:
    unknown_metadata = set(metadata) - discovered_paths
    if unknown_metadata:
        rendered = ", ".join(sorted(unknown_metadata))
        raise ValueError(f"Metadata references files that were not discovered: {rendered}")


def _local_sources(folder: Path) -> list[tuple[Path, str]]:
    root = folder.resolve()
    if not root.is_dir():
        raise ValueError(f"Folder does not exist or is not a directory: {folder}")

    sources: list[tuple[Path, str]] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative_path = path.relative_to(root).as_posix()
        if _is_supported(relative_path):
            sources.append((path, relative_path))
    return sorted(sources, key=lambda item: item[1])


def _object_key(prefix: str, relative_path: str) -> str:
    normalized_prefix = prefix.strip("/")
    key = f"{normalized_prefix}/{relative_path}" if normalized_prefix else relative_path
    if "\x00" in key or len(key.encode("utf-8")) > 1024:
        raise ValueError(f"Invalid or oversized S3 object key: {key}")
    return key


def _s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{quote(key, safe='/')}"


def generate_from_folder(
    *,
    folder: Path,
    question: str,
    bucket: str,
    prefix: str,
    s3_client: Any,
    metadata_path: Path | None = None,
    max_evidence: int = 20,
) -> RunRequest:
    root = folder.resolve()
    effective_metadata_path = metadata_path
    default_metadata_path = root / "source-metadata.json"
    if effective_metadata_path is None and default_metadata_path.is_file():
        effective_metadata_path = default_metadata_path
    metadata = _load_source_metadata(effective_metadata_path)
    local_sources = _local_sources(root)
    _validate_metadata_paths(
        {relative_path for _, relative_path in local_sources},
        metadata,
    )

    discovered: list[DiscoveredSource] = []
    uploads: list[tuple[Path, str, str]] = []
    for local_path, relative_path in local_sources:
        key = _object_key(prefix, relative_path)
        media_type = SUPPORTED_MEDIA_TYPES[PurePosixPath(relative_path).suffix.lower()]
        uploads.append((local_path, key, media_type))
        discovered.append(
            DiscoveredSource(
                relative_path=relative_path,
                uri=_s3_uri(bucket, key),
                media_type=media_type,
            )
        )
    request = _build_request(
        question=question,
        discovered=discovered,
        metadata=metadata,
        max_evidence=max_evidence,
    )
    for local_path, key, media_type in uploads:
        s3_client.upload_file(
            str(local_path),
            bucket,
            key,
            ExtraArgs={"ContentType": media_type},
        )
    return request


def generate_local_from_folder(
    *,
    folder: Path,
    question: str,
    metadata_path: Path | None = None,
    max_evidence: int = 20,
) -> RunRequest:
    root = folder.resolve()
    effective_metadata_path = metadata_path
    default_metadata_path = root / "source-metadata.json"
    if effective_metadata_path is None and default_metadata_path.is_file():
        effective_metadata_path = default_metadata_path
    metadata = _load_source_metadata(effective_metadata_path)
    local_sources = _local_sources(root)
    _validate_metadata_paths(
        {relative_path for _, relative_path in local_sources},
        metadata,
    )
    discovered = [
        DiscoveredSource(
            relative_path=relative_path,
            uri=relative_path,
            media_type=SUPPORTED_MEDIA_TYPES[
                PurePosixPath(relative_path).suffix.lower()
            ],
        )
        for _, relative_path in local_sources
    ]
    return _build_request(
        question=question,
        discovered=discovered,
        metadata=metadata,
        max_evidence=max_evidence,
    )


def _parse_s3_prefix(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if (
        parsed.scheme != "s3"
        or not parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Invalid S3 prefix URI: {uri}")
    return parsed.netloc, unquote(parsed.path.strip("/"))


def generate_from_s3_prefix(
    *,
    s3_prefix: str,
    question: str,
    s3_client: Any,
    metadata_path: Path | None = None,
    max_evidence: int = 20,
) -> RunRequest:
    bucket, raw_prefix = _parse_s3_prefix(s3_prefix)
    list_prefix = f"{raw_prefix}/" if raw_prefix else ""
    discovered: list[DiscoveredSource] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=list_prefix):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            if int(item.get("Size", 0)) <= 0 or not key.startswith(list_prefix):
                continue
            relative_path = key[len(list_prefix) :]
            if not _is_supported(relative_path):
                continue
            suffix = PurePosixPath(relative_path).suffix.lower()
            discovered.append(
                DiscoveredSource(
                    relative_path=relative_path,
                    uri=_s3_uri(bucket, key),
                    media_type=SUPPORTED_MEDIA_TYPES[suffix],
                )
            )
    return _build_request(
        question=question,
        discovered=discovered,
        metadata=_load_source_metadata(metadata_path),
        max_evidence=max_evidence,
    )


def _render(request: RunRequest, output: Path | None) -> None:
    rendered = request.model_dump_json(indent=2) + "\n"
    if output is None:
        print(rendered, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(output.resolve())


def main() -> int:
    args = _parser().parse_args()
    session = boto3.Session(region_name=args.region)
    config = Config(
        retries={"total_max_attempts": 3, "mode": "adaptive"},
        connect_timeout=5,
        read_timeout=60,
        user_agent_appid="evidence-synthesis-manifest/0.1.0",
    )
    s3 = session.client("s3", config=config)
    try:
        if args.folder is not None:
            bucket = args.bucket or session.client("ssm", config=config).get_parameter(
                Name="/evidence-synthesis/input-bucket"
            )["Parameter"]["Value"]
            prefix = args.prefix or f"sources/{args.folder.resolve().name}"
            request = generate_from_folder(
                folder=args.folder,
                question=args.question,
                bucket=bucket,
                prefix=prefix,
                s3_client=s3,
                metadata_path=args.metadata,
                max_evidence=args.max_evidence,
            )
        else:
            if args.bucket or args.prefix:
                raise ValueError("--bucket and --prefix apply only with --folder")
            request = generate_from_s3_prefix(
                s3_prefix=args.s3_prefix,
                question=args.question,
                s3_client=s3,
                metadata_path=args.metadata,
                max_evidence=args.max_evidence,
            )
        _render(request, args.output)
        return 0
    except (BotoCoreError, ClientError, OSError, ValidationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
