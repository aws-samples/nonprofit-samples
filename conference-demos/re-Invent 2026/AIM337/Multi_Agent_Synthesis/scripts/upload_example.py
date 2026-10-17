# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path
from typing import Any

import boto3

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "examples" / "coastal-resilience" / "manifest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload a local manifest's source files to the deployed input bucket."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--bucket")
    parser.add_argument("--prefix", default="sources/coastal-resilience")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--region", default="us-east-1")
    return parser


def main() -> int:
    args = _parser().parse_args()
    session = boto3.Session(region_name=args.region)
    bucket = args.bucket or session.client("ssm").get_parameter(
        Name="/evidence-synthesis/input-bucket"
    )["Parameter"]["Value"]
    manifest_path = args.manifest.resolve()
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    s3 = session.client("s3")
    prefix = args.prefix.strip("/")

    for source in manifest["sources"]:
        uri = source.get("uri")
        if not uri or str(uri).startswith("s3://"):
            continue
        local_path = (manifest_path.parent / str(uri)).resolve()
        key = f"{prefix}/{local_path.name}"
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        s3.upload_file(
            str(local_path),
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        source["uri"] = f"s3://{bucket}/{key}"

    rendered = json.dumps(manifest, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output.resolve())
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
