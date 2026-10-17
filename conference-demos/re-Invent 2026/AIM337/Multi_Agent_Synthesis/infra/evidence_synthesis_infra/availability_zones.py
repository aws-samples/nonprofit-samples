# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import boto3
from botocore.config import Config


def resolve_availability_zone_names(
    region: str,
    zone_ids: Sequence[str],
    *,
    client: Any | None = None,
) -> list[str]:
    """Resolve stable physical AZ IDs to account-specific AZ names."""
    if len(zone_ids) != 2 or len(set(zone_ids)) != 2:
        raise ValueError("Provide exactly two unique AgentCore-supported AZ IDs")
    ec2 = client or boto3.Session(region_name=region).client(
        "ec2",
        config=Config(
            retries={"total_max_attempts": 3, "mode": "adaptive"},
            connect_timeout=5,
            read_timeout=15,
            user_agent_appid="evidence-synthesis-infra/0.1.0",
        ),
    )
    response = ec2.describe_availability_zones(
        ZoneIds=list(zone_ids),
        Filters=[{"Name": "state", "Values": ["available"]}],
    )
    names_by_id = {
        str(zone["ZoneId"]): str(zone["ZoneName"])
        for zone in response["AvailabilityZones"]
    }
    missing = [zone_id for zone_id in zone_ids if zone_id not in names_by_id]
    if missing:
        raise ValueError(
            "AgentCore-supported availability zones are unavailable: "
            + ", ".join(missing)
        )
    return [names_by_id[zone_id] for zone_id in zone_ids]
