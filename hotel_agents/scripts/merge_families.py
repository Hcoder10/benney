"""Merge family JSONL shards from per-batch Haiku subagent outputs into
the canonical `families.jsonl` + `itineraries.jsonl`.

Each shard is one JSONL file with records of shape:
  {"id": "...", "family": {...15kw...}, "itinerary": [act_id, ...30]}

We split this into two files for downstream training:
  - families.jsonl    (id + the 15-keyword family object)
  - itineraries.jsonl (family_id + ordered list of activity_ids)

Validation:
  - drop families whose itinerary length != 30
  - drop families that reference unknown activity IDs (after the merged
    activity bank is loaded)
  - rename id collisions across shards
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.schema import FAMILY_FIELDS, TOTAL_SLOTS  # noqa: E402
from hotel_agents.shared.storage import (  # noqa: E402
    ACTIVITIES_PATH,
    ANCHORS_PATH,
    DATA_DIR,
    FAMILIES_PATH,
    ITINERARIES_PATH,
    append_jsonl,
    read_json,
)


def _archetype_from_filename(name: str) -> str:
    """families_part_<archetype_id>__b<n>.jsonl → <archetype_id>"""
    stem = name.replace("families_part_", "").replace(".jsonl", "")
    return stem.split("__b")[0]


def _norm(aid: str) -> str:
    """Normalize an activity ID for fuzzy matching: lowercase, strip
    apostrophes/spaces/punct, collapse repeated underscores."""
    s = aid.lower()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def build_repair_map(valid_ids: set[str], unknown_ids: set[str]) -> dict[str, str]:
    """For each unknown id, try to find a valid id it's a typo of.
    Two-tier match: (1) normalized exact match, (2) difflib similarity >= 0.78.
    Generic placeholders ('lunch_casual', 'final_dinner', etc.) won't match
    anything in the bank and are left for the validator to drop.
    """
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
    if not ACTIVITIES_PATH.exists():
        sys.exit(f"missing {ACTIVITIES_PATH} — run merge_activities.py first")
    activities = read_json(ACTIVITIES_PATH)
    valid_ids = {a["id"] for a in activities}

    shards = sorted(DATA_DIR.glob("families_part_*.jsonl"))
    if not shards:
        sys.exit(f"no families_part_*.jsonl shards found in {DATA_DIR}")

    # First pass: collect all unknown IDs across all shards so we can build
    # one repair map (cheaper than per-line difflib calls).
    raw_records: list[tuple[str, str, dict]] = []  # (shard_name, archetype_id, rec)
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
    print(f"unknown activity IDs: {len(unknown_set)}, repaired: {len(repair_map)}")

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

        # Apply repair pass
        repaired_it = []
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

    # Reset target files, then bulk-write the canonical merged set.
    for p in (FAMILIES_PATH, ITINERARIES_PATH, ANCHORS_PATH):
        p.write_text("", encoding="utf-8")
    append_jsonl(FAMILIES_PATH, families_out)
    append_jsonl(ITINERARIES_PATH, itineraries_out)
    append_jsonl(ANCHORS_PATH, anchors_out)

    print(f"=== merged {len(families_out)} families from {stats['shards']} shards ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
