"""
Audit + fix families_part_sports_fan_game_weekend__b0.jsonl
Writes families_clean_sports_fan_game_weekend__b0.jsonl

Pass 1: slot-type + open-hours violations (strict usage cap)
Pass 2: repetition cap
Pass 3: night-slot gap fill — for any remaining type-5 violations,
        pick the best-available night candidate even if it nudges past
        the cap, preferring the least-used eligible option.
"""

import json
import re
import math
from collections import defaultdict

# ---- time helpers ----

def parse_time_24h(s):
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
    if not h_str:
        return None
    h_str = h_str.strip()
    if h_str.lower() in ('closed', 'n/a', ''):
        return None
    for sep in (' - ', ' to '):
        if sep in h_str:
            parts = h_str.split(sep, 1)
            o = parse_time_24h(parts[0])
            c = parse_time_24h(parts[1])
            if o is not None and c is not None:
                return (o, c)
    m = re.match(r'^(.+?)-(.+)$', h_str)
    if m:
        o = parse_time_24h(m.group(1).strip())
        c = parse_time_24h(m.group(2).strip())
        if o is not None and c is not None:
            return (o, c)
    m2 = re.match(r'^(\d{1,2}:\d{2})', h_str)
    if m2 and ('sunset' in h_str.lower() or 'open' in h_str.lower()):
        o = parse_time_24h(m2.group(1))
        if o is not None:
            return (o, 20.0)
    return None


def hours_overlap(h_str, slot_open, slot_close):
    parsed = parse_hours(h_str)
    if parsed is None:
        return False
    ao, ac = parsed
    return max(ao, slot_open) < min(ac, slot_close)


# ---- slot rules ----

SLOT_WINDOWS = [
    (7, 9),
    (8, 10),
    (10, 13),
    (12, 17),
    (17, 20),
    (20, 23),
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

TAG_NORM = {
    'fine dining': 'fine-dining',
    'fine_dining': 'fine-dining',
    'bars': 'bar',
    'outdoors': 'outdoor',
}


def normalise_tags(tags):
    return [TAG_NORM.get(t, t) for t in tags]


def budget_ok(activity, family_tier):
    a_tier = BUDGET_ORDER.get(activity.get('budget_tier', 'shoestring'), 0)
    f_tier = BUDGET_ORDER.get(family_tier, 1)
    return a_tier <= f_tier


def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2)
    return R * 2 * math.asin(math.sqrt(a))


# ---- load data ----

with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    activities_list = json.load(f)

for a in activities_list:
    a['tags'] = normalise_tags(a.get('tags', []))

act = {a['id']: a for a in activities_list}

with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_sports_fan_game_weekend__b0.jsonl') as f:
    families = [json.loads(line) for line in f if line.strip()]


def get_candidates(slot_idx, family, used_counts, anchor_lat=None, anchor_lng=None,
                   ignore_cap=False, exclude_id=None):
    sw = SLOT_WINDOWS[slot_idx]
    req = SLOT_TAGS[slot_idx]
    candidates = []
    for aid, a in act.items():
        if aid == exclude_id:
            continue
        atags = set(a.get('tags', []))
        if not (atags & req):
            continue
        oh = a.get('open_hours', {}).get('fri', '')
        if not hours_overlap(oh, sw[0], sw[1]):
            continue
        if family.get('kid_ages', 'none') != 'none' and not a.get('kid_ok', True):
            continue
        if family.get('mobility', 'full') != 'full' and not a.get('mobility_ok', True):
            continue
        if not budget_ok(a, family.get('budget_tier', 'mid')):
            continue
        if not ignore_cap:
            is_cafe = bool({'cafe', 'coffee'} & atags)
            cap = 5 if is_cafe else 3
            if used_counts.get(aid, 0) >= cap:
                continue
        candidates.append(aid)

    if anchor_lat is not None and anchor_lng is not None:
        candidates.sort(key=lambda aid: haversine(
            anchor_lat, anchor_lng,
            act[aid].get('lat', anchor_lat),
            act[aid].get('lng', anchor_lng)
        ))
    else:
        # Secondary sort: least used first
        candidates.sort(key=lambda aid: used_counts.get(aid, 0))
    return candidates


# ---- main audit loop ----

total_slot_replacements = 0
total_repetition_fixes = 0
total_gap_fills = 0
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
    gap_fills = 0

    # --- pass 1: slot-type + open-hours violations ---
    for i in range(len(itinerary)):
        aid = itinerary[i]
        slot_idx = i % 6
        sw = SLOT_WINDOWS[slot_idx]
        req = SLOT_TAGS[slot_idx]

        a = act.get(aid)
        if a is None:
            anchor_lat = anchor_lng = None
            if i > 0:
                prev = act.get(itinerary[i-1])
                if prev:
                    anchor_lat = prev.get('lat')
                    anchor_lng = prev.get('lng')
            used_counts[aid] = max(0, used_counts[aid] - 1)
            cands = get_candidates(slot_idx, family, used_counts, anchor_lat, anchor_lng)
            if cands:
                new_aid = cands[0]
                itinerary[i] = new_aid
                used_counts[new_aid] += 1
                slot_replacements += 1
                print("  [" + fam_id + "] slot " + str(i) + ": MISSING " + aid + " -> " + new_aid)
            else:
                no_replacement_found.append((fam_id, i, aid, 'missing'))
                print("  [" + fam_id + "] slot " + str(i) + ": MISSING " + aid + " NO REPLACEMENT")
            continue

        atags = set(a.get('tags', []))
        has_tag = bool(atags & req)
        oh = a.get('open_hours', {}).get('fri', '')
        overlap = hours_overlap(oh, sw[0], sw[1])

        if has_tag and overlap:
            continue

        anchor_lat = anchor_lng = None
        if i > 0:
            prev = act.get(itinerary[i-1])
            if prev:
                anchor_lat = prev.get('lat')
                anchor_lng = prev.get('lng')

        used_counts[aid] -= 1
        cands = get_candidates(slot_idx, family, used_counts, anchor_lat, anchor_lng,
                               exclude_id=aid)
        used_counts[aid] += 1

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
            print("  [" + fam_id + "] slot " + str(i) + " (" + '+'.join(reason) + "): " + aid + " -> " + new_aid)
        else:
            # Don't record as no-replacement yet — pass 3 will handle night-slot gaps
            print("  [" + fam_id + "] slot " + str(i) + " (" + '+'.join(reason) + "): " + aid + " deferred to pass3")

    # --- pass 2: repetition cap ---
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

            temp_counts = defaultdict(int, used_counts2)
            temp_counts[aid] -= 1
            cands = get_candidates(slot_idx, family, temp_counts, anchor_lat, anchor_lng,
                                   exclude_id=aid)
            if cands:
                new_aid = cands[0]
                used_counts2[aid] -= 1
                itinerary[i] = new_aid
                used_counts2[new_aid] += 1
                repetition_fixes += 1
                print("  [" + fam_id + "] slot " + str(i) + " REPETITION cap(" + str(cap) + "): " + aid + " -> " + new_aid)
            else:
                print("  [" + fam_id + "] slot " + str(i) + " REPETITION cap(" + str(cap) + "): " + aid + " NO REPLACEMENT")

    # --- pass 3: gap-fill remaining violations with ignore_cap ---
    used_counts3 = defaultdict(int)
    for aid in itinerary:
        used_counts3[aid] += 1

    for i in range(len(itinerary)):
        aid = itinerary[i]
        slot_idx = i % 6
        sw = SLOT_WINDOWS[slot_idx]
        req = SLOT_TAGS[slot_idx]

        a = act.get(aid)
        if a is None:
            continue
        atags = set(a.get('tags', []))
        has_tag = bool(atags & req)
        oh = a.get('open_hours', {}).get('fri', '')
        overlap = hours_overlap(oh, sw[0], sw[1])
        if has_tag and overlap:
            continue

        # Still a violation — try with ignore_cap
        anchor_lat = anchor_lng = None
        if i > 0:
            prev = act.get(itinerary[i-1])
            if prev:
                anchor_lat = prev.get('lat')
                anchor_lng = prev.get('lng')

        used_counts3[aid] -= 1
        cands = get_candidates(slot_idx, family, used_counts3, anchor_lat, anchor_lng,
                               ignore_cap=True, exclude_id=aid)
        # Sort by least-used to prefer diversity even with cap lifted
        cands.sort(key=lambda c: used_counts3.get(c, 0))
        used_counts3[aid] += 1

        reason = []
        if not has_tag: reason.append('BAD_TAG')
        if not overlap: reason.append('CLOSED')

        if cands:
            new_aid = cands[0]
            used_counts3[aid] -= 1
            itinerary[i] = new_aid
            used_counts3[new_aid] += 1
            gap_fills += 1
            print("  [" + fam_id + "] slot " + str(i) + " GAP-FILL(" + '+'.join(reason) + "): " + aid + " -> " + new_aid)
        else:
            no_replacement_found.append((fam_id, i, aid, '+'.join(reason)))
            print("  [" + fam_id + "] slot " + str(i) + " (" + '+'.join(reason) + "): " + aid + " NO REPLACEMENT (bank gap)")

    total_slot_replacements += slot_replacements
    total_repetition_fixes += repetition_fixes
    total_gap_fills += gap_fills
    cleaned_families.append({'id': fam['id'], 'family': fam['family'], 'itinerary': itinerary})
    print("[" + fam_id + "] done: " + str(slot_replacements) + " slot fixes, " +
          str(repetition_fixes) + " rep fixes, " + str(gap_fills) + " gap fills")

# ---- write output ----

out_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_sports_fan_game_weekend__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in cleaned_families:
        f.write(json.dumps(rec) + '\n')

print()
print('=== SUMMARY ===')
print('Families processed     :', len(cleaned_families))
print('Slot replacements      :', total_slot_replacements)
print('Repetition fixes       :', total_repetition_fixes)
print('Gap fills (cap lifted) :', total_gap_fills)
print('Total fixes            :', total_slot_replacements + total_repetition_fixes + total_gap_fills)
if no_replacement_found:
    print('No-replacement (true bank gap):')
    for item in no_replacement_found:
        print(' ', item)
else:
    print('No-replacement cases   : 0')
print('Output written to      :', out_path)
