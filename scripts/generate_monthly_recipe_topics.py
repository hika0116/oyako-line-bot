#!/usr/bin/env python3
"""Generate aggregate monthly collection themes. Dry-run is the default."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from family_os.recipe_catalog import RecipeCatalog  # noqa: E402
from family_os.recipe_topics import generate_collection_topics  # noqa: E402


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", type=int, default=datetime.now().month)
    parser.add_argument("--aggregates", type=Path, help="Aggregate ingredient demand JSON; must not contain user ids")
    parser.add_argument("--dry-run", action="store_true", help="Print themes without writing (the default)")
    parser.add_argument("--enqueue", action="store_true", help="Explicitly insert generated themes into Supabase")
    args = parser.parse_args()
    if args.dry_run and args.enqueue:
        parser.error("--dry-run and --enqueue cannot be used together")
    return args


def load_aggregates(path: Path | None) -> list[dict]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("ingredients", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("aggregate input must be a list")
    for row in rows:
        if not isinstance(row, dict) or any(key in row for key in ("user_id", "line_user_id", "profile")):
            raise ValueError("aggregate input contains a personal identifier or invalid row")
    return rows


def enqueue_topics(topics: list[dict]) -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required only with --enqueue")
    from supabase import create_client

    create_client(url, key).table("recipe_collection_topics").insert(topics).execute()


def main() -> int:
    args = parse_args()
    catalog = RecipeCatalog.from_json()
    aggregates = load_aggregates(args.aggregates)
    topics = [
        item.to_dict()
        for item in generate_collection_topics(
            catalog=catalog,
            inventory_aggregates=aggregates,
            month=args.month,
        )
    ]
    print(json.dumps({"dry_run": not args.enqueue, "topics": topics}, ensure_ascii=False, indent=2))
    if args.enqueue and topics:
        enqueue_topics(topics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
