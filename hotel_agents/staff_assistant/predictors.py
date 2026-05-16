"""Heuristic predictors. No ML — just clock arithmetic + ETA lookups.

Inputs are conservative; outputs are deliberately rounded to the minute
because front-desk staff don't need seconds of precision.

Time-of-day model
-----------------
We borrow SLOT_TIMES from the trip planner: 6 slots/day, each an (open, close)
local-clock window. Slot k of day d ends at:

  slot_end_local = SLOT_TIMES[k % 6][1]
  day_start      = trip_start_ts + d * 86400
  slot_end_ts    = day_start + slot_end_local * 3600

Once we know which slot the guest is on (their current_slot_idx), the next-
return time is:

  return_ts = slot_end_ts + travel_back_seconds

Cleaning window
---------------
  window = return_ts - now - delivery_buffer

If window < 15 min → "skip" (not enough time)
If window < 35 min → "quick-turn" (linens + bathroom only)
Else              → "go-now" (full service)
If in_room        → "in-room" (do not enter)

Hot food delivery
-----------------
Most hot mains are best within 8 minutes of plating. We back-schedule from
the guest's return:

  deliver_ts   = return_ts - 2 min   (arrive just BEFORE guest, kept warm in passage)
  cook_start   = deliver_ts - cook_minutes - 3 min (3 min for plate→cart→ride)

Arrival welcome
---------------
For not-yet-arrived guests, arrival_setup_ts = arrival_eta_ts - 10 min, so the
fruit + welcome card are in place when they swipe in.
"""

from __future__ import annotations

from typing import Callable

from ..trip_planner.slot_constraints import SLOT_TIMES, haversine_km
from .schema import Prediction, RoomState

# Hotel coordinates (Benney Prism demo property: Sand Hill Road, Menlo Park)
DEFAULT_HOTEL_LAT = 37.4243
DEFAULT_HOTEL_LNG = -122.2046

SLOTS_PER_DAY = 6
DELIVERY_BUFFER_MIN = 8          # bring food up just before guest arrives
PASSAGE_MIN = 3                  # plate → cart → corridor → door
CLEANING_BUFFER_MIN = 10         # how late housekeeping can finish before guest

# ETA function signature: returns travel time in seconds.
ETAFn = Callable[[float, float, float, float], float]


def haversine_eta(from_lat: float, from_lng: float,
                  to_lat: float, to_lng: float,
                  avg_kmh: float = 32.0) -> float:
    """Distance / avg_kmh — used when Google Maps key isn't available.

    32 km/h is a reasonable Bay Area surface-street average including stops.
    Highways average higher; we keep one number for simplicity.
    """
    km = haversine_km(from_lat, from_lng, to_lat, to_lng)
    return km / avg_kmh * 3600.0


def _slot_end_ts(slot_idx: int, trip_start_ts: float) -> float:
    """Local wall-clock end of slot `slot_idx` as a unix timestamp."""
    day = slot_idx // SLOTS_PER_DAY
    slot_in_day = slot_idx % SLOTS_PER_DAY
    _, end_hour = SLOT_TIMES[slot_in_day]
    day_start = trip_start_ts + day * 86400
    return day_start + end_hour * 3600


def predict(
    state: RoomState,
    now_ts: float,
    *,
    trip_start_ts: float | None,
    next_activity_loc: tuple[float, float] | None,
    eta_fn: ETAFn = haversine_eta,
    hotel_lat: float = DEFAULT_HOTEL_LAT,
    hotel_lng: float = DEFAULT_HOTEL_LNG,
) -> Prediction:
    raw: dict = {}

    # ─── expected return ───────────────────────────────────────────────────
    expected_return_ts: float | None = None
    if state.expected_return_ts:
        expected_return_ts = state.expected_return_ts
        raw["return_source"] = "event"
    elif state.current_slot_idx is not None and trip_start_ts is not None:
        slot_end = _slot_end_ts(state.current_slot_idx, trip_start_ts)
        if next_activity_loc:
            travel_back = eta_fn(next_activity_loc[0], next_activity_loc[1],
                                 hotel_lat, hotel_lng)
        else:
            travel_back = 15 * 60   # blind 15 min fallback
        expected_return_ts = slot_end + travel_back
        raw["return_source"] = "itinerary"
        raw["slot_end_ts"] = slot_end
        raw["travel_back_min"] = round(travel_back / 60)

    # ─── cleaning window ────────────────────────────────────────────────────
    cleaning_window_min: int | None = None
    cleaning_rec = "in-room"
    if state.in_room:
        cleaning_rec = "in-room"
    elif expected_return_ts is None:
        cleaning_rec = "skip"  # no return time → don't risk it
    else:
        window_s = expected_return_ts - now_ts - CLEANING_BUFFER_MIN * 60
        cleaning_window_min = max(0, int(window_s // 60))
        if cleaning_window_min < 15:
            cleaning_rec = "skip"
        elif cleaning_window_min < 35:
            cleaning_rec = "quick-turn"
        else:
            cleaning_rec = "go-now"

    # ─── hot food timing ────────────────────────────────────────────────────
    food_cook_start_ts: float | None = None
    food_deliver_ts: float | None = None
    if state.pending_food_order and expected_return_ts is not None:
        deliver_ts = expected_return_ts - DELIVERY_BUFFER_MIN * 60
        cook_minutes = int(state.pending_food_order.get("cook_minutes", 25))
        cook_start = deliver_ts - (cook_minutes + PASSAGE_MIN) * 60
        # Override the heuristic if order specified an explicit target_ts
        explicit = state.pending_food_order.get("target_ts")
        if explicit:
            deliver_ts = float(explicit)
            cook_start = deliver_ts - (cook_minutes + PASSAGE_MIN) * 60
        food_cook_start_ts = cook_start
        food_deliver_ts = deliver_ts
        raw["food_cook_minutes"] = cook_minutes

    # ─── arrival welcome ────────────────────────────────────────────────────
    arrival_setup_ts: float | None = None
    if state.arrival_eta_ts and not state.in_room:
        # Setup must be in place 10 min before guest swipes in.
        arrival_setup_ts = state.arrival_eta_ts - 10 * 60

    return Prediction(
        room=state.room,
        now_ts=now_ts,
        in_room=state.in_room,
        expected_return_ts=expected_return_ts,
        cleaning_window_min=cleaning_window_min,
        cleaning_recommendation=cleaning_rec,  # type: ignore[arg-type]
        food_cook_start_ts=food_cook_start_ts,
        food_deliver_ts=food_deliver_ts,
        arrival_eta_ts=state.arrival_eta_ts,
        arrival_setup_ts=arrival_setup_ts,
        raw=raw,
    )
