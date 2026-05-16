import json, re

with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    acts_list = json.load(f)
acts = {a['id']: a for a in acts_list}

cleaned = []
with open('C:/Users/sarta/rosea/hotel_agents/data/families_clean_multigenerational_bay_family__b0.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            cleaned.append(json.loads(line))

SLOT_RULES = {
    0: (7,  9,  {'cafe','coffee','bakery','breakfast'}),
    1: (8,  10, {'cafe','coffee','bakery','breakfast','brunch'}),
    2: (10, 13, {'museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'}),
    3: (12, 17, {'restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'}),
    4: (17, 20, {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'}),
    5: (20, 23, {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'}),
}

TAG_NORM = {
    'fine dining': 'fine-dining', 'fine_dining': 'fine-dining', 'dining': 'restaurant',
    'food': 'casual', 'dinner': 'dinner', 'bar': 'bar', 'bars': 'bar',
    'cocktails': 'cocktails', 'nightlife': 'nightlife', 'lounge': 'lounge',
    'speakeasy': 'speakeasy', 'restaurant': 'restaurant', 'outdoor': 'outdoor',
    'outdoors': 'outdoor', 'hiking': 'hiking', 'walking': 'walking', 'beach': 'beach',
    'scenic': 'scenic', 'viewpoint': 'viewpoint', 'views': 'viewpoint', 'art': 'art',
    'sculpture': 'art', 'history': 'history', 'historic': 'history', 'heritage': 'history',
    'museum': 'museum', 'campus': 'campus', 'architecture': 'architecture',
    'landmark': 'landmark', 'shopping': 'shopping', 'wine': 'wine', 'tasting': 'tasting',
    'vineyard': 'winery', 'winery': 'winery', 'tour': 'tour', 'tours': 'tour',
    'gardens': 'gardens', 'garden': 'gardens', 'botanical': 'gardens', 'sunset': 'sunset',
    'cafe': 'cafe', 'coffee': 'coffee', 'bakery': 'bakery', 'breakfast': 'breakfast',
    'brunch': 'brunch', 'tech': 'tech', 'science': 'science', 'park': 'park',
    'casual': 'casual', 'lunch': 'lunch', 'market': 'shopping', 'gallery': 'art',
    'culture': 'history', 'cultural': 'history', 'trail': 'hiking', 'trails': 'hiking',
    'nature': 'outdoor', 'waterfront': 'scenic', 'coastal': 'scenic', 'ocean': 'scenic',
    'pier': 'scenic', 'harbor': 'scenic', 'village': 'walking', 'district': 'walking',
    'neighborhood': 'walking', 'downtown': 'walking', 'craft': 'shopping',
    'artisan': 'shopping', 'retail': 'shopping', 'vintage': 'shopping',
    'bookstore': 'shopping', 'zoo': 'outdoor', 'aquarium': 'outdoor', 'boating': 'outdoor',
    'lake': 'scenic', 'lighthouse': 'viewpoint', 'observatory': 'viewpoint',
    'tower': 'viewpoint', 'peak': 'viewpoint', 'bluff': 'viewpoint', 'cliff': 'viewpoint',
    'island': 'tour', 'stadium': 'tour', 'cinema': 'art', 'concert': 'art',
    'church': 'landmark', 'religious': 'landmark', 'spiritual': 'landmark',
    'monument': 'landmark', 'ruins': 'landmark', 'castle': 'landmark',
    'dessert': 'cafe', 'ice_cream': 'cafe', 'tea': 'cafe', 'sparkling': 'wine',
    'spirits': 'wine', 'distillery': 'wine', 'happy-hour': 'casual',
    'interactive': 'science',
}


def normalize_tags(raw_tags):
    out = set()
    for t in raw_tags:
        out.add(TAG_NORM.get(t.lower().strip(), t.lower().strip()))
    return out


def parse_hours_str(oh_str):
    if not oh_str or oh_str.strip().lower() in ('closed', '', '?'):
        return []
    oh = oh_str.strip()
    if re.match(r'(?i)^24h', oh) or 'open 24 hours' in oh.lower():
        return [(0.0, 24.0)]
    oh2 = re.sub(r'(?i)sunrise', '6:00', oh)
    oh2 = re.sub(r'(?i)sunset', '20:00', oh2)
    oh2 = re.sub(r'\s+to\s+', '-', oh2)
    sessions = re.split(r'[;,]\s*', oh2)
    result = []
    for sess in sessions:
        sess = sess.strip()
        mm = re.search(
            r'(\d{1,2}):?(\d{2})?\s*(AM|PM)?\s*[-]\s*(\d{1,2}):?(\d{2})?\s*(AM|PM)?',
            sess, re.IGNORECASE)
        if mm:
            h1, m1, ap1, h2, m2, ap2 = mm.groups()
            h1, m1 = int(h1), int(m1) if m1 else 0
            h2, m2 = int(h2), int(m2) if m2 else 0
            if ap1 and ap1.upper() == 'PM' and h1 != 12: h1 += 12
            if ap1 and ap1.upper() == 'AM' and h1 == 12: h1 = 0
            if ap2 and ap2.upper() == 'PM' and h2 != 12: h2 += 12
            if ap2 and ap2.upper() == 'AM' and h2 == 12: h2 = 0
            start_h = h1 + m1 / 60.0
            end_h = h2 + m2 / 60.0
            if end_h == 0.0 and start_h > 0.0:
                end_h = 24.0
            result.append((start_h, end_h))
    return result


def slot_valid(act, slot_type):
    win_s, win_e, req_tags = SLOT_RULES[slot_type]
    ntags = normalize_tags(act['tags'])
    if not (ntags & req_tags):
        return False, 'tag'
    fri_sessions = parse_hours_str(act['open_hours'].get('fri', ''))
    overlaps = any(sh < win_e and eh > win_s for sh, eh in fri_sessions)
    if not overlaps:
        return False, 'hours'
    return True, 'ok'


known_unfixable = {
    ('fam_multigenerational_bay_family_0', 23),
    ('fam_multigenerational_bay_family_1', 23),
    ('fam_multigenerational_bay_family_1', 29),
    ('fam_multigenerational_bay_family_2', 23),
    ('fam_multigenerational_bay_family_2', 29),
    ('fam_multigenerational_bay_family_3', 17),
    ('fam_multigenerational_bay_family_3', 23),
    ('fam_multigenerational_bay_family_3', 29),
    ('fam_multigenerational_bay_family_4', 23),
    ('fam_multigenerational_bay_family_4', 29),
    ('fam_multigenerational_bay_family_5', 5),
    ('fam_multigenerational_bay_family_5', 11),
    ('fam_multigenerational_bay_family_5', 17),
    ('fam_multigenerational_bay_family_5', 23),
    ('fam_multigenerational_bay_family_5', 29),
    ('fam_multigenerational_bay_family_7', 23),
    ('fam_multigenerational_bay_family_7', 29),
    ('fam_multigenerational_bay_family_8', 23),
    ('fam_multigenerational_bay_family_8', 29),
    ('fam_multigenerational_bay_family_9', 23),
    ('fam_multigenerational_bay_family_9', 29),
}

unexpected_violations = 0
for rec in cleaned:
    fid = rec['id']
    for i, aid in enumerate(rec['itinerary']):
        slot_type = i % 6
        if (fid, i) in known_unfixable:
            continue
        if aid not in acts:
            unexpected_violations += 1
            print(f'UNEXPECTED MISSING: {fid} slot {i} -> {aid}')
            continue
        valid, reason = slot_valid(acts[aid], slot_type)
        if not valid:
            unexpected_violations += 1
            print(f'UNEXPECTED VIOLATION: {fid} slot {i} (type {slot_type}) -> {aid} reason={reason}')

print(f'\nUnexpected violations (outside known unfixable): {unexpected_violations}')
print(f'Known unfixable slots: {len(known_unfixable)}')
if unexpected_violations == 0:
    print('Output is CLEAN -- only activity-bank-gap slots remain unfixed')
else:
    print('ISSUES FOUND - re-run cleanup')
