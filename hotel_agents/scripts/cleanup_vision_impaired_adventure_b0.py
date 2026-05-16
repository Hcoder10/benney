"""
Cleanup script for families_part_vision_impaired_adventure__b0.jsonl
Follows sonnet_cleanup_brief.md rules exactly.
"""
import json
import re
from collections import Counter
from copy import deepcopy

# ── helpers ──────────────────────────────────────────────────────────────────

def parse_time(t):
    """Parse 'HH:MM' or '12:30 PM' style into fractional hours. Returns None on failure."""
    if not t or t.lower() in ("closed", "24 hours", ""):
        return None
    t = t.strip()
    # Handle "HH:MM AM/PM" format
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)?$', t, re.IGNORECASE)
    if not m:
        # Try "H AM" style
        m2 = re.match(r'^(\d{1,2})\s*(AM|PM)$', t, re.IGNORECASE)
        if m2:
            h = int(m2.group(1))
            ampm = m2.group(2).upper()
            if ampm == 'PM' and h != 12:
                h += 12
            elif ampm == 'AM' and h == 12:
                h = 0
            return float(h)
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    ampm = m.group(3).upper() if m.group(3) else None
    if ampm == 'PM' and h != 12:
        h += 12
    elif ampm == 'AM' and h == 12:
        h = 0
    return h + mn / 60.0


def parse_range(s):
    """Parse 'HH:MM - HH:MM' or 'HH:MM AM/PM - HH:MM AM/PM' into (open, close) floats."""
    if not s or s.lower() == 'closed':
        return None
    # Normalize separators
    s = s.replace('–', '-').strip()
    # Split on ' - ' or '-' between time tokens
    # Allow for patterns like "9:00 AM - 5:00 PM" or "09:30-16:00" or "5:30 PM - 9:30 PM"
    parts = re.split(r'\s*[-–]\s*', s, maxsplit=1)
    if len(parts) != 2:
        return None
    open_t = parse_time(parts[0].strip())
    close_t = parse_time(parts[1].strip())
    if open_t is None or close_t is None:
        return None
    return (open_t, close_t)


def hours_overlap(range1, range2):
    """True if two (open, close) ranges overlap at all."""
    if range1 is None or range2 is None:
        return False
    a0, a1 = range1
    b0, b1 = range2
    # Handle crossing midnight (close < open) as: treat close as next-day
    if a1 < a0:
        a1 += 24
    if b1 < b0:
        b1 += 24
    return a0 < b1 and b0 < a1


def get_open_hours(act):
    """Return parsed (open, close) for fri, falling back to other weekdays."""
    for day in ('fri', 'thu', 'wed', 'tue', 'mon', 'sat'):
        raw = act.get('open_hours', {}).get(day)
        if raw and raw.lower() != 'closed':
            r = parse_range(raw)
            if r:
                return r
    return None  # closed or unparseable


# Slot windows: (start_hour, end_hour) inclusive boundaries
SLOT_WINDOWS = [
    (7, 9),    # 0 early-morning
    (8, 10),   # 1 breakfast
    (10, 13),  # 2 late-morning
    (12, 17),  # 3 lunch+afternoon
    (17, 20),  # 4 evening
    (20, 23),  # 5 night
]

# Required tags per slot (any-of)
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

BUDGET_ORDER = ['shoestring', 'mid', 'premium', 'luxury']

def budget_ok(act_tier, family_tier):
    try:
        return BUDGET_ORDER.index(act_tier) <= BUDGET_ORDER.index(family_tier)
    except ValueError:
        return True  # unknown tier → allow

def tags_match(act_tags, slot_idx):
    req = SLOT_TAGS[slot_idx % 6]
    act_set = {t.lower() for t in act_tags}
    return bool(req & act_set)

def open_hours_ok(act, slot_idx):
    slot_win = SLOT_WINDOWS[slot_idx % 6]
    slot_range = (slot_win[0], slot_win[1])
    act_range = get_open_hours(act)
    if act_range is None:
        return False  # treat closed/unparseable as not OK
    return hours_overlap(act_range, slot_range)

def is_cafe(act):
    return bool({'cafe', 'coffee', 'bakery'} & {t.lower() for t in act.get('tags', [])})

# ── BUDGET MAP ─────────────────────────────────────────────────────────────

BUDGET_MAP = {
    'shoestring': 0, 'mid': 1, 'premium': 2, 'luxury': 3
}

# ── Main logic ───────────────────────────────────────────────────────────────

def normalize_tags(act):
    """Normalize tag strings: lowercase, strip, dedupe, sort."""
    raw = act.get('tags', [])
    normed = sorted(set(t.strip().lower() for t in raw if t.strip()))
    return normed

def find_replacement(slot_idx, family, act_bank, used_counts, exclude_ids=None):
    """
    Find the best replacement activity for slot_idx given family constraints.
    Returns act dict or None.
    """
    exclude_ids = exclude_ids or set()
    slot_si = slot_idx % 6
    candidates = []
    for act in act_bank:
        aid = act['id']
        if aid in exclude_ids:
            continue
        # Count cap
        count = used_counts.get(aid, 0)
        is_c = is_cafe(act)
        if is_c and count >= 5:
            continue
        if not is_c and count >= 3:
            continue
        # Tag match
        if not tags_match(act.get('tags', []), slot_si):
            continue
        # Open hours
        if not open_hours_ok(act, slot_si):
            continue
        # Kid constraint
        if family.get('kid_ages') != 'none' and not act.get('kid_ok', True):
            continue
        # Mobility constraint
        if family.get('mobility') == 'limited' and not act.get('mobility_ok', True):
            continue
        # Budget
        if not budget_ok(act.get('budget_tier', 'mid'), family.get('budget_tier', 'mid')):
            continue
        candidates.append(act)
    if not candidates:
        return None
    # Prefer lower usage count for diversity
    candidates.sort(key=lambda a: used_counts.get(a['id'], 0))
    return candidates[0]


def clean_family(record, act_bank, act_map):
    family = record['family']
    itinerary = list(record['itinerary'])

    slot_replacements = 0
    repetition_fixes = 0
    unfixable = []

    used_counts = Counter(itinerary)

    # First pass: repetition cap (>3 for non-cafe, >5 for cafe) — mark excess
    # We'll handle these in the main walk
    # Build normalized itinerary with corrected tags
    normalized_itinerary = []

    # Reset used_counts for fresh counting during walk
    used_counts = Counter()

    for i, act_id in enumerate(itinerary):
        act = act_map.get(act_id)
        slot_si = i % 6

        # Normalize tags in the act if present
        if act:
            act = deepcopy(act)
            act['tags'] = normalize_tags(act)

        needs_replace = False
        reason = None

        if act is None:
            needs_replace = True
            reason = f"slot {i}: activity '{act_id}' not in bank"
        else:
            # Check tag match
            if not tags_match(act['tags'], slot_si):
                needs_replace = True
                reason = f"slot {i} ({slot_si}): '{act_id}' tags {act['tags']} don't match slot"
            # Check open hours
            elif not open_hours_ok(act, slot_si):
                needs_replace = True
                reason = f"slot {i} ({slot_si}): '{act_id}' open_hours don't cover slot window"

        # Check repetition cap (against already-placed slots)
        if not needs_replace and act:
            count_so_far = used_counts.get(act_id, 0)
            is_c = is_cafe(act)
            cap = 5 if is_c else 3
            if count_so_far >= cap:
                needs_replace = True
                reason = f"slot {i}: '{act_id}' used {count_so_far} times (cap={cap})"

        if needs_replace:
            # Find replacement
            already_used = set(a for a, c in used_counts.items()
                               if c >= (5 if is_cafe(act_map.get(a, {})) else 3))
            replacement = find_replacement(i, family, act_bank, used_counts,
                                           exclude_ids=already_used)
            if replacement:
                if reason and 'used' in reason:
                    repetition_fixes += 1
                else:
                    slot_replacements += 1
                normalized_itinerary.append(replacement['id'])
                used_counts[replacement['id']] += 1
                print(f"  REPLACE [{reason}] -> '{replacement['id']}'")
            else:
                # Can't fix — keep original if act exists, else skip
                unfixable.append((i, act_id, reason))
                if act_id in act_map:
                    normalized_itinerary.append(act_id)
                    used_counts[act_id] += 1
                    print(f"  UNFIXABLE [{reason}] — keeping original")
                else:
                    # Skip with a dummy (best we can do)
                    normalized_itinerary.append(act_id)
                    used_counts[act_id] += 1
                    print(f"  UNFIXABLE (missing) [{reason}] — keeping placeholder")
        else:
            normalized_itinerary.append(act_id)
            used_counts[act_id] += 1

    out = deepcopy(record)
    out['itinerary'] = normalized_itinerary
    return out, slot_replacements, repetition_fixes, unfixable


def main():
    with open('data/activities_bay.json') as f:
        act_bank = json.load(f)

    # Normalize all tags in bank
    for act in act_bank:
        act['tags'] = sorted(set(t.strip().lower() for t in act.get('tags', []) if t.strip()))

    act_map = {a['id']: a for a in act_bank}

    input_path = 'data/families_part_vision_impaired_adventure__b0.jsonl'
    output_path = 'data/families_clean_vision_impaired_adventure__b0.jsonl'

    records = []
    with open(input_path) as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    total_slot_replacements = 0
    total_repetition_fixes = 0
    all_unfixable = []

    cleaned = []
    for rec in records:
        fid = rec['id']
        print(f"\n=== {fid} ===")
        out, sr, rf, uf = clean_family(rec, act_bank, act_map)
        total_slot_replacements += sr
        total_repetition_fixes += rf
        all_unfixable.extend([(fid, *u) for u in uf])
        cleaned.append(out)

    with open(output_path, 'w') as f:
        for rec in cleaned:
            f.write(json.dumps(rec) + '\n')

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"  Families processed: {len(records)}")
    print(f"  Slot-type / open-hours replacements: {total_slot_replacements}")
    print(f"  Repetition-cap fixes: {total_repetition_fixes}")
    print(f"  Total fixes: {total_slot_replacements + total_repetition_fixes}")
    print(f"  Unfixable slots: {len(all_unfixable)}")
    if all_unfixable:
        for item in all_unfixable:
            print(f"    {item}")
    print(f"\nOutput written to: {output_path}")


if __name__ == '__main__':
    main()
