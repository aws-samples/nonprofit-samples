# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import json
from pathlib import Path

from evidence_synthesis.models import RunRequest
from evidence_synthesis.source_loader import SourceLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROJECT_ROOT / "examples" / "harris-county-climate-risk"
MANIFEST = FIXTURE_DIR / "manifest.json"


def test_harris_county_real_data_fixture_is_valid_and_readable() -> None:
    request = RunRequest.model_validate_json(MANIFEST.read_text(encoding="utf-8"))

    chunks = SourceLoader().load_all(request.sources, FIXTURE_DIR)

    assert len(request.sources) == 5
    assert len(chunks) == 23
    assert {chunk.source_id for chunk in chunks} == {
        "noaa-climate-2024",
        "fema-risk-index",
        "environmental-justice-comparison",
        "landsat-surface-temperature",
        "dataset-notes",
    }
    assert all(chunk.content for chunk in chunks)
    assert all(not source.metadata for source in request.sources)
    assert not (FIXTURE_DIR / "source-metadata.json").exists()
    assert all('"stance"' not in chunk.content for chunk in chunks)


def test_harris_county_real_data_fixture_preserves_representative_values() -> None:
    noaa = json.loads(
        (FIXTURE_DIR / "noaa-climate-summary-2024.json").read_text(encoding="utf-8")
    )
    fema = json.loads(
        (FIXTURE_DIR / "fema-national-risk-index.json").read_text(encoding="utf-8")
    )
    environmental_justice = json.loads(
        (FIXTURE_DIR / "environmental-justice-blockgroups.json").read_text(
            encoding="utf-8"
        )
    )
    landsat = json.loads(
        (FIXTURE_DIR / "landsat-surface-temperature-samples.json").read_text(
            encoding="utf-8"
        )
    )

    assert noaa["annual_summary"]["days_maximum_temperature_at_least_35c"] == 64
    assert noaa["annual_summary"]["maximum_daily_precipitation_mm"] == 119.9
    assert fema[0]["risk_index_score"] == 94.21
    assert fema[2]["expected_annual_loss_dollars"] == 1_100_000_000
    assert environmental_justice[0]["demographic_index_national_percentile"] == 100
    assert environmental_justice[3]["no2_national_percentile"] == 99
    assert landsat[0]["mean_surface_temperature_c"] == 39.7
    assert landsat[3]["mean_surface_temperature_c"] == 37.7
