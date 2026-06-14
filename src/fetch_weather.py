"""Step 1 of the pipeline: fetch per-store NOAA + EPA data and write it to the
'Weather Data' tab of the configured Google Sheet.

Reads the target store list from config/targets.csv (columns: store_name,
city_name, state_id, lat, lng). Deduplicates NOAA grid lookups and EPA UV
calls so a list of ~2000 stores stays tractable.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from sheets_client import open_sheet, write_weather
from weather_sources import (
    PointsCache,
    build_session,
    fetch_store_snapshots,
)

log = logging.getLogger("fetch_weather")


def load_targets(path: Path) -> list[dict]:
    with path.open() as fh:
        reader = csv.DictReader(fh)
        rows = []
        for r in reader:
            if not (r.get("lat") and r.get("lng") and r.get("store_name")):
                continue
            rows.append(r)
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull NOAA + EPA data per store into Google Sheet")
    parser.add_argument("--targets", default="config/targets.csv",
                        help="Path to targets CSV (default: config/targets.csv)")
    parser.add_argument("--points-cache", default="config/.noaa-points-cache.json",
                        help="Where to persist NOAA /points lookups")
    parser.add_argument("--credentials", default=os.environ.get("GOOGLE_SHEETS_CREDENTIALS"),
                        help="Path to Google service account JSON")
    parser.add_argument("--spreadsheet-id", default=os.environ.get("SPREADSHEET_ID"),
                        help="Google Sheet ID to write into")
    parser.add_argument("--user-agent", default=os.environ.get(
        "NOAA_USER_AGENT",
        "Sundriver/1.0 (sunscreen ad automation; contact: ops@example.com)"
    ), help="User-Agent header NOAA requires")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.credentials or not args.spreadsheet_id:
        log.error("Need --credentials and --spreadsheet-id (or env vars).")
        return 2

    stores = load_targets(Path(args.targets))
    log.info("Loaded %d stores from %s", len(stores), args.targets)

    session = build_session(args.user_agent)
    cache = PointsCache(Path(args.points_cache))
    snapshots = fetch_store_snapshots(session, stores, workers=args.workers, points_cache=cache)

    snapshots.sort(key=lambda s: (s.state, s.city, s.store_name))

    sheet = open_sheet(args.credentials, args.spreadsheet_id)
    write_weather(sheet, snapshots)
    log.info("Wrote %d rows to 'Weather Data'", len(snapshots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
