"""Step 1 of the pipeline: fetch government weather/UV data per state and
write it to the 'Weather Data' tab of the configured Google Sheet.

Run hourly (cron, GitHub Actions, etc.). Idempotent — overwrites the tab.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sheets_client import open_sheet, write_weather
from weather_sources import build_session, fetch_state_snapshot

log = logging.getLogger("fetch_weather")


def load_states(path: Path) -> list[dict]:
    with path.open() as fh:
        return json.load(fh)["states"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull NOAA + EPA data into Google Sheet")
    parser.add_argument("--states", default="config/states.json",
                        help="Path to states config (default: config/states.json)")
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

    states = load_states(Path(args.states))
    session = build_session(args.user_agent)

    snapshots = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_state_snapshot, session, s): s for s in states}
        for fut in as_completed(futures):
            cfg = futures[fut]
            try:
                snap = fut.result()
                snapshots.append(snap)
                log.info("%s temp=%s uv=%s", snap.state, snap.temperature_f, snap.uv_index)
            except Exception as exc:
                log.exception("Unhandled error for %s: %s", cfg["state"], exc)

    snapshots.sort(key=lambda s: s.state)
    sheet = open_sheet(args.credentials, args.spreadsheet_id)
    write_weather(sheet, snapshots)
    log.info("Wrote %d rows to 'Weather Data'", len(snapshots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
