"""
Cleanup script for families_part_wedding_guest_casual__b0.jsonl
Applies slot-type, open-hours (ANY-overlap multi-session), repetition, and tag normalization rules.
"""

import json
import re
from collections import Counter
from copy import deepcopy

# ── paths ──────────────────────────────────────────────────────────────────────
ACTIVITIES_PATH = "C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json"
INPUT_PATH      = "C:/Users/sarta/rosea/hotel_agents/data/families_part_wedding_guest_casual__b0.jsonl"
OUTPUT_PATH     = "C:/Users/sarta/rosea/hotel_agents/data/families_clean_wedding_guest_casual__b0.jsonl"

# ── slot rules ─────────────────────────────────────────────────────────────────
# Each slot: (time_start_h, time_end_h, required_tags_any_of)
SLOT_RULES = [
    (7,  9,  {"cafe", "coffee", "bakery", "breakfast"}),                        # 0 early-morning
    (8,  10, {"cafe", "coffee", "bakery", "breakfast", "brunch"}),              # 1 breakfast
    (10, 13, {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art",
              "science", "history", "gardens", "walking", "shopping",
              "viewpoint", "landmark", "architecture"}),                         # 2 late-morning
    (12, 17, {"restaurant", "lunch", "casual", "park", "scenic",
              "beach", "outdoor", "hiking", "tour", "shopping",
              "winery", "wine", "tasting"}),                                     # 3 lunch+afternoon
    (17, 20, {"restaurant", "dinner", "fine-dining", "scenic",
              "sunset", "wine", "casual"}),                                      # 4 evening
    (20, 23, {"bar", "cocktails", "nightlife", "lounge", "dinner",
              "fine-dining", "speakeasy"}),                                      # 5 night
]

# ── tag normalization map ──────────────────────────────────────────────────────
# Maps raw/variant tags → canonical slot-rule forms
TAG_NORM = {
    # fine dining variants
    "fine dining":      "fine-dining",
    "fine_dining":      "fine-dining",
    "michelin":         "fine-dining",
    "michelin 3-star":  "fine-dining",
    "michelin-star":    "fine-dining",
    "michelin star":    "fine-dining",
    "tasting menu":     "fine-dining",
    # bar/cocktail variants
    "cocktail":         "cocktails",
    "wine bar":         "bar",
    "winebar":          "bar",
    # winery variants
    "vineyard":         "winery",
    "winery":           "winery",
    "wine tasting":     "tasting",
    # outdoor/nature variants
    "nature":           "outdoor",
    "outdoors":         "outdoor",
    "trails":           "hiking",
    "trail":            "hiking",
    "bay trail":        "outdoor",
    # museum/culture/tour
    "culture":          "museum",
    "historic":         "history",
    "historical":       "history",
    "gallery":          "art",
    "craft":            "art",
    "island":           "tour",
    "tours":            "tour",
    # misc
    "garden":           "gardens",
    "coffee shop":      "coffee",
    "coffee bar":       "coffee",
    "bars":             "bar",
    "night life":       "nightlife",
    "night-life":       "nightlife",
    "brunch/breakfast": "brunch",
    "italian":          "restaurant",
    "french":           "restaurant",
    "vietnamese":       "restaurant",
    "seafood":          "restaurant",
    "tacos":            "casual",
    "burritos":         "casual",
    "food":             "restaurant",
    "market":           "shopping",
    "shopping center":  "shopping",
}

BUDGET_ORDER = {"shoestring": 0, "budget": 0, "mid": 1, "premium": 2, "luxury": 3}


def normalize_tags(tags):
    """Normalize a raw tag list → deduplicated list of canonical tags."""
    result = []
    for t in tags:
        t = t.strip().lower()
        t = TAG_NORM.get(t, t)
        if t and t not in result:
            result.append(t)
    return result


def parse_time(s, is_close=False):
    """
    Parse '09:30' or '9:30 AM' or '5:30 PM' or '17:30' → fractional hour float.
    If is_close=True and the result is 0.0, treat as 24.0 (midnight close).
    """
    s = s.strip()
    if s.lower() == "sunset":
        return 20.0
    # 24h HH:MM
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        val = int(m.group(1)) + int(m.group(2)) / 60
        if is_close and val == 0.0:
            return 24.0
        return val
    # 12h with AM/PM
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$', s, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        period = m.group(3).upper()
        if period == "PM" and h != 12:
            h += 12
        if period == "AM" and h == 12:
            h = 0
        val = h + mins / 60
        if is_close and val == 0.0:
            return 24.0
        return val
    return None


def parse_hours_range(s):
    """Parse 'open - close' string → (open_h, close_h) floats or None."""
    if not s or s.lower().strip() in ("closed", "n/a", ""):
        return None
    s = s.strip()
    # Handle 24h shorthand
    if "24h" in s.lower() or "always" in s.lower():
        return (0.0, 24.0)
    # Try HH:MM-HH:MM (24h compact, no space around dash)
    m = re.match(r'^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$', s)
    if m:
        o = parse_time(m.group(1))
        c = parse_time(m.group(2), is_close=True)
        if o is not None and c is not None:
            return (o, c)
    # Try "H:MM AM/PM - H:MM AM/PM" or "H:MM - H:MM" with spaces around dash
    m = re.match(r'^(.+?)\s*[-–]\s*(.+)$', s)
    if m:
        o = parse_time(m.group(1).strip())
        c = parse_time(m.group(2).strip(), is_close=True)
        if o is not None and c is not None:
            return (o, c)
    return None


def hours_overlap(activity_hours_str, slot_start, slot_end):
    """
    True if the activity is open during ANY part of [slot_start, slot_end).
    Handles multi-session strings like "9:00-13:00, 17:00-21:00".
    If the string cannot be parsed, we assume open (generous, don't penalize).
    """
    if not activity_hours_str or activity_hours_str.lower().strip() == "closed":
        return False
    sessions = activity_hours_str.split(",")
    parsed_any = False
    for session in sessions:
        r = parse_hours_range(session.strip())
        if r is None:
            # Unparseable segment — assume open
            return True
        parsed_any = True
        open_h, close_h = r
        # overlap: not (close_h <= slot_start or open_h >= slot_end)
        if not (close_h <= slot_start or open_h >= slot_end):
            return True
    # All parsed and none overlapped
    return False


def get_representative_hours(act):
    """Return the best representative hours string for an activity (prefer Fri, fallback chain)."""
    oh = act.get("open_hours", {})
    for day in ("fri", "thu", "sat", "wed", "mon", "tue", "sun"):
        val = oh.get(day, "")
        if val and val.lower().strip() not in ("closed", ""):
            return val
    return ""


def tags_match_slot(tags_normalized, slot_idx):
    """True if any tag in tags_normalized matches the required set for the slot."""
    _, _, required = SLOT_RULES[slot_idx]
    return bool(set(tags_normalized) & required)


def budget_ok(act_budget, family_budget):
    return BUDGET_ORDER.get(act_budget, 99) <= BUDGET_ORDER.get(family_budget, 99)


def build_candidate_pool(act_map, slot_idx, family):
    """Return list of activity IDs satisfying slot-type + open-hours + family constraints."""
    slot_start, slot_end, _ = SLOT_RULES[slot_idx]
    candidates = []
    for aid, act in act_map.items():
        tags = act.get("_tags_norm", normalize_tags(act.get("tags", [])))
        if not tags_match_slot(tags, slot_idx):
            continue
        rep_hours = get_representative_hours(act)
        if rep_hours and rep_hours.lower().strip() != "closed":
            if not hours_overlap(rep_hours, slot_start, slot_end):
                continue
        # else: no hours listed → assume always open, accept
        if not budget_ok(act.get("budget_tier", "luxury"), family["budget_tier"]):
            continue
        if not act.get("kid_ok", True) and family.get("kid_ages", "none") != "none":
            continue
        if not act.get("mobility_ok", True) and family.get("mobility", "full") != "full":
            continue
        candidates.append(aid)
    return candidates


def pick_replacement(act_map, slot_idx, family, usage_counter, exclude_id, prefer_near=None):
    """
    Pick best replacement: matches slot + hours + family constraints,
    not over usage cap, not the same as exclude_id.
    Prefer least-used, then geographically close to prefer_near (lat, lng).
    """
    candidates = build_candidate_pool(act_map, slot_idx, family)
    result = []
    for aid in candidates:
        if aid == exclude_id:
            continue
        tags = act_map[aid].get("_tags_norm", [])
        is_cafe = "cafe" in tags or "coffee" in tags
        cap = 5 if is_cafe else 3
        if usage_counter[aid] >= cap:
            continue
        # geo score
        score = 0.0
        if prefer_near:
            plat, plng = prefer_near
            alat = act_map[aid].get("lat", 0)
            alng = act_map[aid].get("lng", 0)
            score = ((alat - plat) ** 2 + (alng - plng) ** 2) ** 0.5
        result.append((usage_counter[aid], score, aid))
    if not result:
        return None
    result.sort()
    return result[0][2]


def process_family(family_record, act_map):
    """
    Clean one family record.
    Returns (new_record, slot_fixes, rep_fixes, unfixable_list).
    """
    family = family_record["family"]
    itinerary = list(family_record["itinerary"])
    fam_id = family_record["id"]

    slot_fixes = 0
    rep_fixes = 0
    unfixable = []

    usage = Counter(itinerary)

    # ── Pass 1: fix missing IDs, slot-type violations, open-hours violations ───
    for i, act_id in enumerate(itinerary):
        slot_idx = i % 6
        slot_start, slot_end, _ = SLOT_RULES[slot_idx]

        act = act_map.get(act_id)
        if act is None:
            # Not in bank — must replace
            prev_loc = None
            if i > 0:
                prev_act = act_map.get(itinerary[i - 1])
                if prev_act:
                    prev_loc = (prev_act.get("lat", 0), prev_act.get("lng", 0))
            usage[act_id] -= 1
            replacement = pick_replacement(act_map, slot_idx, family, usage, act_id, prefer_near=prev_loc)
            if replacement:
                print(f"  [{fam_id}] slot {i} (day{i//6+1} s{slot_idx}): {act_id} (NOT IN BANK) -> {replacement}")
                itinerary[i] = replacement
                usage[replacement] += 1
                slot_fixes += 1
            else:
                usage[act_id] += 1  # restore count
                print(f"  [{fam_id}] slot {i}: NO REPLACEMENT found for {act_id} (not-in-bank)")
                unfixable.append(f"slot {i} ({act_id}): not-in-bank, no replacement")
            continue

        tags = act.get("_tags_norm", normalize_tags(act.get("tags", [])))
        rep_hours = get_representative_hours(act)

        slot_ok = tags_match_slot(tags, slot_idx)
        if rep_hours and rep_hours.lower().strip() != "closed":
            hours_ok = hours_overlap(rep_hours, slot_start, slot_end)
        else:
            hours_ok = True  # no hours listed → assume open

        if not slot_ok or not hours_ok:
            reasons = []
            if not slot_ok:
                reasons.append("tag-violation")
            if not hours_ok:
                reasons.append("hours-violation")

            prev_loc = None
            if i > 0:
                prev_act = act_map.get(itinerary[i - 1])
                if prev_act:
                    prev_loc = (prev_act.get("lat", 0), prev_act.get("lng", 0))

            usage[act_id] -= 1
            replacement = pick_replacement(act_map, slot_idx, family, usage, act_id, prefer_near=prev_loc)
            if replacement:
                print(f"  [{fam_id}] slot {i} (day{i//6+1} s{slot_idx}): {act_id} ({','.join(reasons)}) -> {replacement}")
                itinerary[i] = replacement
                usage[replacement] += 1
                slot_fixes += 1
            else:
                usage[act_id] += 1  # restore
                print(f"  [{fam_id}] slot {i}: NO REPLACEMENT for {act_id} ({','.join(reasons)})")
                unfixable.append(f"slot {i} ({act_id}): {'+'.join(reasons)}, no replacement")

    # ── Pass 2: fix repetition cap ─────────────────────────────────────────────
    usage = Counter(itinerary)
    for i in range(len(itinerary)):
        act_id = itinerary[i]
        act = act_map.get(act_id)
        tags = act.get("_tags_norm", []) if act else []
        is_cafe = "cafe" in tags or "coffee" in tags
        cap = 5 if is_cafe else 3

        if usage[act_id] > cap:
            slot_idx = i % 6
            prev_loc = None
            if i > 0:
                prev_act = act_map.get(itinerary[i - 1])
                if prev_act:
                    prev_loc = (prev_act.get("lat", 0), prev_act.get("lng", 0))

            usage[act_id] -= 1
            replacement = pick_replacement(act_map, slot_idx, family, usage, act_id, prefer_near=prev_loc)
            if replacement:
                print(f"  [{fam_id}] slot {i} rep-cap: {act_id} ({usage[act_id]+1}x>{cap}) -> {replacement}")
                itinerary[i] = replacement
                usage[replacement] += 1
                rep_fixes += 1
            else:
                usage[act_id] += 1  # restore
                print(f"  [{fam_id}] slot {i}: NO REPLACEMENT for rep-cap on {act_id}")
                unfixable.append(f"slot {i} ({act_id}): repetition-cap, no replacement")

    new_record = deepcopy(family_record)
    new_record["itinerary"] = itinerary
    return new_record, slot_fixes, rep_fixes, unfixable


def main():
    # ── Load activity bank, pre-normalize tags ─────────────────────────────────
    with open(ACTIVITIES_PATH, "r", encoding="utf-8") as f:
        activities = json.load(f)

    act_map = {}
    for act in activities:
        act = deepcopy(act)
        act["_tags_norm"] = normalize_tags(act.get("tags", []))
        act_map[act["id"]] = act

    print(f"Loaded {len(act_map)} activities")

    # ── Load families ──────────────────────────────────────────────────────────
    families = []
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                families.append(json.loads(line))

    print(f"Loaded {len(families)} family records")
    print()

    total_slot_fixes = 0
    total_rep_fixes  = 0
    all_unfixable    = []
    out_records      = []

    for rec in families:
        new_rec, sf, rf, uf = process_family(rec, act_map)
        total_slot_fixes += sf
        total_rep_fixes  += rf
        all_unfixable.extend([(rec["id"], u) for u in uf])
        out_records.append(new_rec)

    # ── Write output ───────────────────────────────────────────────────────────
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for rec in out_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("=== SUMMARY ===")
    print(f"Families processed          : {len(families)}")
    print(f"Slot-type/hours replacements: {total_slot_fixes}")
    print(f"Repetition fixes            : {total_rep_fixes}")
    print(f"Total replacements          : {total_slot_fixes + total_rep_fixes}")
    if all_unfixable:
        print(f"Unfixable slots             : {len(all_unfixable)}")
        for fid, msg in all_unfixable:
            print(f"  [{fid}] {msg}")
    else:
        print(f"Unfixable slots             : 0")
    print(f"\nOutput written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
