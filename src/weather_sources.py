"""Government weather + UV data clients (store-level, with deduplication).

Sources (both free, no paid keys required):
  - NOAA National Weather Service API (api.weather.gov) — temperature + short
    forecast. Two-step lookup: /points/{lat,lng} returns a grid identifier and a
    forecastHourly URL; multiple stores typically share a NOAA grid so the
    second call is deduplicated.
  - EPA Envirofacts UV Hourly Forecast API (data.epa.gov) — UV index by city.
    Stores in the same (state, city) share one EPA call.

A persistent on-disk JSON cache stores the /points response for each
(lat, lng) pair indefinitely (the grid never moves), so warm runs only hit
NOAA's hourly forecast endpoint, not /points.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

NOAA_BASE = "https://api.weather.gov"
EPA_UV_CITY = "https://data.epa.gov/efservice/getEnvirofactsUVHOURLY/CITY/{city}/STATE/{state}/JSON"

DEFAULT_POINTS_CACHE = Path("config/.noaa-points-cache.json")


@dataclass
class StoreSnapshot:
    store_name: str
    city: str
    state: str
    lat: float
    lng: float
    temperature_f: Optional[float] = None
    short_forecast: Optional[str] = None
    uv_index: Optional[float] = None
    observed_at_utc: str = ""
    source_notes: str = ""

    def add_note(self, note: str) -> None:
        self.source_notes = f"{self.source_notes}; {note}" if self.source_notes else note


def build_session(user_agent: str) -> requests.Session:
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


class PointsCache:
    """Persistent JSON cache for NOAA /points lookups, keyed on rounded lat/lng."""

    def __init__(self, path: Path = DEFAULT_POINTS_CACHE):
        self.path = path
        self._lock = threading.Lock()
        try:
            self._data = json.loads(path.read_text()) if path.exists() else {}
        except Exception as exc:
            log.warning("Could not read points cache %s (%s); starting empty", path, exc)
            self._data = {}

    @staticmethod
    def _key(lat: float, lng: float) -> str:
        return f"{lat:.4f},{lng:.4f}"

    def get(self, lat: float, lng: float) -> Optional[dict]:
        return self._data.get(self._key(lat, lng))

    def put(self, lat: float, lng: float, value: dict) -> None:
        with self._lock:
            self._data[self._key(lat, lng)] = value

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._data, separators=(",", ":")))
            except Exception as exc:
                log.warning("Could not save points cache %s: %s", self.path, exc)


def _noaa_points(session: requests.Session, lat: float, lng: float) -> dict:
    r = session.get(f"{NOAA_BASE}/points/{lat:.4f},{lng:.4f}", timeout=20)
    r.raise_for_status()
    props = r.json()["properties"]
    return {
        "forecastHourly": props["forecastHourly"],
        "grid": [props.get("gridId"), props.get("gridX"), props.get("gridY")],
    }


def _noaa_first_period(session: requests.Session, forecast_url: str) -> dict:
    r = session.get(forecast_url, timeout=20)
    r.raise_for_status()
    periods = r.json()["properties"].get("periods") or []
    if not periods:
        raise RuntimeError("NOAA returned no forecast periods")
    p = periods[0]
    temp = p.get("temperature")
    if temp is not None and p.get("temperatureUnit") == "C":
        temp = float(temp) * 9 / 5 + 32
    return {
        "temperature_f": float(temp) if temp is not None else None,
        "short_forecast": p.get("shortForecast") or "",
    }


def _epa_uv_city(session: requests.Session, city: str, state: str) -> Optional[float]:
    url = EPA_UV_CITY.format(city=quote(city.upper()), state=quote(state.upper()))
    r = session.get(url, timeout=20)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    now_local = datetime.now()
    best, best_delta = None, None
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


def fetch_store_snapshots(
    session: requests.Session,
    stores: list[dict],
    workers: int = 8,
    points_cache: Optional[PointsCache] = None,
) -> list[StoreSnapshot]:
    """Pull NOAA + EPA data for many stores, deduping where the API allows.

    Pipeline:
      1. Resolve each unique lat/lng to a NOAA grid (cached on disk forever).
      2. Group stores by NOAA grid; fetch one hourly forecast per grid.
      3. Group stores by (state, city); fetch one EPA UV reading per pair.
      4. Fan results back out to per-store snapshots.

    Never raises for transient failures — missing pieces land in source_notes.
    """
    if points_cache is None:
        points_cache = PointsCache()

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshots = [
        StoreSnapshot(
            store_name=s["store_name"], city=s["city_name"], state=s["state_id"],
            lat=float(s["lat"]), lng=float(s["lng"]),
            observed_at_utc=now_iso,
        )
        for s in stores
    ]

    # ---- Phase 1: NOAA /points (cached) ----
    point_results: dict[int, dict] = {}
    uncached: list[int] = []
    for i, snap in enumerate(snapshots):
        cached = points_cache.get(snap.lat, snap.lng)
        if cached:
            point_results[i] = cached
        else:
            uncached.append(i)

    log.info("NOAA /points: %d cached, %d to fetch", len(point_results), len(uncached))

    def _points_worker(i: int) -> tuple[int, dict]:
        snap = snapshots[i]
        try:
            value = _noaa_points(session, snap.lat, snap.lng)
            points_cache.put(snap.lat, snap.lng, value)
            return i, value
        except Exception as exc:
            return i, {"_error": str(exc)}

    if uncached:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, result in pool.map(_points_worker, uncached):
                point_results[i] = result
        points_cache.save()

    # ---- Phase 2: NOAA hourly per unique grid ----
    grid_members: dict[tuple, list[int]] = {}
    for i, pt in point_results.items():
        if "_error" in pt:
            snapshots[i].add_note(f"NOAA points: {pt['_error']}")
            continue
        grid = tuple(pt.get("grid") or [])
        grid_members.setdefault((grid, pt["forecastHourly"]), []).append(i)

    log.info("NOAA hourly: %d unique grids for %d stores", len(grid_members), len(snapshots))

    def _grid_worker(item):
        (_grid, url), members = item
        try:
            return members, _noaa_first_period(session, url)
        except Exception as exc:
            return members, {"_error": str(exc)}

    if grid_members:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for members, result in pool.map(_grid_worker, list(grid_members.items())):
                for i in members:
                    if "_error" in result:
                        snapshots[i].add_note(f"NOAA hourly: {result['_error']}")
                    else:
                        snapshots[i].temperature_f = result["temperature_f"]
                        snapshots[i].short_forecast = result["short_forecast"]

    # ---- Phase 3: EPA UV per unique (state, city) ----
    city_members: dict[tuple[str, str], list[int]] = {}
    for i, snap in enumerate(snapshots):
        city_members.setdefault((snap.state, snap.city), []).append(i)

    log.info("EPA UV: %d unique (state, city) for %d stores", len(city_members), len(snapshots))

    def _uv_worker(item):
        (state, city), members = item
        try:
            return members, _epa_uv_city(session, city, state)
        except Exception as exc:
            return members, exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for members, result in pool.map(_uv_worker, list(city_members.items())):
            for i in members:
                if isinstance(result, Exception):
                    snapshots[i].add_note(f"EPA: {result}")
                elif result is not None:
                    snapshots[i].uv_index = result

    return snapshots
