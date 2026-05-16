import json
import re
from collections import Counter

# Load data
with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json', 'r') as f:
    acts_list = json.load(f)

acts = {a['id']: a for a in acts_list}

# Slot rules
SLOT_TAGS = {
    0: {'cafe', 'coffee', 'bakery', 'breakfast'},
    1: {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'},
    2: {'museum', 'tour', 'outdoor', 'hiking', 'campus', 'tech', 'art',
        'science', 'history', 'gardens', 'walking', 'shopping', 'viewpoint',
        'landmark', 'architecture'},
    3: {'restaurant', 'lunch', 'casual', 'park', 'scenic', 'beach', 'outdoor',
        'hiking', 'tour', 'shopping', 'winery', 'wine', 'tasting'},
    4: {'restaurant', 'dinner', 'fine-dining', 'scenic', 'sunset', 'wine', 'casual'},
    5: {'bar', 'cocktails', 'nightlife', 'lounge', 'dinner', 'fine-dining',
        'speakeasy'},
}

# Slot time windows as (start_hour, end_hour)
SLOT_WINDOW = {
    0: (7, 9),
    1: (8, 10),
    2: (10, 13),
    3: (12, 17),
    4: (17, 20),
    5: (20, 23),
}

BUDGET_ORDER = ['shoestring', 'mid', 'premium', 'luxury']

def budget_ok(act_tier, fam_tier):
    try:
        return BUDGET_ORDER.index(act_tier) <= BUDGET_ORDER.index(fam_tier)
    except ValueError:
        return True

def parse_hour(s):
    s = s.strip()
    m = re.match(r'(\d+)(?::(\d+))?\s*(AM|PM)', s, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mn = int(m.group(2) or 0)
        period = m.group(3).upper()
        if period == 'PM' and h != 12:
            h += 12
        if period == 'AM' and h == 12:
            h = 0
        return h + mn / 60
    m = re.match(r'(\d+):(\d+)', s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
    return None

def parse_open_hours(oh_str):
    if not oh_str or oh_str.lower() in ('closed', 'n/a', ''):
        return None
    parts = re.split(r'\s*[-]\s*', oh_str, maxsplit=1)
    if len(parts) != 2:
        return None
    o = parse_hour(parts[0])
    c = parse_hour(parts[1])
    if o is None or c is None:
        return None
    return (o, c)

def hours_overlap(oh_tuple, slot_idx):
    if oh_tuple is None:
        return True
    sw = SLOT_WINDOW[slot_idx]
    return oh_tuple[0] < sw[1] and oh_tuple[1] > sw[0]

def act_tag_set(act):
    raw = act.get('tags', [])
    result = set()
    for t in raw:
        tl = t.lower()
        result.add(tl)
        result.add(tl.replace(' ', '-'))
    return result

def tags_ok(act_id, slot_idx):
    act = acts.get(act_id)
    if not act:
        return False
    return bool(act_tag_set(act) & SLOT_TAGS[slot_idx])

def open_ok(act_id, slot_idx):
    act = acts.get(act_id)
    if not act:
        return False
    fri = act.get('open_hours', {}).get('fri', '')
    oh = parse_open_hours(fri)
    return hours_overlap(oh, slot_idx)

def is_valid(act_id, slot_idx):
    return tags_ok(act_id, slot_idx) and open_ok(act_id, slot_idx)

def family_filter(act, fam):
    if fam.get('kid_ages') != 'none' and not act.get('kid_ok', True):
        return False
    if fam.get('mobility') != 'full' and not act.get('mobility_ok', True):
        return False
    if not budget_ok(act.get('budget_tier', 'mid'), fam.get('budget_tier', 'mid')):
        return False
    return True

def geo_dist(a1, a2):
    if not a1 or not a2:
        return 0
    dlat = (a1.get('lat', 0) - a2.get('lat', 0))
    dlng = (a1.get('lng', 0) - a2.get('lng', 0))
    return (dlat**2 + dlng**2) ** 0.5

def is_cafe_act(act):
    tags = [t.lower() for t in act.get('tags', [])]
    return 'cafe' in tags or 'coffee' in tags

def find_replacement(slot_idx, fam, used_counts, prev_act_id=None, exclude=None):
    exclude = exclude or set()
    prev_act = acts.get(prev_act_id) if prev_act_id else None

    def _candidates(budget_relaxed=False):
        result = []
        for act in acts_list:
            aid = act['id']
            if aid in exclude:
                continue
            if not tags_ok(aid, slot_idx):
                continue
            if not open_ok(aid, slot_idx):
                continue
            if not budget_relaxed and not family_filter(act, fam):
                continue
            # kid / mobility still hard constraints even when relaxing budget
            if budget_relaxed:
                if fam.get('kid_ages') != 'none' and not act.get('kid_ok', True):
                    continue
                if fam.get('mobility') != 'full' and not act.get('mobility_ok', True):
                    continue
            cap = 5 if is_cafe_act(act) else 3
            if used_counts.get(aid, 0) >= cap:
                continue
            dist = geo_dist(act, prev_act) if prev_act else 0
            result.append((dist, aid))
        return result

    candidates = _candidates(budget_relaxed=False)
    if not candidates:
        # Relax budget as last resort
        candidates = _candidates(budget_relaxed=True)
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]

# Process each family
total_slot_fixes = 0
total_rep_fixes = 0
no_replacement = []
output_records = []

with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_bachelorette_party_crew__b0.jsonl', 'r') as f:
    records = [json.loads(line) for line in f if line.strip()]

for rec in records:
    fam_id = rec['id']
    fam = rec['family']
    itinerary = list(rec['itinerary'])

    used_counts = Counter(itinerary)
    slot_fixes = 0
    rep_fixes = 0

    # Pass 1: fix slot-type and open-hours violations
    for i in range(len(itinerary)):
        act_id = itinerary[i]
        slot_idx = i % 6
        if not is_valid(act_id, slot_idx):
            prev_id = itinerary[i-1] if i > 0 else None
            exclude = {act_id}
            recount = Counter(itinerary)
            replacement = find_replacement(slot_idx, fam, recount, prev_id, exclude)
            if replacement:
                old = itinerary[i]
                itinerary[i] = replacement
                used_counts[old] -= 1
                used_counts[replacement] = used_counts.get(replacement, 0) + 1
                slot_fixes += 1
            else:
                no_replacement.append((fam_id, i, act_id, slot_idx, 'slot'))

    # Pass 2: fix repetition violations
    used_counts = Counter(itinerary)
    for act_id, count in list(used_counts.items()):
        act = acts.get(act_id, {})
        cap = 5 if is_cafe_act(act) else 3
        if count > cap:
            positions = [i for i, a in enumerate(itinerary) if a == act_id]
            to_replace = positions[cap:]
            for pos in to_replace:
                slot_idx = pos % 6
                prev_id = itinerary[pos-1] if pos > 0 else None
                recount = Counter(itinerary)
                exclude = {act_id}
                replacement = find_replacement(slot_idx, fam, recount, prev_id, exclude)
                if replacement:
                    itinerary[pos] = replacement
                    rep_fixes += 1
                else:
                    no_replacement.append((fam_id, pos, act_id, slot_idx, 'rep'))

    print(f'{fam_id}: slot_fixes={slot_fixes}, rep_fixes={rep_fixes}')
    total_slot_fixes += slot_fixes
    total_rep_fixes += rep_fixes

    output_records.append({'id': rec['id'], 'family': rec['family'], 'itinerary': itinerary})

# Write output
out_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_bachelorette_party_crew__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in output_records:
        f.write(json.dumps(rec) + '\n')

print()
print(f'TOTAL slot replacements: {total_slot_fixes}')
print(f'TOTAL repetition fixes: {total_rep_fixes}')
print(f'Unfilled slots: {len(no_replacement)}')
for nr in no_replacement:
    print('  NO REPLACEMENT:', nr)
