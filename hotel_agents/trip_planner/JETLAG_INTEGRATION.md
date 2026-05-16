# Jet-lag integration plan (Phase 9, post-demo)

The standalone `jetlag.py` predicts a continuous phase-offset curve in
hours over the days following arrival. This doc captures how to wire it
into the scheduler / aggregator / UI **without** retraining anything.

## What the model gives us

For a flight `(origin_iata, dest_iata, depart_ts, arrive_ts)`:

```python
res = JetLagModel("JFK", "CDG", "2026-06-01 18:00", "2026-06-02 07:30").simulate()
# res.days_post     [0, 1, 2, 3, 4, ...]
# res.offset_h      [-5.8, -4.6, -3.4, -2.3, -1.4, -0.7, ...]
# res.recovery_day  ~5.4
# res.direction     "eastward"
```

`offset_h[day]` is how many hours the traveler's circadian clock is
*behind* (negative) or *ahead of* (positive) destination local time at
local midnight of post-arrival day `day`.

For a 5-day Bay Area trip from JFK: day 1 of the trip = day 0 of recovery
(landed exactly at start). Offset trajectory is monotone toward 0.

## Integration surfaces

There are four places the offset signal should land. Listed in order of
implementation effort + impact:

### 1. Per-slot energy penalty in the scheduler  (smallest change, biggest demo win)

The scheduler already does `tag_boost` + `distance_penalty` over the
FitScorer logits. Add a third signed term: **subjective time-of-day vs
slot time-of-day**.

Conceptually: on day 1 of a +6h eastward shift, slot 0 (local 7 AM) is
really 1 AM in the traveler's body. High-energy activities at slot 0
should score lower; sleep-friendly low-energy ones should score higher.

```python
def jetlag_energy_penalty(
    activities: list[Activity],
    day_of_trip: int,
    slot_in_day: int,
    offset_h: float,        # from JetLagModel.simulate()
    family_energy: str,
) -> np.ndarray:
    """Logit subtraction per activity."""
    slot_mid_local = SLOT_TIMES[slot_in_day][0] + 1   # ~middle of window
    subjective_h = (slot_mid_local - offset_h) % 24    # body time at that wall clock

    # If body is between 22 and 6, push toward low-energy / cafes / hotels
    is_body_night = subjective_h >= 22 or subjective_h < 6
    is_body_morning = 6 <= subjective_h < 9

    penalty = np.zeros(len(activities), dtype=np.float32)
    for i, a in enumerate(activities):
        e = a["energy"]                    # "low" | "medium" | "high"
        if is_body_night and e == "high":
            penalty[i] = 2.0               # strongly avoid
        elif is_body_night and e == "medium":
            penalty[i] = 1.0
        elif is_body_morning and e == "high":
            penalty[i] = 0.6
    return penalty
```

Wire into `schedule()`:

```python
# inside the slot loop
if jetlag_offsets is not None:
    day_of_trip = slot // SLOTS_PER_DAY
    offset = jetlag_offsets[min(day_of_trip, len(jetlag_offsets) - 1)]
    pen = jetlag_energy_penalty(
        [activities[r] for r in pool.tolist()],
        day_of_trip, slot_in_day, offset, family["energy"],
    )
    logits = logits - torch.from_numpy(pen).to(device)
```

### 2. Time-of-day slot shift (more accurate, harder to demo)

Instead of penalizing high-energy at "wrong" body times, you can *shift
the slot's open-hours mask*. On day 1 of an eastward trip, slot 0 (local
7 AM = body 1 AM) is effectively cancelled — propose a later breakfast.

```python
def shifted_slot_times(slot_idx: int, offset_h: float) -> tuple[int, int]:
    lo, hi = SLOT_TIMES[slot_idx]
    # Negative offset = body behind dest = push slots later (sleep in)
    # Positive offset = body ahead = push slots earlier (early wake)
    shift = -offset_h  # opposite sign
    return (max(0, min(23, int(lo + shift))),
            max(1, min(24, int(hi + shift))))
```

For demo this is risky because the prism UI labels slots by local clock
time — shifting them visually conflicts with the user's wall clock. Phase
1 implementation should be the energy-penalty approach above.

### 3. Family-vec augmentation (smartest, most architectural)

Treat `offset_h` as a 16th input to the FamilyEncoder. The model already
takes 15 categorical tokens; add a continuous "jetlag day" token that
maps an offset in `[-12, 12]` h to a learned 32-D embedding (via small
MLP, not lookup since it's continuous). KNN then naturally clusters
guests not just by who they are but by where they are in their recovery.

Pros: end-to-end signal — encoder, FitScorer, aggregator all benefit.
Cons: requires retraining the encoder + FitScorer, and our cohort
doesn't have jetlag labels yet (would need to synthesize them — assume
N flights into Bay Area with realistic depart/arrive times, attach
offsets per day, regenerate itineraries).

Defer until post-hackathon.

### 4. Haiku reasoning gets the jetlag context

Pass `offset_h_today` into the `/reasoning` prompt:

```
Guest profile: {summary}
Time slot: {day} {time_of_day}
Activity: {name} — {description}
Jet-lag: offset {offset_h:+.1f}h, day {day_of_trip} of {recovery_day:.0f}-day recovery
Population stat: {pct}% of similar families chose this...

Explain why this fits THIS guest. If jet-lag offset is > 2h, mention how
this activity respects their body clock. Otherwise ignore jet-lag.
```

Haiku already handles the conditional ("if jet-lag offset > 2h...") well.
Two-sentence cap stays.

## API + UI changes

### Family payload — three new optional fields

```typescript
type FamilyPayload = {
  // ...existing 15 keywords...
  origin_iata?: string;           // "JFK"
  departure_ts?: string;          // ISO 8601 with timezone
  arrival_ts?: string;            // ISO 8601 with destination tz
}
```

Server-side, `JetLagModel(...)` runs once when the family hits the API,
and the resulting offset array is cached in `_state[guest_session]`. It's
threaded through `next_slot_probabilities()` to the scheduler.

### Prism display — left monitor adds a jet-lag chip

Below the 15-kw profile card:

```
┌───────────────────────────────────┐
│  Jet lag                          │
│  JFK → SFO                  -3h   │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━       │
│  Day 1: -3.0h  ████████░░         │
│  Day 2: -1.8h  █████░░░░░         │
│  Day 3: -0.7h  ██░░░░░░░░         │
│  Day 4:  0.0h  ░░░░░░░░░░ ✓       │
│  Day 5:  0.0h  ░░░░░░░░░░ ✓       │
└───────────────────────────────────┘
```

When the right-monitor itinerary loads, slots on jet-laggy days carry a
small "🌙 body clock 3 AM" annotation underneath the activity name.

## Dependencies

```bash
pip install circadian airportsdata
```

Both pure-Python, ~5 MB total. Add to `hotel_agents/requirements.txt`.

## Acceptance test

Same 5-day chain test we use elsewhere, but with a JFK→SFO flight
attached. Expectations:

| Day 1 (body -3h) | Day 5 (body 0h) |
|---|---|
| Slot 0 (7 AM): cafe with LOW energy (body says 4 AM) | Slot 0: any cafe |
| Slot 5 (9 PM): bar disabled OR low-energy lounge (body says 6 PM) | Slot 5: any bar |
| Total day-1 high-energy activities ≤ 1 | Total day-5 high-energy ≤ usual |

Verify via a manual run of `full_run.py` with `--origin JFK --departure …`.

## Order of work (post-current-demo)

1. Add `circadian`, `airportsdata` to requirements.
2. Verify `python -m hotel_agents.trip_planner.jetlag` runs the three CLI
   demos cleanly.
3. Add the optional flight fields to the `/next-slot` payload schema +
   parser.
4. Compute the offset array once per query, thread through scheduler.
5. Add the `jetlag_energy_penalty` term to `schedule()`.
6. Update Haiku reasoning prompt to optionally mention jet lag.
7. Wire the left-monitor chip on the prism.

Estimated effort: ~6 hours, all in `trip_planner/` (no encoder retraining).
