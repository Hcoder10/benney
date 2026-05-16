"""
Audit and repair itinerary data for families_part_retired_wine_couple__b0.jsonl.
Rules from sonnet_cleanup_brief.md:
  - Slot-type tags
  - Open-hours compatibility (use fri as representative)
  - Repetition cap (3x max, 5x for cafes)
  - family-level filters: budget_tier, mobility_ok, kid_ok
"""

import json
import re
import math
from collections import Counter
from copy import deepcopy

# ── slot rules ──────────────────────────────────────────────────────────────
SLOT_TAGS = {
    0: {"cafe", "coffee", "bakery", "breakfast"},
    1: {"cafe", "coffee", "bakery", "breakfast", "brunch"},
    2: {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art", "science",
        "history", "gardens", "walking", "shopping", "viewpoint", "landmark", "architecture"},
    3: {"restaurant", "lunch", "casual", "park", "scenic", "beach", "outdoor", "hiking",
        "tour", "shopping", "winery", "wine", "tasting"},
    4: {"restaurant", "dinner", "fine-dining", "scenic", "sunset", "wine", "casual"},
    5: {"bar", "cocktails", "nightlife", "lounge", "dinner", "fine-dining", "speakeasy"},
}

# Slot time windows for open-hours checking (start_hour, end_hour) - inclusive start, exclusive end
SLOT_WINDOWS = {
    0: (7, 9),
    1: (8, 10),
    2: (10, 13),
    3: (12, 17),
    4: (17, 20),
    5: (20, 23),
}

BUDGET_ORDER = ["shoestring", "mid", "premium", "luxury"]

def parse_hours(s):
    """Parse a time string like '11:00-17:00', '17:30-21:30', '9:00 AM - 5:00 PM',
    or multi-segment '11:30-14:00, 17:30-22:30'.
    Returns list of (open_hour_float, close_hour_float) tuples; empty list if closed."""
    if not s or s.strip().lower() in ("closed", "n/a", ""):
        return []
    def to_24(t):
        t = t.strip()
        m = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$', t, re.IGNORECASE)
        if not m:
            return None
        h = int(m.group(1))
        mn = int(m.group(2)) if m.group(2) else 0
        ap = m.group(3)
        if ap:
            ap = ap.upper()
            if ap == "PM" and h != 12:
                h += 12
            elif ap == "AM" and h == 12:
                h = 0
        # treat 00:00 / 0.0 close-time as midnight (24.0)
        val = h + mn / 60.0
        return val
    result = []
    for segment in s.split(","):
        segment = segment.strip()
        parts = re.split(r'\s*[-–]\s*', segment)
        if len(parts) == 2:
            o = to_24(parts[0])
            c = to_24(parts[1])
            if o is not None and c is not None:
                # midnight close: if close <= open assume next-day midnight = 24
                if c <= o:
                    c = 24.0
                result.append((o, c))
    return result

def hours_overlap(open_hrs, slot_idx):
    """True if the activity's fri open hours overlap with the slot's window."""
    win = SLOT_WINDOWS[slot_idx]
    win_start, win_end = win
    ranges = parse_hours(open_hrs)
    if not ranges:
        return False  # closed on fri -> can't use
    # any segment overlapping is enough
    for (open_h, close_h) in ranges:
        if open_h < win_end and close_h > win_start:
            return True
    return False

def budget_ok(act_budget, family_budget):
    try:
        return BUDGET_ORDER.index(act_budget) <= BUDGET_ORDER.index(family_budget)
    except ValueError:
        return True

def tags_match(act_tags, slot_idx):
    return bool(set(act_tags) & SLOT_TAGS[slot_idx])

def is_cafe(act):
    return bool(set(act.get("tags", [])) & {"cafe", "coffee", "bakery"})

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# ── load data ────────────────────────────────────────────────────────────────
with open(r'C:\Users\sarta\rosea\hotel_agents\data\activities_bay.json', 'r', encoding='utf-8') as f:
    acts_list = json.load(f)

acts_by_id = {a["id"]: a for a in acts_list}

with open(r'C:\Users\sarta\rosea\hotel_agents\data\families_part_retired_wine_couple__b0.jsonl', 'r', encoding='utf-8') as f:
    families = [json.loads(l) for l in f if l.strip()]

# ── repair ───────────────────────────────────────────────────────────────────
total_slot_fixes = 0
total_rep_fixes = 0
no_replacement_found = []  # (fam_id, slot_idx, reason)

output_records = []

for rec in families:
    fam = rec["family"]
    itinerary = list(rec["itinerary"])  # 30 slots
    fam_id = rec["id"]

    family_budget = fam["budget_tier"]
    family_mobility = fam.get("mobility", "full")
    family_kid_ages = fam.get("kid_ages", "none")

    def family_ok(act):
        # mobility: if act mobility_ok=False, only ok if family mobility=full
        if not act.get("mobility_ok", True):
            if family_mobility != "full":
                return False
        # kid_ok: if act kid_ok=False, only ok if family has no kids
        if not act.get("kid_ok", True):
            if family_kid_ages != "none":
                return False
        # budget
        if not budget_ok(act.get("budget_tier", "shoestring"), family_budget):
            return False
        return True

    # First pass: fix open-hours and tag violations, building usage counts
    usage = Counter(itinerary)

    # We'll iterate and fix, tracking changes
    slot_fixes_this = 0
    rep_fixes_this = 0

    # Build a working copy
    new_itin = list(itinerary)

    def find_replacement(slot_idx, current_usage, exclude_id=None, ref_lat=None, ref_lng=None):
        """Find best replacement for this slot. Returns activity id or None."""
        required = SLOT_TAGS[slot_idx]
        candidates = []
        for act in acts_list:
            aid = act["id"]
            if aid == exclude_id:
                continue
            # Already used too much?
            cap = 5 if is_cafe(act) else 3
            if current_usage.get(aid, 0) >= cap:
                continue
            # Tag match
            if not tags_match(act.get("tags", []), slot_idx):
                continue
            # Open hours
            fri_hrs = act.get("open_hours", {}).get("fri", "")
            if not hours_overlap(fri_hrs, slot_idx):
                continue
            # Family filters
            if not family_ok(act):
                continue
            # Distance score
            dist = 0
            if ref_lat is not None and act.get("lat") and act.get("lng"):
                dist = haversine(ref_lat, ref_lng, act["lat"], act["lng"])
            candidates.append((dist, aid))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    # Pass 1: fix slot-type and open-hours violations
    for i in range(30):
        slot_idx = i % 6
        act_id = new_itin[i]
        act = acts_by_id.get(act_id)

        if act is None:
            # Unknown activity — must replace
            ref_lat = ref_lng = None
            if i > 0 and new_itin[i-1] in acts_by_id:
                prev = acts_by_id[new_itin[i-1]]
                ref_lat, ref_lng = prev.get("lat"), prev.get("lng")
            repl = find_replacement(slot_idx, Counter(new_itin[:i]), exclude_id=None,
                                    ref_lat=ref_lat, ref_lng=ref_lng)
            if repl:
                new_itin[i] = repl
                slot_fixes_this += 1
            else:
                no_replacement_found.append((fam_id, i, f"unknown act {act_id}"))
            continue

        fri_hrs = act.get("open_hours", {}).get("fri", "")
        tag_ok = tags_match(act.get("tags", []), slot_idx)
        hours_ok = hours_overlap(fri_hrs, slot_idx)

        if tag_ok and hours_ok:
            continue  # fine

        # Need replacement
        ref_lat = ref_lng = None
        if i > 0 and new_itin[i-1] in acts_by_id:
            prev = acts_by_id[new_itin[i-1]]
            ref_lat, ref_lng = prev.get("lat"), prev.get("lng")
        repl = find_replacement(slot_idx, Counter(new_itin[:i] + new_itin[i+1:]),
                                 exclude_id=act_id,
                                 ref_lat=ref_lat, ref_lng=ref_lng)
        if repl:
            reason = []
            if not tag_ok: reason.append("tag-mismatch")
            if not hours_ok: reason.append("hours-violation")
            print(f"  [{fam_id}] slot {i} (day {i//6+1} slot-{slot_idx}): {act_id} -> {repl} ({', '.join(reason)})")
            new_itin[i] = repl
            slot_fixes_this += 1
        else:
            reason = []
            if not tag_ok: reason.append("tag-mismatch")
            if not hours_ok: reason.append("hours-violation")
            no_replacement_found.append((fam_id, i, f"{act_id}: {', '.join(reason)}"))

    # Pass 2: enforce repetition cap on the result
    usage2 = Counter(new_itin)
    for act_id, cnt in list(usage2.items()):
        cap = 5 if (act_id in acts_by_id and is_cafe(acts_by_id[act_id])) else 3
        if cnt <= cap:
            continue
        # Need to remove (cnt - cap) occurrences, replacing from the back
        excess = cnt - cap
        replaced_count = 0
        for i in range(29, -1, -1):
            if new_itin[i] == act_id and replaced_count < excess:
                slot_idx = i % 6
                ref_lat = ref_lng = None
                if i > 0 and new_itin[i-1] in acts_by_id:
                    prev = acts_by_id[new_itin[i-1]]
                    ref_lat, ref_lng = prev.get("lat"), prev.get("lng")
                cur_usage = Counter(new_itin)
                cur_usage[act_id] -= (replaced_count + 1)  # pretend we already removed some
                repl = find_replacement(slot_idx, cur_usage, exclude_id=act_id,
                                        ref_lat=ref_lat, ref_lng=ref_lng)
                if repl:
                    print(f"  [{fam_id}] slot {i}: repetition cap -- {act_id}(x{cnt}) -> {repl}")
                    new_itin[i] = repl
                    replaced_count += 1
                    rep_fixes_this += 1
                else:
                    no_replacement_found.append((fam_id, i, f"rep cap exceeded for {act_id}"))

    total_slot_fixes += slot_fixes_this
    total_rep_fixes += rep_fixes_this

    out_rec = {"id": rec["id"], "family": rec["family"], "itinerary": new_itin}
    output_records.append(out_rec)

# ── write output ─────────────────────────────────────────────────────────────
out_path = r'C:\Users\sarta\rosea\hotel_agents\data\families_clean_retired_wine_couple__b0.jsonl'
with open(out_path, 'w', encoding='utf-8') as f:
    for rec in output_records:
        f.write(json.dumps(rec) + '\n')

print()
print("=" * 60)
print(f"Slot / open-hours fixes : {total_slot_fixes}")
print(f"Repetition fixes        : {total_rep_fixes}")
print(f"Total fixes             : {total_slot_fixes + total_rep_fixes}")
if no_replacement_found:
    print(f"Could not replace ({len(no_replacement_found)} slots):")
    for fam_id, idx, reason in no_replacement_found:
        print(f"  {fam_id} slot {idx}: {reason}")
else:
    print("No unresolved slots.")
print(f"Output written to: {out_path}")
