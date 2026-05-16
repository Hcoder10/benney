"""
Cleanup script for families_part_van_lifer_couple__b0.jsonl
Applies: tag normalization, slot-type rule enforcement, open-hours check (multi-session),
         repetition cap enforcement.
"""
import json, re
from collections import defaultdict

# ---- Load data ----
with open(r'C:\Users\sarta\rosea\hotel_agents\data\activities_bay.json') as f:
    acts_list = json.load(f)
acts = {a['id']: a for a in acts_list}

# ---- Tag normalization ----
# Three surface-forms all mean the canonical slot-tag 'fine-dining'
TAG_NORMALIZE = {
    'fine dining': 'fine-dining',
    'fine_dining': 'fine-dining',
}

def normalize_tag(t):
    return TAG_NORMALIZE.get(t, t)

for a in acts.values():
    a['_norm_tags'] = [normalize_tag(t) for t in a.get('tags', [])]

# ---- Slot rules ----
SLOT_RULES = [
    # 0 early-morning 7-9 AM
    {'window': (7,  9),  'tags': {'cafe','coffee','bakery','breakfast'}},
    # 1 breakfast     8-10 AM
    {'window': (8,  10), 'tags': {'cafe','coffee','bakery','breakfast','brunch'}},
    # 2 late-morning  10-1 PM
    {'window': (10, 13), 'tags': {'museum','tour','outdoor','hiking','campus','tech',
                                  'art','science','history','gardens','walking',
                                  'shopping','viewpoint','landmark','architecture'}},
    # 3 lunch+afternoon 12-5 PM
    {'window': (12, 17), 'tags': {'restaurant','lunch','casual','park','scenic','beach',
                                  'outdoor','hiking','tour','shopping','winery','wine','tasting'}},
    # 4 evening 5-8 PM
    {'window': (17, 20), 'tags': {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'}},
    # 5 night  8-11 PM
    {'window': (20, 23), 'tags': {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'}},
]

# ---- Multi-session hours parsing ----

def parse_hour(s):
    """Parse a single time string into a decimal hour (24-hour). Returns None on failure."""
    s = s.strip()
    if not s:
        return None
    if s.lower() == 'sunset':
        return 20.0
    # 12-hour AM/PM: '5:30 PM', '9 AM'
    m = re.match(r'^(\d+)(?::(\d+))?\s*(AM|PM)$', s, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mn = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3).upper()
        if ampm == 'PM' and h != 12:
            h += 12
        if ampm == 'AM' and h == 12:
            h = 0
        return h + mn / 60.0
    # 24-hour with colon: '17:30', '08:00'
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60.0
    # bare integer
    m = re.match(r'^(\d+)$', s)
    if m:
        return float(m.group(1))
    return None


def parse_sessions(oh_str):
    """
    Parse an open_hours string that may contain multiple sessions
    separated by commas or semicolons.
    Returns a list of (open_h, close_h) float pairs.
    Midnight wrap: if close_h <= open_h, add 24 (e.g. 16:00-00:00 -> 16.0-24.0).
    """
    if not oh_str or oh_str.lower().strip() == 'closed':
        return []

    sessions = []
    # Split into individual session strings on ',' or ';'
    raw_parts = re.split(r'[;,]', oh_str)
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        # Match  <time>  <separator>  <time>
        # separator is ' - ' or just '-'
        # time = digits optionally ':' digits optionally ' AM/PM'
        TIME_RE = r'\d+(?::\d+)?(?:\s*(?:AM|PM))?'
        SEP_RE = r'\s*[-–]\s*'
        m = re.match(r'^(' + TIME_RE + r')' + SEP_RE + r'(' + TIME_RE + r')$', part, re.IGNORECASE)
        if m:
            o = parse_hour(m.group(1))
            c = parse_hour(m.group(2))
            if o is not None and c is not None:
                if c <= o:
                    c += 24  # midnight wrap
                sessions.append((o, c))
    return sessions


def any_overlap(sessions, sw_start, sw_end):
    """True if ANY session overlaps the window [sw_start, sw_end)."""
    for (o, c) in sessions:
        if max(o, sw_start) < min(c, sw_end):
            return True
    return False


def hours_overlap(act_id, slot_type):
    act = acts.get(act_id)
    if not act:
        return False
    oh = act.get('open_hours', {})
    # Prefer friday as representative weekday; fall back to any non-closed day
    for day in ('fri', 'mon', 'tue', 'wed', 'thu', 'sat', 'sun'):
        dh = oh.get(day, '')
        if dh and dh.lower().strip() != 'closed':
            sessions = parse_sessions(dh)
            if sessions:
                rule = SLOT_RULES[slot_type]
                return any_overlap(sessions, rule['window'][0], rule['window'][1])
    return False


def tags_match(act_id, slot_type):
    act = acts.get(act_id)
    if not act:
        return False
    norm = set(act.get('_norm_tags', []))
    required = SLOT_RULES[slot_type]['tags']
    return bool(norm & required)


# ---- Budget / family filters ----
BUDGET_ORDER = {'shoestring': 0, 'mid': 1, 'upscale': 2, 'premium': 3, 'luxury': 4}


def budget_ok(act_id, fam_budget):
    act = acts.get(act_id)
    if not act:
        return False
    act_tier = BUDGET_ORDER.get(act['budget_tier'], 99)
    fam_tier = BUDGET_ORDER.get(fam_budget, 0)
    return act_tier <= fam_tier


def kid_ok_fn(act_id, fam):
    act = acts.get(act_id)
    if not act:
        return False
    if not act['kid_ok'] and fam['kid_ages'] != 'none':
        return False
    return True


def mobility_ok_fn(act_id, fam):
    act = acts.get(act_id)
    if not act:
        return False
    if not act['mobility_ok'] and fam['mobility'] != 'full':
        return False
    return True


def family_ok(act_id, fam):
    return budget_ok(act_id, fam['budget_tier']) and kid_ok_fn(act_id, fam) and mobility_ok_fn(act_id, fam)


# ---- Repetition caps ----
CAFE_IDS = frozenset(
    aid for aid, act in acts.items()
    if 'cafe' in act.get('_norm_tags', []) or 'coffee' in act.get('_norm_tags', [])
)
MAX_USES = 3
MAX_CAFE = 5


def get_cap(aid):
    return MAX_CAFE if aid in CAFE_IDS else MAX_USES


# ---- Candidate selection ----
def candidates_for_slot(slot_type, fam, current_usage, exclude_id=None):
    rule = SLOT_RULES[slot_type]
    results = []
    for aid, act in acts.items():
        if aid == exclude_id:
            continue
        norm = set(act.get('_norm_tags', []))
        if not (norm & rule['tags']):
            continue
        if not hours_overlap(aid, slot_type):
            continue
        if not family_ok(aid, fam):
            continue
        cap = get_cap(aid)
        if current_usage.get(aid, 0) >= cap:
            continue
        results.append(aid)
    return results


# ---- Main processing ----
input_path  = r'C:\Users\sarta\rosea\hotel_agents\data\families_part_van_lifer_couple__b0.jsonl'
output_path = r'C:\Users\sarta\rosea\hotel_agents\data\families_clean_van_lifer_couple__b0.jsonl'

total_slot_replacements = 0
total_repetition_fixes = 0
no_replacement_events = []   # (fam_id, slot_i, old_aid, reason)

out_lines = []

with open(input_path) as f:
    records = [json.loads(line) for line in f if line.strip()]

for rec in records:
    fam_id = rec['id']
    fam = rec['family']
    itinerary = rec['itinerary'][:]

    # --- Pass 1: slot-type + open-hours violations ---
    usage = defaultdict(int)
    for i, aid in enumerate(itinerary):
        slot_type = i % 6
        t_ok = tags_match(aid, slot_type)
        h_ok = hours_overlap(aid, slot_type)
        if not t_ok or not h_ok:
            cands = candidates_for_slot(slot_type, fam, usage, exclude_id=aid)
            if cands:
                replacement = cands[0]
                itinerary[i] = replacement
                usage[replacement] += 1
                total_slot_replacements += 1
                reasons = []
                if not t_ok: reasons.append('tag')
                if not h_ok: reasons.append('hours')
                print(f"SLOT_FIX [{fam_id}] slot {i}(t{slot_type}): {aid} -> {replacement} [{','.join(reasons)}]")
            else:
                usage[aid] += 1
                no_replacement_events.append((fam_id, i, aid, 'slot'))
                print(f"NO_REPL  [{fam_id}] slot {i}(t{slot_type}): {aid} [no bank candidate]")
        else:
            usage[aid] += 1

    # --- Pass 2: repetition cap ---
    usage2 = defaultdict(int)
    for i, aid in enumerate(itinerary):
        slot_type = i % 6
        usage2[aid] += 1
        cap = get_cap(aid)
        if usage2[aid] > cap:
            snap = defaultdict(int)
            for j in range(i):
                snap[itinerary[j]] += 1
            cands = candidates_for_slot(slot_type, fam, snap, exclude_id=aid)
            if cands:
                old = itinerary[i]
                replacement = cands[0]
                itinerary[i] = replacement
                usage2[old] -= 1
                usage2[replacement] += 1
                total_repetition_fixes += 1
                print(f"REP_FIX  [{fam_id}] slot {i}(t{slot_type}): {old}(#{usage2[old]+1}) -> {replacement}")
            else:
                no_replacement_events.append((fam_id, i, aid, 'rep'))
                print(f"NO_REPL  [{fam_id}] slot {i}(t{slot_type}): {aid} [no rep-fix candidate]")

    out_rec = {'id': fam_id, 'family': fam, 'itinerary': itinerary}
    out_lines.append(json.dumps(out_rec))

with open(output_path, 'w') as f:
    f.write('\n'.join(out_lines) + '\n')

print()
print('=== FINAL SUMMARY ===')
print(f'Slot-type/hours replacements : {total_slot_replacements}')
print(f'Repetition cap fixes         : {total_repetition_fixes}')
print(f'No-replacement events        : {len(no_replacement_events)}')
for item in no_replacement_events:
    print(f'  {item}')
print(f'\nOutput -> {output_path}')
