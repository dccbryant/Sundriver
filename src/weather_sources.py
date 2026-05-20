"""Government weather + UV data clients.

Sources (both free, no paid keys required):
  - NOAA National Weather Service API (api.weather.gov) — temperature, conditions,
    cloud cover. Requires a descriptive User-Agent string per NOAA policy.
  - EPA Envirofacts UV Hourly Forecast API (data.epa.gov) — hourly UV index by
    ZIP code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

NOAA_BASE = "https://api.weather.gov"
EPA_UV_HOURLY = "https://data.epa.gov/efservice/getEnvirofactsUVHOURLY/ZIP/{zip}/JSON"


@dataclass
class WeatherSnapshot:
    state: str
    name: str
    city: str
    lat: float
    lon: float
    zip: str
    temperature_f: Optional[float]
    sky_cover_pct: Optional[float]
    short_forecast: Optional[str]
    uv_index: Optional[float]
    observed_at_utc: str
    source_notes: str


def _session(user_agent: str) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
    return s


def fetch_noaa_current(session: requests.Session, lat: float, lon: float) -> dict:
    """Return the first period (~current hour) of the NOAA hourly forecast.

    NOAA requires a two-step lookup: /points/{lat,lon} -> forecastHourly URL.
    """
    pts = session.get(f"{NOAA_BASE}/points/{lat:.4f},{lon:.4f}", timeout=20)
    pts.raise_for_status()
    hourly_url = pts.json()["properties"]["forecastHourly"]

    fc = session.get(hourly_url, timeout=20)
    fc.raise_for_status()
    periods = fc.json()["properties"]["periods"]
    if not periods:
        raise RuntimeError("NOAA returned no forecast periods")
    return periods[0]


def fetch_epa_uv_current(session: requests.Session, zip_code: str) -> Optional[float]:
    """Return the UV index nearest the current local hour for the given ZIP.

    The EPA hourly UV endpoint returns a list of forecast points whose
    DATE_TIME is formatted as 'MMM/DD/YYYY HH AM/PM'. We pick the row whose
    timestamp is closest to (and not after) the current local time at that
    location. If we can't parse, we fall back to the first row.
    """
    url = EPA_UV_HOURLY.format(zip=zip_code)
    r = session.get(url, timeout=20)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None

    now_local = datetime.now()
    best = None
    best_delta = None
    for row in rows:
        ts_raw = row.get("DATE_TIME") or row.get("ORDER")
        try:
            ts = datetime.strptime(ts_raw, "%b/%d/%Y %I %p")
        except (TypeError, ValueError):
            continue
        delta = abs((ts - now_local).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = row, delta

    chosen = best or rows[0]
    try:
        return float(chosen.get("UV_VALUE"))
    except (TypeError, ValueError):
        return None


def fetch_state_snapshot(
    session: requests.Session,
    state_cfg: dict,
) -> WeatherSnapshot:
    """Pull NOAA + EPA data for one configured location. Never raises; missing
    pieces become None with a note in source_notes so the operator can see why."""
    notes = []
    temp_f = sky_pct = uv = None
    short = None

    try:
        period = fetch_noaa_current(session, state_cfg["lat"], state_cfg["lon"])
        temp_f = float(period["temperature"]) if period.get("temperatureUnit") == "F" \
            else (float(period["temperature"]) * 9 / 5 + 32 if period.get("temperature") is not None else None)
        short = period.get("shortForecast")
        # NWS sometimes exposes skyCover in detailed grid; hourly forecast only
        # carries probabilityOfPrecipitation. Leave sky_cover_pct None unless present.
        if isinstance(period.get("probabilityOfPrecipitation"), dict):
            pass
    except Exception as exc:
        notes.append(f"NOAA error: {exc}")
        log.warning("NOAA fetch failed for %s: %s", state_cfg["state"], exc)

    try:
        uv = fetch_epa_uv_current(session, state_cfg["zip"])
    except Exception as exc:
        notes.append(f"EPA error: {exc}")
        log.warning("EPA fetch failed for %s: %s", state_cfg["state"], exc)

    return WeatherSnapshot(
        state=state_cfg["state"],
        name=state_cfg["name"],
        city=state_cfg["city"],
        lat=state_cfg["lat"],
        lon=state_cfg["lon"],
        zip=state_cfg["zip"],
        temperature_f=temp_f,
        sky_cover_pct=sky_pct,
        short_forecast=short,
        uv_index=uv,
        observed_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_notes="; ".join(notes),
    )


def build_session(user_agent: str) -> requests.Session:
    return _session(user_agent)
