"""External API clients: AviationStack (flights) + Google Maps (traffic).

Both fall back to deterministic mocks if their respective env var is unset,
so the demo never breaks for missing keys. The mocks are realistic enough
to be demo-grade but not training-grade.

Env vars:
  AVIATIONSTACK_API_KEY  — http://api.aviationstack.com (free 100 req/mo)
  GOOGLE_MAPS_API_KEY    — Directions API must be enabled, billing on

Caching: in-process LRU, 5 min TTL — enough to demo without hammering quotas.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass

import httpx

_AVIATIONSTACK_KEY = os.environ.get("AVIATIONSTACK_API_KEY", "").strip()
_GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()

# (key, ts, value) cache
_FLIGHT_CACHE: dict[str, tuple[float, "FlightInfo"]] = {}
_TRAFFIC_CACHE: dict[tuple, tuple[float, float]] = {}
_TTL_S = 300.0


@dataclass
class FlightInfo:
    flight: str                   # "UA742"
    status: str                   # "scheduled" | "active" | "landed" | "cancelled"
    scheduled_arr_ts: float       # original
    estimated_arr_ts: float       # with delay
    delay_min: int
    arrival_airport: str
    source: str                   # "aviationstack" | "mock"


# ────────────────────────────────────────────────────────────────────────────
# Flights
# ────────────────────────────────────────────────────────────────────────────

async def get_flight(flight_iata: str) -> FlightInfo:
    """Look up a flight's live status + ETA."""
    now = time.time()
    cached = _FLIGHT_CACHE.get(flight_iata)
    if cached and now - cached[0] < _TTL_S:
        return cached[1]

    info = (await _fetch_aviationstack(flight_iata)) if _AVIATIONSTACK_KEY \
        else _mock_flight(flight_iata)
    _FLIGHT_CACHE[flight_iata] = (now, info)
    return info


async def _fetch_aviationstack(flight_iata: str) -> FlightInfo:
    url = "http://api.aviationstack.com/v1/flights"
    params = {"access_key": _AVIATIONSTACK_KEY, "flight_iata": flight_iata, "limit": 1}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    rows = data.get("data") or []
    if not rows:
        return _mock_flight(flight_iata, status="not_found")
    row = rows[0]
    arr = row.get("arrival") or {}
    sched = arr.get("scheduled") or arr.get("estimated")
    est = arr.get("estimated") or arr.get("actual") or sched
    return FlightInfo(
        flight=flight_iata,
        status=str(row.get("flight_status") or "scheduled"),
        scheduled_arr_ts=_iso_to_ts(sched),
        estimated_arr_ts=_iso_to_ts(est),
        delay_min=int(arr.get("delay") or 0),
        arrival_airport=str(arr.get("iata") or "?"),
        source="aviationstack",
    )


def _iso_to_ts(s: str | None) -> float:
    if not s:
        return time.time()
    from datetime import datetime
    # API returns "2026-05-16T15:42:00+00:00"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def _mock_flight(flight_iata: str, *, status: str = "active") -> FlightInfo:
    """Deterministic mock. Hash of the flight code drives the delay so calls
    are repeatable across the demo."""
    h = abs(hash(flight_iata)) % 1000
    rng = random.Random(h)
    sched = time.time() + rng.randint(1, 120) * 60
    delay = rng.choice([0, 0, 0, 8, 14, 22, 35, 47])
    est = sched + delay * 60
    return FlightInfo(
        flight=flight_iata,
        status=status,
        scheduled_arr_ts=sched,
        estimated_arr_ts=est,
        delay_min=delay,
        arrival_airport="SFO",
        source="mock",
    )


# ────────────────────────────────────────────────────────────────────────────
# Traffic / ETA
# ────────────────────────────────────────────────────────────────────────────

async def get_eta_seconds(from_lat: float, from_lng: float,
                          to_lat: float, to_lng: float) -> float:
    """Driving ETA in seconds, with live traffic if Google key is set."""
    now = time.time()
    key = (round(from_lat, 4), round(from_lng, 4),
           round(to_lat, 4), round(to_lng, 4))
    cached = _TRAFFIC_CACHE.get(key)
    if cached and now - cached[0] < _TTL_S:
        return cached[1]

    if _GOOGLE_KEY:
        eta = await _fetch_google_eta(from_lat, from_lng, to_lat, to_lng)
    else:
        eta = _mock_eta(from_lat, from_lng, to_lat, to_lng)
    _TRAFFIC_CACHE[key] = (now, eta)
    return eta


async def _fetch_google_eta(from_lat: float, from_lng: float,
                            to_lat: float, to_lng: float) -> float:
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": f"{from_lat},{from_lng}",
        "destination": f"{to_lat},{to_lng}",
        "key": _GOOGLE_KEY,
        "departure_time": "now",
        "traffic_model": "best_guess",
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    routes = data.get("routes") or []
    if not routes:
        return _mock_eta(from_lat, from_lng, to_lat, to_lng)
    leg = routes[0]["legs"][0]
    # Prefer duration_in_traffic when available; fall back to plain duration.
    dur = leg.get("duration_in_traffic") or leg.get("duration") or {}
    return float(dur.get("value") or 0.0) or _mock_eta(
        from_lat, from_lng, to_lat, to_lng,
    )


def _mock_eta(from_lat: float, from_lng: float,
              to_lat: float, to_lng: float) -> float:
    """Haversine / 32 km/h + a deterministic ±20% traffic wobble."""
    from ..trip_planner.slot_constraints import haversine_km
    km = haversine_km(from_lat, from_lng, to_lat, to_lng)
    base = km / 32.0 * 3600.0
    # Hash the coords for a stable wobble so demo runs match
    h = abs(hash((round(from_lat, 3), round(from_lng, 3),
                  round(to_lat, 3), round(to_lng, 3)))) % 100
    wobble = 1.0 + (h - 50) / 250.0     # -0.2 .. +0.2
    return base * wobble


# ────────────────────────────────────────────────────────────────────────────
# Status helpers
# ────────────────────────────────────────────────────────────────────────────

def status_summary() -> dict:
    return {
        "aviationstack": "live" if _AVIATIONSTACK_KEY else "mock",
        "google_maps": "live" if _GOOGLE_KEY else "mock",
        "flight_cache_size": len(_FLIGHT_CACHE),
        "traffic_cache_size": len(_TRAFFIC_CACHE),
    }
