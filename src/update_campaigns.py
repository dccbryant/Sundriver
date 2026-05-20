"""Step 2 of the pipeline: read 'Weather Data' + 'Thresholds' from the Google
Sheet, decide which campaigns should be ENABLED or PAUSED, and call the
Google Ads API. Every decision is appended to the 'Campaign Log' tab.

Decision rule (per row in Thresholds):
  - If enabled is FALSE → skip (kill switch for that row).
  - Otherwise compare the matching state's weather to the thresholds:
      met = (min_temp_f is blank OR temperature_f >= min_temp_f)
            AND
            (min_uv      is blank OR uv_index      >= min_uv)
    If both thresholds are blank, the row is treated as 'always pause' — we
    refuse to act and log a warning so the marketer notices.
  - met == True  → desired status ENABLED
    met == False → desired status PAUSED
  - We only call the API when the campaign's current status differs from
    the desired status, so re-runs are cheap and the audit log stays clean.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from ads_client import AdsClient, CampaignAction
from sheets_client import (
    append_log,
    open_sheet,
    read_thresholds,
    read_weather,
)

log = logging.getLogger("update_campaigns")


def decide(threshold, weather_row) -> tuple[str, str]:
    """Return (desired_status, reason)."""
    if weather_row is None:
        return "PAUSED", f"no weather data for {threshold.state}; pausing as failsafe"

    temp = weather_row.get("temperature_f")
    uv = weather_row.get("uv_index")
    try:
        temp_val = float(temp) if temp not in (None, "") else None
    except (TypeError, ValueError):
        temp_val = None
    try:
        uv_val = float(uv) if uv not in (None, "") else None
    except (TypeError, ValueError):
        uv_val = None

    if threshold.min_temp_f is None and threshold.min_uv is None:
        return "SKIP", "both thresholds blank — refusing to act, please set min_temp_f or min_uv"

    parts = []
    temp_ok = True
    if threshold.min_temp_f is not None:
        if temp_val is None:
            return "PAUSED", f"missing temperature for {threshold.state}; pausing as failsafe"
        temp_ok = temp_val >= threshold.min_temp_f
        parts.append(f"temp {temp_val}>= {threshold.min_temp_f}={temp_ok}")

    uv_ok = True
    if threshold.min_uv is not None:
        if uv_val is None:
            return "PAUSED", f"missing UV for {threshold.state}; pausing as failsafe"
        uv_ok = uv_val >= threshold.min_uv
        parts.append(f"uv {uv_val}>= {threshold.min_uv}={uv_ok}")

    met = temp_ok and uv_ok
    return ("ENABLED" if met else "PAUSED"), "; ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive Google Ads campaigns from the sheet")
    parser.add_argument("--credentials", default=os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))
    parser.add_argument("--spreadsheet-id", default=os.environ.get("SPREADSHEET_ID"))
    parser.add_argument("--google-ads-yaml", default=os.environ.get(
        "GOOGLE_ADS_YAML", "google-ads.yaml"))
    parser.add_argument("--customer-id", default=os.environ.get("GOOGLE_ADS_CUSTOMER_ID"),
                        help="Google Ads customer ID (digits only, no dashes)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Decide and log, but don't call the Ads API")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.credentials or not args.spreadsheet_id or not args.customer_id:
        log.error("Need --credentials, --spreadsheet-id and --customer-id (or env vars).")
        return 2

    sheet = open_sheet(args.credentials, args.spreadsheet_id)
    weather = read_weather(sheet)
    thresholds = read_thresholds(sheet)
    log.info("Loaded %d weather rows, %d threshold rows", len(weather), len(thresholds))

    ads = AdsClient(args.google_ads_yaml, args.customer_id, dry_run=args.dry_run)
    campaign_ids = sorted({t.campaign_id for t in thresholds if t.enabled})
    try:
        current = ads.get_campaign_statuses(campaign_ids) if not args.dry_run else {}
    except Exception as exc:
        log.error("Could not fetch current campaign statuses: %s", exc)
        current = {}

    log_rows: list[list] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for t in thresholds:
        if not t.enabled:
            log_rows.append([now, t.state, t.campaign_id, "skip", "row disabled in sheet"])
            continue

        desired, reason = decide(t, weather.get(t.state))
        if desired == "SKIP":
            log_rows.append([now, t.state, t.campaign_id, "skip", reason])
            log.warning("Skipping %s/%s: %s", t.state, t.campaign_id, reason)
            continue

        current_status = current.get(t.campaign_id)
        if current_status == desired:
            log_rows.append([now, t.state, t.campaign_id, "no-change",
                             f"already {desired}; {reason}"])
            continue

        outcome = ads.apply(CampaignAction(
            customer_id=args.customer_id,
            campaign_id=t.campaign_id,
            desired_status=desired,
            reason=reason,
        ))
        log_rows.append([now, t.state, t.campaign_id, desired.lower(),
                         f"{outcome}; {reason}"])
        log.info("%s/%s: %s", t.state, t.campaign_id, outcome)

    append_log(sheet, log_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
