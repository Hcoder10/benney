"""Demo seed: 6 rooms in different states so the staff board has variety.

Usage:
  python -m hotel_agents.staff_assistant.seed_demo

Picks 6 real families from families.jsonl, attaches them to rooms, and
fires the events that put each room in its desired state.

Scenarios (each renders a distinct card type):

  207 - guest OUT, due back ~25 min       -> quick-turn housekeeping
  308 - guest IN room                     -> housekeeping hold
  412 - guest OUT, due back ~90 min       -> full-turn housekeeping
  519 - guest OUT, has dinner order       -> room-service + housekeeping
  605 - ARRIVING (flight inbound)         -> arrival welcome card
  717 - guest OUT, returns in 8 min       -> SKIP housekeeping
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.storage import FAMILIES_PATH, read_jsonl  # noqa: E402

API = "http://127.0.0.1:7879"


def send(room: str, kind: str, *, payload: dict | None = None,
         ts: float | None = None) -> None:
    ev = {"room": room, "type": kind, "ts": ts, "payload": payload or {}}
    r = httpx.post(f"{API}/event", json=ev, timeout=5.0)
    r.raise_for_status()


def main() -> None:
    families = read_jsonl(FAMILIES_PATH)
    if len(families) < 6:
        sys.exit("need >= 6 families in families.jsonl")

    now = time.time()
    # Trip anchor: 2 days ago, 07:00 local
    trip_start = now - 2 * 86400 - (time.localtime().tm_hour - 7) * 3600

    rooms_payload = [
        # (room, family_idx, in_room, return_in_min, food_order, flight)
        ("207", 0, False, 25, None, None),
        ("308", 1, True,  None, None, None),
        ("412", 2, False, 90, None, None),
        ("519", 3, False, 35, {"items": ["sous-vide salmon", "garden salad",
                                          "key lime pie"],
                                "cook_minutes": 28}, None),
        ("605", None, False, None, None, ("UA742", now + 75 * 60)),
        ("717", 5, False, 8, None, None),
    ]

    for (room, fam_idx, in_room, return_in_min, food, flight) in rooms_payload:
        if fam_idx is not None:
            fam = families[fam_idx]
            send(room, "checkin", payload={"family_id": fam["id"]},
                 ts=trip_start)
            if in_room:
                send(room, "door_open", ts=now - 10 * 60)
            else:
                send(room, "door_close", ts=now - 30 * 60)
        if return_in_min is not None:
            send(room, "override",
                 payload={"expected_return_ts": now + return_in_min * 60})
        if food:
            send(room, "food_order",
                 payload={"items": food["items"],
                          "cook_minutes": food["cook_minutes"]},
                 ts=now - 5 * 60)
        if flight:
            iata, eta_ts = flight
            send(room, "flight_update",
                 payload={"flight": iata, "eta_ts": eta_ts})

    print("seeded 6 rooms.")
    print("UI:   http://127.0.0.1:5173/?staff=1")
    print("API:  curl http://127.0.0.1:7879/staff-feed")


if __name__ == "__main__":
    main()
