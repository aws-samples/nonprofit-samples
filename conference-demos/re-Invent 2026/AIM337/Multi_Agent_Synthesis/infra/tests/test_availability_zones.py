# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from typing import Any

import pytest
from evidence_synthesis_infra.availability_zones import (
    resolve_availability_zone_names,
)


class FakeEc2Client:
    def describe_availability_zones(self, **request: Any) -> dict[str, Any]:
        assert request["ZoneIds"] == ["use1-az4", "use1-az1"]
        return {
            "AvailabilityZones": [
                {"ZoneId": "use1-az1", "ZoneName": "us-east-1c"},
                {"ZoneId": "use1-az4", "ZoneName": "us-east-1a"},
            ]
        }


def test_resolves_stable_zone_ids_in_requested_order() -> None:
    assert resolve_availability_zone_names(
        "us-east-1",
        ["use1-az4", "use1-az1"],
        client=FakeEc2Client(),
    ) == ["us-east-1a", "us-east-1c"]


def test_requires_two_unique_zone_ids() -> None:
    with pytest.raises(ValueError, match="exactly two unique"):
        resolve_availability_zone_names(
            "us-east-1",
            ["use1-az4", "use1-az4"],
            client=FakeEc2Client(),
        )
