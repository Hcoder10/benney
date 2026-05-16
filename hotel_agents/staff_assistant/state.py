"""In-memory state store for the staff assistant.

Reset on restart — this is a demo. A real deployment would back this with
Redis or sqlite + a write-ahead event log.

Events mutate RoomState through a tiny finite state machine:

  door_open  → in_room = True   (cleaning blocked)
  door_close → in_room = ?       — ambiguous; we treat the most recent
                                   open-then-close pair as "guest left",
                                   so a close after an open flips in_room=False
                                   only if door has been closed for >30s with
                                   no re-open. For the demo we treat door_close
                                   as immediate "guest left."

A real system needs an in-room presence sensor (PIR, weight on bed, badged
staff vs unbadged guest). For 4-hour scope, door events are the signal.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable

from .schema import Event, RoomState


class StateStore:
    """Thread-safe per-room state. One instance shared by the FastAPI app."""

    def __init__(self) -> None:
        self._rooms: dict[str, RoomState] = {}
        self._lock = threading.Lock()

    # ---- read --------------------------------------------------------- #
    def get(self, room: str) -> RoomState:
        with self._lock:
            return self._rooms.setdefault(room, RoomState(room=room))

    def all(self) -> list[RoomState]:
        with self._lock:
            return list(self._rooms.values())

    # ---- write -------------------------------------------------------- #
    def apply(self, event: Event) -> RoomState:
        with self._lock:
            r = self._rooms.setdefault(event["room"], RoomState(room=event["room"]))
            r.events.append(event)
            t = event["type"]
            ts = event["ts"]
            payload = event.get("payload", {}) or {}

            if t == "checkin":
                r.family_id = payload.get("family_id")
                r.in_room = True
                r.last_door_ts = ts
                r.last_door_kind = "open"
                r.arrival_flight = payload.get("flight")
                r.current_slot_idx = int(payload.get("current_slot_idx", 0))
            elif t == "checkout":
                r.in_room = False
                r.current_slot_idx = None
                r.next_planned_activity = None
                r.expected_return_ts = None
            elif t == "door_open":
                r.in_room = True
                r.last_door_ts = ts
                r.last_door_kind = "open"
            elif t == "door_close":
                # We treat a close as "guest left the room" for demo purposes
                # unless the next door event arrives within 30 seconds (we
                # detect this lazily in the predictor by reading last_door_kind).
                r.in_room = False
                r.last_door_ts = ts
                r.last_door_kind = "close"
            elif t == "food_order":
                r.pending_food_order = {
                    "items": payload.get("items", []),
                    "cook_minutes": payload.get("cook_minutes", 25),
                    "target_ts": payload.get("target_ts"),
                    "ordered_ts": ts,
                }
            elif t == "flight_update":
                r.arrival_eta_ts = payload.get("eta_ts")
                if payload.get("flight"):
                    r.arrival_flight = payload["flight"]
            elif t == "override":
                # Direct field assignment for demo seeding / manual ops.
                for k, v in payload.items():
                    if hasattr(r, k):
                        setattr(r, k, v)
            return r

    # ---- bulk seed (for demo) ----------------------------------------- #
    def seed(self, rooms: Iterable[RoomState]) -> None:
        with self._lock:
            for r in rooms:
                self._rooms[r.room] = r


# module-level singleton used by the FastAPI app
STORE = StateStore()
