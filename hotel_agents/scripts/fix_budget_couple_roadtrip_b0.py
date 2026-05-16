"""
Audit and fix families_part_budget_couple_roadtrip__b0.jsonl
following sonnet_cleanup_brief.md rules.
"""

import json
import re
import math
from collections import Counter

# ── time parsing ─────────────────────────────────────────────────────────────

def parse_hour(s):
    """Parse a time string -> float hours (24h). Returns None on failure."""
    if not s:
        return None
    s = s.strip()
    # Handle midnight '00:00' or '24:00'
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)?$', s, re.IGNORECASE)
    if m:
        h, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if ampm and ampm.upper() == 'PM' and h != 12:
            h += 12
        elif ampm and ampm.upper() == 'AM' and h == 12:
            h = 0
        return h + mn / 60
    # "9 AM", "10 PM" etc.
    m2 = re.match(r'^(\d{1,2})\s*(AM|PM)$', s, re.IGNORECASE)
    if m2:
        h, ampm = int(m2.group(1)), m2.group(2)
        if ampm.upper() == 'PM' and h != 12:
            h += 12
        elif ampm.upper() == 'AM' and h == 12:
            h = 0
        return float(h)
    return None

def parse_single_range(raw):
    """Parse one range string like 'HH:MM-HH:MM' or '9 AM - 5 PM' -> (open, close) or None."""
    if not raw:
        return None
    raw = raw.strip()
    # Split on dash, but be careful with AM/PM
    # Use regex to find two time tokens separated by dash or 'to'
    # Handles: "17:00-22:00", "9:00 AM - 5:00 PM", "1:00 PM - 7:00 PM"
    m = re.match(
        r'^(\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)\s*[-–to]+\s*(\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)$',
        raw, re.IGNORECASE
    )
    if not m:
        return None
    o = parse_hour(m.group(1))
    c = parse_hour(m.group(2))
    if o is None or c is None:
        return None
    # Midnight close = 24 (e.g. "11:00-00:00")
    if c == 0:
        c = 24.0
    return (o, c)

def parse_open_hours_raw(raw):
    """
    Parse an open_hours string (possibly multi-window) into list of (open, close) tuples.
    Returns [] if closed, [(0, 24)] if '24h'.
    """
    if not raw:
        return []
    raw = raw.strip()
    low = raw.lower()
    if low in ('closed', 'none', ''):
        return []
    if low == '24h':
        return [(0, 24)]
    if 'sunrise' in low or 'sunset' in low or 'dawn' in low or 'varies' in low:
        return [(6, 21)]  # approximate all-day open
    # Split on comma or semicolon for multi-window
    segments = re.split(r'[;,]', raw)
    result = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        oc = parse_single_range(seg)
        if oc:
            result.append(oc)
    return result

def windows_overlap(windows, ws, we):
    """Do any of the (open,close) windows overlap with [ws, we)?"""
    if not windows:
        return True  # no data → assume open
    for (o, c) in windows:
        if o < we and c > ws:
            return True
    return False

# ── slot rules ───────────────────────────────────────────────────────────────

def slot_window(slot_in_day):
    return [(7,9),(8,10),(10,13),(12,17),(17,20),(20,23)][slot_in_day]

SLOT_TAGS = [
    {'cafe', 'coffee', 'bakery', 'breakfast'},                                     # 0
    {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'},                           # 1
    {'museum', 'tour', 'outdoor', 'hiking', 'campus', 'tech', 'art', 'science',   # 2
     'history', 'gardens', 'walking', 'shopping', 'viewpoint', 'landmark', 'architecture'},
    {'restaurant', 'lunch', 'casual', 'park', 'scenic', 'beach', 'outdoor',       # 3
     'hiking', 'tour', 'shopping', 'winery', 'wine', 'tasting'},
    {'restaurant', 'dinner', 'fine-dining', 'scenic', 'sunset', 'wine', 'casual'},# 4
    {'bar', 'cocktails', 'nightlife', 'lounge', 'dinner', 'fine-dining',          # 5
     'speakeasy'},
]

BUDGET_ORDER = ['shoestring', 'mid', 'upscale', 'luxury', 'premium']

def budget_ok(act_tier, family_tier):
    ai = BUDGET_ORDER.index(act_tier) if act_tier in BUDGET_ORDER else 99
    fi = BUDGET_ORDER.index(family_tier) if family_tier in BUDGET_ORDER else 0
    return ai <= fi

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ── load activity bank ────────────────────────────────────────────────────────

with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    raw_acts = json.load(f)

ACT = {a['id']: a for a in raw_acts}

def get_act_windows(act_id):
    """Return list of (open,close) tuples for the representative weekday (fri preferred)."""
    act = ACT.get(act_id)
    if not act:
        return []
    oh = act.get('open_hours', {})
    # Prefer fri, else thu, else sat, else first non-closed
    for day in ('fri', 'thu', 'sat', 'mon', 'tue', 'wed', 'sun'):
        v = oh.get(day, '')
        if v and v.lower() not in ('closed', ''):
            return parse_open_hours_raw(v)
    return []

def tags_ok(act_id, slot_in_day):
    act = ACT.get(act_id)
    if not act:
        return False
    act_tags = {t.lower() for t in act.get('tags', [])}
    return bool(act_tags & SLOT_TAGS[slot_in_day])

def hours_ok(act_id, slot_in_day):
    windows = get_act_windows(act_id)
    ws, we = slot_window(slot_in_day)
    return windows_overlap(windows, ws, we)

def is_cafe(act_id):
    act = ACT.get(act_id)
    if not act:
        return False
    return bool({t.lower() for t in act.get('tags', [])} & {'cafe', 'coffee', 'bakery'})

def find_replacement(slot_in_day, family, used_counts_running, prev_lat=None, prev_lng=None, exclude=None):
    """Find best replacement for the given slot."""
    exclude = exclude or set()
    required = SLOT_TAGS[slot_in_day]
    ws, we = slot_window(slot_in_day)
    candidates = []
    for act_id, act in ACT.items():
        if act_id in exclude:
            continue
        max_uses = 5 if is_cafe(act_id) else 3
        if used_counts_running.get(act_id, 0) >= max_uses:
            continue
        act_tags = {t.lower() for t in act.get('tags', [])}
        if not (act_tags & required):
            continue
        windows = get_act_windows(act_id)
        if not windows_overlap(windows, ws, we):
            continue
        if family.get('kid_ages') != 'none' and not act.get('kid_ok', True):
            continue
        if family.get('mobility') != 'full' and not act.get('mobility_ok', True):
            continue
        if not budget_ok(act.get('budget_tier', 'shoestring'), family.get('budget_tier', 'shoestring')):
            continue
        dist = 0
        if prev_lat and prev_lng and act.get('lat') and act.get('lng'):
            dist = haversine(prev_lat, prev_lng, act['lat'], act['lng'])
        candidates.append((dist, act_id))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]

# ── main audit loop ───────────────────────────────────────────────────────────

total_slot_replacements = 0
total_repetition_fixes = 0
no_replacement_found = []

input_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_part_budget_couple_roadtrip__b0.jsonl'
output_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_budget_couple_roadtrip__b0.jsonl'

with open(input_path) as f:
    records = [json.loads(line) for line in f if line.strip()]

output_records = []

for rec in records:
    fam_id = rec['id']
    family = rec['family']
    itinerary = list(rec['itinerary'])

    used_counts_running = Counter()
    fixed_itinerary = []

    for slot_idx, act_id in enumerate(itinerary):
        slot_in_day = slot_idx % 6
        prev_lat = prev_lng = None
        if fixed_itinerary:
            prev_act = ACT.get(fixed_itinerary[-1])
            if prev_act:
                prev_lat = prev_act.get('lat')
                prev_lng = prev_act.get('lng')

        act = ACT.get(act_id)
        needs_replace = False
        reason = None

        if not act:
            needs_replace = True
            reason = 'unknown_activity'
        else:
            if not tags_ok(act_id, slot_in_day):
                needs_replace = True
                reason = 'slot_type'
            elif not hours_ok(act_id, slot_in_day):
                needs_replace = True
                reason = 'open_hours'

        if not needs_replace:
            max_uses = 5 if is_cafe(act_id) else 3
            if used_counts_running.get(act_id, 0) >= max_uses:
                needs_replace = True
                reason = 'repetition'

        if needs_replace:
            day_start = (slot_idx // 6) * 6
            same_day_acts = set(fixed_itinerary[day_start:])
            replacement = find_replacement(
                slot_in_day, family, used_counts_running,
                prev_lat=prev_lat, prev_lng=prev_lng,
                exclude=same_day_acts
            )
            if replacement:
                if reason == 'repetition':
                    total_repetition_fixes += 1
                else:
                    total_slot_replacements += 1
                used_counts_running[replacement] = used_counts_running.get(replacement, 0) + 1
                fixed_itinerary.append(replacement)
                print(f"  [{fam_id}] slot {slot_idx} (day{slot_idx//6+1}/pos{slot_in_day}): {act_id!r} -> {replacement!r} [{reason}]")
            else:
                no_replacement_found.append((fam_id, slot_idx, act_id, reason))
                used_counts_running[act_id] = used_counts_running.get(act_id, 0) + 1
                fixed_itinerary.append(act_id)
                print(f"  [{fam_id}] slot {slot_idx}: NO replacement for {act_id!r} [{reason}] -- kept")
        else:
            used_counts_running[act_id] = used_counts_running.get(act_id, 0) + 1
            fixed_itinerary.append(act_id)

    output_records.append({
        'id': rec['id'],
        'family': rec['family'],
        'itinerary': fixed_itinerary,
    })

with open(output_path, 'w') as f:
    for rec in output_records:
        f.write(json.dumps(rec) + '\n')

print()
print("=" * 60)
print(f"Slot-type / open-hours replacements: {total_slot_replacements}")
print(f"Repetition-cap fixes:                {total_repetition_fixes}")
print(f"Total replacements:                  {total_slot_replacements + total_repetition_fixes}")
print(f"Slots with no valid replacement:     {len(no_replacement_found)}")
if no_replacement_found:
    for fam_id, slot_idx, act_id, reason in no_replacement_found:
        print(f"  {fam_id} slot {slot_idx}: {act_id} [{reason}]")
print(f"Output written to: {output_path}")
