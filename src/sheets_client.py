"""Thin wrapper around gspread for the store-level workflow.

Sheet layout (the marketer owns the spreadsheet; this code only writes to
'Weather Data' and 'Campaign Log' and reads from 'Thresholds'):

  Weather Data   — overwritten every run with one row per store
    store_name | city | state | lat | lng | temperature_f | uv_index |
    short_forecast | observed_at_utc | source_notes

  Thresholds     — hand-edited by the marketer; one row per (store, campaign)
    store_name | campaign_id | campaign_name | min_temp_f | min_uv | enabled |
    notes
    - store_name must match the Weather Data store_name exactly
    - min_temp_f / min_uv may be blank to ignore that signal
    - 'enabled' (TRUE/FALSE) is a kill switch; FALSE means leave campaign alone

  Campaign Log   — append-only audit trail of enable/pause actions
    timestamp_utc | store_name | campaign_id | action | reason
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import gspread
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

WEATHER_TAB = "Weather Data"
THRESHOLDS_TAB = "Thresholds"
LOG_TAB = "Campaign Log"

WEATHER_HEADERS = [
    "store_name", "city", "state", "lat", "lng",
    "temperature_f", "uv_index", "short_forecast",
    "observed_at_utc", "source_notes",
]
THRESHOLD_HEADERS = [
    "store_name", "campaign_id", "campaign_name",
    "min_temp_f", "min_uv", "enabled", "notes",
]
LOG_HEADERS = ["timestamp_utc", "store_name", "campaign_id", "action", "reason"]

WEATHER_RANGE = f"A2:{chr(ord('A') + len(WEATHER_HEADERS) - 1)}"  # "A2:J" today
WEATHER_COLS = len(WEATHER_HEADERS)


@dataclass
class ThresholdRow:
    store_name: str
    campaign_id: str
    campaign_name: str
    min_temp_f: Optional[float]
    min_uv: Optional[float]
    enabled: bool
    notes: str


def open_sheet(credentials_path: str, spreadsheet_id: str) -> gspread.Spreadsheet:
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(spreadsheet_id)


def _ensure_tab(sheet: gspread.Spreadsheet, title: str, headers: list[str]) -> gspread.Worksheet:
    try:
        ws = sheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=title, rows=2200, cols=max(10, len(headers)))
        ws.update(values=[headers], range_name="A1")
        return ws
    existing = ws.row_values(1)
    if existing != headers:
        ws.update(values=[headers], range_name="A1")
    return ws


def write_weather(sheet: gspread.Spreadsheet, snapshots: list) -> None:
    ws = _ensure_tab(sheet, WEATHER_TAB, WEATHER_HEADERS)
    rows = [
        [s.store_name, s.city, s.state, s.lat, s.lng,
         s.temperature_f, s.uv_index, s.short_forecast,
         s.observed_at_utc, s.source_notes]
        for s in snapshots
    ]
    last_col = chr(ord("A") + WEATHER_COLS - 1)
    # Clear past the new dataset so a shrinking input doesn't leave stale rows.
    end_row = max(ws.row_count, len(rows) + 1)
    ws.batch_clear([f"A2:{last_col}{end_row}"])
    if rows:
        ws.update(values=rows, range_name="A2", value_input_option="RAW")


def read_thresholds(sheet: gspread.Spreadsheet) -> List[ThresholdRow]:
    ws = _ensure_tab(sheet, THRESHOLDS_TAB, THRESHOLD_HEADERS)
    records = ws.get_all_records(expected_headers=THRESHOLD_HEADERS)
    out: list[ThresholdRow] = []
    for r in records:
        name = str(r.get("store_name", "")).strip()
        cid = str(r.get("campaign_id", "")).strip()
        if not name or not cid:
            continue
        out.append(ThresholdRow(
            store_name=name,
            campaign_id=cid,
            campaign_name=str(r.get("campaign_name", "")).strip(),
            min_temp_f=_to_float(r.get("min_temp_f")),
            min_uv=_to_float(r.get("min_uv")),
            enabled=_to_bool(r.get("enabled")),
            notes=str(r.get("notes", "")).strip(),
        ))
    return out


def read_weather(sheet: gspread.Spreadsheet) -> dict[str, dict]:
    """Return {store_name: row_dict} for every row in the Weather Data tab."""
    ws = _ensure_tab(sheet, WEATHER_TAB, WEATHER_HEADERS)
    records = ws.get_all_records(expected_headers=WEATHER_HEADERS)
    return {str(r["store_name"]): r for r in records if r.get("store_name")}


def append_log(sheet: gspread.Spreadsheet, entries: list[list]) -> None:
    if not entries:
        return
    ws = _ensure_tab(sheet, LOG_TAB, LOG_HEADERS)
    ws.append_rows(entries, value_input_option="RAW")


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"true", "yes", "y", "1", "on"}
