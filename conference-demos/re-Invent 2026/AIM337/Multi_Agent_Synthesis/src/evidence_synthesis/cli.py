# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from evidence_synthesis.factory import build_orchestrator
from evidence_synthesis.models import RunRequest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evidence-synthesis",
        description="Run the four-agent evidence synthesis pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run a manifest through the pipeline.")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument(
        "--runtime",
        choices=("deterministic", "strands"),
        default="deterministic",
    )
    run.add_argument("--output", type=Path, default=Path("runs"))
    run.add_argument("--json", action="store_true", help="Print the complete state as JSON.")
    validate = subparsers.add_parser("validate", help="Validate a manifest.")
    validate.add_argument("--manifest", type=Path, required=True)
    return parser


def _load_manifest(path: Path) -> RunRequest:
    return RunRequest.model_validate_json(path.read_text(encoding="utf-8"))


async def _run(args: argparse.Namespace) -> int:
    manifest = args.manifest.resolve()
    request = _load_manifest(manifest)
    orchestrator = build_orchestrator(
        runtime_name=args.runtime,
        output_dir=args.output.resolve(),
    )
    state = await orchestrator.run(request, base_dir=manifest.parent)
    if args.json:
        print(state.model_dump_json(indent=2))
    else:
        assert state.synthesis is not None
        print(state.synthesis.answer)
        print()
        print(
            f"status={state.status.value} run_id={state.run_id} "
            f"tokens={state.total_tokens} cycles={state.verification_cycles}"
        )
        print(f"artifacts={args.output.resolve() / state.run_id}")
    return 0 if state.status.value in {"completed", "blocked", "partial"} else 1


def main() -> int:
    # Load .env from the working directory so plain os.getenv reads
    # (SOURCE_BUCKET, ARTIFACT_BUCKET, ...) see it too. Real environment
    # variables take precedence; deployed entry points do not load .env.
    load_dotenv(override=False)
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.command == "validate":
            request = _load_manifest(args.manifest.resolve())
            print(
                json.dumps(
                    {
                        "valid": True,
                        "question": request.question,
                        "source_count": len(request.sources),
                    },
                    indent=2,
                )
            )
            return 0
        return asyncio.run(_run(args))
    except (OSError, ValidationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

