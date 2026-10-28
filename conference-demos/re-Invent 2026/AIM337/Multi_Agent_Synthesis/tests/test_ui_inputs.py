# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from pathlib import Path

import pytest

import evidence_synthesis.ui.inputs as inputs
from evidence_synthesis.ui.inputs import UploadedSource


def test_uploaded_sources_need_no_metadata_file(tmp_path: Path) -> None:
    inputs.write_uploaded_sources([UploadedSource("notes.md", b"Observed evidence.")], tmp_path)

    request = inputs.local_request(tmp_path, question="What is supported?", max_evidence=10)

    assert len(request.sources) == 1
    assert request.sources[0].uri == "notes.md"
    assert request.sources[0].metadata == {}
    assert request.sources[0].methodology == "unspecified"


def test_upload_fingerprint_detects_same_size_content_changes() -> None:
    original = [UploadedSource("notes.md", b"one")]
    replacement = [UploadedSource("notes.md", b"two")]

    assert inputs.content_fingerprint(None, original) == inputs.content_fingerprint(None, original)
    assert inputs.content_fingerprint(None, original) != inputs.content_fingerprint(
        None, replacement,
    )


def test_folder_fingerprint_tracks_sources_and_legacy_hints(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("one", encoding="utf-8")
    original = inputs.content_fingerprint(tmp_path, [])
    source.write_text("two", encoding="utf-8")
    replacement = inputs.content_fingerprint(tmp_path, [])
    assert replacement != original

    (tmp_path / "provenance.yml").write_text("not a supported source", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden source", encoding="utf-8")
    assert inputs.content_fingerprint(tmp_path, []) == replacement

    metadata = tmp_path / "source-metadata.json"
    metadata.write_text('{"notes.md":{"title":"Old"}}', encoding="utf-8")
    with_metadata = inputs.content_fingerprint(tmp_path, [])
    assert with_metadata != replacement
    metadata.write_text('{"notes.md":{"title":"New"}}', encoding="utf-8")
    assert inputs.content_fingerprint(tmp_path, []) != with_metadata
    metadata.unlink()
    source.unlink()
    assert inputs.content_fingerprint(tmp_path, []) != replacement


def test_uploaded_sources_generate_local_request_with_metadata(
    tmp_path: Path,
) -> None:
    inputs.write_uploaded_sources(
        [
            UploadedSource("notes.md", b"Observed evidence."),
            UploadedSource(
                "source-metadata.json",
                b'{"notes.md":{"title":"Field notes","methodology":"observations"}}',
            ),
        ],
        tmp_path,
    )

    request = inputs.local_request(
        tmp_path,
        question="What is supported?",
        max_evidence=10,
    )

    assert len(request.sources) == 1
    assert request.sources[0].uri == "notes.md"
    assert request.sources[0].title == "Field notes"
    assert request.sources[0].methodology == "observations"


@pytest.mark.parametrize(
    "name",
    ["../secret.txt", "/absolute.txt", ".hidden.txt", "folder/../../bad.md"],
)
def test_uploaded_sources_reject_unsafe_paths(
    tmp_path: Path,
    name: str,
) -> None:
    with pytest.raises(ValueError, match="Unsafe uploaded path"):
        inputs.write_uploaded_sources(
            [UploadedSource(name, b"evidence")],
            tmp_path,
        )


def test_uploaded_sources_reject_duplicates_and_unsupported_types(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Duplicate uploaded path"):
        inputs.write_uploaded_sources(
            [
                UploadedSource("notes.md", b"one"),
                UploadedSource("NOTES.md", b"two"),
            ],
            tmp_path,
        )
    with pytest.raises(ValueError, match="Unsupported uploaded file type"):
        inputs.write_uploaded_sources(
            [UploadedSource("archive.zip", b"data")],
            tmp_path,
        )


def test_uploaded_sources_enforce_file_and_total_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inputs, "MAX_FILE_BYTES", 3)
    with pytest.raises(ValueError, match="exceeds 25 MB"):
        inputs.write_uploaded_sources(
            [UploadedSource("notes.md", b"four")],
            tmp_path,
        )

    monkeypatch.setattr(inputs, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(inputs, "MAX_TOTAL_BYTES", 5)
    with pytest.raises(ValueError, match="100 MB total"):
        inputs.write_uploaded_sources(
            [
                UploadedSource("one.md", b"123"),
                UploadedSource("two.md", b"456"),
            ],
            tmp_path,
        )
