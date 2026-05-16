import json, re

# ---- helpers ---------------------------------------------------------------

def parse_hour(s, is_close=False):
    s = s.strip()
    if not s or s.lower() in ('closed', 'sunset', 'sunrise', 'varies'):
        return None
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        h = int(m.group(1)) + int(m.group(2))/60
        # 00:00 as a closing time means midnight (end of day = 24)
        if is_close and h == 0:
            h = 24
        return h
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$', s, re.IGNORECASE)
    if m:
        h, mins = int(m.group(1)), int(m.group(2)) if m.group(2) else 0
        p = m.group(3).upper()
        if p == 'PM' and h != 12: h += 12
        if p == 'AM' and h == 12: h = 0
        return h + mins/60
    return None

def parse_open_range(oh_str):
    if not oh_str:
        return None
    s = oh_str.strip()
    low = s.lower()
    if low == '24h':
        return [(0, 24)]
    if 'sunset' in low or 'sunrise' in low:
        return [(6, 20)]
    if low in ('closed', 'varies'):
        return None
    windows = []
    for part in s.split(','):
        part = part.strip()
        pieces = re.split(r'\s*[-]\s*', part, maxsplit=1)
        if len(pieces) == 2:
            o = parse_hour(pieces[0], is_close=False)
            c = parse_hour(pieces[1], is_close=True)
            if o is not None and c is not None:
                windows.append((o, c))
    return windows if windows else None

SLOT_TAGS = {
    0: {'cafe','coffee','bakery','breakfast'},
    1: {'cafe','coffee','bakery','breakfast','brunch'},
    2: {'museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'},
    3: {'restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'},
    4: {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'},
    5: {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'},
}
SLOT_WINDOW = {
    0: (7, 9),
    1: (8, 10),
    2: (10, 13),
    3: (12, 17),
    4: (17, 20),
    5: (20, 23),
}

def windows_overlap(windows, sw, ew):
    if not windows:
        return False
    for (o, c) in windows:
        if o < ew and c > sw:
            return True
    return False

def normalize_tag(t):
    return t.lower().replace(' ', '').replace('-', '')

def tag_ok(act, slot_idx):
    if act is None:
        return False
    required = SLOT_TAGS[slot_idx % 6]
    raw_tags = set(t.lower() for t in act.get('tags', []))
    norm_tags = set(normalize_tag(t) for t in act.get('tags', []))
    norm_req = set(normalize_tag(r) for r in required)
    return bool(required & raw_tags) or bool(norm_req & norm_tags)

def hours_ok(act, slot_idx):
    if act is None:
        return False
    oh = act.get('open_hours', {})
    fri_str = oh.get('fri', 'closed')
    windows = parse_open_range(fri_str)
    sw, ew = SLOT_WINDOW[slot_idx % 6]
    return windows_overlap(windows, sw, ew)

def budget_rank(bt):
    return {'shoestring': 0, 'mid': 1, 'premium': 2, 'luxury': 3}.get(bt, 0)

def is_cafe(act):
    if act is None:
        return False
    return 'cafe' in [t.lower() for t in act.get('tags', [])]

def rep_limit(act):
    return 5 if is_cafe(act) else 3

# ---- load data -------------------------------------------------------------

with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json', 'r') as f:
    activities = json.load(f)
act_map = {a['id']: a for a in activities}

with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_celiac_foodie_explorer__b0.jsonl', 'r') as f:
    families = [json.loads(line) for line in f if line.strip()]

# ---- candidate pool builder ------------------------------------------------

def candidates_for_slot(slot_idx, family):
    fam = family['family']
    kid_ages = fam.get('kid_ages', 'none')
    mob = fam.get('mobility', 'full')
    budget = fam.get('budget_tier', 'mid')
    has_kids = kid_ages != 'none'
    br = budget_rank(budget)
    result = []
    for act in activities:
        if not tag_ok(act, slot_idx):
            continue
        if not hours_ok(act, slot_idx):
            continue
        if has_kids and not act.get('kid_ok', True):
            continue
        if mob != 'full' and not act.get('mobility_ok', True):
            continue
        if budget_rank(act.get('budget_tier', 'shoestring')) > br:
            continue
        result.append(act['id'])
    return result

# ---- fix per family --------------------------------------------------------

total_slot_fixes = 0
total_rep_fixes = 0
no_candidate_slots = []

cleaned_families = []

for fam in families:
    itinerary = list(fam['itinerary'])
    slot_fixes = 0
    rep_fixes = 0

    # --- Pass 1: slot-type and open-hours ---
    usage = {}
    for aid in itinerary:
        usage[aid] = usage.get(aid, 0) + 1

    for i in range(30):
        aid = itinerary[i]
        act = act_map.get(aid)
        if tag_ok(act, i) and hours_ok(act, i):
            continue

        pool = candidates_for_slot(i, fam)
        usage[aid] = max(0, usage.get(aid, 1) - 1)

        chosen = None
        for candidate in pool:
            if candidate == aid:
                continue
            act_c = act_map.get(candidate)
            lim = rep_limit(act_c)
            if usage.get(candidate, 0) < lim:
                chosen = candidate
                break

        if chosen:
            usage[chosen] = usage.get(chosen, 0) + 1
            itinerary[i] = chosen
            slot_fixes += 1
        else:
            # Restore usage count
            usage[aid] = usage.get(aid, 0) + 1
            no_candidate_slots.append((fam['id'], i, i % 6, aid, 'slot'))

    # --- Pass 2: repetition cap ---
    usage2 = {}
    for i in range(30):
        aid = itinerary[i]
        act = act_map.get(aid)
        lim = rep_limit(act)
        usage2[aid] = usage2.get(aid, 0) + 1
        if usage2[aid] > lim:
            pool = candidates_for_slot(i, fam)
            chosen = None
            for candidate in pool:
                if candidate == aid:
                    continue
                act_c = act_map.get(candidate)
                lim_c = rep_limit(act_c)
                if usage2.get(candidate, 0) < lim_c:
                    chosen = candidate
                    break
            if chosen:
                usage2[chosen] = usage2.get(chosen, 0) + 1
                usage2[aid] -= 1
                itinerary[i] = chosen
                rep_fixes += 1
            else:
                no_candidate_slots.append((fam['id'], i, i % 6, aid, 'rep'))

    total_slot_fixes += slot_fixes
    total_rep_fixes += rep_fixes
    cleaned_families.append({'id': fam['id'], 'family': fam['family'], 'itinerary': itinerary})

# ---- write output ----------------------------------------------------------
out_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_celiac_foodie_explorer__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in cleaned_families:
        f.write(json.dumps(rec) + '\n')

print('DONE')
print('Slot-type / open-hours fixes:', total_slot_fixes)
print('Repetition cap fixes:', total_rep_fixes)
print('Total fixes:', total_slot_fixes + total_rep_fixes)
if no_candidate_slots:
    print('Slots with no suitable candidate found:')
    for s in no_candidate_slots:
        print(' ', s)
else:
    print('No unfixable slots.')
