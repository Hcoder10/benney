# Building Your Own Guest Persona

Benney is driven by a real cohort of 9,867 synthetic families clustered into 40 archetypes.
Picking a persona on the landing page seeds Benney's recommendations, voice context,
families network, and staff requests — all from a single localStorage key.

This guide walks you through adding a **41st archetype** and seeing it light up the demo.

---

## 1. The 15-keyword schema

Every persona is a JSON object with these fields:

| field               | values                                                                 |
| ------------------- | ---------------------------------------------------------------------- |
| `group_type`        | `solo`, `couple`, `family`, `friends`, `multi-gen`, `corporate`        |
| `adult_count`       | integer 1–8                                                            |
| `kid_ages`          | comma-separated ages (`"none"` if no kids)                             |
| `trip_purpose`      | `leisure`, `business`, `celebration`, `recovery`, `cultural`           |
| `budget_tier`       | `shoestring`, `mid`, `premium`, `luxury`, `ultra-luxury`               |
| `trip_length_days`  | integer 1–14                                                           |
| `pace`              | `relaxed`, `balanced`, `packed`                                        |
| `primary_interest`  | `food`, `art`, `nature`, `wine`, `gardens`, `tech`, `kids`, `wellness` |
| `secondary_interest`| same set as primary                                                    |
| `crowd_tolerance`   | `low`, `okay`, `high`                                                  |
| `energy`            | `low`, `medium`, `high`                                                |
| `local_interaction` | `none`, `mixed`, `deep`                                                |
| `mobility`          | `full`, `slow`, `wheelchair`                                           |
| `dietary`           | `none`, `vegetarian`, `vegan`, `kosher`, `halal`, `gluten-free`        |
| `language_comfort`  | `english-only`, `bilingual`, `spanish`, `mandarin`                     |

Every persona also carries a `must_include_guidance` string. This is plain English that
the seed-generator (Claude) reads when it builds the 30-slot itinerary; it nudges the
model toward activities that match the spirit of the archetype.

---

## 2. Add an entry to `v4_personas.json`

Open `hotel_agents/data/v4_personas.json`. Append a new key-value pair. Example:

```json
"surf_dad_dawn_patrol": {
  "family": {
    "group_type": "family",
    "adult_count": 2,
    "kid_ages": "10, 13",
    "trip_purpose": "leisure",
    "budget_tier": "mid",
    "trip_length_days": 4,
    "pace": "packed",
    "primary_interest": "nature",
    "secondary_interest": "food",
    "crowd_tolerance": "okay",
    "energy": "high",
    "local_interaction": "mixed",
    "mobility": "full",
    "dietary": "none",
    "language_comfort": "english-only"
  },
  "must_include_guidance": "Anchor at least one slot per day on the coast — Pillar Point, Mavericks, Half Moon Bay tide pools. Breakfast burritos beat formal dining. Bring the kids on the dawn paddle."
}
```

Save the file.

---

## 3. Regenerate seeds for just your new archetype

```bash
cd hotel_agents/scripts
python async_regen_v4_seeds.py --only surf_dad_dawn_patrol --tier mid
```

The script fans out to Claude with the schema and the `must_include_guidance`, validates
against the activity bank (8 region/time files in `hotel_agents/data/activities_*.json`),
and writes `families_clean_surf_dad_dawn_patrol__b0.jsonl` plus a fresh shard of
roughly 160 synthetic families with persona-specific 30-slot plans.

Tip: drop `--only` to regen all 40 archetypes (about 6 minutes on parallel workers).

---

## 4. Rebuild the cohort embedding store

```bash
python build_v4_cohort.py
```

This re-ingests every `families_clean_*.jsonl` shard, runs each family through the
`FamilyEncoder` (190k params, frozen), and writes `itineraries_cohort.npz` — the
KNN target the trip planner reads at request time. Your new archetype is now
candidate #9868+.

---

## 5. Restart the trip planner server

```bash
# from repo root
python -m hotel_agents.trip_planner.server
```

It binds `:7878`. The `/personas` route auto-picks up the new JSON entry.

---

## 6. Pick your persona on the landing page

Open `http://127.0.0.1:1010/?landing=1`. Your new persona appears in the grid with its
group/budget/interest summary. Tap the card.

`benney_persona_key` and `benney_family` are written to `localStorage`, then:

- `?trip=1` — TripPlannerLive uses your family vector for the KNN search, so the
  three probability bars and the 30-slot bank reflect *your* archetype, not the
  cohort average.
- `?home=1` — BenneyHomeLive's `/next-slot` fetch and `/voice` POST use your family.
  Ask Benney "show me the itinerary" and you'll see your slot-0 recommendations
  before you even open the planner.
- `?families=1` — your archetype highlights in the families network ring.
- `?staff=1` — every request Benney sends (housekeeping, room service, arrivals)
  is tagged with your persona's name and assigned a deterministic room number
  derived from the persona key, so the staff board shows multiple distinct guests
  if you cycle through personas.

---

## 7. Resetting

Open DevTools and run:

```js
localStorage.removeItem("benney_persona_key");
localStorage.removeItem("benney_family");
localStorage.removeItem("benney.staffBoard.cards.v1");
```

Then reload `/?landing=1` for a clean state.

---

## Anatomy reference

```
hotel_agents/
  data/
    v4_personas.json              <-- you edit this
    families_clean_*.jsonl        <-- regenerated per archetype
    itineraries_cohort.npz        <-- rebuilt from all shards
    activities_*.json             <-- 8 region/time activity files
  scripts/
    async_regen_v4_seeds.py       <-- step 3
    build_v4_cohort.py            <-- step 4
  trip_planner/
    server.py                     <-- /personas, /next-slot, /cohort-sample, /voice
src/sides/
  LandingPageLive.tsx             <-- persona picker UI
  TripPlannerLive.tsx             <-- reads benney_family from localStorage
  BenneyHomeLive.tsx              <-- reads benney_family for /voice + /next-slot
  StaffBoardLive.tsx              <-- shows guest persona on each card
src/services/
  staffRequests.ts                <-- attaches benney_persona_key to every card
```

That's it. One JSON entry, one regen, one rebuild, one server restart — the new
archetype is a first-class citizen across every surface.
