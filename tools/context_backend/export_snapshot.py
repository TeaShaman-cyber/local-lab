#!/usr/bin/env python3
"""Validate a Hermes snapshot or emit a private canonical context bundle.

The input must be a verified, offline SQLite snapshot. This command never opens the
live Hermes database and never writes to the source file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.context_backend.core import SourceContractError, describe_snapshot, write_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=pathlib.Path, help="verified offline state.db snapshot")
    parser.add_argument("--receipt", required=True, type=pathlib.Path, help="matching verify.py receipt JSON")
    parser.add_argument("--source-id", default="hermes-local", help="stable identity of this source")
    parser.add_argument("--output-dir", type=pathlib.Path, help="new directory for the private canonical bundle")
    parser.add_argument("--validate-only", action="store_true", help="validate and print metadata without exporting content")
    args = parser.parse_args()

    if not args.validate_only and args.output_dir is None:
        parser.error("--output-dir is required unless --validate-only is used")
    try:
        if args.validate_only:
            descriptor = describe_snapshot(args.snapshot, args.source_id, args.receipt)
            result = descriptor.manifest()
            result["validation"] = "structural-read-only"
        else:
            result = write_bundle(args.snapshot, args.output_dir, args.source_id, args.receipt)
            result["validation"] = "structural-read-only"
    except (SourceContractError, RuntimeError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
