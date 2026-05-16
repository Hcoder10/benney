import json, re, math, sys
from collections import defaultdict

# ---- Load activity bank ----
with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json', encoding='utf-8') as f:
    raw_acts = json.load(f)

# Build lookup by id
act_by_id = {a['id']: a for a in raw_acts}

# ---- Slot rules ----
SLOT_WINDOWS = [
    (7, 9),    # 0 early-morning
    (8, 10),   # 1 breakfast
    (10, 13),  # 2 late-morning
    (12, 17),  # 3 lunch+afternoon
    (17, 20),  # 4 evening
    (20, 23),  # 5 night
]
SLOT_TAGS = [
    {'cafe', 'coffee', 'bakery', 'breakfast'},                         # 0
    {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'},               # 1
    {'museum', 'tour', 'outdoor', 'hiking', 'campus', 'tech', 'art',
     'science', 'history', 'gardens', 'walking', 'shopping',
     'viewpoint', 'landmark', 'architecture'},                          # 2
    {'restaurant', 'lunch', 'casual', 'park', 'scenic', 'beach',
     'outdoor', 'hiking', 'tour', 'shopping', 'winery', 'wine', 'tasting'},  # 3
    {'restaurant', 'dinner', 'fine-dining', 'scenic', 'sunset',
     'wine', 'casual'},                                                # 4
    {'bar', 'cocktails', 'nightlife', 'lounge', 'dinner',
     'fine-dining', 'speakeasy'},                                       # 5
]

def parse_hour(s):
    s = s.strip()
    m = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)?', s, re.IGNORECASE)
    if not m:
        return None
    h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm:
        ampm = ampm.upper()
        if ampm == 'PM' and h != 12:
            h += 12
        if ampm == 'AM' and h == 12:
            h = 0
    return h + mi / 60

def parse_sessions(hours_str):
    """Return list of (open_h, close_h) tuples — supports multi-session strings
    like '11:30-14:00, 17:00-22:30' or '5:30 PM - 10:00 PM; 11:30 AM - 1:00 PM'."""
    if not hours_str or hours_str.lower() in ('closed', 'unknown', ''):
        return []
    hours_str = hours_str.strip()
    if 'sunrise' in hours_str.lower() or '24' in hours_str:
        return [(6, 24)]
    # Split multi-session on ',' or ';'
    segments = re.split(r'[;,]\s*', hours_str)
    sessions = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if 'sunset' in seg.lower():
            parts = re.split(r'\s*-\s*|\s+to\s+', seg, maxsplit=1)
            o = parse_hour(parts[0])
            if o is not None:
                sessions.append((o, 20))
            continue
        parts = re.split(r'\s*-\s*|\s+to\s+', seg, maxsplit=1)
        if len(parts) != 2:
            continue
        o = parse_hour(parts[0])
        c = parse_hour(parts[1])
        if o is None or c is None:
            continue
        if c < o:
            c += 24
        sessions.append((o, c))
    return sessions

def parse_hours(hours_str):
    """Return (earliest_open, latest_close) or None if closed."""
    sessions = parse_sessions(hours_str)
    if not sessions:
        return None
    return sessions  # return all sessions for overlap check

def hours_overlap(act_hours, slot_window):
    """Check if ANY session of act_hours overlaps with the slot window."""
    if act_hours is None:
        return True  # unknown hours: assume ok
    sw_s, sw_e = slot_window
    # act_hours is now a list of (o, c) tuples
    for (o, c) in act_hours:
        if max(o, sw_s) < min(c, sw_e):
            return True
    return False

# ---- Normalize tags ----
TAG_NORMALIZE = {
    'fine dining': 'fine-dining',
    'fine_dining': 'fine-dining',
    'cocktail': 'cocktails',
    'night life': 'nightlife',
    'night-life': 'nightlife',
    'cafe': 'cafe',
    'brunch/breakfast': 'brunch',
    'coffeeshop': 'coffee',
    'coffee shop': 'coffee',
}

def normalize_tags(tags):
    out = []
    for t in tags:
        t = t.strip().lower()
        t = TAG_NORMALIZE.get(t, t)
        if t not in out:
            out.append(t)
    return out

# Normalize all activities tags once, and cache parsed fri hours
for a in raw_acts:
    a['tags'] = normalize_tags(a['tags'])
    a['_fri_hours'] = parse_hours(a['open_hours'].get('fri', ''))

BUDGET_ORDER = ['shoestring', 'mid', 'premium', 'luxury']

def budget_ok(act, family):
    fb = family.get('budget_tier', 'luxury')
    ab = act.get('budget_tier', 'shoestring')
    fi = BUDGET_ORDER.index(fb) if fb in BUDGET_ORDER else 3
    ai = BUDGET_ORDER.index(ab) if ab in BUDGET_ORDER else 0
    return ai <= fi

def family_ok(act, family):
    if not act.get('kid_ok', True) and family.get('kid_ages', 'none') != 'none':
        return False
    if not act.get('mobility_ok', True) and family.get('mobility', 'full') != 'full':
        return False
    if not budget_ok(act, family):
        return False
    return True

def slot_ok(act, slot_idx):
    sw = SLOT_WINDOWS[slot_idx]
    st = SLOT_TAGS[slot_idx]
    tags = set(act['tags'])
    if not tags & st:
        return False
    if not hours_overlap(act['_fri_hours'], sw):
        return False
    return True

def is_cafe(act):
    return bool({'cafe', 'coffee', 'bakery'} & set(act['tags']))

def find_replacement(slot_idx, family, used_counts, lat=None, lng=None, exclude_id=None):
    candidates = []
    for a in raw_acts:
        if a['id'] == exclude_id:
            continue
        if not slot_ok(a, slot_idx):
            continue
        if not family_ok(a, family):
            continue
        max_use = 5 if is_cafe(a) else 3
        if used_counts.get(a['id'], 0) >= max_use:
            continue
        dist = 0
        if lat is not None and a.get('lat') and a.get('lng'):
            dlat = a['lat'] - lat
            dlng = a['lng'] - lng
            dist = math.sqrt(dlat**2 + dlng**2)
        candidates.append((dist, a['id'], a))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][2]

# ---- Process families ----
input_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_part_homeschool_family_stem__b0.jsonl'
output_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_homeschool_family_stem__b0.jsonl'

total_slot_replacements = 0
total_repetition_fixes = 0
no_replacement_slots = []

out_records = []

with open(input_path, encoding='utf-8') as f:
    raw_text = f.read()

# Join any lines that were split mid-token (e.g. a newline inside a JSON string value)
# Strategy: concatenate the whole file and split on lines that start with '{'
import re as _re
# Remove newlines that are NOT at the start of a JSON object
# We know each record starts with {"id":
chunks = _re.split(r'\n(?=\{)', raw_text.strip())
lines = [''.join(chunk.split('\n')) for chunk in chunks if chunk.strip()]

for line in lines:
    line = line.strip()
    if not line:
        continue
    record = json.loads(line)
    fid = record['id']
    family = record['family']
    itinerary = record['itinerary'][:]

    print(f'\n=== {fid} ===')

    # Count current usage
    used_counts = defaultdict(int)
    for aid in itinerary:
        used_counts[aid] += 1

    # Pass 1: slot-type and open-hours violations
    slot_rep = 0
    for i in range(len(itinerary)):
        aid = itinerary[i]
        act = act_by_id.get(aid)
        slot_idx = i % 6
        sw = SLOT_WINDOWS[slot_idx]
        st = SLOT_TAGS[slot_idx]

        if act is None:
            print(f'  Slot {i} (slot_type={slot_idx}): UNKNOWN activity id={aid}')
            prev_lat = act_by_id[itinerary[i-1]]['lat'] if i > 0 and itinerary[i-1] in act_by_id else None
            prev_lng = act_by_id[itinerary[i-1]]['lng'] if i > 0 and itinerary[i-1] in act_by_id else None
            rep = find_replacement(slot_idx, family, used_counts, prev_lat, prev_lng, exclude_id=aid)
            if rep:
                old = itinerary[i]
                used_counts[old] -= 1
                itinerary[i] = rep['id']
                used_counts[rep['id']] += 1
                slot_rep += 1
                print(f'    -> replaced with {rep["id"]}')
            else:
                no_replacement_slots.append((fid, i, aid))
            continue

        # Check tags overlap
        tags_ok = bool(set(act['tags']) & st)
        # Check hours overlap
        oh_ok = hours_overlap(act['_fri_hours'], sw)

        if not tags_ok or not oh_ok:
            reason = []
            if not tags_ok:
                reason.append(f'tags {act["tags"]} not in slot-{slot_idx} required')
            if not oh_ok:
                reason.append(f'hours {act["open_hours"].get("fri","?")} not in {sw}')
            print(f'  Slot {i} (type={slot_idx}): VIOLATION {aid} -- {" | ".join(reason)}')

            prev_lat = act_by_id[itinerary[i-1]]['lat'] if i > 0 and itinerary[i-1] in act_by_id else None
            prev_lng = act_by_id[itinerary[i-1]]['lng'] if i > 0 and itinerary[i-1] in act_by_id else None
            rep = find_replacement(slot_idx, family, used_counts, prev_lat, prev_lng, exclude_id=aid)
            if rep:
                old = itinerary[i]
                used_counts[old] -= 1
                itinerary[i] = rep['id']
                used_counts[rep['id']] += 1
                slot_rep += 1
                print(f'    -> replaced with {rep["id"]}')
            else:
                no_replacement_slots.append((fid, i, aid))

    # Pass 2: repetition cap  (recount after pass1)
    rep_fix = 0
    used_counts2 = defaultdict(int)
    for i in range(len(itinerary)):
        aid = itinerary[i]
        act = act_by_id.get(aid)
        max_use = 5 if (act and is_cafe(act)) else 3
        used_counts2[aid] += 1
        if used_counts2[aid] > max_use:
            print(f'  Slot {i}: REPETITION {aid} (count={used_counts2[aid]}, max={max_use})')
            prev_lat = act_by_id[itinerary[i-1]]['lat'] if i > 0 and itinerary[i-1] in act_by_id else None
            prev_lng = act_by_id[itinerary[i-1]]['lng'] if i > 0 and itinerary[i-1] in act_by_id else None
            rep = find_replacement(i % 6, family, used_counts2, prev_lat, prev_lng, exclude_id=aid)
            if rep:
                old = itinerary[i]
                used_counts2[old] -= 1
                itinerary[i] = rep['id']
                used_counts2[rep['id']] += 1
                rep_fix += 1
                print(f'    -> replaced with {rep["id"]}')
            else:
                no_replacement_slots.append((fid, i, aid))

    print(f'  slot_replacements={slot_rep}, repetition_fixes={rep_fix}')
    total_slot_replacements += slot_rep
    total_repetition_fixes += rep_fix

    record['itinerary'] = itinerary
    out_records.append(record)

# Write output
with open(output_path, 'w', encoding='utf-8') as f:
    for rec in out_records:
        f.write(json.dumps(rec) + '\n')

print(f'\n==== SUMMARY ====')
print(f'Families processed: {len(out_records)}')
print(f'Total slot-type/open-hours replacements: {total_slot_replacements}')
print(f'Total repetition fixes: {total_repetition_fixes}')
print(f'Total replacements: {total_slot_replacements + total_repetition_fixes}')
if no_replacement_slots:
    print(f'No-replacement failures ({len(no_replacement_slots)}):')
    for x in no_replacement_slots:
        print(f'  {x}')
else:
    print('No replacement failures.')
