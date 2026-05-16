"""
cleanup_b0.py — generic cleanup skeleton (see family-specific variants for usage).
This file is kept for reference; actual per-batch cleanup scripts live alongside it.
"""
import json, re

# --- Load data ---
with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json', 'r') as f:
    activities = json.load(f)
act_map = {a['id']: a for a in activities}

# --- Slot rules ---
SLOT_TAGS = [
    {'cafe','coffee','bakery','breakfast'},              # 0 early-morning 7-9
    {'cafe','coffee','bakery','breakfast','brunch'},     # 1 breakfast 8-10
    {'museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'},  # 2 late-morning 10-1
    {'restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'},  # 3 lunch+afternoon 12-5
    {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'},  # 4 evening 5-8
    {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'},  # 5 night 8-11
]

# Slot time windows as (start_h, end_h) in 24h
SLOT_WINDOWS = [
    (7, 9),
    (8, 10),
    (10, 13),
    (12, 17),
    (17, 20),
    (20, 23),
]

# Tag normalization: map variant forms to canonical slot-rule forms
TAG_NORMALIZE = {
    'fine dining':  'fine-dining',
    'fine_dining':  'fine-dining',
    'bars':         'bar',
    'outdoors':     'outdoor',
    'tours':        'tour',
}

def normalize_tag(t):
    t = t.lower().strip()
    return TAG_NORMALIZE.get(t, t)

def parse_hour(s):
    s = s.strip()
    if not s or s.lower() == 'closed':
        return None
    m = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?', s, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mn = int(m.group(2)) if m.group(2) else 0
        ap = m.group(3)
        frac = h + mn/60
        if ap:
            if ap.upper() == 'PM' and h != 12:
                frac += 12
            elif ap.upper() == 'AM' and h == 12:
                frac = mn/60
        return frac
    return None

def parse_open_hours(oh_str):
    """Return list of (open_h, close_h) segments, or None if unparseable/closed."""
    if not oh_str or oh_str.lower() in ('closed', ''):
        return None
    if '24h' in oh_str.lower() or 'sunrise' in oh_str.lower() or 'always' in oh_str.lower():
        return [(0, 24)]
    segments = []
    for part in oh_str.split(','):
        part = part.strip()
        m = re.match(r'(\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)\s*[-–]\s*(\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)', part, re.IGNORECASE)
        if m:
            o = parse_hour(m.group(1))
            c = parse_hour(m.group(2))
            if o is not None and c is not None:
                segments.append((o, c))
    return segments if segments else None

def get_fri_hours(act):
    oh = act.get('open_hours', {})
    return oh.get('fri') or oh.get('sat') or oh.get('thu') or oh.get('sun') or ''

def open_hours_ok(act_id, slot_idx):
    """ANY-overlap: at least one activity segment overlaps the slot time window."""
    if act_id not in act_map:
        return False
    act = act_map[act_id]
    oh_str = get_fri_hours(act)
    if not oh_str or oh_str.lower() == 'closed':
        return False
    segments = parse_open_hours(oh_str)
    if segments is None:
        # Cannot parse hours — assume open (don't penalize)
        return True
    slot_start, slot_end = SLOT_WINDOWS[slot_idx % 6]
    # ANY segment that overlaps the slot window
    return any(o < slot_end and c > slot_start for (o, c) in segments)

def tags_ok(act_id, slot_idx):
    if act_id not in act_map:
        return False
    act = act_map[act_id]
    act_tags = {normalize_tag(t) for t in act.get('tags', [])}
    required = SLOT_TAGS[slot_idx % 6]
    return bool(act_tags & required)

def family_ok(act_id, family):
    if act_id not in act_map:
        return False
    act = act_map[act_id]
    if not act.get('kid_ok', True) and family.get('kid_ages', 'none') != 'none':
        return False
    if not act.get('mobility_ok', True) and family.get('mobility', 'full') != 'full':
        return False
    tier_order = {'budget': 0, 'shoestring': 0, 'mid': 1, 'premium': 2, 'luxury': 3}
    fam_tier = tier_order.get(family.get('budget_tier', 'mid'), 1)
    act_tier = tier_order.get(act.get('budget_tier', 'mid'), 1)
    if act_tier > fam_tier:
        return False
    return True

def is_cafe(act_id):
    if act_id not in act_map:
        return False
    tags = {normalize_tag(t) for t in act_map[act_id].get('tags', [])}
    return bool(tags & {'cafe', 'coffee'})

def count_all(itin):
    c = {}
    for aid in itin:
        c[aid] = c.get(aid, 0) + 1
    return c

def find_replacement(slot_idx, family, itinerary_counts, prefer_lat=None, prefer_lng=None, exclude_ids=None):
    required_tags = SLOT_TAGS[slot_idx % 6]
    exclude_ids = exclude_ids or set()
    candidates = []
    for act in activities:
        aid = act['id']
        if aid in exclude_ids:
            continue
        act_tags = {normalize_tag(t) for t in act.get('tags', [])}
        if not (act_tags & required_tags):
            continue
        if not open_hours_ok(aid, slot_idx):
            continue
        if not family_ok(aid, family):
            continue
        count = itinerary_counts.get(aid, 0)
        max_count = 5 if is_cafe(aid) else 3
        if count >= max_count:
            continue
        score = 0
        if prefer_lat is not None and prefer_lng is not None:
            dlat = act.get('lat', 0) - prefer_lat
            dlng = act.get('lng', 0) - prefer_lng
            score = (dlat**2 + dlng**2)**0.5
        candidates.append((score, aid))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def run_cleanup(input_path, output_path):
    """Run full cleanup pipeline on a JSONL file and write cleaned output."""
    all_records = []
    with open(input_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                all_records.append(json.loads(line))

    total_slot_replacements = 0
    total_repetition_fixes = 0
    no_replacement_found = []
    cleaned_records = []

    for rec in all_records:
        family = rec['family']
        itinerary = list(rec['itinerary'])
        fam_id = rec['id']

        slot_replacements = 0
        repetition_fixes = 0

        # First pass: fix missing IDs, slot-type violations, open-hours violations
        for i in range(len(itinerary)):
            act_id = itinerary[i]
            si = i % 6

            # Missing from bank
            if act_id not in act_map:
                reason = ['missing_id']
            else:
                reason = []
                if not tags_ok(act_id, si):
                    reason.append('tag')
                if not open_hours_ok(act_id, si):
                    reason.append('hours')

            if reason:
                prev_lat = act_map.get(itinerary[i-1], {}).get('lat') if i > 0 else None
                prev_lng = act_map.get(itinerary[i-1], {}).get('lng') if i > 0 else None
                counts = count_all(itinerary)
                counts[act_id] = max(0, counts.get(act_id, 1) - 1)
                repl = find_replacement(si, family, counts, prefer_lat=prev_lat, prefer_lng=prev_lng, exclude_ids={act_id})
                if repl:
                    print(f'  [{fam_id}] slot {i} (day{i//6+1} s{si}): {act_id} ({",".join(reason)}) -> {repl}')
                    itinerary[i] = repl
                    slot_replacements += 1
                else:
                    print(f'  [{fam_id}] slot {i}: NO REPLACEMENT found for {act_id} ({",".join(reason)})')
                    no_replacement_found.append((fam_id, i, act_id, reason))

        # Second pass: repetition cap
        counts = count_all(itinerary)
        for act_id, cnt in list(counts.items()):
            max_count = 5 if is_cafe(act_id) else 3
            if cnt > max_count:
                excess = cnt - max_count
                print(f'  [{fam_id}] repetition: {act_id} appears {cnt}x > {max_count}, replacing {excess}')
                replaced_so_far = 0
                for i in range(len(itinerary) - 1, -1, -1):
                    if itinerary[i] == act_id and replaced_so_far < excess:
                        si = i % 6
                        cur_counts = count_all(itinerary)
                        cur_counts[act_id] = max(0, cur_counts[act_id] - 1)
                        prev_lat = act_map.get(itinerary[i-1], {}).get('lat') if i > 0 else None
                        prev_lng = act_map.get(itinerary[i-1], {}).get('lng') if i > 0 else None
                        repl = find_replacement(si, family, cur_counts, prefer_lat=prev_lat, prefer_lng=prev_lng, exclude_ids={act_id})
                        if repl:
                            print(f'    slot {i} (day{i//6+1} s{si}): {act_id} -> {repl}')
                            itinerary[i] = repl
                            replaced_so_far += 1
                            repetition_fixes += 1
                        else:
                            print(f'    slot {i}: NO REPLACEMENT for repetition fix of {act_id}')
                            no_replacement_found.append((fam_id, i, act_id, ['repetition']))

        total_slot_replacements += slot_replacements
        total_repetition_fixes += repetition_fixes
        cleaned_records.append({'id': rec['id'], 'family': family, 'itinerary': itinerary})

    print()
    print('=== SUMMARY ===')
    print(f'Families processed          : {len(all_records)}')
    print(f'Slot-type/hours replacements: {total_slot_replacements}')
    print(f'Repetition fixes            : {total_repetition_fixes}')
    print(f'Total replacements          : {total_slot_replacements + total_repetition_fixes}')
    print(f'No replacement found        : {len(no_replacement_found)}')
    for item in no_replacement_found:
        print(f'  {item}')

    with open(output_path, 'w') as f:
        for rec in cleaned_records:
            f.write(json.dumps(rec) + '\n')
    print(f'Written to {output_path}')
    return total_slot_replacements, total_repetition_fixes, no_replacement_found


if __name__ == '__main__':
    # Default: wine region b0
    run_cleanup(
        'C:/Users/sarta/rosea/hotel_agents/data/families_part_sales_rep_wine_region__b0.jsonl',
        'C:/Users/sarta/rosea/hotel_agents/data/families_clean_sales_rep_wine_region__b0.jsonl',
    )
