"""
Audit + fix families_part_sports_fan_game_weekend__b0.jsonl
Writes families_clean_sports_fan_game_weekend__b0.jsonl
"""

import json
import re
import math
from collections import defaultdict

# ---- time helpers ----

def parse_time_24h(s):
    """Return float hours from strings like 7:30, 07:30, 17:00, 7 AM, 5:30 PM."""
    s = s.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$', s, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3).upper()
        if ampm == 'PM' and h != 12:
            h += 12
        if ampm == 'AM' and h == 12:
            h = 0
        return h + mins / 60
    return None


def parse_hours(h_str):
    """Return (open_float, close_float) or None if closed/unparseable."""
    if not h_str:
        return None
    h_str = h_str.strip()
    if h_str.lower() in ('closed', 'n/a', ''):
        return None

    # Handle 'HH:MM - HH:MM', 'HH:MM-HH:MM', '7 AM - 9 PM', etc.
    for sep in (' - ', ' to '):
        if sep in h_str:
            parts = h_str.split(sep, 1)
            o = parse_time_24h(parts[0])
            c = parse_time_24h(parts[1])
            if o is not None and c is not None:
                return (o, c)

    # Try plain dash but avoid splitting '09:00' as '09' + '00'
    # Only split on dash that is surrounded by time tokens
    m = re.match(r'^(.+?)-(.+)$', h_str)
    if m:
        o = parse_time_24h(m.group(1).strip())
        c = parse_time_24h(m.group(2).strip())
        if o is not None and c is not None:
            return (o, c)

    # Handle 'HH:MM-sunset' or 'HH:MM-open'
    m2 = re.match(r'^(\d{1,2}:\d{2})', h_str)
    if m2 and ('sunset' in h_str.lower() or 'open' in h_str.lower()):
        o = parse_time_24h(m2.group(1))
        if o is not None:
            return (o, 20.0)

    return None


def hours_overlap(h_str, slot_open, slot_close):
    """True if activity is open during ANY part of [slot_open, slot_close)."""
    parsed = parse_hours(h_str)
    if parsed is None:
        return False
    ao, ac = parsed
    return max(ao, slot_open) < min(ac, slot_close)


# ---- slot rules ----

SLOT_WINDOWS = [
    (7, 9),    # 0 early-morning
    (8, 10),   # 1 breakfast
    (10, 13),  # 2 late-morning
    (12, 17),  # 3 lunch+afternoon
    (17, 20),  # 4 evening
    (20, 23),  # 5 night
]

SLOT_TAGS = [
    {'cafe', 'coffee', 'bakery', 'breakfast'},
    {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'},
    {'museum', 'tour', 'outdoor', 'hiking', 'campus', 'tech', 'art', 'science',
     'history', 'gardens', 'walking', 'shopping', 'viewpoint', 'landmark', 'architecture'},
    {'restaurant', 'lunch', 'casual', 'park', 'scenic', 'beach', 'outdoor',
     'hiking', 'tour', 'shopping', 'winery', 'wine', 'tasting'},
    {'restaurant', 'dinner', 'fine-dining', 'scenic', 'sunset', 'wine', 'casual'},
    {'bar', 'cocktails', 'nightlife', 'lounge', 'dinner', 'fine-dining', 'speakeasy'},
]

BUDGET_ORDER = {'shoestring': 0, 'mid': 1, 'premium': 2, 'luxury': 3}


def budget_ok(activity, family_tier):
    a_tier = BUDGET_ORDER.get(activity.get('budget_tier', 'shoestring'), 0)
    f_tier = BUDGET_ORDER.get(family_tier, 1)
    return a_tier <= f_tier


def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# ---- tag normalisation ----
# Map non-canonical tags to canonical equivalents that appear in SLOT_TAGS

TAG_NORM = {
    'fine dining': 'fine-dining',
    'fine_dining': 'fine-dining',
    'bars': 'bar',
    'outdoors': 'outdoor',
}


def normalise_tags(tags):
    out = []
    for t in tags:
        out.append(TAG_NORM.get(t, t))
    return out


# ---- load data ----

with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    activities_list = json.load(f)

# Normalise tags in place
for a in activities_list:
    a['tags'] = normalise_tags(a.get('tags', []))

act = {a['id']: a for a in activities_list}

with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_sports_fan_game_weekend__b0.jsonl') as f:
    families = [json.loads(line) for line in f if line.strip()]


# ---- replacement candidate builder ----

def get_candidates(slot_idx, family, used_counts, anchor_lat=None, anchor_lng=None):
    """
    Return list of activity IDs sorted by distance to anchor (if given),
    that satisfy slot tags, open hours, kid/mobility/budget constraints,
    and haven't hit the usage cap.
    """
    sw = SLOT_WINDOWS[slot_idx]
    req = SLOT_TAGS[slot_idx]
    candidates = []
    for aid, a in act.items():
        # tag check
        atags = set(a.get('tags', []))
        if not (atags & req):
            continue
        # open hours check
        oh = a.get('open_hours', {}).get('fri', '')
        if not hours_overlap(oh, sw[0], sw[1]):
            continue
        # kid check
        if family.get('kid_ages', 'none') != 'none' and not a.get('kid_ok', True):
            continue
        # mobility check
        if family.get('mobility', 'full') != 'full' and not a.get('mobility_ok', True):
            continue
        # budget check
        if not budget_ok(a, family.get('budget_tier', 'mid')):
            continue
        # usage cap
        is_cafe = bool({'cafe', 'coffee'} & atags)
        cap = 5 if is_cafe else 3
        if used_counts.get(aid, 0) >= cap:
            continue
        candidates.append(aid)

    # Sort by distance to anchor
    if anchor_lat is not None and anchor_lng is not None:
        candidates.sort(key=lambda aid: haversine(
            anchor_lat, anchor_lng,
            act[aid].get('lat', anchor_lat),
            act[aid].get('lng', anchor_lng)
        ))
    return candidates


# ---- main audit loop ----

total_slot_replacements = 0
total_repetition_fixes = 0
no_replacement_found = []
cleaned_families = []

for fam in families:
    fam_id = fam['id']
    family = fam['family']
    itinerary = list(fam['itinerary'])

    used_counts = defaultdict(int)
    for aid in itinerary:
        used_counts[aid] += 1

    slot_replacements = 0
    repetition_fixes = 0

    # --- pass 1: slot-type + open-hours violations ---
    for i in range(len(itinerary)):
        aid = itinerary[i]
        slot_idx = i % 6
        sw = SLOT_WINDOWS[slot_idx]
        req = SLOT_TAGS[slot_idx]

        a = act.get(aid)
        if a is None:
            # Activity missing from bank — must replace
            anchor = None
            if i > 0:
                prev = act.get(itinerary[i-1])
                anchor = (prev.get('lat'), prev.get('lng')) if prev else (None, None)
            else:
                anchor = (None, None)
            cands = get_candidates(slot_idx, family, used_counts, *anchor)
            # exclude current (missing) from counts
            used_counts[aid] = max(0, used_counts[aid] - 1)
            if cands:
                new_aid = cands[0]
                itinerary[i] = new_aid
                used_counts[new_aid] += 1
                slot_replacements += 1
                print(f"  [{fam_id}] slot {i}: MISSING {aid} -> {new_aid}")
            else:
                no_replacement_found.append((fam_id, i, aid, 'missing'))
                print(f"  [{fam_id}] slot {i}: MISSING {aid} NO REPLACEMENT")
            continue

        atags = set(a.get('tags', []))
        has_tag = bool(atags & req)
        oh = a.get('open_hours', {}).get('fri', '')
        overlap = hours_overlap(oh, sw[0], sw[1])

        if has_tag and overlap:
            continue  # OK

        # Need replacement
        anchor_lat = anchor_lng = None
        if i > 0:
            prev = act.get(itinerary[i-1])
            if prev:
                anchor_lat = prev.get('lat')
                anchor_lng = prev.get('lng')

        used_counts[aid] -= 1  # don't count self while searching
        cands = [c for c in get_candidates(slot_idx, family, used_counts, anchor_lat, anchor_lng)
                 if c != aid]
        used_counts[aid] += 1  # restore

        reason = []
        if not has_tag:
            reason.append('BAD_TAG')
        if not overlap:
            reason.append('CLOSED')

        if cands:
            new_aid = cands[0]
            used_counts[aid] -= 1
            itinerary[i] = new_aid
            used_counts[new_aid] += 1
            slot_replacements += 1
            print(f"  [{fam_id}] slot {i} ({'+'.join(reason)}): {aid} -> {new_aid}")
        else:
            no_replacement_found.append((fam_id, i, aid, '+'.join(reason)))
            print(f"  [{fam_id}] slot {i} ({'+'.join(reason)}): {aid} NO REPLACEMENT")

    # --- pass 2: repetition cap ---
    # Rebuild count from cleaned itinerary
    used_counts2 = defaultdict(int)
    for aid in itinerary:
        used_counts2[aid] += 1

    for i in range(len(itinerary)):
        aid = itinerary[i]
        a = act.get(aid)
        atags = set(a.get('tags', [])) if a else set()
        is_cafe = bool({'cafe', 'coffee'} & atags)
        cap = 5 if is_cafe else 3

        if used_counts2[aid] > cap:
            slot_idx = i % 6
            anchor_lat = anchor_lng = None
            if i > 0:
                prev = act.get(itinerary[i-1])
                if prev:
                    anchor_lat = prev.get('lat')
                    anchor_lng = prev.get('lng')

            # Build used counts excluding this slot
            temp_counts = defaultdict(int, used_counts2)
            temp_counts[aid] -= 1
            cands = [c for c in get_candidates(slot_idx, family, temp_counts, anchor_lat, anchor_lng)
                     if c != aid]
            if cands:
                new_aid = cands[0]
                used_counts2[aid] -= 1
                itinerary[i] = new_aid
                used_counts2[new_aid] += 1
                repetition_fixes += 1
                print(f"  [{fam_id}] slot {i} REPETITION cap({cap}): {aid} -> {new_aid}")
            else:
                print(f"  [{fam_id}] slot {i} REPETITION cap({cap}): {aid} NO REPLACEMENT")

    total_slot_replacements += slot_replacements
    total_repetition_fixes += repetition_fixes
    cleaned_families.append({'id': fam['id'], 'family': fam['family'], 'itinerary': itinerary})
    print(f"[{fam_id}] done: {slot_replacements} slot fixes, {repetition_fixes} repetition fixes")

# ---- write output ----

out_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_sports_fan_game_weekend__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in cleaned_families:
        f.write(json.dumps(rec) + '\n')

print()
print('=== SUMMARY ===')
print(f'Families processed     : {len(cleaned_families)}')
print(f'Slot replacements      : {total_slot_replacements}')
print(f'Repetition fixes       : {total_repetition_fixes}')
print(f'Total fixes            : {total_slot_replacements + total_repetition_fixes}')
if no_replacement_found:
    print('No-replacement cases:')
    for item in no_replacement_found:
        print(f'  {item}')
else:
    print('No-replacement cases   : 0')
print(f'Output written to      : {out_path}')
