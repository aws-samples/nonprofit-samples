# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evidence_synthesis.manifest_generator import (
    generate_from_folder,
    generate_from_s3_prefix,
)


class FakePaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.requests: list[dict[str, Any]] = []

    def paginate(self, **request: Any) -> list[dict[str, Any]]:
        self.requests.append(request)
        return self.pages


class FakeS3Client:
    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.paginator = FakePaginator(pages or [])

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict[str, Any],
    ) -> None:
        self.uploads.append(
            {
                "filename": filename,
                "bucket": bucket,
                "key": key,
                "extra_args": ExtraArgs,
            }
        )

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return self.paginator


def test_generates_manifest_from_folder_and_uploads_supported_files(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "case"
    folder.mkdir()
    (folder / "nested").mkdir()
    (folder / ".hidden.md").write_text("hidden", encoding="utf-8")
    (folder / "manifest.json").write_text("{}", encoding="utf-8")
    (folder / "ignore.bin").write_bytes(b"ignored")
    (folder / "notes.md").write_text("Notes", encoding="utf-8")
    (folder / "nested" / "data.json").write_text('{"value": 1}', encoding="utf-8")
    (folder / "source-metadata.json").write_text(
        json.dumps(
            {
                "notes.md": {
                    "source_id": "field-notes",
                    "title": "Field notes",
                    "methodology": "field observations",
                    "metadata": {"topic": "risk", "stance": "higher"},
                }
            }
        ),
        encoding="utf-8",
    )
    client = FakeS3Client()

    request = generate_from_folder(
        folder=folder,
        question="What does the evidence support?",
        bucket="input-bucket",
        prefix="/sources/case/",
        s3_client=client,
    )

    assert [upload["key"] for upload in client.uploads] == [
        "sources/case/nested/data.json",
        "sources/case/notes.md",
    ]
    assert [upload["extra_args"]["ContentType"] for upload in client.uploads] == [
        "application/json",
        "text/markdown",
    ]
    assert [source.source_id for source in request.sources] == [
        "nested-data",
        "field-notes",
    ]
    assert request.sources[1].methodology == "field observations"
    assert request.sources[1].metadata == {"topic": "risk", "stance": "higher"}
    assert request.sources[0].uri == (
        "s3://input-bucket/sources/case/nested/data.json"
    )


def test_generates_manifest_from_all_pages_of_s3_prefix(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "a.csv": {
                    "title": "Administrative data",
                    "methodology": "records",
                }
            }
        ),
        encoding="utf-8",
    )
    client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "sources/case/z.md", "Size": 10},
                    {"Key": "sources/case/folder/", "Size": 0},
                    {"Key": "sources/case/.hidden.txt", "Size": 10},
                ]
            },
            {
                "Contents": [
                    {"Key": "sources/case/a.csv", "Size": 10},
                    {"Key": "sources/case/ignore.bin", "Size": 10},
                ]
            },
        ]
    )

    request = generate_from_s3_prefix(
        s3_prefix="s3://input-bucket/sources/case/",
        question="What does the evidence support?",
        s3_client=client,
        metadata_path=metadata,
        max_evidence=30,
    )

    assert client.paginator.requests == [
        {"Bucket": "input-bucket", "Prefix": "sources/case/"}
    ]
    assert [source.uri for source in request.sources] == [
        "s3://input-bucket/sources/case/a.csv",
        "s3://input-bucket/sources/case/z.md",
    ]
    assert request.sources[0].title == "Administrative data"
    assert request.max_evidence == 30


def test_rejects_metadata_for_undiscovered_file(tmp_path: Path) -> None:
    folder = tmp_path / "case"
    folder.mkdir()
    (folder / "notes.md").write_text("Notes", encoding="utf-8")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps({"missing.pdf": {"title": "Missing"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing.pdf"):
        generate_from_folder(
            folder=folder,
            question="What does the evidence support?",
            bucket="input-bucket",
            prefix="sources/case",
            s3_client=FakeS3Client(),
            metadata_path=metadata,
        )
