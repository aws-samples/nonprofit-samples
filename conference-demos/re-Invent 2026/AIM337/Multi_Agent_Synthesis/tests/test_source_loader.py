# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import io
from pathlib import Path
from typing import Any

import pytest

from evidence_synthesis.models import SourceSpec
from evidence_synthesis.source_loader import SourceLimitError, SourceLoader


def test_loads_structured_and_unstructured_sources(tmp_path: Path) -> None:
    (tmp_path / "rows.csv").write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("First note.\n\nSecond note.\n", encoding="utf-8")
    sources = [
        SourceSpec(source_id="rows", uri="rows.csv"),
        SourceSpec(source_id="notes", uri="notes.md"),
    ]

    chunks = SourceLoader().load_all(sources, tmp_path)

    assert len(chunks) == 4
    assert {chunk.source_id for chunk in chunks} == {"rows", "notes"}
    assert any("#row=2" in chunk.locator for chunk in chunks)
    assert any("#paragraph=2" in chunk.locator for chunk in chunks)


class FakeS3Client:
    def __init__(self, body: bytes = b"Allowed evidence.") -> None:
        self.body = body
        self.requests: list[dict[str, Any]] = []

    def get_object(self, **request: Any) -> dict[str, io.BytesIO]:
        self.requests.append(request)
        return {"Body": io.BytesIO(self.body)}


@pytest.mark.parametrize("use_s3", [False, True])
def test_coastal_pdf_preserves_original_evidence_and_page_citation(use_s3: bool) -> None:
    folder = Path(__file__).resolve().parents[1] / "examples" / "coastal-resilience"
    pdf_path = folder / "mapped-risk.pdf"
    client = FakeS3Client(pdf_path.read_bytes())
    uri = "s3://evidence-input/sources/mapped-risk.pdf" if use_s3 else "mapped-risk.pdf"
    chunks = SourceLoader(client=client).load(SourceSpec(source_id="mapped-risk", uri=uri), folder)

    assert len(chunks) == 1
    chunk = chunks[0]
    content = " ".join(chunk.content.split())
    assert chunk.chunk_id == "mapped-risk:page-1"
    assert chunk.locator == f"{uri if use_s3 else pdf_path.resolve()}#page=1"
    assert "ILLUSTRATIVE DEMO DATA" in content
    assert (
        "The current planning map classifies the neighborhood in a lower-hazard category."
    ) in content
    assert (
        "The classification is a planning reference and does not represent "
        "a property-level guarantee."
    ) in content
    assert "Not an official hazard map or property assessment." in content
    assert chunk.metadata == {}
    assert len(client.requests) == int(use_s3)


def test_restricts_s3_sources_to_configured_bucket_and_prefix() -> None:
    client = FakeS3Client()
    loader = SourceLoader(
        allowed_s3_bucket="evidence-input",
        allowed_s3_prefix="sources",
        client=client,
    )

    chunks = loader.load(
        SourceSpec(source_id="allowed", uri="s3://evidence-input/sources/notes.md"),
        Path.cwd(),
    )

    assert len(chunks) == 1
    assert client.requests == [
        {"Bucket": "evidence-input", "Key": "sources/notes.md"}
    ]

    with pytest.raises(ValueError, match="bucket is not allowed"):
        loader.load(
            SourceSpec(source_id="wrong-bucket", uri="s3://other/sources/notes.md"),
            Path.cwd(),
        )

    with pytest.raises(ValueError, match="outside the allowed prefix"):
        loader.load(
            SourceSpec(source_id="wrong-prefix", uri="s3://evidence-input/private/notes.md"),
            Path.cwd(),
        )


def test_decodes_s3_object_keys_from_manifest_uri() -> None:
    client = FakeS3Client()
    loader = SourceLoader(
        allowed_s3_bucket="evidence-input",
        allowed_s3_prefix="sources",
        client=client,
    )

    loader.load(
        SourceSpec(
            source_id="encoded",
            uri="s3://evidence-input/sources/notes%20%231.md",
        ),
        Path.cwd(),
    )

    assert client.requests == [
        {"Bucket": "evidence-input", "Key": "sources/notes #1.md"}
    ]


@pytest.mark.parametrize("path_kind", ["parent", "absolute", "symlink", "directory-symlink"])
def test_rejects_local_sources_outside_base_directory(tmp_path: Path, path_kind: str) -> None:
    base = tmp_path / "allowed"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    document = outside / "notes.md"
    document.write_text("Private dummy text.")
    if path_kind == "parent":
        uri = "../outside/notes.md"
    elif path_kind == "absolute":
        uri = str(document)
    elif path_kind == "symlink":
        (base / "link.md").symlink_to(document)
        uri = "link.md"
    else:
        (base / "linked-folder").symlink_to(outside, target_is_directory=True)
        uri = "linked-folder/notes.md"

    with pytest.raises(ValueError, match="outside the allowed directory"):
        SourceLoader().load(SourceSpec(source_id="outside", uri=uri), base)


def test_accepts_resolved_local_paths_inside_base_directory(tmp_path: Path) -> None:
    folder = tmp_path / "nested"
    folder.mkdir()
    document = folder / "notes.md"
    document.write_text("Allowed evidence.")
    (tmp_path / "link.md").symlink_to(document)

    for uri in ("nested/notes.md", str(document), "link.md"):
        chunks = SourceLoader().load(SourceSpec(source_id="allowed", uri=uri), tmp_path)
        assert chunks[0].content == "Allowed evidence."


def test_runtime_source_policy_rejects_local_files_but_accepts_inline_and_s3(
    tmp_path: Path,
) -> None:
    document = tmp_path / "notes.md"
    document.write_text("This file is inside the base directory.")
    client = FakeS3Client()
    loader = SourceLoader(
        allow_local_files=False,
        allowed_s3_bucket="evidence-input",
        allowed_s3_prefix="sources",
        client=client,
    )
    with pytest.raises(ValueError, match="Local file sources are not allowed"):
        loader.load(SourceSpec(source_id="local", uri=str(document)), tmp_path)

    inline = loader.load(SourceSpec(source_id="inline", content="Inline evidence."), tmp_path)
    remote = loader.load(
        SourceSpec(source_id="s3", uri="s3://evidence-input/sources/notes.md"), tmp_path,
    )
    assert inline[0].content == "Inline evidence."
    assert remote[0].content == "Allowed evidence."


def test_source_download_is_bounded_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    monkeypatch.setattr("evidence_synthesis.source_loader.MAX_SOURCE_BYTES", 8)
    body = Mock(wraps=io.BytesIO(b"x" * 20))
    client = Mock()
    client.get_object.return_value = {"Body": body}
    with pytest.raises(SourceLimitError, match="byte limit"):
        SourceLoader(client=client).load(
            SourceSpec(source_id="big", uri="s3://input/sources/big.txt"), tmp_path,
        )
    body.read.assert_called_once_with(9)
    body.close.assert_called_once()


def test_extracted_text_and_json_record_limits_are_enforced(tmp_path: Path) -> None:
    with pytest.raises(SourceLimitError, match="extracted-text"):
        SourceLoader().load(SourceSpec(source_id="large", content="x" * 100_001), tmp_path)
    with pytest.raises(SourceLimitError, match="item limit"):
        SourceLoader().load(SourceSpec(
            source_id="rows", content="[" + ",".join(["1"] * 1001) + "]",
            media_type="application/json",
        ), tmp_path)


def test_s3_citations_preserve_and_reuse_the_object_version(tmp_path: Path) -> None:
    from unittest.mock import Mock

    client = Mock()
    client.get_object.return_value = {
        "Body": io.BytesIO(b"Original version."), "VersionId": "version+one",
    }
    loader = SourceLoader(client=client)
    chunks = loader.load(
        SourceSpec(source_id="one", uri="s3://input/sources/notes.md"), tmp_path,
    )
    assert "?versionId=version%2Bone#paragraph=1" in chunks[0].locator
    client.get_object.return_value = {"Body": io.BytesIO(b"Original version.")}
    loader.load(
        SourceSpec(source_id="one", uri=chunks[0].locator.split("#")[0]), tmp_path,
    )
    assert client.get_object.call_args.kwargs["VersionId"] == "version+one"
