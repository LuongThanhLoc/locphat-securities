"""CLI for Macro v2 refresh, backfill and quality audit."""

from __future__ import annotations

import argparse
import json

from macro import get_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Verified Macro v2 data operations")
    parser.add_argument("command", choices=("refresh", "backfill", "audit"))
    args = parser.parse_args()
    service = get_service()
    result = service.sync() if args.command in {"refresh", "backfill"} else service.repository.audit()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if args.command in {"refresh", "backfill"} and result.get("state") == "error":
        return 1
    if args.command == "audit" and result.get("events_with_actual_without_official_evidence"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
