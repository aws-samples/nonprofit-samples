# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections import defaultdict

from evidence_synthesis.models import (
    EvidenceRecord,
    SourceChunk,
    SourceProfile,
    SourceReference,
)


def merge_evidence_gaps(*groups: list[str]) -> list[str]:
    merged = []
    seen = set()
    for group in groups:
        for gap in group:
            if gap not in seen:
                merged.append(gap)
                seen.add(gap)
    return merged


def normalized_text(value: str) -> str:
    return " ".join(value.split())


def excerpt_is_supported(excerpt: str, content: str) -> bool:
    normalized = normalized_text(excerpt)
    return bool(normalized) and normalized in normalized_text(content)


def grounded_profiles(
    chunks: list[SourceChunk],
    records: list[EvidenceRecord],
    proposed: list[SourceProfile],
) -> tuple[list[SourceProfile], list[str]]:
    by_source: dict[str, list[SourceChunk]] = defaultdict(list)
    for chunk in chunks:
        by_source[chunk.source_id].append(chunk)
    retained = {(record.source_id, record.locator) for record in records}
    content_by_key = {
        (chunk.source_id, chunk.locator): chunk.content
        for chunk in chunks
        if (chunk.source_id, chunk.locator) in retained
    }
    accepted: dict[str, SourceProfile] = {}
    gaps = []

    def references_supported(source_id: str, references: list[SourceReference]) -> bool:
        if not references:
            return False
        for reference in references:
            source_text = content_by_key.get((source_id, reference.locator), "")
            if not excerpt_is_supported(reference.excerpt, source_text):
                return False
        return True

    # The Verifier still checks what these excerpts actually support.
    for profile in proposed:
        if profile.source_id not in by_source or profile.source_id in accepted:
            gaps.append("An unknown or duplicate source description was discarded.")
            continue
        if not references_supported(profile.source_id, profile.references):
            gaps.append(
                f"Description for {profile.source_id} lacked supporting excerpts "
                "in retained source chunks; file-derived details are shown instead."
            )
            continue
        context = [
            item for item in profile.context
            if references_supported(profile.source_id, item.references)
        ]
        if len(context) != len(profile.context):
            gaps.append(f"Unbacked context notes for {profile.source_id} were discarded.")
        accepted[profile.source_id] = profile.model_copy(
            update={"context": context, "origin": "extracted"}
        )

    profiles = []
    for source_id, source_chunks in by_source.items():
        source_profile = accepted.get(source_id)
        if source_profile is None:
            # Use file details when the proposed description lacks supporting text.
            retained_count = sum(record.source_id == source_id for record in records)
            source_profile = SourceProfile(
                source_id=source_id,
                title=source_chunks[0].title[:160] or source_id[:160],
                description=(
                    f"{len(source_chunks)} readable source chunks; "
                    f"{retained_count} evidence records retained. "
                    "No model-derived description is available."
                ),
                origin="mechanical",
            )
        profiles.append(source_profile)
    return profiles, gaps
