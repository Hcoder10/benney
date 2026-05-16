#!/usr/bin/env python3
"""
Cleanup script for families_part_remote_work_couple__b0.jsonl
Follows sonnet_cleanup_brief.md rules.
"""

import json
import math
import re
from collections import defaultdict

# ── paths ──────────────────────────────────────────────────────────────────
ACTIVITIES_PATH = r"C:\Users\sarta\rosea\hotel_agents\data\activities_bay.json"
INPUT_PATH  = r"C:\Users\sarta\rosea\hotel_agents\data\families_part_remote_work_couple__b0.jsonl"
OUTPUT_PATH = r"C:\Users\sarta\rosea\hotel_agents\data\families_clean_remote_work_couple__b0.jsonl"

# ── slot rules ─────────────────────────────────────────────────────────────
# (window_start_h, window_end_h, required_tags_any_of)
SLOT_RULES = {
    0: (7,  9,  {"cafe", "coffee", "bakery", "breakfast"}),
    1: (8,  10, {"cafe", "coffee", "bakery", "breakfast", "brunch"}),
    2: (10, 13, {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art",
                  "science", "history", "gardens", "walking", "shopping",
                  "viewpoint", "landmark", "architecture"}),
    3: (12, 17, {"restaurant", "lunch", "casual", "park", "scenic", "beach",
                  "outdoor", "hiking", "tour", "shopping", "winery", "wine", "tasting"}),
    4: (17, 20, {"restaurant", "dinner", "fine-dining", "scenic", "sunset", "wine", "casual"}),
    5: (20, 23, {"bar", "cocktails", "nightlife", "lounge", "dinner",
                  "fine-dining", "speakeasy"}),
}

# budget tier ordering
BUDGET_ORDER = {"shoestring": 0, "mid": 1, "premium": 2, "luxury": 3}

# ── tag normalisation map ──────────────────────────────────────────────────
# Collapse synonyms / variants into canonical forms that match SLOT_RULES
TAG_NORM = {
    "fine dining": "fine-dining",
    "fine_dining": "fine-dining",
    "bars":        "bar",
    "outdoors":    "outdoor",
    "tours":       "tour",
    "trails":      "hiking",
}

# ── helpers ────────────────────────────────────────────────────────────────
def normalize_tags(tags):
    """Return a new list with canonical tag strings."""
    return [TAG_NORM.get(t, t) for t in tags]


def parse_time_str(s):
    """
    Parse a time string into a list of (open_h_float, close_h_float) windows.
    Returns [] if closed/unparseable.
    Handles:
      - "24h" / "24/7" / "always open"  -> [(0, 24)]
      - "sunrise-sunset" / "sunrise to sunset" -> [(7, 20)]  (generous estimate)
      - "16:00-00:00" midnight close -> close = 24
      - multi-session "11:00-15:00, 17:00-22:00" -> two windows
      - "9:30 AM - 3:30 PM", "17:30-21:30", "5:30 PM - 9:00 PM"
    """
    s = s.strip()
    if not s or s.lower() in ("closed", "n/a", ""):
        return []

    sl = s.lower()

    # Always-open variants
    if sl in ("24h", "24/7", "always open", "open 24 hours"):
        return [(0, 24)]

    # Sunrise/sunset — treat as 7 AM to 8 PM (generous; covers all day slots 0-4)
    if "sunrise" in sl or "sunset" in sl:
        return [(7, 20)]

    def to_float(t):
        t = t.strip()
        # Handle "00:00" or "0:00" as midnight = 24 when used as a close time
        m = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)?$', t, re.I)
        if not m:
            m2 = re.match(r'(\d{1,2})\s*(AM|PM)$', t, re.I)
            if m2:
                h = int(m2.group(1))
                ampm = m2.group(2).upper()
                if ampm == "PM" and h != 12:
                    h += 12
                elif ampm == "AM" and h == 12:
                    h = 0
                return float(h)
            return None
        h = int(m.group(1))
        mins = int(m.group(2))
        ampm = (m.group(3) or "").upper()
        if ampm == "PM" and h != 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
        return h + mins / 60.0

    def parse_single_range(rng):
        """Parse a single 'open-close' or 'open - close' range string."""
        rng = rng.strip()
        if " - " in rng:
            parts = rng.split(" - ", 1)
        elif re.search(r'\d-\d', rng):
            # Split on the dash between digits (e.g. "08:00-17:00", "16:00-00:00")
            # Use re.split to split on first '-' that is preceded and followed by digit chars
            parts = re.split(r'(?<=\d)-(?=\d)', rng, maxsplit=1)
        else:
            return None
        if len(parts) != 2:
            return None
        op = to_float(parts[0])
        cl = to_float(parts[1])
        if op is None or cl is None:
            return None
        # midnight close: "00:00" or "0:00" parsed as 0.0 should become 24.0 when < open
        if cl == 0.0 and op > 0:
            cl = 24.0
        return (op, cl)

    # Multi-session: "11:00-15:00, 17:00-22:00"
    if "," in s:
        windows = []
        for seg in s.split(","):
            r = parse_single_range(seg)
            if r:
                windows.append(r)
        return windows if windows else []

    r = parse_single_range(s)
    return [r] if r else []


def parse_time_str_compat(s):
    """Backward-compat wrapper: return first window or None."""
    windows = parse_time_str(s)
    return windows[0] if windows else None


def hours_overlap(open_h, close_h, slot_start, slot_end):
    """True if [open_h, close_h] overlaps [slot_start, slot_end] (ANY overlap)."""
    # overlap iff open < slot_end AND close > slot_start
    return open_h < slot_end and close_h > slot_start


def activity_ok_for_slot(act, slot_idx, family):
    """
    Return True if act passes:
      1. tag match for slot_idx % 6
      2. open_hours (fri) has ANY window overlapping the slot's time window
    Does NOT check kid/mobility/budget here (handled separately).
    """
    slot_type = slot_idx % 6
    win_start, win_end, req_tags = SLOT_RULES[slot_type]

    tags = set(normalize_tags(act["tags"]))

    # tag check
    if not (tags & req_tags):
        return False

    # hours check — ANY session window may overlap
    fri_hours = act["open_hours"].get("fri", "")
    if not fri_hours:
        fri_hours = act["open_hours"].get("Fri", "")
    windows = parse_time_str(fri_hours)
    if not windows:
        return False  # closed or unparseable
    if not any(hours_overlap(op, cl, win_start, win_end) for op, cl in windows):
        return False

    return True


def family_ok(act, family):
    """Check kid_ok, mobility_ok, budget_tier constraints."""
    # kid constraint
    if not act["kid_ok"] and family["kid_ages"] != "none":
        return False
    # mobility
    if not act["mobility_ok"] and family["mobility"] != "full":
        return False
    # budget
    fam_tier = BUDGET_ORDER.get(family["budget_tier"], 99)
    act_tier = BUDGET_ORDER.get(act["budget_tier"], 0)
    if act_tier > fam_tier:
        return False
    return True


def haversine(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def is_cafe(act):
    return bool(set(normalize_tags(act["tags"])) & {"cafe", "coffee", "bakery"})


# ── main ───────────────────────────────────────────────────────────────────
def main():
    with open(ACTIVITIES_PATH, encoding="utf-8") as f:
        raw_acts = json.load(f)

    # Normalise all tags in the bank
    for a in raw_acts:
        a["tags"] = normalize_tags(a["tags"])

    act_map = {a["id"]: a for a in raw_acts}

    with open(INPUT_PATH, encoding="utf-8") as f:
        families = [json.loads(line) for line in f if line.strip()]

    total_slot_replacements = 0
    total_repetition_fixes  = 0
    no_replacement_found    = []  # (fam_id, slot_idx, missing_act_id)

    out_records = []

    for fam_rec in families:
        fam_id  = fam_rec["id"]
        family  = fam_rec["family"]
        itinerary = list(fam_rec["itinerary"])  # copy

        # ── pass 1: fix unknown IDs & slot violations ──────────────────────
        # We need to track counts as we go
        use_counts = defaultdict(int)
        for act_id in itinerary:
            use_counts[act_id] += 1

        # We'll rebuild slot by slot
        new_itinerary = []
        for slot_idx, act_id in enumerate(itinerary):
            act = act_map.get(act_id)

            # Determine if replacement is needed
            needs_replace = False
            reason = ""
            if act is None:
                needs_replace = True
                reason = f"unknown_id:{act_id}"
            elif not activity_ok_for_slot(act, slot_idx, family):
                needs_replace = True
                reason = f"slot_violation:{act_id}"
            elif not family_ok(act, family):
                needs_replace = True
                reason = f"family_constraint:{act_id}"

            if needs_replace:
                # Decrement old usage (it was pre-counted; we're replacing it)
                if act_id in use_counts:
                    use_counts[act_id] -= 1

                # Find replacement
                slot_type = slot_idx % 6
                _, _, req_tags = SLOT_RULES[slot_type]

                # Prefer geographically close to previous slot
                prev_act = None
                if new_itinerary:
                    prev_act = act_map.get(new_itinerary[-1])

                candidates = []
                for cand in raw_acts:
                    if not activity_ok_for_slot(cand, slot_idx, family):
                        continue
                    if not family_ok(cand, family):
                        continue
                    # repetition cap
                    cap = 5 if is_cafe(cand) else 3
                    if use_counts[cand["id"]] >= cap:
                        continue
                    dist = 0.0
                    if prev_act:
                        try:
                            dist = haversine(prev_act["lat"], prev_act["lng"],
                                             cand["lat"], cand["lng"])
                        except Exception:
                            dist = 999.0
                    candidates.append((dist, cand["id"]))

                if candidates:
                    candidates.sort(key=lambda x: x[0])
                    chosen_id = candidates[0][1]
                    new_itinerary.append(chosen_id)
                    use_counts[chosen_id] += 1
                    total_slot_replacements += 1
                    print(f"  [{fam_id}] slot {slot_idx}: replaced '{act_id}' -> '{chosen_id}' ({reason})")
                else:
                    # Keep original but log it
                    new_itinerary.append(act_id)
                    use_counts[act_id] += 1
                    no_replacement_found.append((fam_id, slot_idx, act_id))
                    print(f"  [{fam_id}] slot {slot_idx}: NO REPLACEMENT for '{act_id}' ({reason}) — kept")
            else:
                new_itinerary.append(act_id)

        # ── pass 2: repetition cap enforcement ────────────────────────────
        # Recount with new_itinerary
        use_counts2 = defaultdict(int)
        for act_id in new_itinerary:
            use_counts2[act_id] += 1

        # Find violators (>5 times always; >3 for non-cafe)
        violators = {act_id for act_id, cnt in use_counts2.items()
                     if cnt > (5 if (act_map.get(act_id) and is_cafe(act_map[act_id])) else 3)}

        if violators:
            # Second sweep: replace late occurrences of violators
            seen_counts = defaultdict(int)
            final_itinerary = []
            for slot_idx, act_id in enumerate(new_itinerary):
                act = act_map.get(act_id)
                cap = 5 if (act and is_cafe(act)) else 3
                seen_counts[act_id] += 1

                if seen_counts[act_id] > cap:
                    # Find replacement (not the violator itself)
                    slot_type = slot_idx % 6
                    prev_act = None
                    if final_itinerary:
                        prev_act = act_map.get(final_itinerary[-1])

                    # Build live counts for cap check
                    live_counts = defaultdict(int)
                    for a in final_itinerary:
                        live_counts[a] += 1

                    candidates = []
                    for cand in raw_acts:
                        if cand["id"] == act_id:
                            continue
                        if not activity_ok_for_slot(cand, slot_idx, family):
                            continue
                        if not family_ok(cand, family):
                            continue
                        c_cap = 5 if is_cafe(cand) else 3
                        if live_counts[cand["id"]] >= c_cap:
                            continue
                        dist = 0.0
                        if prev_act:
                            try:
                                dist = haversine(prev_act["lat"], prev_act["lng"],
                                                 cand["lat"], cand["lng"])
                            except Exception:
                                dist = 999.0
                        candidates.append((dist, cand["id"]))

                    if candidates:
                        candidates.sort(key=lambda x: x[0])
                        chosen_id = candidates[0][1]
                        print(f"  [{fam_id}] slot {slot_idx}: repetition fix '{act_id}' (use #{seen_counts[act_id]}) -> '{chosen_id}'")
                        final_itinerary.append(chosen_id)
                        seen_counts[act_id] -= 1  # undo over-count for this slot
                        total_repetition_fixes += 1
                    else:
                        final_itinerary.append(act_id)
                        no_replacement_found.append((fam_id, slot_idx, f"rep_fix:{act_id}"))
                        print(f"  [{fam_id}] slot {slot_idx}: NO rep-fix candidate for '{act_id}' — kept")
                else:
                    final_itinerary.append(act_id)
        else:
            final_itinerary = new_itinerary

        out_records.append({
            "id": fam_id,
            "family": family,
            "itinerary": final_itinerary,
        })

    # ── write output ───────────────────────────────────────────────────────
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for rec in out_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── report ─────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Families processed      : {len(families)}")
    print(f"Slot replacements       : {total_slot_replacements}")
    print(f"Repetition fixes        : {total_repetition_fixes}")
    print(f"Total fixes             : {total_slot_replacements + total_repetition_fixes}")
    if no_replacement_found:
        print(f"Slots with no candidate : {len(no_replacement_found)}")
        for item in no_replacement_found:
            print(f"  {item}")
    else:
        print("Slots with no candidate : 0")
    print(f"\nOutput written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
