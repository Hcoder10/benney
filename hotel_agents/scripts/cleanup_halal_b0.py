import json, re, math

with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    activities_list = json.load(f)
activities = {a['id']: a for a in activities_list}

SLOT_TAGS = {
    0: {'cafe','coffee','bakery','breakfast'},
    1: {'cafe','coffee','bakery','breakfast','brunch'},
    2: {'museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'},
    3: {'restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'},
    4: {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'},
    5: {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'},
}
SLOT_WINDOWS = {0:(7,9),1:(8,10),2:(10,13),3:(12,17),4:(17,20),5:(20,23)}
BUDGET_ORDER = ['shoestring','mid','premium','luxury']

def normalize_tag(t):
    return re.sub(r'[\s_-]+', ' ', t.lower()).strip()

def tags_normalized(act):
    return {normalize_tag(t) for t in act.get('tags',[])}

def to_24(time_str):
    time_str = time_str.strip()
    pm = 'pm' in time_str.lower()
    am = 'am' in time_str.lower()
    time_str = re.sub(r'[aApPmMsSuUnNrRiIeEt\s]', '', time_str)
    # Only strip AM/PM characters, not sunrise/sunset words — do it differently
    return None  # placeholder

def to_24v2(time_str):
    time_str = time_str.strip()
    pm = bool(re.search(r'pm', time_str, re.I))
    am = bool(re.search(r'am', time_str, re.I))
    # Remove AM/PM and whitespace
    clean = re.sub(r'\s*(am|pm)\s*', '', time_str, flags=re.I).strip()
    parts = clean.split(':')
    h = int(parts[0])
    m2 = int(parts[1]) if len(parts) > 1 else 0
    if pm and h != 12:
        h += 12
    if am and h == 12:
        h = 0
    return h + m2 / 60.0

def parse_time_str(s):
    s = s.strip()
    if not s or s.lower() == 'closed':
        return []
    if re.match(r'^24\s*h', s, re.I):
        return [(0, 24)]
    if 'sunrise' in s.lower() or 'sunset' in s.lower():
        return [(6, 20)]
    sessions = re.split(r'[;,]', s)
    result = []
    for session in sessions:
        session = session.strip()
        # Match two time tokens separated by dash (with optional spaces)
        # Token: digits optionally with colon + optional AM/PM
        m = re.match(r'^(\d+(?::\d+)?\s*(?:am|pm)?)\s*[-–]\s*(\d+(?::\d+)?\s*(?:am|pm)?)$', session, re.I)
        if m:
            try:
                start = to_24v2(m.group(1))
                end = to_24v2(m.group(2))
                # Midnight fix: 00:00 as end time means 24:00
                if end == 0.0 and start > 0:
                    end = 24.0
                result.append((start, end))
            except Exception:
                pass
    return result

def hours_overlap(sessions, ws, we):
    return any(s < we and e > ws for s, e in sessions)

def is_cafe_act(act):
    ntags = tags_normalized(act)
    return 'cafe' in ntags or 'coffee' in ntags

def budget_ok(act, family_budget):
    if act['budget_tier'] not in BUDGET_ORDER:
        return True
    if family_budget not in BUDGET_ORDER:
        return True
    return BUDGET_ORDER.index(act['budget_tier']) <= BUDGET_ORDER.index(family_budget)

def dist_km(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def resolve_id(aid):
    return 'orens_hummus' if aid == 'oren_s_hummus' else aid

def slot_tags_valid(act, si):
    req_tags = {normalize_tag(t) for t in SLOT_TAGS[si]}
    act_tags = tags_normalized(act)
    return bool(req_tags & act_tags)

def slot_hours_valid(act, si):
    fri = act['open_hours'].get('fri', '')
    sessions = parse_time_str(fri)
    ws, we = SLOT_WINDOWS[si]
    return hours_overlap(sessions, ws, we)

# Precompute pools per slot
def build_pool(si):
    req_tags = {normalize_tag(t) for t in SLOT_TAGS[si]}
    ws, we = SLOT_WINDOWS[si]
    pool = []
    for aid, act in activities.items():
        atags = tags_normalized(act)
        tag_ok = bool(req_tags & atags)
        fri = act['open_hours'].get('fri', '')
        sessions = parse_time_str(fri)
        hours_ok = hours_overlap(sessions, ws, we)
        if tag_ok and hours_ok:
            pool.append(aid)
    return pool

POOLS = {si: build_pool(si) for si in range(6)}

def pick_replacement(si, fam_data, usage, prev_lat, prev_lng, exclude_id):
    kid_ok_req = fam_data.get('kid_ages', 'none') != 'none'
    mob_ok_req = fam_data.get('mobility', 'full') != 'full'
    bud = fam_data.get('budget_tier', 'mid')
    candidates = []
    for aid in POOLS[si]:
        if aid == exclude_id:
            continue
        act = activities[aid]
        if not budget_ok(act, bud):
            continue
        if kid_ok_req and not act['kid_ok']:
            continue
        if mob_ok_req and not act['mobility_ok']:
            continue
        cap = 5 if is_cafe_act(act) else 3
        cur_count = usage.get(aid, 0)
        if cur_count >= cap:
            continue
        if prev_lat is not None:
            d = dist_km(prev_lat, prev_lng, act['lat'], act['lng'])
        else:
            d = 0
        candidates.append((d, aid))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]

families = []
with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_halal_family_foodie__b0.jsonl') as f:
    for line in f:
        if line.strip():
            families.append(json.loads(line.strip()))

total_slot_replacements = 0
total_rep_fixes = 0
failed_replacements = []
output_records = []

for fam in families:
    fid = fam['id']
    itin = list(fam['itinerary'])
    fam_data = fam['family']

    # Pass 1: fix slot-type and hours violations
    slot_replacements_this = 0
    for i in range(30):
        aid = itin[i]
        rid = resolve_id(aid)
        si = i % 6
        act = activities.get(rid)

        if act is None:
            # missing from bank
            prev_act = activities.get(resolve_id(itin[i-1])) if i > 0 else None
            prev_lat = prev_act['lat'] if prev_act else None
            prev_lng = prev_act['lng'] if prev_act else None
            usage = {}
            for j in range(30):
                r = resolve_id(itin[j])
                usage[r] = usage.get(r, 0) + 1
            rep = pick_replacement(si, fam_data, usage, prev_lat, prev_lng, rid)
            if rep:
                itin[i] = rep
                slot_replacements_this += 1
            else:
                failed_replacements.append((fid, i, aid, 'no_candidate_missing'))
            continue

        tag_ok = slot_tags_valid(act, si)
        hours_ok = slot_hours_valid(act, si)

        if tag_ok and hours_ok:
            continue

        prev_act = activities.get(resolve_id(itin[i-1])) if i > 0 else None
        prev_lat = prev_act['lat'] if prev_act else act['lat']
        prev_lng = prev_act['lng'] if prev_act else act['lng']
        usage = {}
        for j in range(30):
            r = resolve_id(itin[j])
            usage[r] = usage.get(r, 0) + 1
        rep = pick_replacement(si, fam_data, usage, prev_lat, prev_lng, rid)
        if rep:
            itin[i] = rep
            slot_replacements_this += 1
        else:
            failed_replacements.append((fid, i, aid, 'no_candidate_slot'))

    total_slot_replacements += slot_replacements_this

    # Pass 2: repetition cap
    rep_fixes_this = 0
    usage = {}
    for j in range(30):
        r = resolve_id(itin[j])
        usage[r] = usage.get(r, 0) + 1

    # Iterate until no more violations
    changed = True
    while changed:
        changed = False
        for rid_check, cnt in list(usage.items()):
            act = activities.get(rid_check)
            if act is None:
                continue
            cap = 5 if is_cafe_act(act) else 3
            if cnt > cap:
                excess = cnt - cap
                positions = [j for j in range(30) if resolve_id(itin[j]) == rid_check]
                for pos in positions[-excess:]:
                    si = pos % 6
                    prev_act = activities.get(resolve_id(itin[pos-1])) if pos > 0 else None
                    prev_lat = prev_act['lat'] if prev_act else act['lat']
                    prev_lng = prev_act['lng'] if prev_act else act['lng']
                    cur_usage = {}
                    for j in range(30):
                        r2 = resolve_id(itin[j])
                        cur_usage[r2] = cur_usage.get(r2, 0) + 1
                    rep = pick_replacement(si, fam_data, cur_usage, prev_lat, prev_lng, rid_check)
                    if rep:
                        itin[pos] = rep
                        rep_fixes_this += 1
                        usage[rid_check] -= 1
                        usage[rep] = usage.get(rep, 0) + 1
                        changed = True
                    else:
                        failed_replacements.append((fid, pos, rid_check, 'no_candidate_rep'))
                break  # restart outer loop after any change

    total_rep_fixes += rep_fixes_this
    output_records.append({'id': fid, 'family': fam['family'], 'itinerary': itin})

# Write output
out_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_halal_family_foodie__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in output_records:
        f.write(json.dumps(rec) + '\n')

print(f'Slot replacements (tag/hours violations): {total_slot_replacements}')
print(f'Repetition fixes: {total_rep_fixes}')
print(f'Failed replacements: {len(failed_replacements)}')
for item in failed_replacements:
    print(f'  FAILED: {item}')
print(f'Output written to: {out_path}')
