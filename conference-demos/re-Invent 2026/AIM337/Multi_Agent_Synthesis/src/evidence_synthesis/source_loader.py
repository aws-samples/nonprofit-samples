# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import csv
import io
import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from evidence_synthesis.models import SourceChunk, SourceSpec
from evidence_synthesis.pdf_reader import MAX_TEXT_CHARACTERS, read_pdf_pages

MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 100 * 1024 * 1024
MAX_SOURCE_CHUNKS = 1000


class SourceLimitError(ValueError):
    pass


class SourceLoader:
    def __init__(
        self,
        *,
        region: str | None = None,
        allowed_s3_bucket: str | None = None,
        allowed_s3_prefix: str = "",
        allow_local_files: bool = True,
        client: Any | None = None,
    ) -> None:
        self._region = region
        self._allowed_s3_bucket = allowed_s3_bucket
        normalized_prefix = allowed_s3_prefix.strip("/")
        self._allowed_s3_prefix = f"{normalized_prefix}/" if normalized_prefix else ""
        self._allow_local_files = allow_local_files
        self._s3_client: Any | None = client

    def load_all(
        self, sources: list[SourceSpec], base_dir: Path,
        *, cancel_signal: threading.Event | None = None,
    ) -> list[SourceChunk]:
        if len(sources) > 20:
            raise SourceLimitError("A run accepts at most 20 sources")
        chunks: list[SourceChunk] = []
        remaining_bytes = MAX_TOTAL_SOURCE_BYTES
        for source in sources:
            if cancel_signal is not None and cancel_signal.is_set():
                raise SourceLimitError("Source loading cancelled")
            loaded, size = self._load(
                source, base_dir, min(MAX_SOURCE_BYTES, remaining_bytes), cancel_signal,
            )
            remaining_bytes -= size
            chunks.extend(loaded)
            self._check_chunks(chunks)
        return chunks

    def load(self, source: SourceSpec, base_dir: Path) -> list[SourceChunk]:
        chunks, _ = self._load(source, base_dir, MAX_SOURCE_BYTES)
        return chunks

    def _load(
        self, source: SourceSpec, base_dir: Path, limit: int,
        cancel_signal: threading.Event | None = None,
    ) -> tuple[list[SourceChunk], int]:
        data, suffix, origin = self._read(source, base_dir, limit)
        if len(data) > limit:
            raise SourceLimitError("Source exceeds the byte limit")
        if suffix != ".pdf" and len(data) > 1_000_000:
            raise SourceLimitError("Text, CSV, and JSON sources are limited to 1 MB")
        if cancel_signal is not None and cancel_signal.is_set():
            raise SourceLimitError("Source loading cancelled")
        chunks = self._parse_bytes(
            source, data, suffix=suffix, origin=origin, cancel_signal=cancel_signal,
        )
        self._check_chunks(chunks)
        return chunks, len(data)

    @staticmethod
    def _check_chunks(chunks: list[SourceChunk]) -> None:
        if len(chunks) > MAX_SOURCE_CHUNKS:
            raise SourceLimitError("Run exceeds the 1000-chunk limit")
        if sum(len(chunk.content) for chunk in chunks) > MAX_TEXT_CHARACTERS:
            raise SourceLimitError("Run exceeds the 100000-character extracted-text limit")
        if sum(len(chunk.model_dump_json()) for chunk in chunks) > 200_000:
            raise SourceLimitError("Source text and metadata exceed the model-input limit")

    def _read(self, source: SourceSpec, base_dir: Path, limit: int) -> tuple[bytes, str, str]:
        if source.content is not None:
            suffix = self._suffix_from_media_type(source.media_type)
            return source.content.encode("utf-8"), suffix, "inline"

        assert source.uri is not None
        if source.uri.startswith("s3://"):
            return self._read_s3(source.uri, limit)

        if not self._allow_local_files:
            raise ValueError("Local file sources are not allowed for runtime requests")
        # Resolve symlinks before checking the local source boundary.
        allowed_root = base_dir.resolve()
        path = (allowed_root / source.uri).resolve()
        if not path.is_relative_to(allowed_root):
            raise ValueError("Local source path is outside the allowed directory")
        if path.stat().st_size > limit:
            raise SourceLimitError("Source exceeds the byte limit")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        return data, path.suffix.lower(), str(path)

    def _read_s3(self, uri: str, limit: int = MAX_SOURCE_BYTES) -> tuple[bytes, str, str]:
        parsed = urlparse(uri)
        if not parsed.netloc or not parsed.path:
            raise ValueError(f"Invalid S3 URI: {uri}")
        key = unquote(parsed.path.lstrip("/"))
        if self._allowed_s3_bucket and parsed.netloc != self._allowed_s3_bucket:
            raise ValueError("S3 source bucket is not allowed")
        if self._allowed_s3_prefix and not key.startswith(self._allowed_s3_prefix):
            raise ValueError("S3 source key is outside the allowed prefix")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query and (
            set(query) != {"versionId"} or len(query["versionId"]) != 1 or not query["versionId"][0]
        ):
            raise ValueError("S3 source URI accepts only one nonempty versionId")
        if self._s3_client is None:
            import boto3
            from botocore.config import Config

            config = Config(
                retries={"total_max_attempts": 3, "mode": "adaptive"},
                connect_timeout=5,
                read_timeout=30,
                user_agent_appid="evidence-synthesis/0.1.0",
            )
            self._s3_client = boto3.Session(region_name=self._region).client(
                "s3", config=config
            )

        request = {"Bucket": parsed.netloc, "Key": key}
        if "versionId" in query:
            request["VersionId"] = query["versionId"][0]
        response = self._s3_client.get_object(**request)
        body = response["Body"]
        try:
            if response.get("ContentLength", 0) > limit:
                raise SourceLimitError("Source exceeds the byte limit")
            data = body.read(limit + 1)
        finally:
            body.close()
        # Citations identify the actual version read, even if the key changes later.
        version = response.get("VersionId")
        origin = uri
        if version and version != "null":
            origin = (
                f"s3://{parsed.netloc}/{quote(key, safe='/')}"
                f"?versionId={quote(version, safe='')}"
            )
        return data, Path(key).suffix.lower(), origin

    def _parse_bytes(
        self,
        source: SourceSpec,
        data: bytes,
        *,
        suffix: str,
        origin: str,
        cancel_signal: threading.Event | None = None,
    ) -> list[SourceChunk]:
        title = source.title or source.source_id
        if suffix == ".pdf":
            return self._pdf_chunks(source, data, title, origin, cancel_signal)
        if suffix == ".csv":
            return self._csv_chunks(source, data, title, origin)
        if suffix == ".json":
            return self._json_chunks(source, data, title, origin)
        return self._text_chunks(source, data.decode("utf-8"), title, origin)

    def _pdf_chunks(
        self, source: SourceSpec, data: bytes, title: str, origin: str,
        cancel_signal: threading.Event | None = None,
    ) -> list[SourceChunk]:
        chunks = []
        # Keep page numbers so citations can point back to the PDF.
        try:
            pages = read_pdf_pages(data, cancel_signal=cancel_signal)
        except ValueError as error:
            raise SourceLimitError(str(error)) from None
        for index, text in pages:
            if text:
                chunks.append(
                    self._chunk(
                        source,
                        title,
                        text,
                        f"{origin}#page={index}",
                        f"page-{index}",
                    )
                )
        return chunks

    def _csv_chunks(
        self, source: SourceSpec, data: bytes, title: str, origin: str
    ) -> list[SourceChunk]:
        rows = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        chunks = []
        # Row one is the header, so data citations start at row two.
        for index, row in enumerate(rows, start=2):
            if index > MAX_SOURCE_CHUNKS + 1:
                raise SourceLimitError("CSV exceeds the 1000-row limit")
            metadata = {**source.metadata, **self._row_metadata(row)}
            chunks.append(
                self._chunk(
                    source,
                    title,
                    json.dumps(row, sort_keys=True),
                    f"{origin}#row={index}",
                    f"row-{index}",
                    metadata=metadata,
                )
            )
        return chunks

    def _json_chunks(
        self, source: SourceSpec, data: bytes, title: str, origin: str
    ) -> list[SourceChunk]:
        value = json.loads(data)
        values = value if isinstance(value, list) else [value]
        if len(values) > MAX_SOURCE_CHUNKS:
            raise SourceLimitError("JSON exceeds the 1000-item limit")
        chunks = []
        for index, item in enumerate(values, start=1):
            metadata = dict(source.metadata)
            if isinstance(item, dict):
                metadata.update(self._row_metadata(item))
            chunks.append(
                self._chunk(
                    source,
                    title,
                    json.dumps(item, sort_keys=True),
                    f"{origin}#item={index}",
                    f"item-{index}",
                    metadata=metadata,
                )
            )
        return chunks

    def _text_chunks(
        self, source: SourceSpec, text: str, title: str, origin: str
    ) -> list[SourceChunk]:
        paragraphs = [
            part.strip() for part in text.split("\n\n", MAX_SOURCE_CHUNKS) if part.strip()
        ]
        if not paragraphs:
            return []
        return [
            self._chunk(
                source,
                title,
                paragraph,
                f"{origin}#paragraph={index}",
                f"paragraph-{index}",
            )
            for index, paragraph in enumerate(paragraphs, start=1)
        ]

    def _chunk(
        self,
        source: SourceSpec,
        title: str,
        content: str,
        locator: str,
        suffix: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> SourceChunk:
        return SourceChunk(
            chunk_id=f"{source.source_id}:{suffix}",
            source_id=source.source_id,
            title=title,
            content=content,
            locator=locator,
            methodology=source.methodology,
            metadata=metadata or source.metadata,
        )

    @staticmethod
    def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
        keys = {"topic", "stance", "as_of", "unit", "measure"}
        return {key: row[key] for key in keys if row.get(key) not in (None, "")}

    @staticmethod
    def _suffix_from_media_type(media_type: str | None) -> str:
        return {
            "application/json": ".json",
            "text/csv": ".csv",
            "application/pdf": ".pdf",
            "text/markdown": ".md",
        }.get(media_type or "", ".txt")
