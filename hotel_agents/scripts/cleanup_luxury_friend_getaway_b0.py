"""
Cleanup script for families_part_luxury_friend_getaway__b0.jsonl
Applies slot-type, open-hours, repetition, and tag normalization rules.
"""

import json
import re
from collections import Counter
from copy import deepcopy

# ── paths ──────────────────────────────────────────────────────────────────────
ACTIVITIES_PATH = "C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json"
INPUT_PATH      = "C:/Users/sarta/rosea/hotel_agents/data/families_part_luxury_friend_getaway__b0.jsonl"
OUTPUT_PATH     = "C:/Users/sarta/rosea/hotel_agents/data/families_clean_luxury_friend_getaway__b0.jsonl"

# ── slot rules ─────────────────────────────────────────────────────────────────
SLOT_RULES = [
    (7,  9,  {"cafe", "coffee", "bakery", "breakfast"}),           # 0 early-morning
    (8,  10, {"cafe", "coffee", "bakery", "breakfast", "brunch"}), # 1 breakfast
    (10, 13, {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art",
              "science", "history", "gardens", "walking", "shopping",
              "viewpoint", "landmark", "architecture"}),             # 2 late-morning
    (12, 17, {"restaurant", "lunch", "casual", "park", "scenic",
              "beach", "outdoor", "hiking", "tour", "shopping",
              "winery", "wine", "tasting"}),                        # 3 lunch+afternoon
    (17, 20, {"restaurant", "dinner", "fine-dining", "scenic",
              "sunset", "wine", "casual"}),                         # 4 evening
    (20, 23, {"bar", "cocktails", "nightlife", "lounge", "dinner",
              "fine-dining", "speakeasy"}),                         # 5 night
]

# ── tag normalization map ──────────────────────────────────────────────────────
TAG_NORM = {
    "fine dining":     "fine-dining",
    "fine_dining":     "fine-dining",
    "michelin 3-star": "fine-dining",
    "michelin 2-star": "fine-dining",
    "michelin 1-star": "fine-dining",
    "michelin":        "fine-dining",
    "cocktail":        "cocktails",
    "drinks":          "cocktails",
    "night life":      "nightlife",
    "night-life":      "nightlife",
    "coffee shop":     "coffee",
    "coffee bar":      "coffee",
    "roastery":        "coffee",
    "roaster":         "coffee",
    "specialty":       "coffee",
    "pour_over":       "coffee",
    "vinyard":         "winery",
    "vineyard":        "winery",
    "wine bar":        "wine",
    "wine tasting":    "tasting",
    "wine-bar":        "wine",
    "brunch/breakfast":"brunch",
    "dim_sum":         "restaurant",
    "ice_cream":       "casual",
    "dessert":         "casual",
}

BUDGET_ORDER = {"shoestring": 0, "mid": 1, "premium": 2, "luxury": 3}


def normalize_tags(tags):
    """Normalize tags list and return sorted deduplicated list."""
    result = []
    for t in tags:
        t = t.strip().lower()
        t = TAG_NORM.get(t, t)
        if t and t not in result:
            result.append(t)
    return result


def parse_time(s):
    """Parse '09:30' or '9:30 AM' or '5:30 PM' -> fractional hour float."""
    s = s.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$', s, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        period = m.group(3).upper()
        if period == "PM" and h != 12:
            h += 12
        if period == "AM" and h == 12:
            h = 0
        return h + mins / 60
    return None


def parse_hours_range(s):
    """Parse 'open - close' string -> (open_h, close_h) floats or None."""
    if not s or s.lower() in ("closed", "n/a", ""):
        return None
    m = re.match(r'^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$', s)
    if m:
        o = parse_time(m.group(1))
        c = parse_time(m.group(2))
        if o is not None and c is not None:
            return (o, c)
    m = re.match(r'^(.+?)\s*[-–]\s*(.+)$', s)
    if m:
        o = parse_time(m.group(1).strip())
        c = parse_time(m.group(2).strip())
        if o is not None and c is not None:
            return (o, c)
    return None


def hours_overlap(activity_hours, slot_start, slot_end):
    """
    True if the activity is open during ANY part of [slot_start, slot_end).
    Multi-session hours: any session overlapping counts.
    """
    if not activity_hours or activity_hours.lower() == "closed":
        return False
    if "24h" in activity_hours.lower():
        return True
    if "sunset" in activity_hours.lower() or "sunrise" in activity_hours.lower():
        # Treat as 8:00-20:00
        return slot_start < 20 and slot_end > 8
    sessions = activity_hours.split(",")
    for session in sessions:
        r = parse_hours_range(session.strip())
        if r is None:
            # Cannot parse -> assume open (generous)
            return True
        open_h, close_h = r
        if not (close_h <= slot_start or open_h >= slot_end):
            return True
    return False


def tags_match_slot(tags_normalized, slot_idx):
    _, _, required = SLOT_RULES[slot_idx]
    return bool(set(tags_normalized) & required)


def budget_ok(act_budget, family_budget):
    return BUDGET_ORDER.get(act_budget, 99) <= BUDGET_ORDER.get(family_budget, 99)


def build_candidate_pool(act_map, slot_idx, family):
    slot_start, slot_end, _ = SLOT_RULES[slot_idx]
    candidates = []
    for aid, act in act_map.items():
        tags = act["tags"]  # already normalized
        if not tags_match_slot(tags, slot_idx):
            continue
        oh = act.get("open_hours", {})
        fri_hours = oh.get("fri", oh.get("mon", ""))
        if not hours_overlap(fri_hours, slot_start, slot_end):
            continue
        if not budget_ok(act.get("budget_tier", "luxury"), family["budget_tier"]):
            continue
        if not act.get("kid_ok", True) and family["kid_ages"] != "none":
            continue
        if not act.get("mobility_ok", True) and family["mobility"] != "full":
            continue
        candidates.append(aid)
    return candidates


def pick_replacement(act_map, slot_idx, family, usage_counter, exclude_id):
    candidates = build_candidate_pool(act_map, slot_idx, family)
    result = []
    for aid in candidates:
        if aid == exclude_id:
            continue
        tags = act_map[aid]["tags"]
        is_cafe = "cafe" in tags or "coffee" in tags
        cap = 5 if is_cafe else 3
        if usage_counter[aid] >= cap:
            continue
        result.append(aid)
    if not result:
        return None
    result.sort(key=lambda aid: usage_counter[aid])
    return result[0]


def process_family(family_record, act_map):
    family = family_record["family"]
    itinerary = list(family_record["itinerary"])

    slot_fixes = 0
    rep_fixes = 0
    unfixable = []

    usage = Counter(itinerary)

    # Pass 1: fix slot-type and open-hours violations
    for i, act_id in enumerate(itinerary):
        slot_idx = i % 6
        slot_start, slot_end, _ = SLOT_RULES[slot_idx]

        act = act_map.get(act_id)
        if act is None:
            replacement = pick_replacement(act_map, slot_idx, family, usage, act_id)
            if replacement:
                usage[act_id] -= 1
                itinerary[i] = replacement
                usage[replacement] += 1
                slot_fixes += 1
            else:
                unfixable.append(f"slot {i} ({act_id}): unknown activity + no replacement")
            continue

        tags = act["tags"]
        oh = act.get("open_hours", {})
        fri_hours = oh.get("fri", oh.get("mon", ""))

        slot_ok = tags_match_slot(tags, slot_idx)
        hrs_ok = hours_overlap(fri_hours, slot_start, slot_end)

        if not slot_ok or not hrs_ok:
            replacement = pick_replacement(act_map, slot_idx, family, usage, act_id)
            if replacement:
                usage[act_id] -= 1
                itinerary[i] = replacement
                usage[replacement] += 1
                slot_fixes += 1
            else:
                reasons = []
                if not slot_ok:
                    reasons.append("slot-type")
                if not hrs_ok:
                    reasons.append("hours")
                unfixable.append(f"slot {i} ({act_id}): {'+'.join(reasons)} violation + no replacement")

    # Pass 2: fix repetition cap
    usage = Counter(itinerary)
    for i, act_id in enumerate(itinerary):
        act = act_map.get(act_id)
        tags = act["tags"] if act else []
        is_cafe = "cafe" in tags or "coffee" in tags
        cap = 5 if is_cafe else 3

        if usage[act_id] > cap:
            slot_idx = i % 6
            replacement = pick_replacement(act_map, slot_idx, family, usage, act_id)
            if replacement:
                usage[act_id] -= 1
                itinerary[i] = replacement
                usage[replacement] += 1
                rep_fixes += 1
            else:
                unfixable.append(f"slot {i} ({act_id}): over repetition cap + no replacement")

    new_record = deepcopy(family_record)
    new_record["itinerary"] = itinerary
    return new_record, slot_fixes, rep_fixes, unfixable


def main():
    with open(ACTIVITIES_PATH, "r", encoding="utf-8") as f:
        activities = json.load(f)

    # Normalize tags in activity bank in-memory
    act_map = {}
    for act in activities:
        act = deepcopy(act)
        act["tags"] = normalize_tags(act.get("tags", []))
        act_map[act["id"]] = act

    print(f"Loaded {len(act_map)} activities")

    families = []
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                families.append(json.loads(line))

    print(f"Loaded {len(families)} family records")

    total_slot_fixes = 0
    total_rep_fixes = 0
    all_unfixable = []

    out_records = []
    for rec in families:
        new_rec, sf, rf, uf = process_family(rec, act_map)
        total_slot_fixes += sf
        total_rep_fixes += rf
        all_unfixable.extend([(rec["id"], u) for u in uf])
        out_records.append(new_rec)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for rec in out_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n=== Cleanup Summary ===")
    print(f"Families processed:    {len(families)}")
    print(f"Slot-type/hours fixes: {total_slot_fixes}")
    print(f"Repetition fixes:      {total_rep_fixes}")
    print(f"Total fixes:           {total_slot_fixes + total_rep_fixes}")
    if all_unfixable:
        print(f"Unfixable slots:       {len(all_unfixable)}")
        for fid, msg in all_unfixable:
            print(f"  [{fid}] {msg}")
    else:
        print(f"Unfixable slots:       0")
    print(f"\nOutput written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
