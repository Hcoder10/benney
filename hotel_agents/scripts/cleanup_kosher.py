import json, math, re

# ---- Load activity bank ----
with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json', 'r') as f:
    acts = json.load(f)

act_map = {a['id']: a for a in acts}

# ---- ID normalization: map known broken IDs to correct ones ----
ID_FIX = {
    'techinteractive_san_jose': 'tech_interactive_san_jose',
    "phil's_coffee": 'phils_coffee',
    "levi's_stadium_tour": 'levis_stadium_tour',
    'land_end_trail': 'lands_end_trail',
    'conservation_of_flowers': 'conservatory_flowers',
    'philharmonic_evening': None,   # not in bank at all -> mark for replacement
}

# ---- Slot type rules ----
# (slot_mod, label, required_tags_any_of, window_start_h, window_end_h)
SLOT_RULES = [
    (0, 'early-morning',   {'cafe','coffee','bakery','breakfast'},              7, 9),
    (1, 'breakfast',       {'cafe','coffee','bakery','breakfast','brunch'},     8, 10),
    (2, 'late-morning',    {'museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'}, 10, 13),
    (3, 'lunch+afternoon', {'restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'}, 12, 17),
    (4, 'evening',         {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'}, 17, 20),
    (5, 'night',           {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'}, 20, 23),
]

def parse_hour(s):
    s = s.strip()
    if not s or s.lower() == 'closed':
        return None
    m = re.match(r'(\d+):(\d+)\s*(AM|PM)?', s, re.IGNORECASE)
    if m:
        h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if ampm:
            ampm = ampm.upper()
            if ampm == 'PM' and h != 12:
                h += 12
            if ampm == 'AM' and h == 12:
                h = 0
        return h + mi / 60.0
    m2 = re.match(r'(\d+)\s*(AM|PM)', s, re.IGNORECASE)
    if m2:
        h, ampm = int(m2.group(1)), m2.group(2).upper()
        if ampm == 'PM' and h != 12:
            h += 12
        if ampm == 'AM' and h == 12:
            h = 0
        return float(h)
    return None

def parse_open_hours(hours_str):
    """Return a list of (open_h, close_h) tuples, or None if always closed."""
    if not hours_str or hours_str.lower() in ('closed', 'n/a', ''):
        return None
    # Split on comma to handle compound entries like '11:30-14:00, 17:30-22:30'
    segments = [s.strip() for s in hours_str.split(',')]
    result = []
    for seg in segments:
        if not seg:
            continue
        if ' - ' in seg:
            parts = seg.split(' - ', 1)
        else:
            m = re.match(r'^([^-]+?)\s*-\s*(.+)$', seg)
            if m:
                parts = [m.group(1), m.group(2)]
            else:
                continue
        if len(parts) == 2:
            open_h = parse_hour(parts[0])
            close_h = parse_hour(parts[1])
            if open_h is not None and close_h is not None:
                result.append((open_h, close_h))
    return result if result else None


def any_range_overlaps(ranges, win_start, win_end):
    """Check if any (open_h, close_h) range overlaps [win_start, win_end)."""
    if not ranges:
        return False
    for open_h, close_h in ranges:
        if open_h < win_end and close_h > win_start:
            return True
    return False

# hours_overlap is now handled by any_range_overlaps above

def tags_match(act_tags, required_tags):
    return bool(set(act_tags) & required_tags)

def budget_ok(act_budget, family_budget):
    tiers = ['shoestring', 'mid', 'premium', 'luxury']
    try:
        return tiers.index(act_budget) <= tiers.index(family_budget)
    except ValueError:
        return True

def kid_filter(act, family):
    if family['kid_ages'] == 'none':
        return True
    return act['kid_ok']

def mobility_filter(act, family):
    if family['mobility'] == 'full':
        return True
    return act['mobility_ok']

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def is_cafe(act):
    return bool(set(act['tags']) & {'cafe','coffee','bakery','breakfast'})

def max_uses(act):
    return 5 if is_cafe(act) else 3

def check_slot(aid, slot_mod, family):
    """Return (tag_ok, hours_ok)."""
    if aid not in act_map:
        return False, False
    act = act_map[aid]
    rule = SLOT_RULES[slot_mod]
    required_tags = rule[2]
    win_start, win_end = rule[3], rule[4]

    t_ok = tags_match(act['tags'], required_tags)

    # Use fri as representative; fall back to sat
    fri_hours = (act.get('open_hours') or {}).get('fri') or (act.get('open_hours') or {}).get('sat')
    parsed = parse_open_hours(fri_hours) if fri_hours else None
    h_ok = any_range_overlaps(parsed, win_start, win_end) if parsed else False

    return t_ok, h_ok

def find_replacement(slot_mod, family, itinerary, current_counts, prev_lat=None, prev_lng=None, exclude_id=None):
    rule = SLOT_RULES[slot_mod]
    required_tags = rule[2]
    win_start, win_end = rule[3], rule[4]

    candidates = []
    for act in acts:
        aid = act['id']
        if aid == exclude_id:
            continue
        if not tags_match(act['tags'], required_tags):
            continue
        fri_hours = (act.get('open_hours') or {}).get('fri') or (act.get('open_hours') or {}).get('sat')
        parsed = parse_open_hours(fri_hours) if fri_hours else None
        if not parsed or not any_range_overlaps(parsed, win_start, win_end):
            continue
        if not budget_ok(act['budget_tier'], family['budget_tier']):
            continue
        if not kid_filter(act, family):
            continue
        if not mobility_filter(act, family):
            continue
        cnt = current_counts.get(aid, 0)
        if cnt >= max_uses(act):
            continue
        dist = 0.0
        if prev_lat is not None and act.get('lat') is not None:
            dist = haversine(prev_lat, prev_lng, act['lat'], act['lng'])
        candidates.append((dist, cnt, aid))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]

# ---- Main processing ----
total_slot_replacements = 0
total_repetition_fixes = 0
unfixable = []

output_lines = []

with open('C:/Users/sarta/rosea/hotel_agents/data/families_part_kosher_orthodox_family__b0.jsonl') as f:
    records = [json.loads(line) for line in f]

for rec in records:
    fam_id = rec['id']
    family = rec['family']
    itinerary = list(rec['itinerary'])

    # Step 1: Normalize broken IDs (keep original string as fallback key for unfixable)
    original_ids = list(itinerary)
    for i, aid in enumerate(itinerary):
        if aid in ID_FIX:
            fixed = ID_FIX[aid]
            itinerary[i] = fixed if fixed is not None else '__NEED_REPLACE__'

    slot_fixes_this = 0
    rep_fixes_this = 0

    # Step 2: Multi-pass slot checks (tags + hours)
    for _pass in range(4):
        counts = {}
        for aid in itinerary:
            if aid and aid != '__NEED_REPLACE__' and aid in act_map:
                counts[aid] = counts.get(aid, 0) + 1

        changed = False
        for i in range(30):
            slot_mod = i % 6
            aid = itinerary[i]

            prev_lat, prev_lng = None, None
            if i > 0:
                prev_aid = itinerary[i-1]
                if prev_aid and prev_aid != '__NEED_REPLACE__' and prev_aid in act_map:
                    prev_act = act_map[prev_aid]
                    prev_lat, prev_lng = prev_act.get('lat'), prev_act.get('lng')

            needs_replace = False
            is_rep_fix = False

            if aid == '__NEED_REPLACE__' or not aid or aid not in act_map:
                needs_replace = True
            else:
                t_ok, h_ok = check_slot(aid, slot_mod, family)
                if not t_ok or not h_ok:
                    needs_replace = True
                elif counts.get(aid, 0) > max_uses(act_map[aid]):
                    needs_replace = True
                    is_rep_fix = True

            if needs_replace:
                repl = find_replacement(slot_mod, family, itinerary, counts,
                                        prev_lat, prev_lng,
                                        exclude_id=aid if aid in act_map else None)
                if repl:
                    old = aid
                    if old in act_map:
                        counts[old] = max(0, counts.get(old, 0) - 1)
                    counts[repl] = counts.get(repl, 0) + 1
                    itinerary[i] = repl
                    changed = True
                    if is_rep_fix:
                        rep_fixes_this += 1
                    else:
                        slot_fixes_this += 1
                else:
                    # No valid replacement: restore original ID if slot was a broken-ID placeholder
                    if aid == '__NEED_REPLACE__':
                        itinerary[i] = original_ids[i]  # keep original broken-bank ID as-is
                    key = f'{fam_id} slot {i} ({SLOT_RULES[slot_mod][1]})'
                    if key not in unfixable:
                        unfixable.append(key)

        if not changed:
            break

    # Step 3: Final repetition sweep
    counts = {}
    for aid in itinerary:
        if aid in act_map:
            counts[aid] = counts.get(aid, 0) + 1

    for i in range(30):
        aid = itinerary[i]
        if aid in act_map and counts.get(aid, 0) > max_uses(act_map[aid]):
            slot_mod = i % 6
            prev_lat, prev_lng = None, None
            if i > 0 and itinerary[i-1] in act_map:
                pa = act_map[itinerary[i-1]]
                prev_lat, prev_lng = pa.get('lat'), pa.get('lng')
            repl = find_replacement(slot_mod, family, itinerary, counts, prev_lat, prev_lng, exclude_id=aid)
            if repl:
                counts[aid] -= 1
                counts[repl] = counts.get(repl, 0) + 1
                itinerary[i] = repl
                rep_fixes_this += 1

    total_slot_replacements += slot_fixes_this
    total_repetition_fixes += rep_fixes_this
    print(f'{fam_id}: {slot_fixes_this} slot fixes, {rep_fixes_this} repetition fixes')

    output_lines.append(json.dumps({'id': fam_id, 'family': family, 'itinerary': itinerary}))

with open('C:/Users/sarta/rosea/hotel_agents/data/families_clean_kosher_orthodox_family__b0.jsonl', 'w') as f:
    for line in output_lines:
        f.write(line + '\n')

print()
print(f'=== TOTALS: {total_slot_replacements} slot-type/hours replacements, {total_repetition_fixes} repetition fixes ===')
if unfixable:
    print('UNFIXABLE slots:', unfixable)
else:
    print('All slots resolved.')
