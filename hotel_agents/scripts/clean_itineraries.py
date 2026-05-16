import json, re, math, sys

# ---- helpers ----------------------------------------------------------------
BUDGET_ORDER = ['shoestring', 'mid', 'premium', 'luxury']

def budget_ok(act_tier, fam_tier):
    ai = BUDGET_ORDER.index(act_tier) if act_tier in BUDGET_ORDER else 99
    fi = BUDGET_ORDER.index(fam_tier) if fam_tier in BUDGET_ORDER else 99
    return ai <= fi

def parse_hour(s):
    s = s.strip()
    m = re.match(r'(\d+):(\d+)\s*(AM|PM)?', s, re.I)
    if not m:
        return None
    h, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm:
        ampm = ampm.upper()
        if ampm == 'PM' and h != 12:
            h += 12
        if ampm == 'AM' and h == 12:
            h = 0
    return h + mn / 60

def parse_range(s):
    if not s or s.lower() in ('closed', 'unknown', 'n/a', ''):
        return None
    if 'sunrise' in s.lower() and 'sunset' in s.lower():
        return (6.0, 20.0)
    if 'sunrise' in s.lower():
        return (6.0, 20.0)
    if 'sunset' in s.lower():
        parts = re.split(r'\s*[-]\s*', s)
        start = parse_hour(parts[0]) if parts else None
        return (start, 20.0) if start is not None else None
    parts = re.split(r'\s*[-]\s*', s, 1)
    if len(parts) != 2:
        return None
    a, b = parse_hour(parts[0]), parse_hour(parts[1])
    if a is None or b is None:
        return None
    return (a, b)

# Slot windows (start, end) in 24h floats
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

def normalize_tag(t):
    return t.lower().strip()

def tags_ok(act, slot_idx):
    required = SLOT_TAGS[slot_idx % 6]
    act_tags = {normalize_tag(t) for t in act['tags']}
    # Also try hyphenated/unhyphenated variants
    act_tags_hyph = {t.replace(' ', '-') for t in act_tags}
    act_tags_space = {t.replace('-', ' ') for t in act_tags}
    all_act_tags = act_tags | act_tags_hyph | act_tags_space
    return bool(required & all_act_tags)

def hours_ok(act, slot_idx):
    sw_start, sw_end = SLOT_WINDOWS[slot_idx % 6]
    rng = None
    for day in ['fri', 'mon', 'tue', 'wed', 'thu', 'sat', 'sun']:
        rng = parse_range(act['open_hours'].get(day, ''))
        if rng:
            break
    if rng is None:
        return False
    op_start, op_end = rng
    # overlap
    return op_start < sw_end and op_end > sw_start

def haversine(a, b):
    R = 6371
    lat1, lng1 = math.radians(a['lat']), math.radians(a['lng'])
    lat2, lng2 = math.radians(b['lat']), math.radians(b['lng'])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    x = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(x))

IS_CAFE_TAGS = {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'}

def is_cafe(act):
    act_tags = {normalize_tag(t) for t in act['tags']}
    return bool(IS_CAFE_TAGS & act_tags)

def max_uses(act):
    return 5 if is_cafe(act) else 3


# ---- load data --------------------------------------------------------------
DATA_DIR = 'hotel_agents/data'
with open(f'{DATA_DIR}/activities_bay.json') as f:
    acts_list = json.load(f)
acts_by_id = {a['id']: a for a in acts_list}

families = []
with open(f'{DATA_DIR}/families_part_young_family_outdoorsy__b0.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            families.append(json.loads(line))

# ---- clean ------------------------------------------------------------------
total_slot_replacements = 0
total_rep_fixes = 0
families_with_missing = []
cleaned = []

for fam_rec in families:
    fam = fam_rec['family']
    itinerary = list(fam_rec['itinerary'])
    kid_ages = fam['kid_ages']
    mobility = fam['mobility']
    budget = fam['budget_tier']
    slot_replacements = 0
    rep_fixes = 0

    def family_ok(act):
        if kid_ages != 'none' and not act['kid_ok']:
            return False
        if mobility != 'full' and not act['mobility_ok']:
            return False
        if not budget_ok(act['budget_tier'], budget):
            return False
        return True

    def find_replacement(slot_idx, counts, prev_act, exclude_ids=None):
        if exclude_ids is None:
            exclude_ids = set()
        sw = SLOT_WINDOWS[slot_idx % 6]
        candidates = []
        for act in acts_list:
            if act['id'] in exclude_ids:
                continue
            if counts.get(act['id'], 0) >= max_uses(act):
                continue
            if not tags_ok(act, slot_idx):
                continue
            if not hours_ok(act, slot_idx):
                continue
            if not family_ok(act):
                continue
            d = haversine(act, prev_act) if prev_act else 0
            candidates.append((d, act['id'], act))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][2]

    # Round 1: fix hard slot/hours violations
    counts = {}
    for i in range(30):
        aid = itinerary[i]
        act = acts_by_id.get(aid)
        slot_type = i % 6
        prev_act = acts_by_id.get(itinerary[i-1]) if i > 0 else None

        needs_replace = False
        if act is None:
            needs_replace = True
        elif not tags_ok(act, slot_type):
            needs_replace = True
        elif not hours_ok(act, slot_type):
            needs_replace = True

        if needs_replace:
            over_limit = {aid2 for aid2, cnt in counts.items()
                          if acts_by_id.get(aid2) and cnt >= max_uses(acts_by_id[aid2])}
            repl = find_replacement(slot_type, counts, prev_act, exclude_ids=over_limit)
            if repl is None:
                print(f'  WARNING: no replacement for fam {fam_rec["id"]} slot {i} (type={slot_type})')
                families_with_missing.append(fam_rec['id'])
            else:
                old = itinerary[i]
                itinerary[i] = repl['id']
                slot_replacements += 1

        counts[itinerary[i]] = counts.get(itinerary[i], 0) + 1

    # Round 2: fix repetition overages
    counts2 = {}
    for aid in itinerary:
        counts2[aid] = counts2.get(aid, 0) + 1

    for i in range(30):
        aid = itinerary[i]
        act = acts_by_id.get(aid)
        if act is None:
            continue
        lim = max_uses(act)
        if counts2.get(aid, 0) > lim:
            prev_act = acts_by_id.get(itinerary[i-1]) if i > 0 else None
            temp_counts = dict(counts2)
            temp_counts[aid] -= 1

            over_limit = {aid2 for aid2, cnt in temp_counts.items()
                          if acts_by_id.get(aid2) and cnt >= max_uses(acts_by_id[aid2])}
            repl = find_replacement(i % 6, temp_counts, prev_act, exclude_ids=over_limit)
            if repl is None:
                print(f'  WARNING: no rep-fix for fam {fam_rec["id"]} slot {i} (type={i%6})')
            else:
                counts2[aid] -= 1
                counts2[repl['id']] = counts2.get(repl['id'], 0) + 1
                itinerary[i] = repl['id']
                rep_fixes += 1

    total_slot_replacements += slot_replacements
    total_rep_fixes += rep_fixes
    print(f'{fam_rec["id"]}: slot_replacements={slot_replacements}, rep_fixes={rep_fixes}')

    cleaned_rec = dict(fam_rec)
    cleaned_rec['itinerary'] = itinerary
    cleaned.append(cleaned_rec)

# ---- write output -----------------------------------------------------------
out_path = f'{DATA_DIR}/families_clean_young_family_outdoorsy__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in cleaned:
        f.write(json.dumps(rec) + '\n')

print()
print(f'TOTAL slot replacements: {total_slot_replacements}')
print(f'TOTAL repetition fixes: {total_rep_fixes}')
print(f'Families with missing replacements: {list(set(families_with_missing))}')
print(f'Output written to: {out_path}')
