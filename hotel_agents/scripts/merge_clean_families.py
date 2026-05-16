"""Re-merge families from cleaned shards (`families_clean_*.jsonl`) instead
of the raw Haiku shards (`families_part_*.jsonl`).

Same validation pass as merge_families.py — fuzzy ID repair, vocab check,
slot-count check — but reads the Sonnet-corrected files. The cleaned shards
already have valid bank IDs and slot-aware itineraries, so the drop rate
should be near-zero.

Output is the same canonical trio:
  families.jsonl     (id + 15kw family)
  itineraries.jsonl  (family_id + 30 activity_ids)
  anchors.jsonl      (family_id + archetype_id from filename)
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.schema import FAMILY_FIELDS, TOTAL_SLOTS
from hotel_agents.shared.storage import (
    ACTIVITIES_PATH,
    ANCHORS_PATH,
    DATA_DIR,
    FAMILIES_PATH,
    ITINERARIES_PATH,
    append_jsonl,
    read_json,
)


def _norm(aid: str) -> str:
    s = aid.lower()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _archetype_from_filename(name: str) -> str:
    stem = name.replace("families_clean_", "").replace(".jsonl", "")
    return stem.split("__b")[0]


def build_repair_map(valid_ids: set[str], unknown_ids: set[str]) -> dict[str, str]:
    valid_norm = {_norm(v): v for v in valid_ids}
    repair: dict[str, str] = {}
    for u in unknown_ids:
        un = _norm(u)
        if un in valid_norm:
            repair[u] = valid_norm[un]
            continue
        candidates = difflib.get_close_matches(un, valid_norm.keys(), n=1, cutoff=0.78)
        if candidates:
            repair[u] = valid_norm[candidates[0]]
    return repair


def main() -> None:
    activities = read_json(ACTIVITIES_PATH)
    valid_ids = {a["id"] for a in activities}

    shards = sorted(DATA_DIR.glob("families_clean_*.jsonl"))
    if not shards:
        sys.exit("no families_clean_*.jsonl shards found — run Sonnet cleanup first")
    print(f"merging from {len(shards)} cleaned shards")

    raw_records: list[tuple[str, str, dict]] = []
    unknown_set: set[str] = set()
    for shard in shards:
        archetype_id = _archetype_from_filename(shard.name)
        with shard.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    raw_records.append((shard.name, archetype_id, {}))
                    continue
                raw_records.append((shard.name, archetype_id, rec))
                for aid in rec.get("itinerary") or []:
                    if isinstance(aid, str) and aid not in valid_ids:
                        unknown_set.add(aid)

    repair_map = build_repair_map(valid_ids, unknown_set)
    print(f"unknown ids: {len(unknown_set)}, repaired: {len(repair_map)}")

    seen_ids: set[str] = set()
    families_out: list[dict] = []
    itineraries_out: list[dict] = []
    anchors_out: list[dict] = []
    stats = {"shards": len(shards), "records": 0, "dropped_bad_field": 0,
             "dropped_bad_itinerary": 0, "dropped_unknown_activity": 0,
             "renamed": 0, "repairs_applied": 0}

    for shard_name, archetype_id, rec in raw_records:
        if not rec:
            stats["dropped_bad_field"] += 1
            continue
        stats["records"] += 1
        fam = rec.get("family") or {}
        it = rec.get("itinerary") or []
        missing = [f for f in FAMILY_FIELDS if f not in fam]
        if missing:
            stats["dropped_bad_field"] += 1
            continue
        if not isinstance(it, list) or len(it) != TOTAL_SLOTS:
            stats["dropped_bad_itinerary"] += 1
            continue

        repaired_it: list[str] = []
        bad = False
        for aid in it:
            if aid in valid_ids:
                repaired_it.append(aid)
            elif aid in repair_map:
                repaired_it.append(repair_map[aid])
                stats["repairs_applied"] += 1
            else:
                bad = True
                break
        if bad:
            stats["dropped_unknown_activity"] += 1
            continue

        base_id = rec.get("id") or f"fam_{stats['records']}"
        new_id = base_id
        n = 2
        while new_id in seen_ids:
            new_id = f"{base_id}_{n}"
            n += 1
        if new_id != base_id:
            stats["renamed"] += 1
        seen_ids.add(new_id)
        families_out.append({"id": new_id, **fam})
        itineraries_out.append({"family_id": new_id, "activity_ids": repaired_it})
        anchors_out.append({"family_id": new_id, "archetype_id": archetype_id})

    for p in (FAMILIES_PATH, ITINERARIES_PATH, ANCHORS_PATH):
        p.write_text("", encoding="utf-8")
    append_jsonl(FAMILIES_PATH, families_out)
    append_jsonl(ITINERARIES_PATH, itineraries_out)
    append_jsonl(ANCHORS_PATH, anchors_out)

    print(f"\n=== merged {len(families_out)} cleaned families ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
