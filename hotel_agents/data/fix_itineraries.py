import json, re
from copy import deepcopy

# ---- helpers ----------------------------------------------------------------

def parse_time_range(s):
    """Return list of (start_min, end_min) tuples from open_hours string."""
    if not s or s.lower().strip() in ('closed',):
        return None
    s = s.strip()
    if '24h' in s.lower() or s == '24':
        return [(0, 1440)]
    s = re.sub(r'sunset', '20:00', s, flags=re.I)
    s = re.sub(r'sunrise', '06:00', s, flags=re.I)

    def to24(t):
        t = t.strip()
        m = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)?', t, re.I)
        if not m:
            m2 = re.match(r'(\d{1,2})\s*(AM|PM)', t, re.I)
            if m2:
                h = int(m2.group(1))
                ap = m2.group(2).upper()
                if ap == 'PM' and h != 12: h += 12
                if ap == 'AM' and h == 12: h = 0
                return h * 60
            return None
        h, mn, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or '').upper()
        if ap == 'PM' and h != 12: h += 12
        if ap == 'AM' and h == 12: h = 0
        return h * 60 + mn

    # Handle comma-separated dual ranges e.g. "11:00-15:00, 17:00-22:00"
    if ',' in s:
        ranges = [r.strip() for r in s.split(',')]
        results = []
        for r in ranges:
            pr = re.split(r'\s*[-]\s*(?=\d)', r, maxsplit=1)
            if len(pr) == 2:
                st = to24(pr[0])
                en = to24(pr[1])
                if st is not None and en is not None:
                    results.append((st, en))
        return results if results else None

    parts = re.split(r'\s*[-]\s*(?=\d)', s, maxsplit=1)
    if len(parts) == 2:
        st = to24(parts[0])
        en = to24(parts[1])
        if st is not None and en is not None:
            return [(st, en)]
    return None


def slot_window(slot_idx):
    windows = [
        (7*60, 9*60),
        (8*60, 10*60),
        (10*60, 13*60),
        (12*60, 17*60),
        (17*60, 20*60),
        (20*60, 23*60),
    ]
    return windows[slot_idx % 6]


SLOT_TAGS = [
    {'cafe','coffee','bakery','breakfast'},
    {'cafe','coffee','bakery','breakfast','brunch'},
    {'museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'},
    {'restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'},
    {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'},
    {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'},
]


def tags_ok(act_tags, slot_type):
    req = SLOT_TAGS[slot_type]
    act_set = {t.lower() for t in act_tags}
    # also check hyphen/underscore variants
    act_set_norm = {t.replace('_','-') for t in act_set} | {t.replace('-','_') for t in act_set}
    req_norm = {t.replace('_','-') for t in req} | {t.replace('-','_') for t in req}
    return bool(act_set & req) or bool(act_set_norm & req_norm)


def hours_overlap(open_hours_val, sw_start, sw_end):
    if not open_hours_val:
        return False
    ranges = parse_time_range(open_hours_val)
    if not ranges:
        return False
    for (s, e) in ranges:
        if s < sw_end and e > sw_start:
            return True
    return False


# ---- load data --------------------------------------------------------------
with open('activities_bay.json', 'r') as f:
    activities = json.load(f)
act_map = {a['id']: a for a in activities}

# Normalize tags: lowercase, deduplicate
for a in activities:
    seen = set()
    normalized = []
    for t in a['tags']:
        tn = t.lower().strip()
        if tn not in seen:
            seen.add(tn)
            normalized.append(tn)
    a['tags'] = normalized

families = []
with open('families_part_newborn_parents_getaway__b0.jsonl', 'r') as f:
    for line in f:
        line = line.strip()
        if line:
            families.append(json.loads(line))

BUDGET_ORDER = ['shoestring', 'mid', 'premium', 'luxury']


def budget_ok(act_budget, fam_budget):
    ai = BUDGET_ORDER.index(act_budget) if act_budget in BUDGET_ORDER else 99
    fi = BUDGET_ORDER.index(fam_budget) if fam_budget in BUDGET_ORDER else 99
    return ai <= fi


def is_cafe(aid):
    if aid not in act_map:
        return False
    return any(t in ('cafe', 'coffee', 'bakery', 'breakfast', 'brunch') for t in act_map[aid]['tags'])


def max_allowed(aid):
    return 5 if is_cafe(aid) else 3


def build_pool(slot_type, fam):
    sw_start, sw_end = slot_window(slot_type)
    pool = []
    for a in activities:
        if not tags_ok(a['tags'], slot_type):
            continue
        fri = a['open_hours'].get('fri', 'closed') or 'closed'
        if not hours_overlap(fri, sw_start, sw_end):
            continue
        if not a['kid_ok'] and fam.get('kid_ages', 'none') != 'none':
            continue
        if not a['mobility_ok'] and fam.get('mobility', 'full') != 'full':
            continue
        if not budget_ok(a['budget_tier'], fam['budget_tier']):
            continue
        pool.append(a['id'])
    return pool


# ---- audit & fix per family ------------------------------------------------
total_slot_replacements = 0
total_repetition_fixes = 0
unfixable = []
cleaned_families = []

# Log violations for reporting
violation_log = []

for fam_rec in families:
    fam = fam_rec['family']
    itinerary = list(fam_rec['itinerary'])
    n = len(itinerary)

    new_itinerary = list(itinerary)
    slot_replacements = 0
    repetition_fixes = 0

    # Build initial usage map
    usage = {}
    for aid in new_itinerary:
        usage[aid] = usage.get(aid, 0) + 1

    # Pass 1: fix missing, tag violations, open-hours violations
    for i in range(n):
        aid = new_itinerary[i]
        slot_type = i % 6
        sw_start, sw_end = slot_window(slot_type)

        needs_replace = False
        reason = ''

        if aid not in act_map:
            needs_replace = True
            reason = f'missing from bank'
        else:
            act = act_map[aid]
            fri = act['open_hours'].get('fri', 'closed') or 'closed'
            tag_good = tags_ok(act['tags'], slot_type)
            hours_good = hours_overlap(fri, sw_start, sw_end)
            if not tag_good:
                needs_replace = True
                reason = f'tag mismatch (tags={act["tags"]}, slot={slot_type})'
            elif not hours_good:
                needs_replace = True
                reason = f'hours closed (fri={fri}, window={sw_start//60}:{sw_start%60:02d}-{sw_end//60}:{sw_end%60:02d})'

        if needs_replace:
            violation_log.append(f'{fam_rec["id"]} slot {i} ({aid}): {reason}')
            pool = build_pool(slot_type, fam)
            candidates = [c for c in pool if c != aid and usage.get(c, 0) < max_allowed(c)]
            if candidates:
                chosen = candidates[0]
                usage[aid] = usage.get(aid, 0) - 1
                if usage[aid] <= 0:
                    del usage[aid]
                usage[chosen] = usage.get(chosen, 0) + 1
                new_itinerary[i] = chosen
                slot_replacements += 1
            else:
                unfixable.append(f'{fam_rec["id"]} slot {i} ({aid}): {reason} - no candidate')

    # Rebuild usage after pass 1
    usage = {}
    for aid in new_itinerary:
        usage[aid] = usage.get(aid, 0) + 1

    # Pass 2: fix repetition violations
    # Process from back so we replace later occurrences first
    for i in range(n - 1, -1, -1):
        aid = new_itinerary[i]
        ma = max_allowed(aid)
        if usage.get(aid, 0) > ma:
            slot_type = i % 6
            pool = build_pool(slot_type, fam)
            candidates = [c for c in pool if c != aid and usage.get(c, 0) < max_allowed(c)]
            if candidates:
                chosen = candidates[0]
                violation_log.append(f'{fam_rec["id"]} slot {i} ({aid}): repetition count={usage[aid]} > {ma}')
                usage[aid] -= 1
                if usage[aid] <= 0:
                    del usage[aid]
                usage[chosen] = usage.get(chosen, 0) + 1
                new_itinerary[i] = chosen
                repetition_fixes += 1
            else:
                unfixable.append(f'{fam_rec["id"]} slot {i} ({aid}): repetition {usage[aid]}>{ma} - no candidate')

    total_slot_replacements += slot_replacements
    total_repetition_fixes += repetition_fixes

    out_rec = deepcopy(fam_rec)
    out_rec['itinerary'] = new_itinerary
    cleaned_families.append(out_rec)

# Write output
out_path = 'families_clean_newborn_parents_getaway__b0.jsonl'
with open(out_path, 'w') as f:
    for rec in cleaned_families:
        f.write(json.dumps(rec) + '\n')

print(f"=== AUDIT LOG ===")
for v in violation_log:
    print(f"  {v}")

print(f"\n=== RESULTS ===")
print(f"Families processed: {len(cleaned_families)}")
print(f"Slot-type / open-hours replacements: {total_slot_replacements}")
print(f"Repetition-cap fixes: {total_repetition_fixes}")
print(f"Total changes: {total_slot_replacements + total_repetition_fixes}")
if unfixable:
    print(f"Unfixable ({len(unfixable)}):")
    for u in unfixable:
        print(f"  {u}")
else:
    print("Unfixable: 0")
print(f"\nOutput written to: {out_path}")
