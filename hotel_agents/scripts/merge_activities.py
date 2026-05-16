"""Merge the 5 activity-bank shards (peninsula/southbay/sf/outdoors/wine)
into a single deduped + validated activities_bay.json.

Each shard is one JSON array of Activity objects produced by a Haiku
subagent. Failure modes we handle:
  - duplicate IDs across shards (rename second occurrence)
  - missing required fields (drop with warning)
  - shards that didn't write (skip)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Import schema constants from the shared package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.storage import ACTIVITIES_PATH, DATA_DIR, write_json  # noqa: E402

SHARDS = [
    DATA_DIR / "activities_part_peninsula.json",
    DATA_DIR / "activities_part_southbay.json",
    DATA_DIR / "activities_part_sf.json",
    DATA_DIR / "activities_part_outdoors.json",
    DATA_DIR / "activities_part_wine.json",
]

REQUIRED = {
    "id", "name", "description", "lat", "lng", "address", "open_hours",
    "duration_min", "budget_tier", "energy", "indoor_outdoor",
    "kid_ok", "mobility_ok", "tags", "photo_url",
}
BUDGET_TIERS = {"shoestring", "mid", "premium", "luxury"}
ENERGY = {"low", "medium", "high"}
INDOOR_OUTDOOR = {"indoor", "outdoor", "mixed"}


def validate(entry: dict, source: str) -> tuple[bool, str]:
    missing = REQUIRED - set(entry.keys())
    if missing:
        return False, f"missing keys: {sorted(missing)}"
    if entry["budget_tier"] not in BUDGET_TIERS:
        return False, f"bad budget_tier: {entry['budget_tier']}"
    if entry["energy"] not in ENERGY:
        return False, f"bad energy: {entry['energy']}"
    if entry["indoor_outdoor"] not in INDOOR_OUTDOOR:
        return False, f"bad indoor_outdoor: {entry['indoor_outdoor']}"
    if not isinstance(entry["lat"], (int, float)) or not isinstance(entry["lng"], (int, float)):
        return False, "lat/lng must be numeric"
    if not (32 < entry["lat"] < 42) or not (-125 < entry["lng"] < -120):
        return False, f"coords way off CA: {entry['lat']},{entry['lng']}"
    if not isinstance(entry["tags"], list):
        return False, "tags must be a list"
    return True, ""


def main() -> None:
    merged: dict[str, dict] = {}
    stats = {"shards_found": 0, "shards_missing": 0, "loaded": 0, "dropped": 0, "renamed": 0}

    for shard in SHARDS:
        if not shard.exists():
            print(f"[skip] {shard.name} (not written)")
            stats["shards_missing"] += 1
            continue
        try:
            entries = json.loads(shard.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[skip] {shard.name} — malformed JSON: {e}")
            stats["shards_missing"] += 1
            continue
        stats["shards_found"] += 1
        if not isinstance(entries, list):
            print(f"[skip] {shard.name} — top level isn't a list")
            continue

        for entry in entries:
            ok, why = validate(entry, shard.name)
            if not ok:
                print(f"  drop {shard.name}:{entry.get('id', '?')} — {why}")
                stats["dropped"] += 1
                continue
            base_id = entry["id"]
            new_id = base_id
            n = 2
            while new_id in merged:
                new_id = f"{base_id}_{n}"
                n += 1
            if new_id != base_id:
                stats["renamed"] += 1
                entry = {**entry, "id": new_id}
            merged[new_id] = entry
            stats["loaded"] += 1

    activities = sorted(merged.values(), key=lambda a: a["id"])
    write_json(ACTIVITIES_PATH, activities)
    print()
    print(f"=== merged {len(activities)} activities → {ACTIVITIES_PATH} ===")
    print(f"shards found: {stats['shards_found']}, missing: {stats['shards_missing']}")
    print(f"entries loaded: {stats['loaded']}, dropped: {stats['dropped']}, id-renamed: {stats['renamed']}")

    # Print quick coverage summary
    by_budget: dict[str, int] = {}
    by_energy: dict[str, int] = {}
    for a in activities:
        by_budget[a["budget_tier"]] = by_budget.get(a["budget_tier"], 0) + 1
        by_energy[a["energy"]] = by_energy.get(a["energy"], 0) + 1
    print(f"budget breakdown:  {by_budget}")
    print(f"energy breakdown:  {by_energy}")


if __name__ == "__main__":
    main()
