"""Build a combined families.jsonl + itineraries.jsonl from v1 cleaned + v4 seeds.

The trainer reads FAMILIES_PATH / ITINERARIES_PATH. We don't overwrite the v1
canonical files; instead we write families_combined.jsonl + itineraries_combined.jsonl
and the trainer is parameterized to read these.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.schema import FAMILY_FIELDS, TOTAL_SLOTS  # noqa: E402
from hotel_agents.shared.storage import (  # noqa: E402
    DATA_DIR, FAMILIES_PATH, ITINERARIES_PATH, read_jsonl,
)


def main():
    fams_out = DATA_DIR / "families_combined.jsonl"
    its_out = DATA_DIR / "itineraries_combined.jsonl"

    # v1 cleaned families (already loaded from canonical files)
    v1_fams = read_jsonl(FAMILIES_PATH)
    v1_its = read_jsonl(ITINERARIES_PATH)
    v1_fam_by_id = {f["id"]: f for f in v1_fams}
    print(f"v1: {len(v1_fams)} families, {len(v1_its)} itineraries")

    # v4 seeds
    seeds_dir = DATA_DIR / "v4_seeds"
    v4_fams = []
    v4_its = []
    for shard in sorted(seeds_dir.glob("*.jsonl")):
        with shard.open() as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                fam = rec.get("family")
                aid_list = rec.get("activity_ids") or rec.get("itinerary") or []
                if not isinstance(fam, dict) or len(aid_list) != TOTAL_SLOTS:
                    continue
                if not all(f in fam for f in FAMILY_FIELDS):
                    continue
                fid = rec.get("id") or f"v4_{shard.stem}_{len(v4_fams)}"
                v4_fams.append({"id": fid, **fam})
                v4_its.append({"family_id": fid, "activity_ids": aid_list})
    print(f"v4: {len(v4_fams)} families, {len(v4_its)} itineraries")

    # Write combined
    with fams_out.open("w", encoding="utf-8") as f:
        for rec in v1_fams + v4_fams:
            f.write(json.dumps(rec) + "\n")
    with its_out.open("w", encoding="utf-8") as f:
        for rec in v1_its + v4_its:
            f.write(json.dumps(rec) + "\n")
    print(f"\ncombined: {len(v1_fams)+len(v4_fams)} families → {fams_out}")
    print(f"          {len(v1_its)+len(v4_its)} itineraries → {its_out}")


if __name__ == "__main__":
    main()
