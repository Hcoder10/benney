import json
import re
from copy import deepcopy
import datetime

# ---- Load data ----
with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    activities = json.load(f)
act_map = {a['id']: a for a in activities}

families = []
with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_college_reunion_crew__b0.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            families.append(json.loads(line))

# ---- Slot rules ----
SLOT_TAGS = {
    0: {'cafe', 'coffee', 'bakery', 'breakfast'},
    1: {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'},
    2: {'museum', 'tour', 'outdoor', 'hiking', 'campus', 'tech', 'art', 'science', 'history',
        'gardens', 'walking', 'shopping', 'viewpoint', 'landmark', 'architecture'},
    3: {'restaurant', 'lunch', 'casual', 'park', 'scenic', 'beach', 'outdoor', 'hiking',
        'tour', 'shopping', 'winery', 'wine', 'tasting'},
    4: {'restaurant', 'dinner', 'fine-dining', 'scenic', 'sunset', 'wine', 'casual'},
    5: {'bar', 'cocktails', 'nightlife', 'lounge', 'dinner', 'fine-dining', 'speakeasy'},
}

# Slot time windows as (start_hour, end_hour)
SLOT_WINDOWS = {
    0: (7, 9),
    1: (8, 10),
    2: (10, 13),
    3: (12, 17),
    4: (17, 20),
    5: (20, 23),
}

# ---- Parse open_hours string to (open_h, close_h) in 24h floats ----
def to_24h(t):
    t = t.strip()
    if 'PM' in t.upper() or 'AM' in t.upper():
        t2 = t.upper().replace('.', '')
        for fmt in ['%I:%M %p', '%I %p']:
            try:
                dt = datetime.datetime.strptime(t2, fmt)
                return dt.hour + dt.minute / 60
            except Exception:
                pass
        return None
    else:
        parts2 = t.split(':')
        if len(parts2) == 2:
            try:
                h = int(parts2[0])
                m = int(parts2[1])
                # Midnight expressed as 00:00 in a closing time means 24:00
                if h == 0 and m == 0:
                    return 24.0
                return h + m / 60
            except Exception:
                return None
        return None


def parse_hour_range(raw):
    if not raw:
        return None
    s = raw.strip()
    if s.lower() == 'closed':
        return None
    if '24h' in s.lower():
        return (0, 24)
    if 'sunrise' in s.lower() or 'sunset' in s.lower():
        return (6, 20)  # approximate
    windows = s.split(',')
    min_open = None
    max_close = None
    for w in windows:
        w = w.strip()
        parts = re.split(r'\s*-\s*', w, maxsplit=1)
        if len(parts) != 2:
            continue
        o = to_24h(parts[0])
        c = to_24h(parts[1])
        if o is None or c is None:
            continue
        if min_open is None or o < min_open:
            min_open = o
        if max_close is None or c > max_close:
            max_close = c
    if min_open is None:
        return None
    return (min_open, max_close)


def get_open_hours_raw(act):
    oh = act['open_hours']
    for day in ['fri', 'all', 'thu', 'wed', 'tue', 'sat', 'sun', 'mon']:
        v = oh.get(day, '')
        if v and v.lower() != 'closed':
            return v
    return ''


def hours_overlap(act, slot_idx):
    si = slot_idx % 6
    win = SLOT_WINDOWS[si]
    raw = get_open_hours_raw(act)
    parsed = parse_hour_range(raw)
    if parsed is None:
        return False
    open_h, close_h = parsed
    slot_start, slot_end = win
    return open_h < slot_end and close_h > slot_start


def normalize_tag(t):
    """Normalize tag: lowercase, replace spaces with hyphens for matching."""
    return t.lower().replace(' ', '-')


def tags_ok(act, slot_idx):
    si = slot_idx % 6
    required = SLOT_TAGS[si]
    # Normalize activity tags: match both 'fine dining' and 'fine-dining'
    act_tags = set(normalize_tag(t) for t in act.get('tags', []))
    req_normalized = set(normalize_tag(r) for r in required)
    return bool(req_normalized & act_tags)


def budget_ok(act, family):
    tier_order = ['shoestring', 'mid', 'premium', 'luxury']
    fam_tier = family.get('budget_tier', 'luxury')
    act_tier = act.get('budget_tier', 'luxury')
    try:
        return tier_order.index(act_tier) <= tier_order.index(fam_tier)
    except Exception:
        return True


def kid_ok(act, family):
    if family.get('kid_ages', 'none') != 'none':
        return act.get('kid_ok', True)
    return True


def mob_ok(act, family):
    if family.get('mobility', 'full') != 'full':
        return act.get('mobility_ok', True)
    return True


def family_ok(act, family):
    return budget_ok(act, family) and kid_ok(act, family) and mob_ok(act, family)


CAFE_TAGS = {'cafe', 'coffee', 'bakery'}


def is_cafe(act):
    tags = set(t.lower() for t in act.get('tags', []))
    return bool(tags & CAFE_TAGS)


def max_repeats(act):
    return 5 if is_cafe(act) else 3


def find_replacement(slot_idx, family, used_counts, prefer_lat=None, prefer_lng=None, exclude_id=None):
    candidates = []
    for a in activities:
        if a['id'] == exclude_id:
            continue
        count = used_counts.get(a['id'], 0)
        if count >= max_repeats(a):
            continue
        if not tags_ok(a, slot_idx):
            continue
        if not hours_overlap(a, slot_idx):
            continue
        if not family_ok(a, family):
            continue
        dist = 0
        if prefer_lat is not None and prefer_lng is not None:
            dist = ((a['lat'] - prefer_lat) ** 2 + (a['lng'] - prefer_lng) ** 2) ** 0.5
        candidates.append((dist, a['id'], a))
    candidates.sort(key=lambda x: (x[0], x[1]))
    if candidates:
        return candidates[0][2]['id']
    return None


# ---- Typo/missing ID corrections ----
TYPO_MAP = {
    'ferr_building_marketplace': 'ferry_building_marketplace',
    'ferrry_building_marketplace': 'ferry_building_marketplace',
    # IDs not in bank: del_norte_coast_redwoods_scenic, levi_stadium_tour,
    # mission_district_burrito_crawl, monterey_bay_aquarium_casual,
    # monterey_bay_scenic_afternoon, pascadero_creek_park
    # These will be replaced during the slot-fix pass.
}

# ---- Main audit loop ----
slot_replacements = 0
repetition_fixes = 0
missing_id_fixes = 0
no_replacement_found = []
output_families = []

for fam_data in families:
    fam = deepcopy(fam_data)
    family = fam['family']
    itinerary = fam['itinerary']

    new_itinerary = []
    used_counts = {}

    for i, orig_aid in enumerate(itinerary):
        # Step 1: resolve known typos
        aid = TYPO_MAP.get(orig_aid, orig_aid)
        was_typo = aid != orig_aid

        # Step 2: if ID missing from bank, find replacement
        if aid not in act_map:
            prev_lat = act_map[new_itinerary[-1]]['lat'] if new_itinerary and new_itinerary[-1] in act_map else None
            prev_lng = act_map[new_itinerary[-1]]['lng'] if new_itinerary and new_itinerary[-1] in act_map else None
            rep = find_replacement(i, family, used_counts, prev_lat, prev_lng)
            if rep:
                missing_id_fixes += 1
                slot_replacements += 1
                aid = rep
            else:
                no_replacement_found.append((fam['id'], i, 'missing:' + orig_aid))
                new_itinerary.append(orig_aid)
                used_counts[orig_aid] = used_counts.get(orig_aid, 0) + 1
                continue

        act = act_map[aid]

        # Step 3: check repetition cap
        cur_count = used_counts.get(aid, 0)
        cap = max_repeats(act)
        if cur_count >= cap:
            prev_lat = act_map[new_itinerary[-1]]['lat'] if new_itinerary and new_itinerary[-1] in act_map else None
            prev_lng = act_map[new_itinerary[-1]]['lng'] if new_itinerary and new_itinerary[-1] in act_map else None
            rep = find_replacement(i, family, used_counts, prev_lat, prev_lng, exclude_id=aid)
            if rep:
                repetition_fixes += 1
                aid = rep
                act = act_map[aid]
                cur_count = used_counts.get(aid, 0)
            else:
                no_replacement_found.append((fam['id'], i, 'rep_cap:' + aid))

        # Step 4: check slot-type violation
        if not tags_ok(act, i):
            prev_lat = act_map[new_itinerary[-1]]['lat'] if new_itinerary and new_itinerary[-1] in act_map else None
            prev_lng = act_map[new_itinerary[-1]]['lng'] if new_itinerary and new_itinerary[-1] in act_map else None
            rep = find_replacement(i, family, used_counts, prev_lat, prev_lng, exclude_id=aid)
            if rep:
                slot_replacements += 1
                aid = rep
                act = act_map[aid]
            else:
                no_replacement_found.append((fam['id'], i, 'slot_type:' + aid))

        # Step 5: check open-hours violation
        if not hours_overlap(act, i):
            prev_lat = act_map[new_itinerary[-1]]['lat'] if new_itinerary and new_itinerary[-1] in act_map else None
            prev_lng = act_map[new_itinerary[-1]]['lng'] if new_itinerary and new_itinerary[-1] in act_map else None
            rep = find_replacement(i, family, used_counts, prev_lat, prev_lng, exclude_id=aid)
            if rep:
                slot_replacements += 1
                aid = rep
                act = act_map[aid]
            else:
                no_replacement_found.append((fam['id'], i, 'open_hours:' + aid))

        new_itinerary.append(aid)
        used_counts[aid] = used_counts.get(aid, 0) + 1

    fam['itinerary'] = new_itinerary
    output_families.append(fam)

# ---- Write output ----
out_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_college_reunion_crew__b0.jsonl'
with open(out_path, 'w') as f:
    for fam_out in output_families:
        f.write(json.dumps(fam_out) + '\n')

print(f'Slot replacements (type + hours + missing IDs): {slot_replacements}')
print(f'  Of which missing-ID fixes: {missing_id_fixes}')
print(f'Repetition fixes: {repetition_fixes}')
print(f'No replacement found: {len(no_replacement_found)}')
for nr in no_replacement_found:
    print(f'  {nr}')
print('Output written to families_clean_college_reunion_crew__b0.jsonl')
