"""
Cleanup script for families_part_mandarin_speaking_seniors__b0.jsonl
Follows sonnet_cleanup_brief.md rules:
  - Slot-type hard rule (tag match)
  - Open-hours hard rule (overlap with slot window)
  - Repetition cap (3x general, 5x cafes)
  - Tag normalization: case + space/hyphen/underscore insensitive
  - Multi-session hours: ANY-overlap rule
"""

import json
import re
import copy
from typing import Optional

# ─── Paths ─────────────────────────────────────────────────────────────────
ACTIVITIES_PATH = "C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json"
INPUT_PATH      = "C:/Users/sarta/rosea/hotel_agents/data/families_part_mandarin_speaking_seniors__b0.jsonl"
OUTPUT_PATH     = "C:/Users/sarta/rosea/hotel_agents/data/families_clean_mandarin_speaking_seniors__b0.jsonl"

# ─── Slot definitions ───────────────────────────────────────────────────────
# (start_hour_inclusive, end_hour_exclusive_but_inclusive_for_overlap, required_tags)
SLOT_DEFS = [
    (7,  9,  {"cafe", "coffee", "bakery", "breakfast"}),
    (8,  10, {"cafe", "coffee", "bakery", "breakfast", "brunch"}),
    (10, 13, {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art", "science", "history", "gardens", "walking", "shopping", "viewpoint", "landmark", "architecture"}),
    (12, 17, {"restaurant", "lunch", "casual", "park", "scenic", "beach", "outdoor", "hiking", "tour", "shopping", "winery", "wine", "tasting"}),
    (17, 20, {"restaurant", "dinner", "fine-dining", "scenic", "sunset", "wine", "casual"}),
    (20, 23, {"bar", "cocktails", "nightlife", "lounge", "dinner", "fine-dining", "speakeasy"}),
]

BUDGET_TIERS = ["shoestring", "mid", "premium", "luxury"]

# ─── Tag normalization ──────────────────────────────────────────────────────
def normalize_tag(tag: str) -> str:
    """Lowercase, replace spaces/hyphens/underscores with hyphens."""
    return re.sub(r"[\s_-]+", "-", tag.strip().lower())

def activity_tags_normalized(act: dict) -> set:
    return {normalize_tag(t) for t in act.get("tags", [])}

def slot_tags_normalized(slot_idx: int) -> set:
    required = SLOT_DEFS[slot_idx][2]
    return {normalize_tag(t) for t in required}

# ─── Open-hours parsing ─────────────────────────────────────────────────────
def parse_time_str(s: str) -> Optional[float]:
    """Parse time like '09:30', '9:30 AM', '5:30 PM' → float hours (e.g. 9.5)."""
    s = s.strip()
    if not s:
        return None
    # Handle AM/PM
    pm = s.upper().endswith("PM")
    am = s.upper().endswith("AM")
    s_clean = re.sub(r"\s*[APM]+$", "", s, flags=re.IGNORECASE).strip()
    parts = re.split(r"[:.]", s_clean)
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if pm and h != 12:
        h += 12
    if am and h == 12:
        h = 0
    return h + m / 60.0

def parse_hours_string(hours_str: str):
    """
    Parse an open_hours string into list of (open_float, close_float) tuples.
    Handles:
      - "closed"
      - "08:00-sunset"  → (8.0, 20.0)  (treat sunset as 20:00)
      - "09:30-16:00"
      - "5:30 PM - 9:30 PM"
      - "11:30-14:00, 17:30-22:30"  (multi-session)
    Returns [] for closed.
    """
    if not hours_str or hours_str.strip().lower() in ("closed", ""):
        return []

    # Replace "sunrise" and "sunset" with approximate times
    hours_str = re.sub(r"\bsunrise\b", "06:00", hours_str, flags=re.IGNORECASE)
    hours_str = re.sub(r"\bsunset\b", "20:00", hours_str, flags=re.IGNORECASE)
    hours_str = re.sub(r"\bdusk\b", "20:00", hours_str, flags=re.IGNORECASE)
    hours_str = re.sub(r"\bdawn\b", "06:00", hours_str, flags=re.IGNORECASE)

    # Split by comma for multi-session
    sessions = [seg.strip() for seg in hours_str.split(",")]
    result = []
    for seg in sessions:
        # Split by dash/en-dash/em-dash, but be careful with AM/PM
        # Pattern: TIME - TIME
        m = re.match(r"^(.+?)\s*[-–—]\s*(.+)$", seg.strip())
        if m:
            open_t = parse_time_str(m.group(1))
            close_t = parse_time_str(m.group(2))
            if open_t is not None and close_t is not None:
                result.append((open_t, close_t))
    return result

def hours_overlap(sessions, slot_start: float, slot_end: float) -> bool:
    """
    ANY-overlap rule: returns True if ANY session overlaps [slot_start, slot_end].
    Overlap means sessions[open] < slot_end AND sessions[close] > slot_start.
    """
    for (open_t, close_t) in sessions:
        if open_t < slot_end and close_t > slot_start:
            return True
    return False

def activity_open_for_slot(act: dict, slot_idx: int) -> bool:
    """Check if activity is open during the slot's time window (using fri hours)."""
    open_hours = act.get("open_hours", {})
    hours_str = open_hours.get("fri", "")
    sessions = parse_hours_string(hours_str)
    if not sessions:
        return False
    start, end, _ = SLOT_DEFS[slot_idx]
    return hours_overlap(sessions, float(start), float(end))

# ─── Tag check ──────────────────────────────────────────────────────────────
def tags_match_slot(act: dict, slot_idx: int) -> bool:
    """Return True if activity has at least one tag matching slot requirements."""
    act_tags = activity_tags_normalized(act)
    req_tags = slot_tags_normalized(slot_idx)
    return bool(act_tags & req_tags)

# ─── Budget check ───────────────────────────────────────────────────────────
def budget_ok(act: dict, family_budget: str) -> bool:
    act_tier = act.get("budget_tier", "shoestring")
    try:
        return BUDGET_TIERS.index(act_tier) <= BUDGET_TIERS.index(family_budget)
    except ValueError:
        return True

# ─── Is cafe? ───────────────────────────────────────────────────────────────
def is_cafe(act: dict) -> bool:
    norm = activity_tags_normalized(act)
    return bool(norm & {"cafe", "coffee", "bakery"})

# ─── Load activities ─────────────────────────────────────────────────────────
with open(ACTIVITIES_PATH, encoding="utf-8") as f:
    all_activities = json.load(f)

act_by_id = {a["id"]: a for a in all_activities}

# ─── Find replacement ────────────────────────────────────────────────────────
def find_replacement(
    slot_idx: int,
    family: dict,
    usage_counts: dict,
    exclude_id: str,
    prefer_lat: Optional[float] = None,
    prefer_lng: Optional[float] = None,
) -> Optional[str]:
    """
    Find best replacement activity for a slot.
    Filters: tag match, open hours, kid_ok, mobility_ok, budget.
    Excludes already-overused activities.
    Prefers geographically close if lat/lng provided.
    """
    kid_ages = family.get("kid_ages", "none")
    mobility = family.get("mobility", "full")
    budget = family.get("budget_tier", "mid")

    cap = lambda act_id: 5 if (act_by_id.get(act_id) and is_cafe(act_by_id[act_id])) else 3

    candidates = []
    for act in all_activities:
        aid = act["id"]
        if aid == exclude_id:
            continue
        # Check usage cap
        used = usage_counts.get(aid, 0)
        c = 5 if is_cafe(act) else 3
        if used >= c:
            continue
        # Tag match
        if not tags_match_slot(act, slot_idx):
            continue
        # Open hours
        if not activity_open_for_slot(act, slot_idx):
            continue
        # Kid ok
        if kid_ages != "none" and not act.get("kid_ok", True):
            continue
        # Mobility ok
        if mobility != "full" and not act.get("mobility_ok", True):
            continue
        # Budget
        if not budget_ok(act, budget):
            continue
        # Distance score
        dist = 0.0
        if prefer_lat and prefer_lng:
            dlat = (act.get("lat", prefer_lat) - prefer_lat)
            dlng = (act.get("lng", prefer_lng) - prefer_lng)
            dist = dlat*dlat + dlng*dlng
        candidates.append((dist, aid))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]

# ─── Main processing ─────────────────────────────────────────────────────────
total_slot_replacements = 0
total_repetition_fixes = 0
could_not_fix = []

output_records = []

with open(INPUT_PATH, encoding="utf-8") as f:
    records = [json.loads(line) for line in f if line.strip()]

for record in records:
    fam_id = record["id"]
    family = record["family"]
    itinerary = list(record["itinerary"])  # 30 slots

    # Fix malformed IDs (spaces → underscores, leading/trailing spaces)
    itinerary = [sid.strip().replace(" ", "_") for sid in itinerary]

    usage_counts: dict[str, int] = {}
    # Count initial usage
    for aid in itinerary:
        usage_counts[aid] = usage_counts.get(aid, 0) + 1

    replaced_slots = []
    rep_fixes = []

    # Pass 1: slot-type + open-hours checks (replace violations)
    # We need to rebuild counts as we go
    usage_counts_pass = {}
    new_itinerary = list(itinerary)

    for i, aid in enumerate(new_itinerary):
        slot_idx = i % 6
        act = act_by_id.get(aid)
        need_replace = False
        reason = None

        if act is None:
            need_replace = True
            reason = f"unknown_id:{aid}"
        else:
            tag_ok = tags_match_slot(act, slot_idx)
            hours_ok = activity_open_for_slot(act, slot_idx)
            if not tag_ok:
                need_replace = True
                reason = f"tag_mismatch slot{slot_idx}"
            elif not hours_ok:
                need_replace = True
                reason = f"hours_mismatch slot{slot_idx}"

        if need_replace:
            # Get preferred location from previous slot
            prev_act = act_by_id.get(new_itinerary[i-1]) if i > 0 else None
            plat = prev_act.get("lat") if prev_act else None
            plng = prev_act.get("lng") if prev_act else None

            replacement = find_replacement(
                slot_idx, family, usage_counts_pass,
                exclude_id=aid,
                prefer_lat=plat, prefer_lng=plng
            )
            if replacement:
                new_itinerary[i] = replacement
                replaced_slots.append((i, aid, replacement, reason))
                total_slot_replacements += 1
            else:
                could_not_fix.append(f"{fam_id} slot{i} ({reason})")

        # Update usage counts
        final_aid = new_itinerary[i]
        usage_counts_pass[final_aid] = usage_counts_pass.get(final_aid, 0) + 1

    # Pass 2: repetition cap
    usage_counts_rep: dict[str, int] = {}
    for i, aid in enumerate(new_itinerary):
        usage_counts_rep[aid] = usage_counts_rep.get(aid, 0) + 1

    for i in range(len(new_itinerary)):
        aid = new_itinerary[i]
        act = act_by_id.get(aid)
        cap = 5 if (act and is_cafe(act)) else 3
        # Count how many times this aid appears in slots 0..i (inclusive)
        count_so_far = new_itinerary[:i+1].count(aid)
        if count_so_far > cap:
            slot_idx = i % 6
            # Build current usage excluding future occurrences we haven't processed
            temp_counts = {}
            for j in range(i):
                temp_counts[new_itinerary[j]] = temp_counts.get(new_itinerary[j], 0) + 1

            prev_act = act_by_id.get(new_itinerary[i-1]) if i > 0 else None
            plat = prev_act.get("lat") if prev_act else None
            plng = prev_act.get("lng") if prev_act else None

            replacement = find_replacement(
                slot_idx, family, temp_counts,
                exclude_id=aid,
                prefer_lat=plat, prefer_lng=plng
            )
            if replacement:
                rep_fixes.append((i, aid, replacement))
                total_repetition_fixes += 1
                new_itinerary[i] = replacement
            else:
                could_not_fix.append(f"{fam_id} slot{i} rep-cap({aid})")

    output_records.append({
        "id": fam_id,
        "family": family,
        "itinerary": new_itinerary,
    })

    # Per-family summary
    print(f"\n{fam_id}:")
    if replaced_slots:
        for (idx, old, new, reason) in replaced_slots:
            print(f"  Slot {idx:2d} (type {idx%6}): {old} -> {new}  [{reason}]")
    else:
        print("  No slot violations.")
    if rep_fixes:
        for (idx, old, new) in rep_fixes:
            print(f"  Rep-cap slot {idx:2d}: {old} -> {new}")
    else:
        print("  No repetition fixes.")

# ─── Write output ────────────────────────────────────────────────────────────
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    for rec in output_records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print("\n" + "="*60)
print(f"Total slot replacements (tag/hours): {total_slot_replacements}")
print(f"Total repetition fixes:             {total_repetition_fixes}")
if could_not_fix:
    print(f"Could not fix ({len(could_not_fix)}):")
    for s in could_not_fix:
        print(f"  - {s}")
else:
    print("All violations fixed successfully.")
print(f"Output written to: {OUTPUT_PATH}")
