import json, re, copy

# --- Load data ---
with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    acts_list = json.load(f)
acts = {a['id']: a for a in acts_list}

families = []
with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_hiking_buddies_adventure__b0.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            families.append(json.loads(line))

# --- Slot rules ---
# slot_idx % 6 -> (win_start_h, win_end_h, required_tags_any_of)
SLOT_RULES = {
    0: (7,  9,  {'cafe','coffee','bakery','breakfast'}),
    1: (8,  10, {'cafe','coffee','bakery','breakfast','brunch'}),
    2: (10, 13, {'museum','tour','outdoor','hiking','campus','tech','art','science',
                 'history','gardens','walking','shopping','viewpoint','landmark','architecture'}),
    3: (12, 17, {'restaurant','lunch','casual','park','scenic','beach','outdoor','hiking',
                 'tour','shopping','winery','wine','tasting'}),
    4: (17, 20, {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'}),
    5: (20, 23, {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'}),
}

BUDGET_ORDER = ['shoestring', 'mid', 'premium', 'luxury']
def budget_ok(act_tier, fam_tier):
    try:
        return BUDGET_ORDER.index(act_tier) <= BUDGET_ORDER.index(fam_tier)
    except ValueError:
        return True

# Tag normalization: raw tag -> slot-rule-compatible tag
TAG_NORM = {
    'fine dining':  'fine-dining',
    'fine_dining':  'fine-dining',
    'dining':       'restaurant',
    'bakery':       'bakery',
    'brunch':       'brunch',
    'coffee':       'coffee',
    'cafe':         'cafe',
    'food':         'casual',
    'park':         'park',
    'outdoor':      'outdoor',
    'outdoors':     'outdoor',
    'hiking':       'hiking',
    'walking':      'walking',
    'beach':        'beach',
    'scenic':       'scenic',
    'viewpoint':    'viewpoint',
    'art':          'art',
    'sculpture':    'art',
    'history':      'history',
    'historic':     'history',
    'museum':       'museum',
    'campus':       'campus',
    'architecture': 'architecture',
    'landmark':     'landmark',
    'shopping':     'shopping',
    'restaurant':   'restaurant',
    'wine':         'wine',
    'tasting':      'tasting',
    'vineyard':     'winery',
    'winery':       'winery',
    'tour':         'tour',
    'gardens':      'gardens',
    'garden':       'gardens',
    'sunset':       'sunset',
    'dinner':       'dinner',
    'bar':          'bar',
    'cocktails':    'cocktails',
    'nightlife':    'nightlife',
    'lounge':       'lounge',
    'speakeasy':    'speakeasy',
    'casual':       'casual',
    'lunch':        'lunch',
    'breakfast':    'breakfast',
    'tech':         'tech',
    'science':      'science',
}

def normalize_tags(raw_tags):
    out = set()
    for t in raw_tags:
        tl = t.lower().strip()
        out.add(TAG_NORM.get(tl, tl))
    return out

# Parse open hours string -> list of (start_h_float, end_h_float)
def parse_hours(s):
    if not s or s in ('closed', '?', ''):
        return []
    s = s.strip()
    if re.match(r'(?i)^24h', s) or s == 'open 24 hours':
        return [(0.0, 24.0)]
    # sunrise/sunset substitution
    s2 = re.sub(r'(?i)sunrise', '6:00', s)
    s2 = re.sub(r'(?i)sunset',  '20:00', s2)
    s2 = re.sub(r'\s+to\s+', '-', s2)
    sessions = re.split(r'[;,]\s*', s2)
    result = []
    for sess in sessions:
        sess = sess.strip()
        m = re.search(
            r'(\d{1,2}):?(\d{2})?\s*(AM|PM)?\s*[-]\s*(\d{1,2}):?(\d{2})?\s*(AM|PM)?',
            sess, re.IGNORECASE)
        if m:
            h1, m1, ap1, h2, m2, ap2 = m.groups()
            h1, m1 = int(h1), int(m1) if m1 else 0
            h2, m2 = int(h2), int(m2) if m2 else 0
            if ap1 and ap1.upper() == 'PM' and h1 != 12: h1 += 12
            if ap1 and ap1.upper() == 'AM' and h1 == 12: h1 = 0
            if ap2 and ap2.upper() == 'PM' and h2 != 12: h2 += 12
            if ap2 and ap2.upper() == 'AM' and h2 == 12: h2 = 0
            start = h1 + m1/60.0
            end   = h2 + m2/60.0
            # Midnight 00:00 as closing time means end-of-day; treat as 24.0
            if end == 0.0 and start > 0.0:
                end = 24.0
            result.append((start, end))
    return result

def hours_overlap(sessions, win_s, win_e):
    """True if ANY session overlaps with the slot window."""
    for (s, e) in sessions:
        if s < win_e and e > win_s:
            return True
    return False

def find_replacement(slot_idx, fam, used_counts, excluded_id, prev_act_id=None):
    slot_type = slot_idx % 6
    win_s, win_e, req_tags = SLOT_RULES[slot_type]
    candidates = []
    for a in acts_list:
        aid = a['id']
        if aid == excluded_id:
            continue
        ntags = normalize_tags(a['tags'])
        is_cafe = bool({'cafe','coffee'} & ntags)
        cap = 5 if is_cafe else 3
        if used_counts.get(aid, 0) >= cap:
            continue
        if not (ntags & req_tags):
            continue
        fri_hrs = parse_hours(a['open_hours'].get('fri', ''))
        if not hours_overlap(fri_hrs, win_s, win_e):
            continue
        if not budget_ok(a['budget_tier'], fam['budget_tier']):
            continue
        if not a['kid_ok'] and fam['kid_ages'] != 'none':
            continue
        if not a['mobility_ok'] and fam['mobility'] != 'full':
            continue
        candidates.append(aid)
    if not candidates:
        return None
    if prev_act_id and prev_act_id in acts:
        prev = acts[prev_act_id]
        def dist(aid):
            a = acts[aid]
            return ((a['lat']-prev['lat'])**2 + (a['lng']-prev['lng'])**2)**0.5
        candidates.sort(key=dist)
    # Among ties, prefer least-used
    candidates.sort(key=lambda aid: used_counts.get(aid, 0))
    return candidates[0]

# --- Main ---
total_slot_replacements = 0
total_tag_violations    = 0
total_hours_violations  = 0
total_rep_fixes         = 0
unfixable               = []
cleaned_families        = []

for fam_rec in families:
    fam = fam_rec['family']
    itinerary = list(fam_rec['itinerary'])
    fid = fam_rec['id']
    print(f'\n=== {fid} ===')

    # Phase 1: slot-type and open-hours violations (also catches missing)
    used_counts = {}
    for aid in itinerary:
        used_counts[aid] = used_counts.get(aid, 0) + 1

    for i, act_id in enumerate(itinerary):
        slot_type = i % 6
        win_s, win_e, req_tags = SLOT_RULES[slot_type]
        tag_ok = True
        hrs_ok = True

        if act_id not in acts:
            tag_ok = False
            hrs_ok = False
        else:
            a = acts[act_id]
            ntags = normalize_tags(a['tags'])
            tag_ok = bool(ntags & req_tags)
            fri_hrs = parse_hours(a['open_hours'].get('fri', ''))
            hrs_ok = hours_overlap(fri_hrs, win_s, win_e)

        if not tag_ok or not hrs_ok:
            reasons = []
            if act_id not in acts:
                reasons = ['missing']
            else:
                if not tag_ok: reasons.append('tag')
                if not hrs_ok: reasons.append('hours')

            prev_id = itinerary[i-1] if i > 0 else None
            rep = find_replacement(i, fam, used_counts, excluded_id=act_id, prev_act_id=prev_id)
            if rep:
                used_counts[act_id] = max(0, used_counts.get(act_id, 0) - 1)
                itinerary[i] = rep
                used_counts[rep] = used_counts.get(rep, 0) + 1
                print(f'  slot {i:02d} (type {slot_type}): {act_id!r} -> {rep!r} [{",".join(reasons)}]')
                if 'tag'     in reasons: total_tag_violations += 1
                if 'hours'   in reasons: total_hours_violations += 1
                total_slot_replacements += 1
            else:
                print(f'  slot {i:02d} (type {slot_type}): UNFIXABLE {act_id!r} [{",".join(reasons)}]')
                unfixable.append({'fam': fid, 'slot': i, 'act': act_id, 'reason': reasons})

    # Phase 2: repetition cap
    rep_fixes = 0
    used_counts = {}
    for aid in itinerary:
        used_counts[aid] = used_counts.get(aid, 0) + 1

    for i, act_id in enumerate(itinerary):
        ntags_act = normalize_tags(acts[act_id]['tags']) if act_id in acts else set()
        is_cafe = bool({'cafe','coffee'} & ntags_act)
        cap = 5 if is_cafe else 3
        seen_so_far = itinerary[:i].count(act_id)
        if seen_so_far >= cap:
            prev_id = itinerary[i-1] if i > 0 else None
            # exclude current id from pool
            tmp_counts = {k: (v-1 if k == act_id else v) for k,v in used_counts.items()}
            rep = find_replacement(i, fam, tmp_counts, excluded_id=act_id, prev_act_id=prev_id)
            if rep:
                used_counts[act_id] -= 1
                itinerary[i] = rep
                used_counts[rep] = used_counts.get(rep, 0) + 1
                print(f'  slot {i:02d}: repetition cap {act_id!r} (x{seen_so_far+1}) -> {rep!r}')
                rep_fixes += 1
            else:
                print(f'  slot {i:02d}: repetition UNFIXABLE {act_id!r} (x{seen_so_far+1})')
                unfixable.append({'fam': fid, 'slot': i, 'act': act_id, 'reason': ['repetition']})
    total_rep_fixes += rep_fixes

    cleaned_families.append({
        'id': fam_rec['id'],
        'family': fam_rec['family'],
        'itinerary': itinerary
    })

print()
print('=== FINAL SUMMARY ===')
print(f'Families processed:          {len(cleaned_families)}')
print(f'Slot replacements total:     {total_slot_replacements}')
print(f'  - Tag violations fixed:    {total_tag_violations}')
print(f'  - Hours violations fixed:  {total_hours_violations}')
print(f'Repetition fixes:            {total_rep_fixes}')
print(f'Unfixable slots:             {len(unfixable)}')
for u in unfixable:
    print(f'  {u}')

out_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_hiking_buddies_adventure__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in cleaned_families:
        f.write(json.dumps(rec) + '\n')
print(f'\nWrote {len(cleaned_families)} records -> {out_path}')
