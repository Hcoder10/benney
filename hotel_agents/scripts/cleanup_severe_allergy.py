import json, math, re
from collections import Counter

DATA_DIR = 'C:/Users/sarta/rosea/hotel_agents/data'

# ---- Load activities ----
with open(f'{DATA_DIR}/activities_bay.json') as f:
    acts_list = json.load(f)
acts = {a['id']: a for a in acts_list}

# ---- Load families ----
with open(f'{DATA_DIR}/families_part_severe_allergy_family__b0.jsonl') as f:
    families = [json.loads(l) for l in f if l.strip()]

# ---- Budget tier ordering ----
BUDGET_ORDER = {'shoestring': 0, 'budget': 1, 'mid': 2, 'premium': 3, 'luxury': 4}

# ---- Slot definitions (index, window_start_h, window_end_h, required_tags) ----
SLOTS = [
    (0, 7, 9,   {'cafe', 'coffee', 'bakery', 'breakfast'}),
    (1, 8, 10,  {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'}),
    (2, 10, 13, {'museum', 'tour', 'outdoor', 'hiking', 'campus', 'tech', 'art', 'science', 'history', 'gardens', 'walking', 'shopping', 'viewpoint', 'landmark', 'architecture'}),
    (3, 12, 17, {'restaurant', 'lunch', 'casual', 'park', 'scenic', 'beach', 'outdoor', 'hiking', 'tour', 'shopping', 'winery', 'wine', 'tasting'}),
    (4, 17, 20, {'restaurant', 'dinner', 'fine-dining', 'scenic', 'sunset', 'wine', 'casual'}),
    (5, 20, 23, {'bar', 'cocktails', 'nightlife', 'lounge', 'dinner', 'fine-dining', 'speakeasy'}),
]


def parse_hour(t):
    t = t.strip()
    pm = 'PM' in t.upper()
    am = 'AM' in t.upper()
    t_clean = t.upper().replace('AM', '').replace('PM', '').strip()
    parts = t_clean.split(':')
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    if pm and h != 12:
        h += 12
    if am and h == 12:
        h = 0
    return h + m / 60.0


def parse_open_hours(oh_str):
    if not oh_str or oh_str.lower().strip() in ('closed', 'n/a', 'none', ''):
        return None
    if '24' in oh_str.lower() or 'always' in oh_str.lower():
        return (0, 24)
    m = re.match(r'(.+?)\s*[-–]\s*(.+)', oh_str)
    if m:
        try:
            return (parse_hour(m.group(1)), parse_hour(m.group(2)))
        except Exception:
            return None
    return None


def hours_overlap(open_range, slot_start, slot_end):
    """ANY overlap between activity open hours and slot window."""
    if open_range is None:
        return False
    o_start, o_end = open_range
    return o_start < slot_end and o_end > slot_start


def normalize_tags(act):
    raw = act.get('tags', [])
    normalized = set()
    for t in raw:
        tl = t.lower().strip()
        normalized.add(tl)
        normalized.add(tl.replace(' ', '-').replace('_', '-'))
        normalized.add(tl.replace('-', ' ').replace('_', ' '))
    return normalized


def tags_match_slot(act, slot_required_tags):
    act_tags = normalize_tags(act)
    return bool(act_tags & slot_required_tags)


def get_fri_hours(act):
    oh = act.get('open_hours', {})
    return parse_open_hours(oh.get('fri', ''))


def dist_km(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---- Typo/alias normalization map ----
ID_ALIAS = {
    'calisfia_cafe': 'calafia_cafe',
    'children_discovery_museum': 'childrens_discovery_museum',
    'fisher_mans_wharf_tourist': 'fishermans_wharf_tourist',
    'li_holiho_yacht_club': 'liholiho_yacht_club',
    'mission_district_burrito_crawl': 'mission_burrito_crawl',
    'oren_hummus': 'orens_hummus',
    'palace_of_fine_arts': 'palace_fine_arts',
    'pescadero_state_beach': 'pomponio_state_beach',
    'santa_row_shopping': 'santana_row_shopping',
    'cascade_falls_trail': 'lands_end_trail',
}


def find_replacement(slot_type_idx, fam, used_counts, prefer_lat=None, prefer_lng=None, exclude=None):
    _, w_start, w_end, req_tags = SLOTS[slot_type_idx]
    fam_budget = BUDGET_ORDER.get(fam.get('budget_tier', 'mid'), 2)
    fam_kid_ages = fam.get('kid_ages', 'none')
    fam_mobility = fam.get('mobility', 'full')
    exclude = exclude or set()

    candidates = []
    for aid, act in acts.items():
        if aid in exclude:
            continue
        if not tags_match_slot(act, req_tags):
            continue
        fri_h = get_fri_hours(act)
        if not hours_overlap(fri_h, w_start, w_end):
            continue
        if not act.get('kid_ok', True) and fam_kid_ages != 'none':
            continue
        if not act.get('mobility_ok', True) and fam_mobility != 'full':
            continue
        act_budget = BUDGET_ORDER.get(act.get('budget_tier', 'mid'), 2)
        if act_budget > fam_budget:
            continue
        is_cafe = bool({'cafe', 'coffee', 'bakery'} & normalize_tags(act))
        max_uses = 5 if is_cafe else 3
        if used_counts.get(aid, 0) >= max_uses:
            continue
        geo_dist = 0
        if prefer_lat is not None:
            geo_dist = dist_km(prefer_lat, prefer_lng, act['lat'], act['lng'])
        candidates.append((geo_dist, used_counts.get(aid, 0), aid))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


# ---- Stats ----
total_alias_fixes = 0
total_tag_fixes = 0
total_hours_fixes = 0
total_rep_fixes = 0
total_no_replacement = []
cleaned_families = []

for fam_rec in families:
    fam = fam_rec['family']
    itin = list(fam_rec['itinerary'])

    # Step 1: Normalize aliases
    for i, aid in enumerate(itin):
        if aid in ID_ALIAS:
            new_id = ID_ALIAS[aid]
            print(f'[ALIAS] {fam_rec["id"]} slot {i}: {aid} -> {new_id}')
            itin[i] = new_id
            total_alias_fixes += 1

    # Step 2: Tag + hours violations
    for i in range(len(itin)):
        aid = itin[i]
        slot_type = i % 6
        _, w_start, w_end, req_tags = SLOTS[slot_type]

        act = acts.get(aid)
        if act is None:
            print(f'[MISSING] {fam_rec["id"]} slot {i}: {aid} not in bank')
            used_counts = Counter(itin)
            prev_lat = acts.get(itin[i - 1], {}).get('lat') if i > 0 else None
            prev_lng = acts.get(itin[i - 1], {}).get('lng') if i > 0 else None
            repl = find_replacement(slot_type, fam, used_counts, prev_lat, prev_lng, exclude=set(itin))
            if repl:
                itin[i] = repl
                total_tag_fixes += 1
            else:
                total_no_replacement.append((fam_rec['id'], i, aid, 'missing_no_repl'))
            continue

        tag_ok = tags_match_slot(act, req_tags)
        fri_h = get_fri_hours(act)
        hours_ok = hours_overlap(fri_h, w_start, w_end)

        if not tag_ok or not hours_ok:
            reasons = []
            if not tag_ok:
                reasons.append(f'tag_fail(slot={slot_type},tags={act["tags"]})')
            if not hours_ok:
                fri_str = act.get('open_hours', {}).get('fri', '?')
                reasons.append(f'hours_fail(fri="{fri_str}",window={w_start}-{w_end}h)')

            used_counts = Counter(itin)
            prev_lat = acts.get(itin[i - 1], {}).get('lat') if i > 0 else None
            prev_lng = acts.get(itin[i - 1], {}).get('lng') if i > 0 else None
            repl = find_replacement(slot_type, fam, used_counts, prev_lat, prev_lng, exclude={aid})

            if repl:
                print(f'[FIX] {fam_rec["id"]} slot {i} (type {slot_type}): {aid} -> {repl} | {"; ".join(reasons)}')
                itin[i] = repl
                if not tag_ok:
                    total_tag_fixes += 1
                else:
                    total_hours_fixes += 1
            else:
                print(f'[NO_REPL] {fam_rec["id"]} slot {i} (type {slot_type}): {aid} | {"; ".join(reasons)}')
                total_no_replacement.append((fam_rec['id'], i, aid, '; '.join(reasons)))

    # Step 3: Repetition cap
    used_counts = Counter(itin)
    for aid_check, cnt in list(used_counts.items()):
        act_check = acts.get(aid_check)
        is_cafe = bool({'cafe', 'coffee', 'bakery'} & normalize_tags(act_check)) if act_check else False
        max_uses = 5 if is_cafe else 3
        if cnt > max_uses:
            positions = [i for i, x in enumerate(itin) if x == aid_check]
            excess_positions = positions[max_uses:]
            print(f'[REP] {fam_rec["id"]}: {aid_check} used {cnt}x (max {max_uses}), fixing slots {excess_positions}')
            for pos in excess_positions:
                slot_type = pos % 6
                prev_lat = acts.get(itin[pos - 1], {}).get('lat') if pos > 0 else None
                prev_lng = acts.get(itin[pos - 1], {}).get('lng') if pos > 0 else None
                uc = Counter(itin)
                repl = find_replacement(slot_type, fam, uc, prev_lat, prev_lng, exclude={aid_check})
                if repl:
                    print(f'  slot {pos}: {aid_check} -> {repl}')
                    itin[pos] = repl
                    total_rep_fixes += 1
                else:
                    print(f'  slot {pos}: {aid_check} — no replacement found')
                    total_no_replacement.append((fam_rec['id'], pos, aid_check, 'rep_cap_no_repl'))

    cleaned_families.append({'id': fam_rec['id'], 'family': fam, 'itinerary': itin})

# ---- Write output ----
out_path = f'{DATA_DIR}/families_clean_severe_allergy_family__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in cleaned_families:
        f.write(json.dumps(rec) + '\n')

print()
print('=== SUMMARY ===')
print(f'Families processed     : {len(cleaned_families)}')
print(f'Alias/typo fixes       : {total_alias_fixes}')
print(f'Tag violation fixes    : {total_tag_fixes}')
print(f'Hours violation fixes  : {total_hours_fixes}')
print(f'Repetition cap fixes   : {total_rep_fixes}')
print(f'Unresolved slots       : {len(total_no_replacement)}')
for nr in total_no_replacement:
    print(f'  {nr}')
print(f'Output: {out_path}')
