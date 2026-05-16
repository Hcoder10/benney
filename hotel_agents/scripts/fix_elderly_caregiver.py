import json
import re

# ---- Load activity bank ----
with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json', 'r') as f:
    activities = json.load(f)
act_map = {a['id']: a for a in activities}

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
SLOT_WINDOWS = {
    0: (7, 9),
    1: (8, 10),
    2: (10, 13),
    3: (12, 17),
    4: (17, 20),
    5: (20, 23),
}

def parse_time(s):
    """Return (open_h, close_h) as floats, or None if closed/unknown."""
    if not s or s.lower() in ('closed', 'varies', 'unknown', ''):
        return None
    s = s.strip()
    if 'sunrise' in s.lower() or 'sunset' in s.lower():
        return (6.0, 20.0)
    if s == '24h':
        return (0.0, 24.0)

    def to24(t):
        t = t.strip()
        m = re.match(r'(\d+):?(\d*)\s*(AM|PM)?$', t, re.IGNORECASE)
        if not m:
            return None
        h = int(m.group(1))
        mn = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3)
        if ampm:
            if ampm.upper() == 'PM' and h != 12:
                h += 12
            if ampm.upper() == 'AM' and h == 12:
                h = 0
        return h + mn / 60.0

    segs = re.split(r'[;,]', s)
    spans = []
    for seg in segs:
        seg = seg.strip()
        parts = re.split(r'\s*-\s*', seg)
        if len(parts) == 2:
            o = to24(parts[0])
            c = to24(parts[1])
            if o is not None and c is not None:
                spans.append((o, c))
    if not spans:
        return None
    return (min(sp[0] for sp in spans), max(sp[1] for sp in spans))


def hours_overlap(open_h, close_h, slot_idx):
    win = SLOT_WINDOWS[slot_idx]
    return open_h < win[1] and close_h > win[0]


def tags_match(act, slot_idx):
    # Normalize tags: lowercase, replace spaces with hyphens, also try raw
    act_tags = set()
    for t in act.get('tags', []):
        act_tags.add(t.lower())
        act_tags.add(t.lower().replace(' ', '-'))
    required = SLOT_TAGS[slot_idx]
    return bool(required & act_tags)


def open_hours_ok(act, slot_idx):
    oh = act.get('open_hours', {})
    fri = oh.get('fri', '')
    parsed = parse_time(fri)
    if parsed is None:
        return False
    return hours_overlap(parsed[0], parsed[1], slot_idx)


BUDGET_ORDER = ['shoestring', 'mid', 'premium', 'luxury']


def budget_ok(act, family):
    family_tier = family.get('budget_tier', 'luxury')
    act_tier = act.get('budget_tier', 'shoestring')
    fi = BUDGET_ORDER.index(family_tier) if family_tier in BUDGET_ORDER else 3
    ai = BUDGET_ORDER.index(act_tier) if act_tier in BUDGET_ORDER else 0
    return ai <= fi


def kid_ok(act, family):
    if family.get('kid_ages', 'none') == 'none':
        return True
    return act.get('kid_ok', True)


def mobility_ok_fn(act, family):
    if family.get('mobility', 'full') == 'full':
        return True
    return act.get('mobility_ok', True)


def family_ok(act, family):
    return budget_ok(act, family) and kid_ok(act, family) and mobility_ok_fn(act, family)


CAFE_TAGS_SET = {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'}


def is_cafe(act):
    act_tags = {t.lower() for t in act.get('tags', [])}
    return bool(act_tags & CAFE_TAGS_SET)


def count_usage(itin):
    counts = {}
    for aid in itin:
        counts[aid] = counts.get(aid, 0) + 1
    return counts


def find_replacement(slot_idx, family, current_counts, exclude_over_cap=None):
    """Find best replacement for slot_idx given family constraints and current usage counts."""
    candidates = []
    for act in activities:
        aid = act['id']
        if not tags_match(act, slot_idx):
            continue
        if not open_hours_ok(act, slot_idx):
            continue
        if not family_ok(act, family):
            continue
        count = current_counts.get(aid, 0)
        cap = 5 if is_cafe(act) else 3
        if count >= cap:
            continue
        candidates.append((count, aid, act))
    if not candidates:
        return None
    # Sort by current count ascending (prefer less-used)
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


# ---- Process each family ----
total_slot_fixes = 0
total_rep_fixes = 0
could_not_fix = []

output_records = []

with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_elderly_caregiver_couple__b0.jsonl', 'r') as f:
    lines = [l for l in f if l.strip()]

for line in lines:
    rec = json.loads(line)
    family = rec['family']
    itinerary = list(rec['itinerary'])
    fam_id = rec['id']

    slot_fixes = 0
    rep_fixes = 0

    # Pass 1: slot-type and open-hours violations
    for i in range(len(itinerary)):
        aid = itinerary[i]
        slot_idx = i % 6
        act = act_map.get(aid)

        if act is None:
            # Missing activity ID - replace
            counts = count_usage(itinerary)
            repl = find_replacement(slot_idx, family, counts)
            if repl:
                itinerary[i] = repl
                slot_fixes += 1
                print(f"  [{fam_id}] slot {i} (s{slot_idx}): MISSING {aid} -> {repl}")
            else:
                could_not_fix.append((fam_id, i, aid, 'missing-no-replacement'))
            continue

        tag_ok = tags_match(act, slot_idx)
        oh_ok = open_hours_ok(act, slot_idx)

        if not tag_ok or not oh_ok:
            counts = count_usage(itinerary)
            repl = find_replacement(slot_idx, family, counts)
            if repl:
                reason = []
                if not tag_ok:
                    reason.append('tag')
                if not oh_ok:
                    reason.append('hours')
                print(f"  [{fam_id}] slot {i} (s{slot_idx}): {'+'.join(reason)} {aid} -> {repl}")
                itinerary[i] = repl
                slot_fixes += 1
            else:
                could_not_fix.append((fam_id, i, aid, 'no-replacement'))

    # Pass 2: repetition cap
    counts = count_usage(itinerary)
    for aid in list(counts.keys()):
        cnt = counts[aid]
        act = act_map.get(aid)
        cap = 5 if (act and is_cafe(act)) else 3
        if cnt > cap:
            excess = cnt - cap
            positions = [i for i, x in enumerate(itinerary) if x == aid]
            to_replace = positions[-excess:]
            for pos in to_replace:
                slot_idx = pos % 6
                temp_counts = count_usage(itinerary)
                repl = find_replacement(slot_idx, family, temp_counts)
                if repl:
                    print(f"  [{fam_id}] slot {pos} (s{slot_idx}): REP({cnt}>{cap}) {aid} -> {repl}")
                    itinerary[pos] = repl
                    rep_fixes += 1
                else:
                    could_not_fix.append((fam_id, pos, aid, 'rep-no-replacement'))

    total_slot_fixes += slot_fixes
    total_rep_fixes += rep_fixes
    output_records.append({'id': rec['id'], 'family': family, 'itinerary': itinerary})

# Write output
with open('C:/Users/sarta/rosea/hotel_agents/data/families_clean_elderly_caregiver_couple__b0.jsonl', 'w') as f:
    for rec in output_records:
        f.write(json.dumps(rec) + '\n')

print(f'\nSlot-type / open-hours fixes: {total_slot_fixes}')
print(f'Repetition fixes:             {total_rep_fixes}')
print(f'Total fixes:                  {total_slot_fixes + total_rep_fixes}')
print(f'Could not fix:                {len(could_not_fix)}')
if could_not_fix:
    for item in could_not_fix:
        print(f'  {item}')
print('Done. Output written to families_clean_elderly_caregiver_couple__b0.jsonl')
