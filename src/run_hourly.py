"""Convenience entry point: runs fetch_weather then update_campaigns in-process.

Useful for cron / GitHub Actions where you'd rather invoke one command.
Each step's CLI args still work via env vars (GOOGLE_SHEETS_CREDENTIALS,
SPREADSHEET_ID, GOOGLE_ADS_YAML, GOOGLE_ADS_CUSTOMER_ID, NOAA_USER_AGENT).
"""

from __future__ import annotations

import logging
import sys

import fetch_weather
import update_campaigns


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    rc = fetch_weather.main()
    if rc != 0:
        return rc
    return update_campaigns.main()


if __name__ == "__main__":
    sys.exit(main())
