"""
Itinerary cleanup script for families_part_business_traveler_executive__b0.jsonl
Follows the rules in sonnet_cleanup_brief.md
"""

import json
import math
from pathlib import Path

DATA_DIR = Path("C:/Users/sarta/rosea/hotel_agents/data")
ACTIVITIES_FILE = DATA_DIR / "activities_bay.json"
INPUT_FILE = DATA_DIR / "families_part_business_traveler_executive__b0.jsonl"
OUTPUT_FILE = DATA_DIR / "families_clean_business_traveler_executive__b0.jsonl"

# ─── Slot rules ────────────────────────────────────────────────────────────────
SLOT_RULES = [
    # slot 0: early-morning 7-9 AM
    {"name": "early-morning", "window": (7, 9),  "tags": {"cafe", "coffee", "bakery", "breakfast"}},
    # slot 1: breakfast 8-10 AM
    {"name": "breakfast",     "window": (8, 10), "tags": {"cafe", "coffee", "bakery", "breakfast", "brunch"}},
    # slot 2: late-morning 10 AM-1 PM
    {"name": "late-morning",  "window": (10, 13),"tags": {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art", "science", "history", "gardens", "walking", "shopping", "viewpoint", "landmark", "architecture"}},
    # slot 3: lunch+afternoon 12-5 PM
    {"name": "lunch+afternoon","window": (12, 17),"tags": {"restaurant", "lunch", "casual", "park", "scenic", "beach", "outdoor", "hiking", "tour", "shopping", "winery", "wine", "tasting"}},
    # slot 4: evening 5-8 PM
    {"name": "evening",       "window": (17, 20),"tags": {"restaurant", "dinner", "fine-dining", "scenic", "sunset", "wine", "casual"}},
    # slot 5: night 8-11 PM
    {"name": "night",         "window": (20, 23),"tags": {"bar", "cocktails", "nightlife", "lounge", "dinner", "fine-dining", "speakeasy"}},
]

BUDGET_ORDER = ["shoestring", "mid", "premium", "luxury"]

def budget_ok(activity_tier, family_tier):
    """Activity budget must be <= family budget tier."""
    try:
        return BUDGET_ORDER.index(activity_tier) <= BUDGET_ORDER.index(family_tier)
    except ValueError:
        return True  # unknown tier, allow

def parse_hour(s):
    """Parse '7:00 AM', '07:00', '17:30', etc. → float hours (0-24)."""
    s = s.strip()
    if not s or s.lower() in ("closed", "n/a", ""):
        return None
    # Handle AM/PM
    pm = "PM" in s.upper()
    am = "AM" in s.upper()
    s_clean = s.upper().replace("AM", "").replace("PM", "").strip()
    # Handle colon
    parts = s_clean.replace(":", " ").split()
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

def parse_hours_range(hours_str):
    """Parse '07:00-18:00' or '7:00 AM - 9:00 PM' → (open_float, close_float) or None if closed."""
    if not hours_str or hours_str.strip().lower() in ("closed", "n/a", ""):
        return None
    # Split on dash (but be careful with negative)
    # Try " - " first
    for sep in [" - ", "-"]:
        if sep in hours_str:
            parts = hours_str.split(sep, 1)
            if len(parts) == 2:
                o = parse_hour(parts[0])
                c = parse_hour(parts[1])
                if o is not None and c is not None:
                    return (o, c)
    return None

def is_open_during(activity, window_start, window_end):
    """Check if activity is open during [window_start, window_end) on Friday."""
    fri = activity.get("open_hours", {}).get("fri", "")
    if not fri or fri.strip().lower() == "closed":
        # If no fri hours, try checking if it has any hours at all
        # Some activities use "08:00-sunset" style - treat sunset as 20:00
        return False
    hours_str = fri
    # Handle "sunset" keyword
    hours_str = hours_str.replace("sunset", "20:00").replace("Sunset", "20:00")
    rng = parse_hours_range(hours_str)
    if rng is None:
        # Can't parse - be permissive
        return True
    open_h, close_h = rng
    # Overlap check: activity open window overlaps slot window
    # Activity: [open_h, close_h), Slot: [window_start, window_end)
    # Overlap if open_h < window_end AND close_h > window_start
    return open_h < window_end and close_h > window_start

def tags_ok(activity, required_tags):
    """Check if any activity tag matches any required tag."""
    act_tags = set(t.lower() for t in activity.get("tags", []))
    return bool(act_tags & required_tags)

def haversine(lat1, lng1, lat2, lng2):
    """Distance in km between two lat/lng points."""
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def is_cafe(activity):
    return any(t.lower() in ("cafe", "coffee", "bakery") for t in activity.get("tags", []))

def passes_family_filters(activity, family):
    """kid_ok, mobility_ok, budget_tier checks."""
    kid_ages = family.get("kid_ages", "none")
    mobility = family.get("mobility", "full")
    budget = family.get("budget_tier", "mid")
    # kid constraint
    if not activity.get("kid_ok", True) and kid_ages != "none":
        return False
    # mobility constraint
    if not activity.get("mobility_ok", True) and mobility != "full":
        return False
    # budget constraint
    if not budget_ok(activity.get("budget_tier", "shoestring"), budget):
        return False
    return True

def find_replacement(slot_idx, family, act_bank, used_counts, prev_lat=None, prev_lng=None):
    """Find a valid replacement activity for the given slot."""
    rule = SLOT_RULES[slot_idx % 6]
    window_start, window_end = rule["window"]
    required_tags = rule["tags"]

    candidates = []
    for act in act_bank.values():
        # Must match slot tags
        if not tags_ok(act, required_tags):
            continue
        # Must be open
        if not is_open_during(act, window_start, window_end):
            continue
        # Must pass family filters
        if not passes_family_filters(act, family):
            continue
        # Repetition cap
        act_id = act["id"]
        count = used_counts.get(act_id, 0)
        cap = 5 if is_cafe(act) else 3
        if count >= cap:
            continue
        candidates.append(act)

    if not candidates:
        return None

    # Sort by geographic proximity to previous slot if known
    if prev_lat is not None and prev_lng is not None:
        candidates.sort(key=lambda a: haversine(prev_lat, prev_lng, a["lat"], a["lng"]))

    return candidates[0]

def slot_is_valid(activity, slot_idx):
    """Check slot-type tag rule AND open-hours rule."""
    rule = SLOT_RULES[slot_idx % 6]
    window_start, window_end = rule["window"]
    required_tags = rule["tags"]
    # Tag check
    if not tags_ok(activity, required_tags):
        return False, "tag"
    # Open-hours check
    if not is_open_during(activity, window_start, window_end):
        return False, "hours"
    return True, None

def clean_family(family_record, act_bank):
    """Clean one family record. Returns (cleaned_record, slot_replacements, repetition_fixes)."""
    family = family_record["family"]
    itinerary = family_record["itinerary"][:]  # copy

    slot_replacements = 0
    repetition_fixes = 0
    cant_fix = []

    # Build used_counts
    used_counts = {}
    for act_id in itinerary:
        used_counts[act_id] = used_counts.get(act_id, 0) + 1

    # Pass 1: slot-type and open-hours violations
    prev_lat, prev_lng = None, None
    for i, act_id in enumerate(itinerary):
        act = act_bank.get(act_id)
        if act is None:
            # Unknown activity - try to replace
            repl = find_replacement(i, family, act_bank, {a: c for a, c in used_counts.items() if a != act_id}, prev_lat, prev_lng)
            if repl:
                used_counts[act_id] = used_counts.get(act_id, 1) - 1
                itinerary[i] = repl["id"]
                used_counts[repl["id"]] = used_counts.get(repl["id"], 0) + 1
                slot_replacements += 1
                prev_lat, prev_lng = repl["lat"], repl["lng"]
            else:
                cant_fix.append((i, act_id, "unknown+no-replacement"))
            continue

        valid, reason = slot_is_valid(act, i)
        if not valid:
            # Temporarily reduce count for this slot while searching
            used_counts[act_id] -= 1
            repl = find_replacement(i, family, act_bank, used_counts, prev_lat, prev_lng)
            if repl:
                itinerary[i] = repl["id"]
                used_counts[repl["id"]] = used_counts.get(repl["id"], 0) + 1
                slot_replacements += 1
                prev_lat, prev_lng = repl["lat"], repl["lng"]
            else:
                # Restore and keep original
                used_counts[act_id] += 1
                cant_fix.append((i, act_id, f"slot-{reason}"))
                prev_lat, prev_lng = act["lat"], act["lng"]
        else:
            prev_lat, prev_lng = act["lat"], act["lng"]

    # Pass 2: repetition cap
    # Recount after pass 1
    used_counts = {}
    for act_id in itinerary:
        used_counts[act_id] = used_counts.get(act_id, 0) + 1

    # Find activities exceeding cap
    for act_id, count in list(used_counts.items()):
        act = act_bank.get(act_id)
        cap = 5 if (act and is_cafe(act)) else 3
        if count > cap:
            excess = count - cap
            # Find all positions of this activity and replace excess (from the end)
            positions = [i for i, a in enumerate(itinerary) if a == act_id]
            # Replace the last `excess` occurrences
            for pos in positions[-excess:]:
                used_counts[act_id] -= 1
                repl = find_replacement(pos, family, act_bank, used_counts, None, None)
                if repl and repl["id"] != act_id:
                    itinerary[pos] = repl["id"]
                    used_counts[repl["id"]] = used_counts.get(repl["id"], 0) + 1
                    repetition_fixes += 1
                else:
                    used_counts[act_id] += 1
                    cant_fix.append((pos, act_id, "repetition-no-replacement"))

    cleaned = dict(family_record)
    cleaned["itinerary"] = itinerary
    return cleaned, slot_replacements, repetition_fixes, cant_fix

def main():
    # Load activity bank
    with open(ACTIVITIES_FILE, encoding="utf-8") as f:
        act_list = json.load(f)
    act_bank = {a["id"]: a for a in act_list}

    # Load families
    families = []
    with open(INPUT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                families.append(json.loads(line))

    total_slot_replacements = 0
    total_repetition_fixes = 0
    all_cant_fix = []

    output_lines = []
    for fam in families:
        cleaned, slot_rep, rep_fix, cant_fix = clean_family(fam, act_bank)
        total_slot_replacements += slot_rep
        total_repetition_fixes += rep_fix
        if cant_fix:
            all_cant_fix.extend([(fam["id"], c) for c in cant_fix])
        output_lines.append(json.dumps(cleaned, ensure_ascii=False))

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for line in output_lines:
            f.write(line + "\n")

    print(f"Done. Output: {OUTPUT_FILE}")
    print(f"Total slot-type/open-hours replacements: {total_slot_replacements}")
    print(f"Total repetition fixes: {total_repetition_fixes}")
    if all_cant_fix:
        print(f"Could not fix ({len(all_cant_fix)} cases):")
        for fam_id, (slot_i, act_id, reason) in all_cant_fix:
            print(f"  {fam_id} slot {slot_i} ({slot_i%6} - {SLOT_RULES[slot_i%6]['name']}): {act_id} [{reason}]")
    else:
        print("All violations fixed successfully.")

if __name__ == "__main__":
    main()
