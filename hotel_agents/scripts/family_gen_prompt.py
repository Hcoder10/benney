"""Generate the prompt + system message a Haiku subagent receives when
producing one batch of synthetic families + their 5-day itineraries.

The prompt enforces a CLOSED vocabulary per family field — Haiku must
pick from the preset list for each of the 15 keywords, so downstream
training has clean, learnable signal. (Earlier waves let Haiku
free-style and drifted into 'wellness'/'vegetarian'/'art', which we
had to alias away in the encoder.)

Activity IDs are also closed — Haiku may only choose from the curated
activity bank.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
DATA = ROOT / "data"

from hotel_agents.shared.encoder import FIELD_VOCABS  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Per-batch context
# ─────────────────────────────────────────────────────────────────────────────

def load_activity_ids() -> list[dict]:
    """Return a compact list of activities for the prompt: id, name, tags,
    budget, energy, indoor_outdoor, kid_ok, mobility_ok. We deliberately
    DROP description, lat/lng, address, hours — Haiku doesn't need them to
    choose IDs (the scheduler enforces constraints later).
    """
    path = DATA / "activities_bay.json"
    if not path.exists():
        raise FileNotFoundError(f"missing {path} — run merge_activities.py first")
    activities = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "id": a["id"],
            "name": a["name"],
            "tags": a["tags"],
            "budget": a["budget_tier"],
            "energy": a["energy"],
            "io": a["indoor_outdoor"],
            "kid_ok": a["kid_ok"],
            "mobility_ok": a["mobility_ok"],
        }
        for a in activities
    ]


def load_archetypes() -> list[dict]:
    path = DATA / "archetypes.json"
    if not path.exists():
        raise FileNotFoundError(f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Prompt template
# ─────────────────────────────────────────────────────────────────────────────

def _build_vocab_block() -> str:
    """Render every field's closed vocabulary as a strict picker list.

    The point is to make hallucination structurally impossible: each field
    is presented as a numbered choice list. Haiku must echo back ONE of
    these strings verbatim.
    """
    lines = ["## Closed keyword vocabulary — pick ONE per field, verbatim",
             "(no synonyms, no new values — these are the only legal strings)\n"]
    for field, vocab in FIELD_VOCABS.items():
        choices = " · ".join(f'"{v}"' for v in vocab)
        lines.append(f"- **{field}**: {choices}")
    return "\n".join(lines)


_SCHEMA_BLOCK = """\
Each family record is:
```json
{
  "id": "fam_<archetype_id>_<batch_index>",
  "family": { "group_type": "...", "adult_count": N, ... 15 fields ... },
  "itinerary": ["activity_id_slot0", ..., "activity_id_slot29"]
}
```

The `itinerary` array has EXACTLY 30 elements — 5 days × 6 slots
(early-morning, breakfast, late-morning, lunch-afternoon, evening, night).
Use only activity IDs from the provided activity list. Repeats are allowed
when natural (e.g. same hotel cafe two breakfasts apart).
"""


def build_family_batch_prompt(
    archetype: dict,
    activities: list[dict],
    batch_size: int,
    batch_label: str,
    seed: int,
) -> str:
    """Render the full prompt for one family-generation batch."""
    rng = random.Random(seed)
    # Show a sampled subset of activities if the bank is huge, to fit context
    sample_size = min(len(activities), 220)
    bank = (
        random.Random(seed).sample(activities, sample_size)
        if len(activities) > sample_size
        else activities
    )
    bank_lines = [
        f'  - {a["id"]}: {a["name"]} '
        f'[{a["budget"]}/{a["energy"]}/{a["io"]}'
        f'{",kids-no" if not a["kid_ok"] else ""}'
        f'{",mob-no" if not a["mobility_ok"] else ""}] '
        f'tags={",".join(a["tags"])}'
        for a in bank
    ]

    vocab_block = _build_vocab_block()

    return f"""Generate {batch_size} diverse synthetic families anchored on the archetype below, each with a complete 5-day Bay Area itinerary. Write the JSON array to `C:\\Users\\sarta\\rosea\\hotel_agents\\data\\families_part_{batch_label}.jsonl` — **one record per line, JSONL not JSON array**.

## CRITICAL RULES (enforced downstream — violations cause the record to be dropped)
1. Every value in the `family` object MUST be picked verbatim from the closed
   vocabulary below. No synonyms, no new values. e.g. write `"veg"`, NEVER
   `"vegetarian"`; write `"nature"`, NEVER `"wellness"`.
2. Every `activity_id` in the itinerary MUST exist exactly in the activity
   bank below. Do NOT invent placeholders like `lunch_casual` or
   `final_dinner`. If you want a casual lunch, pick a real cafe ID.
3. Exactly 30 itinerary entries per family — 5 days × 6 slots in order
   (Day 1 slot 0..5, Day 2 slot 0..5, ..., Day 5 slot 0..5).

## Anchor archetype (the seed — you'll produce variations of this)
```json
{json.dumps(archetype, indent=2)}
```

Each family should be a **variation** of this archetype, not a clone:
- vary `adult_count` (1-4 within reason)
- vary `kid_ages` bucket when applicable
- vary `secondary_interest` (pick a different vocab value than the anchor's)
- vary `pace`, `energy`, `crowd_tolerance` (one step from anchor)
- occasionally vary `dietary` (e.g. veg, gf, vegan even from a none anchor)

Keep stable when sensible: `group_type`, `primary_interest`, `budget_tier`,
`mobility`, `language_comfort`.

{vocab_block}

## Activity bank — pick activity_id values ONLY from this list
{chr(10).join(bank_lines)}

## Itinerary rules
1. Realistic chain — breakfast slot is a cafe/restaurant, evening slot is
   a dinner spot, late-morning / lunch-afternoon are flexible.
2. Don't put a winery + fine-dining + late bar in a slot the family can't
   afford (luxury can; shoestring can't).
3. Geographic continuity within a day — if morning is in SF, don't put
   afternoon in Big Basin.
4. Family with `kid_ages != "none"`: avoid activities with `kids-no`.
5. `mobility` in (limited, wheelchair): skip `mob-no` activities.
6. The cohort tallies what families "like this" picked — produce
   **diverse but coherent** trips so the population stats are meaningful,
   not {batch_size} identical schedules.

Batch label: `{batch_label}`, seed: {seed}. Use to vary which days lean
food vs. nature vs. tech.

## Output

Write one JSONL line per family to `families_part_{batch_label}.jsonl`. Use
the Write tool with a single call containing all {batch_size} lines.

## Schema

{_SCHEMA_BLOCK}

After writing, report: number of families written, average # of unique
activity IDs per family (should be 20-28), any field where you weren't
sure which vocab value to pick.
"""


# ─────────────────────────────────────────────────────────────────────────────
# CLI helper: print a sample prompt to stdout for one (archetype, batch).
# Used for dev iteration before dispatching real subagents.
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    activities = load_activity_ids()
    archetypes = load_archetypes()
    print(f"# {len(activities)} activities, {len(archetypes)} archetypes")
    print()
    print("# Sample prompt for archetype[0], batch_size=10:")
    print()
    print(build_family_batch_prompt(
        archetype=archetypes[0],
        activities=activities,
        batch_size=10,
        batch_label="sample_a0",
        seed=42,
    ))


if __name__ == "__main__":
    main()
