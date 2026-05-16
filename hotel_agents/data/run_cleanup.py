"""
Itinerary audit + repair script for families_part_solo_woman_nature_retreat__b0.jsonl
Following sonnet_cleanup_brief.md rules exactly.
"""
import json
import re
import math
from collections import defaultdict

# ---- Slot rules ----------------------------------------------------------------
SLOT_TAGS = {
    0: {"cafe", "coffee", "bakery", "breakfast"},
    1: {"cafe", "coffee", "bakery", "breakfast", "brunch"},
    2: {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art", "science",
        "history", "gardens", "walking", "shopping", "viewpoint", "landmark", "architecture"},
    3: {"restaurant", "lunch", "casual", "park", "scenic", "beach", "outdoor",
        "hiking", "tour", "shopping", "winery", "wine", "tasting"},
    4: {"restaurant", "dinner", "fine-dining", "scenic", "sunset", "wine", "casual"},
    5: {"bar", "cocktails", "nightlife", "lounge", "dinner", "fine-dining",
        "speakeasy"},
}
SLOT_WINDOW = {
    0: (7, 9),
    1: (8, 10),
    2: (10, 13),
    3: (12, 17),
    4: (17, 20),
    5: (20, 23),
}

# ---- Budget ordering -----------------------------------------------------------
BUDGET_ORDER = {"shoestring": 0, "mid": 1, "premium": 2, "luxury": 3}

# ---- Parse open_hours string ---------------------------------------------------
TIME_RE = re.compile(r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM|am|pm)?', re.IGNORECASE)

def parse_time_str(s):
    """Return (hour_float, hour_float) or None.
    Handles: '17:00', '5:30 PM', '17:00-22:00', '16:00-00:00' (midnight=24),
    'closed', 'varies', 'sunrise-sunset', 'sunrise to sunset', '24h', etc.
    """
    s = s.strip()
    if not s or s.lower() in ('closed', 'varies', 'n/a'):
        return None
    # 24h → all day
    if s.lower() in ('24h', '24/7', 'open 24 hours', '24 hours'):
        return (0.0, 24.0)
    sl = s.lower()
    # Sunrise/sunset patterns — normalise "to" separator too
    # "Sunrise to sunset", "sunrise-sunset", "8:00 AM - sunset"
    has_sunrise = 'sunrise' in sl
    has_sunset = 'sunset' in sl
    if has_sunrise and has_sunset:
        return (6.0, 20.0)
    if has_sunrise:
        # "sunrise to HH:MM"
        parts = re.split(r'\s*(?:to|-)\s*', s, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            t2 = _parse_single(parts[1])
            if t2 is not None:
                return (6.0, t2)
        return (6.0, 20.0)
    if has_sunset:
        # "HH:MM to sunset" or "HH:MM - sunset" or just "sunset"
        parts = re.split(r'\s*(?:to|-)\s*', s, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            t1 = _parse_single(parts[0])
            if t1 is not None:
                return (t1, 20.0)
        return (6.0, 20.0)
    # Split on " - " or " to " or "-" (but not in middle of a time like "5:30")
    # Use a separator that is either " - ", " to ", or a dash between two time tokens
    parts = re.split(r'\s*[-–]\s*|\s+to\s+', s, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        t1 = _parse_single(parts[0])
        t2 = _parse_single(parts[1])
        if t1 is not None and t2 is not None:
            # midnight: if end==0 it means 24:00 (next day)
            if t2 == 0.0:
                t2 = 24.0
            return (t1, t2)
    # single time — treat as open all day up to that time
    t = _parse_single(s)
    if t is not None:
        return (0.0, t)
    return None

def _parse_single(s):
    s = s.strip()
    m = TIME_RE.match(s)
    if not m:
        return None
    h = int(m.group(1))
    mins = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm:
        ampm = ampm.upper()
        if ampm == 'PM' and h != 12:
            h += 12
        elif ampm == 'AM' and h == 12:
            h = 0
    return h + mins / 60.0

def hours_overlap(open_hours_str, slot_idx):
    """Does this activity's open_hours.fri overlap with the slot window?"""
    if open_hours_str is None:
        return False
    parsed = parse_time_str(open_hours_str)
    if parsed is None:
        return False  # closed
    act_start, act_end = parsed
    slot_start, slot_end = SLOT_WINDOW[slot_idx]
    # ANY overlap: act open during any part of slot window
    return act_start < slot_end and act_end > slot_start

# ---- Normalize tags for matching -----------------------------------------------
def normalize_tag(t):
    return t.lower().replace(' ', '-').replace('_', '-')

def activity_matches_slot(act, slot_idx):
    """Check tag overlap AND open_hours overlap for a slot."""
    required = SLOT_TAGS[slot_idx]
    act_tags = {normalize_tag(t) for t in act.get('tags', [])}
    if not required & act_tags:
        return False
    fri_hours = act.get('open_hours', {}).get('fri', None)
    return hours_overlap(fri_hours, slot_idx)

# ---- Distance -------------------------------------------------------------------
def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

# ---- Load data -----------------------------------------------------------------
with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json', 'r', encoding='utf-8') as f:
    activities_list = json.load(f)

# Build lookup dict
ACT = {a['id']: a for a in activities_list}

# ---- Process families ----------------------------------------------------------
INPUT_PATH  = 'C:/Users/sarta/rosea/hotel_agents/data/families_part_solo_woman_nature_retreat__b0.jsonl'
OUTPUT_PATH = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_solo_woman_nature_retreat__b0.jsonl'

families = []
with open(INPUT_PATH, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            families.append(json.loads(line))

total_slot_replacements = 0
total_repetition_fixes = 0
unsatisfied = []

output_records = []

for fam_rec in families:
    fam_id = fam_rec['id']
    fam = fam_rec['family']
    itinerary = list(fam_rec['itinerary'])  # copy

    budget_max = BUDGET_ORDER.get(fam.get('budget_tier', 'mid'), 1)
    kid_ages   = fam.get('kid_ages', 'none')
    mobility   = fam.get('mobility', 'full')

    def family_ok(act):
        """Check kid/mobility/budget constraints."""
        if not act.get('kid_ok', True) and kid_ages != 'none':
            return False
        if not act.get('mobility_ok', True) and mobility != 'full':
            return False
        act_budget = BUDGET_ORDER.get(act.get('budget_tier', 'mid'), 1)
        if act_budget > budget_max:
            return False
        return True

    # ---- Step 1: Fix slot violations (tag + open_hours) ------------------------
    for i in range(30):
        slot_idx = i % 6
        act_id = itinerary[i]
        act = ACT.get(act_id)

        if act is None:
            # Unknown activity — try to replace
            print(f"  [WARN] {fam_id} slot {i}: unknown activity '{act_id}'")

        valid = (act is not None and activity_matches_slot(act, slot_idx) and family_ok(act))
        if not valid:
            # Find a replacement
            # Prefer geographically close to previous slot's activity
            prev_lat, prev_lng = None, None
            if i > 0:
                prev_act = ACT.get(itinerary[i-1])
                if prev_act:
                    prev_lat = prev_act.get('lat')
                    prev_lng = prev_act.get('lng')

            # Count current usage
            current_counts = defaultdict(int)
            for x in itinerary:
                current_counts[x] += 1

            # Score candidates
            candidates = []
            for cand_id, cand in ACT.items():
                if not activity_matches_slot(cand, slot_idx):
                    continue
                if not family_ok(cand):
                    continue
                # Repetition check (don't pick something already at its cap)
                is_cafe = bool({'cafe', 'coffee', 'bakery'} & {normalize_tag(t) for t in cand.get('tags', [])})
                cap = 5 if is_cafe else 3
                if current_counts[cand_id] >= cap:
                    continue
                # Distance score
                dist = 0
                if prev_lat is not None and cand.get('lat') and cand.get('lng'):
                    dist = haversine(prev_lat, prev_lng, cand['lat'], cand['lng'])
                candidates.append((dist, cand_id))

            if candidates:
                candidates.sort()
                replacement_id = candidates[0][1]
                old_id = itinerary[i]
                itinerary[i] = replacement_id
                total_slot_replacements += 1
                print(f"  [REPLACE] {fam_id} slot {i} (slot_idx={slot_idx}): {old_id} -> {replacement_id}")
            else:
                unsatisfied.append((fam_id, i, slot_idx, act_id))
                print(f"  [NO_REPLACEMENT] {fam_id} slot {i} (slot_idx={slot_idx}): kept {act_id}")

    # ---- Step 2: Repetition cap ------------------------------------------------
    for pass_num in range(5):  # up to 5 passes to resolve cascading
        counts = defaultdict(int)
        for x in itinerary:
            counts[x] += 1

        changed_this_pass = False
        for act_id, cnt in list(counts.items()):
            act = ACT.get(act_id)
            is_cafe = act and bool({'cafe', 'coffee', 'bakery'} & {normalize_tag(t) for t in act.get('tags', [])})
            cap = 5 if is_cafe else 3
            if cnt <= cap:
                continue
            # Need to reduce appearances — fix last occurrences
            excess = cnt - cap
            fixed = 0
            for i in range(29, -1, -1):
                if fixed >= excess:
                    break
                if itinerary[i] != act_id:
                    continue
                slot_idx = i % 6

                # Count usage up to this point (excluding this slot)
                current_counts = defaultdict(int)
                for j, x in enumerate(itinerary):
                    if j != i:
                        current_counts[x] += 1

                prev_lat, prev_lng = None, None
                if i > 0:
                    prev_act = ACT.get(itinerary[i-1])
                    if prev_act:
                        prev_lat = prev_act.get('lat')
                        prev_lng = prev_act.get('lng')

                # Find replacement
                candidates = []
                for cand_id, cand in ACT.items():
                    if cand_id == act_id:
                        continue
                    if not activity_matches_slot(cand, slot_idx):
                        continue
                    if not family_ok(cand):
                        continue
                    is_cand_cafe = bool({'cafe', 'coffee', 'bakery'} & {normalize_tag(t) for t in cand.get('tags', [])})
                    cand_cap = 5 if is_cand_cafe else 3
                    if current_counts[cand_id] >= cand_cap:
                        continue
                    dist = 0
                    if prev_lat is not None and cand.get('lat') and cand.get('lng'):
                        dist = haversine(prev_lat, prev_lng, cand['lat'], cand['lng'])
                    candidates.append((dist, cand_id))

                if candidates:
                    candidates.sort()
                    replacement_id = candidates[0][1]
                    old_id = itinerary[i]
                    itinerary[i] = replacement_id
                    total_repetition_fixes += 1
                    fixed += 1
                    changed_this_pass = True
                    print(f"  [REP-CAP] {fam_id} slot {i}: {old_id} (count={cnt}) -> {replacement_id}")
                else:
                    print(f"  [REP-CAP-NO-REPL] {fam_id} slot {i}: {act_id} over cap, no replacement found")

        if not changed_this_pass:
            break

    output_records.append({"id": fam_id, "family": fam, "itinerary": itinerary})

# ---- Write output --------------------------------------------------------------
with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
    for rec in output_records:
        f.write(json.dumps(rec) + '\n')

print(f"\n=== SUMMARY ===")
print(f"Families processed: {len(output_records)}")
print(f"Slot replacements (tag/hours violations): {total_slot_replacements}")
print(f"Repetition-cap fixes: {total_repetition_fixes}")
print(f"Unsatisfied slots (no replacement found): {len(unsatisfied)}")
for u in unsatisfied:
    print(f"  {u}")
print(f"Output: {OUTPUT_PATH}")
