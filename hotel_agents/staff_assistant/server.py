"""FastAPI server for the staff assistant.

Endpoints:
  GET  /health                       liveness + API key status
  POST /event                        ingest a door/checkin/checkout/order/flight event
  GET  /rooms                        full state for every room
  GET  /staff-feed                   list[ActionCard] across all rooms (UI polls this)
  POST /seed                         load the demo seed (rooms + scripted event tape)

Run: uvicorn hotel_agents.staff_assistant.server:app --host 127.0.0.1 --port 7879
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import api_clients
from .narrator import narrate
from .predictors import (
    DEFAULT_HOTEL_LAT,
    DEFAULT_HOTEL_LNG,
    haversine_eta,
    predict,
)
from .schema import Event, RoomState
from .state import STORE


app = FastAPI(title="Benney Staff Assistant")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── lazy globals: activity bank + family summaries ──────────────────────────
_BANK: dict[str, dict] = {}        # activity_id → activity dict
_FAMILIES: dict[str, dict] = {}    # family_id → 15-kw dict
_ITINS: dict[str, list[str]] = {}  # family_id → 30 activity_ids


def _load_static() -> None:
    if _BANK:
        return
    from ..shared.storage import (ACTIVITIES_PATH, FAMILIES_PATH,
                                  ITINERARIES_PATH, read_json, read_jsonl)
    for a in read_json(ACTIVITIES_PATH):
        _BANK[a["id"]] = a
    for fam in read_jsonl(FAMILIES_PATH):
        _FAMILIES[fam["id"]] = fam
    for it in read_jsonl(ITINERARIES_PATH):
        _ITINS[it["family_id"]] = it["activity_ids"]


def _family_summary(fid: str | None) -> str:
    if not fid or fid not in _FAMILIES:
        return "guest"
    f = _FAMILIES[fid]
    bits = []
    if f.get("group_type"): bits.append(f["group_type"])
    if f.get("kid_ages") and f["kid_ages"] != "none":
        bits.append(f"with kids {f['kid_ages']}")
    if f.get("primary_interest"): bits.append(f["primary_interest"])
    if f.get("dietary") and f["dietary"] != "none": bits.append(f"({f['dietary']})")
    return " ".join(bits) or "guest"


def _next_activity_for(state: RoomState) -> tuple[float, float] | None:
    if not state.family_id or state.current_slot_idx is None:
        return None
    plan = _ITINS.get(state.family_id) or []
    if state.current_slot_idx >= len(plan):
        return None
    aid = plan[state.current_slot_idx]
    a = _BANK.get(aid)
    if not a:
        return None
    return float(a["lat"]), float(a["lng"])


# ────────────────────────────────────────────────────────────────────────────
# Routes
# ────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "ts": time.time(),
        "apis": api_clients.status_summary(),
        "rooms_tracked": len(STORE.all()),
    }


class EventIn(BaseModel):
    room: str
    type: str
    ts: float | None = None
    payload: dict = Field(default_factory=dict)


@app.post("/event")
def post_event(ev: EventIn) -> dict:
    _load_static()
    event: Event = {
        "room": ev.room,
        "type": ev.type,                # type: ignore[typeddict-item]
        "ts": ev.ts or time.time(),
        "payload": ev.payload,
    }
    state = STORE.apply(event)
    return {"ok": True, "room": ev.room, "in_room": state.in_room}


class LockSlotIn(BaseModel):
    room: str = "412"                       # default demo room
    activity_id: str
    slot_idx: int                            # 0..29 in the 5-day trip
    family_id: str | None = None


@app.post("/lock-slot")
def lock_slot(req: LockSlotIn) -> dict:
    """Trip-planner-side click → staff-side update.

    Looks up the chosen activity, computes ETA back to the hotel via
    haversine + 32 km/h average, and writes an `override` event so the
    staff board sees the next activity + expected return time.
    """
    _load_static()
    from ..trip_planner.slot_constraints import SLOT_TIMES
    from .predictors import (DEFAULT_HOTEL_LAT, DEFAULT_HOTEL_LNG, haversine_eta)

    act = _BANK.get(req.activity_id)
    if not act:
        raise HTTPException(404, f"unknown activity {req.activity_id}")

    now = time.time()
    slot_in_day = req.slot_idx % 6
    day_of_trip = req.slot_idx // 6
    _, slot_end_hour = SLOT_TIMES[slot_in_day]

    # Anchor "today" to local midnight, then offset by trip-day.
    today_midnight = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    slot_end_ts = today_midnight + day_of_trip * 86400 + slot_end_hour * 3600
    travel_back_s = haversine_eta(act["lat"], act["lng"],
                                   DEFAULT_HOTEL_LAT, DEFAULT_HOTEL_LNG)
    expected_return_ts = slot_end_ts + travel_back_s

    payload: dict = {
        "next_planned_activity": req.activity_id,
        "expected_return_ts": expected_return_ts,
        "current_slot_idx": req.slot_idx,
        "in_room": False,
    }
    if req.family_id:
        payload["family_id"] = req.family_id

    STORE.apply({
        "room": req.room, "type": "override", "ts": now, "payload": payload,
    })
    return {
        "ok": True,
        "room": req.room,
        "activity": act.get("name", req.activity_id),
        "activity_id": req.activity_id,
        "slot_idx": req.slot_idx,
        "expected_return_ts": expected_return_ts,
        "expected_return_local": time.strftime("%I:%M %p", time.localtime(expected_return_ts)),
        "travel_back_minutes": round(travel_back_s / 60),
    }


@app.get("/rooms")
def rooms() -> list[dict]:
    _load_static()
    out = []
    for r in STORE.all():
        out.append({
            "room": r.room,
            "family_id": r.family_id,
            "in_room": r.in_room,
            "current_slot_idx": r.current_slot_idx,
            "expected_return_ts": r.expected_return_ts,
            "pending_food_order": r.pending_food_order,
            "arrival_flight": r.arrival_flight,
            "arrival_eta_ts": r.arrival_eta_ts,
        })
    return out


@app.get("/staff-feed")
async def staff_feed() -> list[dict]:
    _load_static()
    now = time.time()
    out: list[dict] = []

    states = STORE.all()
    # Refresh arrival ETAs from external APIs in parallel (cached)
    flight_tasks = {}
    for s in states:
        if s.arrival_flight and not s.in_room:
            flight_tasks[s.room] = asyncio.create_task(
                api_clients.get_flight(s.arrival_flight)
            )
    flight_results = {}
    for room, t in flight_tasks.items():
        try:
            fi = await t
            flight_results[room] = fi
            # Patch arrival ETA from flight + airport→hotel ETA
            arr_eta = fi.estimated_arr_ts
            airport_eta = await api_clients.get_eta_seconds(
                37.6213, -122.3790,                # SFO
                DEFAULT_HOTEL_LAT, DEFAULT_HOTEL_LNG,
            )
            STORE.get(room).arrival_eta_ts = arr_eta + airport_eta
        except Exception as e:
            print(f"flight lookup failed for {room}: {e}")

    # Build predictions + narrate
    for s in states:
        next_loc = _next_activity_for(s)
        # Optionally refine return travel via Google Maps if we have coords
        eta_fn = haversine_eta
        if next_loc:
            async def _real_eta(flat, flng, tlat, tlng):
                return await api_clients.get_eta_seconds(flat, flng, tlat, tlng)
            # Note: predictor is sync; pre-compute travel back here and pass
            # via raw if we want. For 4hr scope keep haversine.
        trip_start = (s.events[0]["ts"] if s.events else now) if s.family_id else None
        pred = predict(
            s, now, trip_start_ts=trip_start, next_activity_loc=next_loc,
        )
        fam_summary = _family_summary(s.family_id)
        flight_info = None
        fi = flight_results.get(s.room)
        if fi:
            flight_info = {
                "flight": fi.flight, "status": fi.status,
                "delay_min": fi.delay_min,
                "estimated_arr_ts": fi.estimated_arr_ts,
                "source": fi.source,
            }
        cards = await narrate(s, pred, fam_summary, flight_info)
        for c in cards:
            d = asdict(c)
            out.append(d)

    # Sort: now > soon > info, then by deadline asc
    rank = {"now": 0, "soon": 1, "info": 2}
    out.sort(key=lambda c: (rank.get(c["urgency"], 9),
                            c.get("deadline_ts") or 1e18))
    return out
