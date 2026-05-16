import json, re, math, copy
from collections import defaultdict

with open("C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json", encoding="utf-8") as f:
    raw_activities = json.load(f)
bank = {a["id"]: a for a in raw_activities}

ID_ALIAS = {
    "phil_coffee":              "phils_coffee",
    "li_holiho_yacht_club":     "liholiho_yacht_club",
    "castle_di_amorosa":        "castello_di_amorosa",
    "san_francisco_museum_of_art": "sf_moma_museum",
    "ferny_building_marketplace": "ferry_building_marketplace",
}

def parse_time(s, is_close=False):
    """Parse time string to decimal hours. If is_close and result is 0, return 24 (midnight)."""
    s = s.strip()
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)$", s, re.I)
    if m:
        h, mn, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if ap == "PM" and h != 12: h += 12
        if ap == "AM" and h == 12: h = 0
        result = h + mn/60
        if is_close and result == 0.0: result = 24.0
        return result
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        result = h + mn/60
        if is_close and result == 0.0: result = 24.0
        return result
    return None

def parse_open_window_single(s):
    s = s.strip()
    for sep in [" - ", "-"]:
        if sep in s:
            parts = s.split(sep, 1)
            if len(parts) == 2:
                o = parse_time(parts[0].strip(), is_close=False)
                c = parse_time(parts[1].strip(), is_close=True)
                if o is not None and c is not None:
                    return (o, c)
    return None

def get_open_windows(act):
    fri = act["open_hours"].get("fri", "closed")
    if not fri or fri.strip().lower() in ("closed", "unknown", ""):
        return []
    if fri.strip().lower() in ("24h", "open 24 hours"):
        return [(0, 24)]
    if "sunrise" in fri.lower() or "sunset" in fri.lower():
        return [(6.5, 20)]
    result = []
    for seg in fri.replace(";", ",").split(","):
        w = parse_open_window_single(seg.strip())
        if w:
            result.append(w)
    return result

SLOT_WINDOWS = {0:(7,9),1:(8,10),2:(10,13),3:(12,17),4:(17,20),5:(20,23)}

SLOT_TAGS = {
    0: {"cafe","coffee","bakery","breakfast"},
    1: {"cafe","coffee","bakery","breakfast","brunch"},
    2: {"museum","tour","outdoor","hiking","campus","tech","art","science","history","gardens","walking","shopping","viewpoint","landmark","architecture"},
    3: {"restaurant","lunch","casual","park","scenic","beach","outdoor","hiking","tour","shopping","winery","wine","tasting"},
    4: {"restaurant","dinner","fine-dining","scenic","sunset","wine","casual"},
    5: {"bar","cocktails","nightlife","lounge","dinner","fine-dining","speakeasy"},
}

def normalize_tag(t):
    return t.lower().replace(" ","-").replace("_","-")

def tags_ok(act, slot_idx):
    required = SLOT_TAGS[slot_idx % 6]
    normalized = {normalize_tag(t) for t in act.get("tags",[])}
    raw_lower = {t.lower() for t in act.get("tags",[])}
    return bool(required & normalized) or bool(required & raw_lower)

def hours_ok(act, slot_idx):
    sw = SLOT_WINDOWS[slot_idx % 6]
    for (o, c) in get_open_windows(act):
        if o < sw[1] and c > sw[0]:
            return True
    return False

BUDGET_ORDER = {"shoestring":0,"mid":1,"premium":2,"luxury":3}

def budget_ok(act, fam_budget):
    return BUDGET_ORDER.get(act["budget_tier"],99) <= BUDGET_ORDER.get(fam_budget,99)

def distance_km(lat1,lng1,lat2,lng2):
    R=6371; dlat=math.radians(lat2-lat1); dlng=math.radians(lng2-lng1)
    a=math.sin(dlat/2)**2+math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R*2*math.atan2(math.sqrt(a),math.sqrt(1-a))

def is_cafe(act):
    return bool({"cafe","coffee","bakery"} & {t.lower() for t in act.get("tags",[])})

def get_use_limit(act):
    return 5 if is_cafe(act) else 3

def resolve_id(aid):
    if aid in bank: return aid
    if aid in ID_ALIAS: return ID_ALIAS[aid]
    return None

def find_replacement(slot_idx, fam_rec, used_counts, prev_act=None, exclude_ids=None):
    if exclude_ids is None: exclude_ids=set()
    fam=fam_rec["family"]
    kid_ages=fam["kid_ages"]; mobility=fam["mobility"]; budget=fam["budget_tier"]
    candidates=[]
    for act in raw_activities:
        aid=act["id"]
        if aid in exclude_ids: continue
        if used_counts.get(aid,0) >= get_use_limit(act): continue
        if kid_ages != "none" and not act["kid_ok"]: continue
        if mobility != "full" and not act["mobility_ok"]: continue
        if not budget_ok(act,budget): continue
        if not tags_ok(act,slot_idx): continue
        if not hours_ok(act,slot_idx): continue
        candidates.append(act)
    if not candidates: return None
    if prev_act:
        candidates.sort(key=lambda a:(distance_km(prev_act["lat"],prev_act["lng"],a["lat"],a["lng"]),a["id"]))
    else:
        candidates.sort(key=lambda a:a["id"])
    return candidates[0]

families=[]
with open("C:/Users/sarta/rosea/hotel_agents/data/families_part_wedding_party_weekend__b0.jsonl",encoding="utf-8") as f:
    for line in f:
        line=line.strip()
        if line: families.append(json.loads(line))

total_slot_replacements=0
total_repetition_fixes=0
no_replacement_found=[]
cleaned_families=[]

for fam_rec in families:
    fam_id=fam_rec["id"]
    itinerary=fam_rec["itinerary"]
    new_itinerary=[]; used_counts=defaultdict(int)
    slot_replacements=0; repetition_fixes=0; prev_act_obj=None

    for i, orig_id in enumerate(itinerary):
        slot_idx=i%6
        act_id=orig_id
        replaced_reason=None

        resolved=resolve_id(act_id)
        if resolved is None:
            replaced_reason="nonexistent-id({})".format(act_id)
            act_id=None
        else:
            act_id=resolved

        if replaced_reason is None:
            act_obj=bank[act_id]
            if not tags_ok(act_obj,slot_idx):
                replaced_reason="tag-mismatch(slot{})".format(slot_idx)
            elif not hours_ok(act_obj,slot_idx):
                replaced_reason="hours-closed(slot{})".format(slot_idx)

        if replaced_reason is None:
            limit=get_use_limit(bank[act_id])
            if used_counts[act_id]>=limit:
                replaced_reason="repetition-cap(limit={})".format(limit)
                repetition_fixes+=1

        if replaced_reason is not None:
            exclude={act_id} if act_id else set()
            repl=find_replacement(slot_idx,fam_rec,used_counts,prev_act=prev_act_obj,exclude_ids=exclude)
            if repl:
                new_id=repl["id"]
                if "repetition-cap" not in replaced_reason:
                    slot_replacements+=1
                    total_slot_replacements+=1
                else:
                    total_repetition_fixes+=1
                used_counts[new_id]+=1
                new_itinerary.append(new_id)
                prev_act_obj=bank[new_id]
                print("  [{}] slot {}(s{}): {} -> {} ({})".format(fam_id,i,slot_idx,orig_id,new_id,replaced_reason))
            else:
                no_replacement_found.append((fam_id,i,orig_id,replaced_reason))
                fallback_id=act_id if (act_id and act_id in bank) else orig_id
                used_counts[fallback_id]+=1
                new_itinerary.append(fallback_id)
                if fallback_id in bank: prev_act_obj=bank[fallback_id]
                print("  [{}] slot {}(s{}): NO REPLACEMENT for {} ({})".format(fam_id,i,slot_idx,orig_id,replaced_reason))
        else:
            used_counts[act_id]+=1
            new_itinerary.append(act_id)
            prev_act_obj=bank[act_id]

    out_rec=copy.deepcopy(fam_rec)
    out_rec["itinerary"]=new_itinerary
    cleaned_families.append(out_rec)
    print("{}: {} slot replacements, {} rep fixes".format(fam_id,slot_replacements,repetition_fixes))

print("\n=== SUMMARY ===")
print("Total slot replacements (tag/hours/nonexistent): {}".format(total_slot_replacements))
print("Total repetition fixes: {}".format(total_repetition_fixes))
print("No replacement found cases: {}".format(no_replacement_found))

out_path="C:/Users/sarta/rosea/hotel_agents/data/families_clean_wedding_party_weekend__b0.jsonl"
with open(out_path,"w",encoding="utf-8") as f:
    for rec in cleaned_families:
        f.write(json.dumps(rec,ensure_ascii=False)+"\n")
print("\nWrote {} records to {}".format(len(cleaned_families),out_path))
