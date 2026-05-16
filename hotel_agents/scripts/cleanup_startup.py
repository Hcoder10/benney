"""
Audit + fix startup_founder_pitch_tour itineraries per sonnet_cleanup_brief.md
"""
import json
import re
from collections import defaultdict

DATADIR = r"C:\Users\sarta\rosea\hotel_agents\data"
INPUT_FILE  = DATADIR + r"\families_part_startup_founder_pitch_tour__b0.jsonl"
OUTPUT_FILE = DATADIR + r"\families_clean_startup_founder_pitch_tour__b0.jsonl"
BANK_FILE   = DATADIR + r"\activities_bay.json"

# ── Slot rules ────────────────────────────────────────────────────────────────
SLOT_RULES = {
    0: {"window": (7, 9),   "tags": {"cafe","coffee","bakery","breakfast"}},
    1: {"window": (8, 10),  "tags": {"cafe","coffee","bakery","breakfast","brunch"}},
    2: {"window": (10, 13), "tags": {"museum","tour","outdoor","hiking","campus","tech","art",
                                      "science","history","gardens","walking","shopping",
                                      "viewpoint","landmark","architecture"}},
    3: {"window": (12, 17), "tags": {"restaurant","lunch","casual","park","scenic","beach",
                                      "outdoor","hiking","tour","shopping","winery","wine","tasting"}},
    4: {"window": (17, 20), "tags": {"restaurant","dinner","fine-dining","scenic","sunset","wine","casual"}},
    5: {"window": (20, 23), "tags": {"bar","cocktails","nightlife","lounge","dinner","fine-dining",
                                      "speakeasy"}},
}

# Budget tier ordering
BUDGET_ORDER = {"shoestring": 0, "mid": 1, "premium": 2, "luxury": 3}

# ── Hour parsing ──────────────────────────────────────────────────────────────
def parse_hour_str(s):
    """Return float hour (e.g. 17.5) from '17:30' or '5:30 PM' style strings. None on fail."""
    if not s or s.lower() in ("closed", ""):
        return None
    # Normalise "sunrise" / "sunset" to 6 / 20
    if "sunrise" in s.lower():
        return 6.0
    if "sunset" in s.lower():
        return 20.0
    # Try HH:MM
    m = re.match(r"(\d{1,2}):(\d{2})", s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        # 12-hour suffix
        if "pm" in s.lower() and h != 12:
            h += 12
        if "am" in s.lower() and h == 12:
            h = 0
        return h + mn / 60
    # bare integer hour
    m = re.match(r"(\d{1,2})\s*(am|pm)", s.lower())
    if m:
        h = int(m.group(1))
        if m.group(2) == "pm" and h != 12:
            h += 12
        if m.group(2) == "am" and h == 12:
            h = 0
        return float(h)
    return None

def parse_open_range(s):
    """Return (open_h, close_h) floats or None."""
    if not s or s.lower() == "closed":
        return None
    parts = re.split(r"\s*[-–]\s*", s, maxsplit=1)
    if len(parts) == 2:
        o = parse_hour_str(parts[0].strip())
        c = parse_hour_str(parts[1].strip())
        if o is not None and c is not None:
            return (o, c)
    return None

def slot_open(activity, slot_idx):
    """True iff activity open_hours.fri overlaps the slot window (ANY overlap)."""
    rule = SLOT_RULES[slot_idx % 6]
    w_open, w_close = rule["window"]   # e.g. (7, 9)
    fri = activity.get("open_hours", {}).get("fri", "")
    if not fri or fri.lower() == "closed":
        return False
    rng = parse_open_range(fri)
    if rng is None:
        return True  # can't parse -> assume OK (conservative)
    act_open, act_close = rng
    # ANY overlap: act opens before window closes AND closes after window opens
    return act_open < w_close and act_close > w_open

def tags_ok(activity, slot_idx):
    """True iff activity tags overlap required tags for the slot."""
    required = SLOT_RULES[slot_idx % 6]["tags"]
    act_tags = {t.lower().replace("_", "-").replace(" ", "-") for t in activity.get("tags", [])}
    # Also check plain space/underscore variants
    act_tags_plain = {t.lower() for t in activity.get("tags", [])}
    combined = act_tags | act_tags_plain
    return bool(required & combined)

def normalize_tags(activity):
    """Return normalised tag list (lowercase, hyphens)."""
    return [t.lower().replace(" ", "-").replace("_", "-") for t in activity.get("tags", [])]

# ── Load bank ─────────────────────────────────────────────────────────────────
with open(BANK_FILE, encoding="utf-8") as f:
    bank_raw = json.load(f)

# Normalize tags in place for matching
act_map = {}
for a in bank_raw:
    a_copy = dict(a)
    a_copy["tags_norm"] = normalize_tags(a)
    act_map[a["id"]] = a_copy

def budget_ok(activity, family_budget):
    fam_level = BUDGET_ORDER.get(family_budget, 0)
    act_level = BUDGET_ORDER.get(activity.get("budget_tier", "shoestring"), 0)
    return act_level <= fam_level

def family_ok(activity, family):
    """kid / mobility / budget checks."""
    if not activity.get("kid_ok", True) and family.get("kid_ages", "none") != "none":
        return False
    if not activity.get("mobility_ok", True) and family.get("mobility", "full") != "full":
        return False
    if not budget_ok(activity, family.get("budget_tier", "shoestring")):
        return False
    return True

def find_replacement(slot_idx, family, used_counts, exclude_id=None):
    """Return best replacement activity id, or None."""
    required_tags = SLOT_RULES[slot_idx % 6]["tags"]
    candidates = []
    for aid, a in act_map.items():
        if aid == exclude_id:
            continue
        if not family_ok(a, family):
            continue
        act_tags = set(a.get("tags_norm", []))
        act_tags_raw = {t.lower() for t in a.get("tags", [])}
        combined = act_tags | act_tags_raw
        if not (required_tags & combined):
            continue
        if not slot_open(a, slot_idx):
            continue
        is_cafe = bool({"cafe","coffee","bakery","breakfast"} & act_tags)
        cap = 5 if is_cafe else 3
        if used_counts.get(aid, 0) >= cap:
            continue
        candidates.append((used_counts.get(aid, 0), aid))
    if not candidates:
        return None
    # Prefer least-used
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]

# ── Process families ──────────────────────────────────────────────────────────
with open(INPUT_FILE, encoding="utf-8") as f:
    families = [json.loads(line) for line in f if line.strip()]

total_slot_fixes = 0
total_rep_fixes = 0
no_replacement_cases = []

cleaned = []

for fam_rec in families:
    fid = fam_rec["id"]
    family = fam_rec["family"]
    itinerary = list(fam_rec["itinerary"])  # copy

    used_counts = defaultdict(int)
    for aid in itinerary:
        used_counts[aid] += 1

    # Pass 1 – slot-type + open-hours violations
    for i, aid in enumerate(itinerary):
        slot_idx = i % 6
        if aid not in act_map:
            # Unknown activity – attempt replacement
            replacement = find_replacement(slot_idx, family, used_counts, exclude_id=None)
            if replacement:
                print(f"  [{fid}] slot {i} (slot_type {slot_idx}): UNKNOWN '{aid}' -> '{replacement}'")
                used_counts[aid] -= 1
                itinerary[i] = replacement
                used_counts[replacement] += 1
                total_slot_fixes += 1
            else:
                no_replacement_cases.append((fid, i, aid, "unknown"))
            continue

        a = act_map[aid]
        tag_fail = not tags_ok(a, slot_idx)
        hours_fail = not slot_open(a, slot_idx)

        if tag_fail or hours_fail:
            reasons = []
            if tag_fail:
                reasons.append("tag")
            if hours_fail:
                reasons.append("hours")
            replacement = find_replacement(slot_idx, family, used_counts, exclude_id=aid)
            if replacement:
                print(f"  [{fid}] slot {i} (slot_type {slot_idx}): {'+'.join(reasons)} fail '{aid}' "
                      f"tags={a.get('tags_norm',[])} fri={a['open_hours'].get('fri','?')} -> '{replacement}'")
                used_counts[aid] -= 1
                itinerary[i] = replacement
                used_counts[replacement] += 1
                total_slot_fixes += 1
            else:
                no_replacement_cases.append((fid, i, aid, "+".join(reasons)))

    # Pass 2 – repetition cap
    used_counts2 = defaultdict(int)
    for i, aid in enumerate(itinerary):
        if aid not in act_map:
            used_counts2[aid] += 1
            continue
        a = act_map[aid]
        act_tags = set(a.get("tags_norm", []))
        is_cafe = bool({"cafe","coffee","bakery","breakfast"} & act_tags)
        cap = 5 if is_cafe else 3
        if used_counts2[aid] >= cap:
            slot_idx = i % 6
            replacement = find_replacement(slot_idx, family, used_counts2, exclude_id=aid)
            if replacement:
                print(f"  [{fid}] slot {i} rep-cap ({used_counts2[aid]}x) '{aid}' -> '{replacement}'")
                itinerary[i] = replacement
                used_counts2[replacement] += 1
                total_rep_fixes += 1
            else:
                no_replacement_cases.append((fid, i, aid, "rep-cap"))
                used_counts2[aid] += 1
        else:
            used_counts2[aid] += 1

    cleaned.append({"id": fid, "family": family, "itinerary": itinerary})

# ── Write output ──────────────────────────────────────────────────────────────
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    for rec in cleaned:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print()
print("=" * 60)
print(f"Slot-type / open-hours replacements : {total_slot_fixes}")
print(f"Repetition-cap replacements         : {total_rep_fixes}")
print(f"Total replacements                  : {total_slot_fixes + total_rep_fixes}")
print(f"No-replacement cases                : {len(no_replacement_cases)}")
for case in no_replacement_cases:
    print(f"  {case}")
print(f"Output written to: {OUTPUT_FILE}")
