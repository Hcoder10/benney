"""
Audit & repair families_part_digital_nomad_solo__b0.jsonl
Output: families_clean_digital_nomad_solo__b0.jsonl
"""

import json
import math
from copy import deepcopy

# ── paths ──────────────────────────────────────────────────────────────────
DATA = r"C:\Users\sarta\rosea\hotel_agents\data"
ACTS_PATH = DATA + r"\activities_bay.json"
IN_PATH   = DATA + r"\families_part_digital_nomad_solo__b0.jsonl"
OUT_PATH  = DATA + r"\families_clean_digital_nomad_solo__b0.jsonl"

# ── load activity bank ─────────────────────────────────────────────────────
with open(ACTS_PATH, encoding="utf-8") as f:
    ACTS_LIST = json.load(f)
ACTS = {a["id"]: a for a in ACTS_LIST}

# ── slot definitions ───────────────────────────────────────────────────────
SLOT_DEFS = [
    # (slot_index, time_start_h, time_end_h, required_tags_any_of)
    (0, 7,  9,  {"cafe","coffee","bakery","breakfast"}),
    (1, 8,  10, {"cafe","coffee","bakery","breakfast","brunch"}),
    (2, 10, 13, {"museum","tour","outdoor","hiking","campus","tech","art","science",
                 "history","gardens","walking","shopping","viewpoint","landmark",
                 "architecture"}),
    (3, 12, 17, {"restaurant","lunch","casual","park","scenic","beach","outdoor",
                 "hiking","tour","shopping","winery","wine","tasting"}),
    (4, 17, 20, {"restaurant","dinner","fine-dining","scenic","sunset","wine","casual"}),
    (5, 20, 23, {"bar","cocktails","nightlife","lounge","dinner","fine-dining",
                 "speakeasy"}),
]

BUDGET_ORDER = ["shoestring", "mid", "premium", "luxury"]

def budget_ok(act_tier, fam_tier):
    return BUDGET_ORDER.index(act_tier) <= BUDGET_ORDER.index(fam_tier)

# ── open-hours parsing ─────────────────────────────────────────────────────
def parse_hour(s):
    """Return float hour from '8:00', '08:00', '17:30', '8 AM', '5:30 PM', etc."""
    s = s.strip()
    pm = s.upper().endswith("PM")
    am = s.upper().endswith("AM")
    s2 = s.upper().replace("PM","").replace("AM","").strip()
    parts = s2.replace(":", ".").split(".")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    fh = h + m/60.0
    if pm and h != 12:
        fh += 12
    if am and h == 12:
        fh -= 12
    return fh

def parse_session(segment):
    """Parse a single 'HH:MM-HH:MM' or 'HH:MM AM - HH:MM PM' segment into (open_s, open_e)."""
    segment = segment.strip()
    # prefer space-surrounded dash separators (handles AM/PM times)
    for sep in [" - ", " – ", "–"]:
        if sep in segment:
            parts = segment.split(sep, 1)
            open_s = parse_hour(parts[0])
            open_e = parse_hour(parts[1])
            if open_e == 0.0 and open_s > 0:
                open_e = 24.0
            return open_s, open_e
    # plain "09:30-16:00" — split on last '-' to avoid splitting negative or hour
    # Use rfind of '-' after the first colon
    idx = segment.rfind("-")
    if idx < 1:
        raise ValueError(f"Cannot parse segment: {segment!r}")
    open_s = parse_hour(segment[:idx])
    open_e = parse_hour(segment[idx+1:])
    if open_e == 0.0 and open_s > 0:
        open_e = 24.0
    return open_s, open_e

def open_hours_overlap(act, slot_start_h, slot_end_h):
    """Return True if the activity is open during any part of [slot_start_h, slot_end_h)."""
    oh = act.get("open_hours", {})
    val = oh.get("fri", oh.get("mon", ""))
    if not val or val.lower() in ("closed", "n/a", ""):
        return False
    if "sunset" in val.lower():
        # treat as 08:00-20:00
        return 8.0 < slot_end_h and 20.0 > slot_start_h

    # Split multi-session strings (e.g., "11:30-14:00, 17:30-22:30" or "A; B")
    raw = val.strip()
    # split on '; ' or ', ' to get individual sessions
    import re
    sessions_raw = re.split(r"[;,]\s*", raw)
    for seg in sessions_raw:
        seg = seg.strip()
        if not seg:
            continue
        try:
            open_s, open_e = parse_session(seg)
            if open_s < slot_end_h and open_e > slot_start_h:
                return True
        except Exception:
            return True  # can't parse -> assume open
    return False

def normalize_tag(t):
    """Normalize tag: lowercase, replace spaces with hyphens so 'fine dining' == 'fine-dining'."""
    return t.lower().replace(" ", "-")

def tags_match(act, required_tags):
    act_tags = set(normalize_tag(t) for t in act.get("tags", []))
    norm_required = set(normalize_tag(r) for r in required_tags)
    return bool(act_tags & norm_required)

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def is_cafe(act):
    tags = set(t.lower() for t in act.get("tags", []))
    return bool(tags & {"cafe","coffee","bakery"})

def max_uses(act):
    return 5 if is_cafe(act) else 3

# ── find replacement ───────────────────────────────────────────────────────
def find_replacement(slot_idx, fam, usage_counts, prev_act_id, current_itin):
    si = slot_idx % 6
    _, start_h, end_h, req_tags = SLOT_DEFS[si]
    fam_tier = fam["budget_tier"]
    kid_ages = fam.get("kid_ages", "none")
    mobility = fam.get("mobility", "full")

    prev_lat = prev_lng = None
    if prev_act_id and prev_act_id in ACTS:
        prev_lat = ACTS[prev_act_id].get("lat")
        prev_lng = ACTS[prev_act_id].get("lng")

    candidates = []
    for act in ACTS_LIST:
        aid = act["id"]
        # not over repetition cap
        if usage_counts.get(aid, 0) >= max_uses(act):
            continue
        # slot tag match
        if not tags_match(act, req_tags):
            continue
        # open hours
        if not open_hours_overlap(act, start_h, end_h):
            continue
        # budget
        if not budget_ok(act["budget_tier"], fam_tier):
            continue
        # kid constraint
        if not act.get("kid_ok", True) and kid_ages != "none":
            continue
        # mobility constraint
        if not act.get("mobility_ok", True) and mobility != "full":
            continue
        # geo score
        dist = 0
        if prev_lat is not None:
            dist = haversine(prev_lat, prev_lng, act.get("lat", prev_lat), act.get("lng", prev_lng))
        candidates.append((dist, aid, act))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][2]["id"]

# ── main audit loop ────────────────────────────────────────────────────────
total_slot_replacements = 0
total_repetition_fixes = 0
families_no_replacement = []

out_lines = []

with open(IN_PATH, encoding="utf-8") as f:
    records = [json.loads(l) for l in f if l.strip()]

for rec in records:
    fam_id = rec["id"]
    fam = rec["family"]
    itin = list(rec["itinerary"])  # 30 slots

    slot_replacements = 0
    rep_fixes = 0

    # pass 1: slot-type + open-hours violations
    usage = {}
    for aid in itin:
        usage[aid] = usage.get(aid, 0) + 1

    new_itin = list(itin)
    usage2 = {}  # track as we build

    for i in range(30):
        aid = new_itin[i]
        si = i % 6
        _, start_h, end_h, req_tags = SLOT_DEFS[si]

        act = ACTS.get(aid)
        if act is None:
            # unknown activity — try to replace
            prev = new_itin[i-1] if i > 0 else None
            repl = find_replacement(i, fam, usage2, prev, new_itin)
            if repl:
                new_itin[i] = repl
                slot_replacements += 1
            else:
                families_no_replacement.append((fam_id, i, aid, "unknown"))
            aid = new_itin[i]
            act = ACTS.get(aid)

        needs_replace = False
        reason = ""
        if act:
            if not tags_match(act, req_tags):
                needs_replace = True
                reason = "tag_mismatch"
            elif not open_hours_overlap(act, start_h, end_h):
                needs_replace = True
                reason = "hours_mismatch"

        if needs_replace:
            prev = new_itin[i-1] if i > 0 else None
            repl = find_replacement(i, fam, usage2, prev, new_itin)
            if repl:
                new_itin[i] = repl
                slot_replacements += 1
                print(f"  [{fam_id}] slot {i} (type {si}): replaced {aid!r} ({reason}) -> {repl!r}")
            else:
                families_no_replacement.append((fam_id, i, aid, reason))
                print(f"  [{fam_id}] slot {i}: NO replacement found for {aid!r} ({reason})")

        usage2[new_itin[i]] = usage2.get(new_itin[i], 0) + 1

    # pass 2: repetition cap
    usage3 = {}
    for i in range(30):
        aid = new_itin[i]
        act = ACTS.get(aid)
        cap = max_uses(act) if act else 3
        usage3[aid] = usage3.get(aid, 0) + 1
        if usage3[aid] > cap:
            prev = new_itin[i-1] if i > 0 else None
            # exclude current over-used activity from candidates
            temp_usage = dict(usage3)
            temp_usage[aid] = cap  # pretend it's at cap so find_replacement excludes it
            repl = find_replacement(i, fam, temp_usage, prev, new_itin)
            if repl:
                print(f"  [{fam_id}] slot {i}: repetition fix {aid!r} (count {usage3[aid]}) -> {repl!r}")
                usage3[aid] -= 1  # undo the count for replaced
                new_itin[i] = repl
                usage3[repl] = usage3.get(repl, 0) + 1
                rep_fixes += 1
            else:
                print(f"  [{fam_id}] slot {i}: no rep-fix replacement for {aid!r}")

    total_slot_replacements += slot_replacements
    total_repetition_fixes += rep_fixes

    out_rec = {"id": fam_id, "family": deepcopy(fam), "itinerary": new_itin}
    out_lines.append(json.dumps(out_rec, ensure_ascii=False))

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines) + "\n")

print("\n=== SUMMARY ===")
print(f"Slot-type / open-hours replacements : {total_slot_replacements}")
print(f"Repetition-cap fixes                 : {total_repetition_fixes}")
print(f"Total fixes                          : {total_slot_replacements + total_repetition_fixes}")
if families_no_replacement:
    print(f"Could not find replacement ({len(families_no_replacement)} cases):")
    for fam_id, slot, aid, reason in families_no_replacement:
        print(f"  {fam_id} slot {slot} {aid!r} ({reason})")
else:
    print("All violations fixed successfully.")
print(f"\nOutput written to: {OUT_PATH}")
