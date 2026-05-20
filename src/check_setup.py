"""Smoke-test the Sundriver setup: verify Sheets access and (optionally)
Google Ads access. Idempotently creates the three expected tabs in the
spreadsheet so the marketer doesn't have to.

Exits 0 on success, non-zero on any failure. Run via setup.sh or directly:

    python src/check_setup.py            # checks both Sheets and Ads
    python src/check_setup.py --skip-ads # only the Sheets half
"""

from __future__ import annotations

import argparse
import os
import sys

from sheets_client import (
    LOG_HEADERS, LOG_TAB,
    THRESHOLD_HEADERS, THRESHOLDS_TAB,
    WEATHER_HEADERS, WEATHER_TAB,
    _ensure_tab, open_sheet,
)


def check_sheets(credentials: str, spreadsheet_id: str) -> bool:
    print(f"[sheets] opening spreadsheet {spreadsheet_id[:8]}…")
    sheet = open_sheet(credentials, spreadsheet_id)
    print(f"[sheets] ok — '{sheet.title}'")
    for tab, headers in (
        (WEATHER_TAB, WEATHER_HEADERS),
        (THRESHOLDS_TAB, THRESHOLD_HEADERS),
        (LOG_TAB, LOG_HEADERS),
    ):
        _ensure_tab(sheet, tab, headers)
        print(f"[sheets] ok — tab '{tab}' present with correct headers")
    return True


def check_ads(yaml_path: str, customer_id: str) -> bool:
    print(f"[ads] connecting to customer {customer_id}…")
    from ads_client import AdsClient
    client = AdsClient(yaml_path, customer_id, dry_run=False)
    ga_service = client._client_lazy().get_service("GoogleAdsService")
    rows = ga_service.search(
        customer_id=client.customer_id,
        query="SELECT customer.descriptive_name FROM customer LIMIT 1",
    )
    for row in rows:
        print(f"[ads] ok — '{row.customer.descriptive_name}'")
        return True
    print("[ads] FAIL — no customer row returned")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Sundriver credentials")
    parser.add_argument("--credentials", default=os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))
    parser.add_argument("--spreadsheet-id", default=os.environ.get("SPREADSHEET_ID"))
    parser.add_argument("--google-ads-yaml", default=os.environ.get(
        "GOOGLE_ADS_YAML", "google-ads.yaml"))
    parser.add_argument("--customer-id", default=os.environ.get("GOOGLE_ADS_CUSTOMER_ID"))
    parser.add_argument("--skip-ads", action="store_true",
                        help="Only verify Google Sheets access")
    args = parser.parse_args()

    if not args.credentials or not args.spreadsheet_id:
        print("ERROR: set GOOGLE_SHEETS_CREDENTIALS and SPREADSHEET_ID (env or flags)")
        return 2

    try:
        check_sheets(args.credentials, args.spreadsheet_id)
    except Exception as exc:
        print(f"[sheets] FAIL — {exc}")
        return 1

    if args.skip_ads:
        print("[ads] skipped")
        return 0

    if not args.customer_id:
        print("ERROR: set GOOGLE_ADS_CUSTOMER_ID, or pass --skip-ads")
        return 2

    try:
        ok = check_ads(args.google_ads_yaml, args.customer_id)
    except Exception as exc:
        print(f"[ads] FAIL — {exc}")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
