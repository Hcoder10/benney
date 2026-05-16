import json
import re

# ---- Load activity bank ----
with open("data/activities_bay.json") as f:
    acts_list = json.load(f)

acts = {a["id"]: a for a in acts_list}

# ---- Slot rules ----
SLOT_TAGS = [
    {"cafe","coffee","bakery","breakfast"},           # 0 early-morning 7-9
    {"cafe","coffee","bakery","breakfast","brunch"},  # 1 breakfast 8-10
    {"museum","tour","outdoor","hiking","campus","tech","art","science","history","gardens","walking","shopping","viewpoint","landmark","architecture"},  # 2 late-morning 10-13
    {"restaurant","lunch","casual","park","scenic","beach","outdoor","hiking","tour","shopping","winery","wine","tasting"},  # 3 lunch+afternoon 12-17
    {"restaurant","dinner","fine-dining","scenic","sunset","wine","casual"},  # 4 evening 17-20
    {"bar","cocktails","nightlife","lounge","dinner","fine-dining","speakeasy"},  # 5 night 20-23
]
SLOT_WINDOWS = [
    (7, 9), (8, 10), (10, 13), (12, 17), (17, 20), (20, 23)
]

def parse_hour(s):
    s = s.strip()
    m = re.match(r"(\d{1,2}):(\d{2})", s)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2))
        # Treat 00:xx as 24:xx when used as a closing time
        # (will be handled at call-site by context)
        return h + mins / 60
    return None

def parse_hour_closing(s):
    """Parse a closing time: 00:xx means midnight = 24.0"""
    s = s.strip()
    m = re.match(r"(\d{1,2}):(\d{2})", s)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2))
        if h == 0:
            h = 24
        return h + mins / 60
    return None

def hours_overlap(open_str, slot_window):
    if not open_str:
        return False
    low = open_str.lower().strip()
    if low in ("closed", "varies", "", "null"):
        return False
    if low == "24h":
        return True
    sessions_raw = [x.strip() for x in open_str.split(",")]
    sw_start, sw_end = slot_window
    for sess in sessions_raw:
        parts = re.split(r"[-–—]", sess)
        if len(parts) == 2:
            a = parse_hour(parts[0])
            b = parse_hour_closing(parts[1])
            if a is not None and b is not None:
                if a < sw_end and b > sw_start:
                    return True
    return False

def normalize_tag(t):
    return t.lower().replace("-","").replace("_","").replace(" ","")

def tags_match(activity_tags, required_tags):
    act_norm = {normalize_tag(t) for t in activity_tags}
    req_norm = {normalize_tag(t) for t in required_tags}
    return bool(act_norm & req_norm)

def is_cafe(activity):
    tags = [t.lower() for t in activity.get("tags", [])]
    return any(t in ("cafe","coffee","bakery","breakfast","brunch") for t in tags)

TIER = {"shoestring": 0, "mid": 1, "premium": 2, "luxury": 3}

def slot_violations(aid, slot_idx):
    if aid not in acts:
        return (False, False)
    act = acts[aid]
    sidx = slot_idx % 6
    required = SLOT_TAGS[sidx]
    window = SLOT_WINDOWS[sidx]
    act_norm = {normalize_tag(t) for t in act.get("tags", [])}
    req_norm = {normalize_tag(t) for t in required}
    tag_ok = bool(act_norm & req_norm)
    open_fri = act.get("open_hours", {}).get("fri", "")
    hour_ok = hours_overlap(open_fri, window)
    return (tag_ok, hour_ok)

def find_replacement(slot_idx, family, used_counts, exclude_id=None):
    sidx = slot_idx % 6
    required = SLOT_TAGS[sidx]
    window = SLOT_WINDOWS[sidx]
    family_budget = family["budget_tier"]
    family_mobility = family["mobility"]
    family_kids = family["kid_ages"]
    req_norm = {normalize_tag(t) for t in required}

    candidates = []
    for aid, act in acts.items():
        if aid == exclude_id:
            continue
        act_norm = {normalize_tag(t) for t in act.get("tags", [])}
        if not (act_norm & req_norm):
            continue
        open_fri = act.get("open_hours", {}).get("fri", "")
        if not hours_overlap(open_fri, window):
            continue
        if TIER.get(act.get("budget_tier","mid"), 1) > TIER.get(family_budget, 1):
            continue
        if family_mobility != "full" and not act.get("mobility_ok", True):
            continue
        if family_kids != "none" and not act.get("kid_ok", True):
            continue
        count = used_counts.get(aid, 0)
        max_count = 5 if is_cafe(act) else 3
        if count >= max_count:
            continue
        candidates.append(act)

    candidates.sort(key=lambda a: (used_counts.get(a["id"], 0), a["id"]))
    return candidates[0]["id"] if candidates else None

# ---- Process families ----
with open("data/families_part_wheelchair_accessible_traveler__b0.jsonl") as f:
    families = [json.loads(line) for line in f if line.strip()]

total_slot_fixes = 0
total_rep_fixes = 0
no_replacement = []
output_records = []

for fam_rec in families:
    fam_id = fam_rec["id"]
    family = fam_rec["family"]
    itinerary = list(fam_rec["itinerary"])

    slot_fixes = 0
    rep_fixes = 0

    # Build usage counts
    used_counts = {}
    for aid in itinerary:
        used_counts[aid] = used_counts.get(aid, 0) + 1

    # Pass 1: fix slot-type & open-hours violations
    for i in range(len(itinerary)):
        aid = itinerary[i]
        if aid not in acts:
            rep = find_replacement(i, family, used_counts, exclude_id=aid)
            if rep:
                used_counts[aid] = max(0, used_counts.get(aid, 0) - 1)
                itinerary[i] = rep
                used_counts[rep] = used_counts.get(rep, 0) + 1
                slot_fixes += 1
                print(f"  [{fam_id}] slot {i} (s{i%6}): {aid} -> {rep} [unknown activity]")
            else:
                no_replacement.append((fam_id, i, aid, "unknown"))
            continue

        tag_ok, hour_ok = slot_violations(aid, i)
        if not (tag_ok and hour_ok):
            old = aid
            rep = find_replacement(i, family, used_counts, exclude_id=aid)
            if rep:
                used_counts[old] = max(0, used_counts.get(old, 0) - 1)
                itinerary[i] = rep
                used_counts[rep] = used_counts.get(rep, 0) + 1
                slot_fixes += 1
                reason = "tag" if not tag_ok else "hours"
                print(f"  [{fam_id}] slot {i} (s{i%6}): {old} -> {rep} [{reason}]")
            else:
                no_replacement.append((fam_id, i, aid, "no_candidate"))

    # Pass 2: fix repetition (>3 for non-cafe, >5 for cafe)
    used_counts2 = {}
    for i in range(len(itinerary)):
        aid = itinerary[i]
        used_counts2[aid] = used_counts2.get(aid, 0) + 1
        act = acts.get(aid)
        max_count = 5 if (act and is_cafe(act)) else 3
        if used_counts2[aid] > max_count:
            old = aid
            temp_counts = dict(used_counts2)
            temp_counts[old] -= 1
            rep = find_replacement(i, family, temp_counts, exclude_id=old)
            if rep:
                used_counts2[old] -= 1
                itinerary[i] = rep
                used_counts2[rep] = used_counts2.get(rep, 0) + 1
                rep_fixes += 1
                print(f"  [{fam_id}] rep fix slot {i} (s{i%6}): {old} (#{used_counts2[old]+1}) -> {rep}")
            else:
                no_replacement.append((fam_id, i, aid, "rep_no_candidate"))

    total_slot_fixes += slot_fixes
    total_rep_fixes += rep_fixes

    out = {"id": fam_id, "family": family, "itinerary": itinerary}
    output_records.append(out)
    print(f"{fam_id}: {slot_fixes} slot fixes, {rep_fixes} rep fixes")

# Write output
with open("data/families_clean_wheelchair_accessible_traveler__b0.jsonl", "w") as f:
    for rec in output_records:
        f.write(json.dumps(rec) + "\n")

print(f"\n=== SUMMARY ===")
print(f"Total slot-type/hours fixes: {total_slot_fixes}")
print(f"Total repetition fixes:      {total_rep_fixes}")
print(f"Grand total fixes:           {total_slot_fixes + total_rep_fixes}")
print(f"Families with unfixable:     {len(set(x[0] for x in no_replacement))}")
if no_replacement:
    print("Unfixable details:")
    for x in no_replacement:
        print(f"  {x}")
