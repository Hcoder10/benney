"""
Cleanup script for families_part_japanese_business_delegation__b0.jsonl
Implements all rules from sonnet_cleanup_brief.md:
  - Slot-type (HARD)
  - Open-hours overlap (HARD)
  - Repetition cap (3x general, 5x cafes)
  - Tag normalization (output only)
  - Multi-session hours = any overlap
"""

import json
import re
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLOT_TAGS = {
    0: {'cafe', 'coffee', 'bakery', 'breakfast'},
    1: {'cafe', 'coffee', 'bakery', 'breakfast', 'brunch'},
    2: {'museum', 'tour', 'outdoor', 'hiking', 'campus', 'tech', 'art', 'science',
        'history', 'gardens', 'walking', 'shopping', 'viewpoint', 'landmark', 'architecture'},
    3: {'restaurant', 'lunch', 'casual', 'park', 'scenic', 'beach', 'outdoor', 'hiking',
        'tour', 'shopping', 'winery', 'wine', 'tasting'},
    4: {'restaurant', 'dinner', 'fine-dining', 'scenic', 'sunset', 'wine', 'casual'},
    5: {'bar', 'cocktails', 'nightlife', 'lounge', 'dinner', 'fine-dining', 'speakeasy'},
}

# Slot time windows (start_h, end_h) - inclusive/exclusive float hours
SLOT_WINDOWS = {
    0: (7, 9),
    1: (8, 10),
    2: (10, 13),
    3: (12, 17),
    4: (17, 20),
    5: (20, 23),
}

BUDGET_ORDER = ['shoestring', 'mid', 'premium', 'luxury']

# ID aliases for IDs in itineraries that differ from activities_bay.json keys
ID_ALIASES = {
    'fisher_mans_wharf_tourist': 'fishermans_wharf_tourist',
    'ferrybuilding_marketplace': 'ferry_building_marketplace',
    'sandhill_vc_tour': 'sand_hill_vc_tour',
    'levi_stadium_tour': 'levis_stadium_tour',
}

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_hour(s):
    """Parse a time component string to float hour. Returns None if unparseable."""
    if not s:
        return None
    s = s.strip().upper()
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)?$', s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        ampm = m.group(3)
        if ampm == 'PM' and h != 12:
            h += 12
        elif ampm == 'AM' and h == 12:
            h = 0
        return h + mn / 60
    m = re.match(r'^(\d{1,2})\s*(AM|PM)$', s)
    if m:
        h = int(m.group(1))
        ampm = m.group(2)
        if ampm == 'PM' and h != 12:
            h += 12
        elif ampm == 'AM' and h == 12:
            h = 0
        return float(h)
    return None


def parse_open_hours(oh_str):
    """
    Parse an open_hours string to (open_h, close_h) float hours.
    Multi-session (e.g. '10:00-14:00, 17:00-22:00') -> union via earliest open, latest close.
    Handles midnight wraparound: if close <= open, treat close as close + 24.
    Returns (None, None) if closed or unparseable.
    """
    if not oh_str:
        return None, None
    oh_lower = oh_str.lower().strip()
    if oh_lower in ('closed', 'n/a', ''):
        return None, None
    if 'sunrise' in oh_lower or 'sunset' in oh_lower:
        return 6.0, 20.0  # treat as broadly open

    opens = []
    closes = []

    # Split on comma or semicolon to handle multi-session hours
    sessions = re.split(r'[;,]', oh_str)
    for session in sessions:
        session = session.strip()
        if not session:
            continue
        # Split session on ' - ' or '-' between times
        parts = re.split(r'\s*[-–]\s*', session, maxsplit=1)
        if len(parts) != 2:
            continue
        o = parse_hour(parts[0].strip())
        c = parse_hour(parts[1].strip())
        if o is not None and c is not None:
            # Handle midnight wraparound: 00:00 close means 24:00
            if c <= o:
                c += 24.0
            opens.append(o)
            closes.append(c)

    if not opens:
        return None, None

    # Multi-session: any overlap counts, so use earliest open, latest close
    return min(opens), max(closes)


def hours_overlap(act_open, act_close, slot_start, slot_end):
    """True if [act_open, act_close) overlaps with [slot_start, slot_end)."""
    if act_open is None or act_close is None:
        return True  # unknown -> assume OK
    return act_open < slot_end and act_close > slot_start


# ---------------------------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------------------------

def normalize_tags(tags):
    """Return a set of normalized tags: lowercase, underscores -> hyphens."""
    return {t.lower().replace('_', '-').replace(' ', '-') for t in tags}


def tags_match_slot(act_tags_raw, slot_type_idx):
    required = SLOT_TAGS[slot_type_idx]
    normalized = normalize_tags(act_tags_raw)
    return bool(normalized & required)


# ---------------------------------------------------------------------------
# Activity validation
# ---------------------------------------------------------------------------

def act_open_ok(act, slot_type_idx):
    """Check that activity open_hours.fri overlaps the slot window."""
    fri_str = act.get('open_hours', {}).get('fri', '')
    if not fri_str:
        return True  # missing data -> assume OK
    open_h, close_h = parse_open_hours(fri_str)
    sw = SLOT_WINDOWS[slot_type_idx]
    return hours_overlap(open_h, close_h, sw[0], sw[1])


def budget_ok(act, fam_budget):
    act_b = act.get('budget_tier', 'shoestring')
    if act_b not in BUDGET_ORDER or fam_budget not in BUDGET_ORDER:
        return True
    return BUDGET_ORDER.index(act_b) <= BUDGET_ORDER.index(fam_budget)


def is_cafe(act):
    return bool(normalize_tags(act.get('tags', [])) & {'cafe', 'coffee', 'bakery'})


def passes_family_filters(act, fam):
    """Check kid_ok / mobility_ok / budget."""
    if not budget_ok(act, fam['budget_tier']):
        return False
    if not act.get('kid_ok', True) and fam.get('kid_ages', 'none') != 'none':
        return False
    if not act.get('mobility_ok', True) and fam.get('mobility', 'full') != 'full':
        return False
    return True


def act_valid_for_slot(act, slot_type_idx, fam):
    return (
        tags_match_slot(act.get('tags', []), slot_type_idx)
        and act_open_ok(act, slot_type_idx)
        and passes_family_filters(act, fam)
    )


# ---------------------------------------------------------------------------
# Main cleanup logic
# ---------------------------------------------------------------------------

def find_replacement(acts_by_id, slot_type_idx, fam, usage_counts, exclude_id=None):
    """
    Find the best replacement activity for a slot.
    Prefer activities not yet used; fallback to those under cap.
    """
    candidates = []
    for aid, act in acts_by_id.items():
        if aid == exclude_id:
            continue
        cap = 5 if is_cafe(act) else 3
        if usage_counts.get(aid, 0) >= cap:
            continue
        if act_valid_for_slot(act, slot_type_idx, fam):
            candidates.append(act)

    if not candidates:
        return None

    # Prefer less-used activities first
    candidates.sort(key=lambda a: usage_counts.get(a['id'], 0))
    return candidates[0]


def clean_family(fam_record, acts_by_id):
    fam = fam_record['family']
    itinerary = list(fam_record['itinerary'])  # copy

    # Resolve aliases first
    itinerary = [ID_ALIASES.get(aid, aid) for aid in itinerary]

    usage_counts = defaultdict(int)
    for aid in itinerary:
        usage_counts[aid] += 1

    slot_replacements = 0
    rep_replacements = 0
    no_replacement_found = []

    # --- Pass 1: Slot-type + Open-hours violations (HARD) ---
    for i, aid in enumerate(itinerary):
        slot_type_idx = i % 6
        act = acts_by_id.get(aid)
        if act is None:
            # Unknown activity - attempt replacement
            repl = find_replacement(acts_by_id, slot_type_idx, fam, usage_counts, exclude_id=aid)
            if repl:
                usage_counts[aid] -= 1
                itinerary[i] = repl['id']
                usage_counts[repl['id']] += 1
                slot_replacements += 1
            else:
                no_replacement_found.append((fam_record['id'], i, aid, 'unknown_id'))
            continue

        tag_ok = tags_match_slot(act.get('tags', []), slot_type_idx)
        open_ok = act_open_ok(act, slot_type_idx)

        if not tag_ok or not open_ok:
            reason = []
            if not tag_ok:
                reason.append('tag')
            if not open_ok:
                reason.append('hours')
            repl = find_replacement(acts_by_id, slot_type_idx, fam, usage_counts, exclude_id=aid)
            if repl:
                usage_counts[aid] -= 1
                itinerary[i] = repl['id']
                usage_counts[repl['id']] += 1
                slot_replacements += 1
            else:
                no_replacement_found.append((fam_record['id'], i, aid, '+'.join(reason)))

    # --- Pass 2: Repetition cap ---
    # Recompute usage after pass 1
    usage_counts = defaultdict(int)
    for aid in itinerary:
        usage_counts[aid] += 1

    for i, aid in enumerate(itinerary):
        act = acts_by_id.get(aid)
        if act is None:
            continue
        cap = 5 if is_cafe(act) else 3
        # Check if this specific occurrence is over cap (keep first cap occurrences)
        occurrences_so_far = itinerary[:i].count(aid)
        if occurrences_so_far >= cap:
            slot_type_idx = i % 6
            repl = find_replacement(acts_by_id, slot_type_idx, fam, usage_counts, exclude_id=aid)
            if repl:
                usage_counts[aid] -= 1
                itinerary[i] = repl['id']
                usage_counts[repl['id']] += 1
                rep_replacements += 1
            else:
                no_replacement_found.append((fam_record['id'], i, aid, 'repetition'))

    cleaned = dict(fam_record)
    cleaned['itinerary'] = itinerary
    return cleaned, slot_replacements, rep_replacements, no_replacement_found


def main():
    acts_path = 'C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json'
    input_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_part_japanese_business_delegation__b0.jsonl'
    output_path = 'C:/Users/sarta/rosea/hotel_agents/data/families_clean_japanese_business_delegation__b0.jsonl'

    with open(acts_path, 'r', encoding='utf-8') as f:
        activities_list = json.load(f)
    acts_by_id = {a['id']: a for a in activities_list}

    families = []
    with open(input_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                families.append(json.loads(line))

    total_slot_replacements = 0
    total_rep_replacements = 0
    all_no_replacement = []

    output_records = []
    for fam_record in families:
        cleaned, sr, rr, nrf = clean_family(fam_record, acts_by_id)
        output_records.append(cleaned)
        total_slot_replacements += sr
        total_rep_replacements += rr
        all_no_replacement.extend(nrf)

    with open(output_path, 'w', encoding='utf-8') as f:
        for record in output_records:
            f.write(json.dumps(record) + '\n')

    print(f"Slot-type/open-hours replacements: {total_slot_replacements}")
    print(f"Repetition-cap replacements:        {total_rep_replacements}")
    print(f"Total replacements:                 {total_slot_replacements + total_rep_replacements}")
    print(f"No replacement found (kept as-is):  {len(all_no_replacement)}")
    if all_no_replacement:
        for item in all_no_replacement:
            print(f"  fam={item[0]}  slot={item[1]}  act={item[2]}  reason={item[3]}")
    print(f"Output written to: {output_path}")


if __name__ == '__main__':
    main()
