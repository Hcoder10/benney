# Sonnet cleanup brief — itinerary audit + repair

Sonnet subagents follow this brief to clean low-quality Haiku itineraries.

## Inputs

- A `families_part_<archetype>__b<n>.jsonl` file (10 family records).
- The `activities_bay.json` activity bank with `id`, `name`, `tags`,
  `open_hours`, `lat`/`lng`, `budget_tier`, `kid_ok`, `mobility_ok`.

## Slot-type rules (HARD — replace any violation)

| Slot index in day | Time window | Required tags (any-of) |
|---|---|---|
| 0 — early-morning | 7-9 AM | `cafe`, `coffee`, `bakery`, `breakfast` |
| 1 — breakfast     | 8-10 AM | `cafe`, `coffee`, `bakery`, `breakfast`, `brunch` |
| 2 — late-morning  | 10 AM-1 PM | `museum`, `tour`, `outdoor`, `hiking`, `campus`, `tech`, `art`, `science`, `history`, `gardens`, `walking`, `shopping`, `viewpoint`, `landmark`, `architecture` |
| 3 — lunch+afternoon | 12-5 PM | `restaurant`, `lunch`, `casual`, `park`, `scenic`, `beach`, `outdoor`, `hiking`, `tour`, `shopping`, `winery`, `wine`, `tasting` |
| 4 — evening       | 5-8 PM | `restaurant`, `dinner`, `fine-dining`, `scenic`, `sunset`, `wine`, `casual` |
| 5 — night         | 8-11 PM | `bar`, `cocktails`, `nightlife`, `lounge`, `dinner`, `fine-dining`, `speakeasy` |

The slot index in the day is `i % 6` for the 30-slot itinerary (0..29 →
Day 1 slot 0..5, Day 2 slot 0..5, ...).

## Open-hours rule (HARD)

The activity's `open_hours.fri` (representative weekday) must overlap with
the slot's time window. Wineries are usually `11:00-17:00` → fine for
slots 2-3, NOT for slots 0, 1, 4, 5. If a slot's pick is closed during
its window, replace it.

## Geographic continuity (SOFT — fix if egregious)

Same-day slots should generally stay within ~30 km of each other. Things
like SF → Big Basin → SF in adjacent slots are unrealistic. A whole day
in Big Basin is fine; bouncing daily is not.

## Repetition rule

The same activity may appear at most 3 times in a 30-slot itinerary
unless it's a cafe (cafes can be re-used as a "neighborhood coffee shop"
across multiple days, up to 5 times). Any activity appearing >5 times
should be replaced for diversity.

## Family-level constraints (already enforced by the original prompt — verify, don't fix)

- `kid_ok: false` activities only if `family.kid_ages == "none"`
- `mobility_ok: false` activities only if `family.mobility == "full"`
- `budget_tier` must be ≤ family's budget tier

## What to do per family

1. Walk the 30 slots in order.
2. For each slot, check: does the activity's tags overlap with the
   required-tags for `slot_idx % 6`? Are the open_hours compatible?
3. If violation: pick a replacement from the activity bank that:
   - matches the required tags for that slot
   - has open_hours covering the slot window
   - passes the family's kid/mobility/budget filters
   - is geographically close to the previous slot's pick if possible
   - hasn't already been used >3 times (or >5 for cafes) in this itinerary
4. Apply repetition cap.
5. Write the corrected family record.

## Output

Write the cleaned JSONL to `families_clean_<original_filename>` in the
same `hotel_agents/data/` directory. Each record has the SAME schema as
the input — just with corrected `itinerary` arrays. The `family` object
stays unchanged.

## Reporting

Summarize at the end: how many slot replacements made, how many
repetition fixes, any family where you couldn't find a suitable
replacement.
