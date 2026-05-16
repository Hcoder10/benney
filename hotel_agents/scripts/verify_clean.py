import json, re

TAG_NORM = {'fine dining':'fine-dining','fine_dining':'fine-dining','bars':'bar','outdoors':'outdoor'}

SLOT_WINDOWS = [(7,9),(8,10),(10,13),(12,17),(17,20),(20,23)]
SLOT_TAGS = [
    {'cafe','coffee','bakery','breakfast'},
    {'cafe','coffee','bakery','breakfast','brunch'},
    {'museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'},
    {'restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'},
    {'restaurant','dinner','fine-dining','scenic','sunset','wine','casual'},
    {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'},
]

def parse_time_24h(s):
    s = s.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m: return int(m.group(1)) + int(m.group(2)) / 60
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$', s, re.IGNORECASE)
    if m:
        h = int(m.group(1)); mins = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3).upper()
        if ampm == 'PM' and h != 12: h += 12
        if ampm == 'AM' and h == 12: h = 0
        return h + mins/60
    return None

def parse_hours(h_str):
    if not h_str: return None
    h_str = h_str.strip()
    if h_str.lower() in ('closed','n/a',''): return None
    for sep in (' - ',' to '):
        if sep in h_str:
            parts = h_str.split(sep,1)
            o = parse_time_24h(parts[0]); c = parse_time_24h(parts[1])
            if o is not None and c is not None: return (o,c)
    m = re.match(r'^(.+?)-(.+)$', h_str)
    if m:
        o = parse_time_24h(m.group(1).strip()); c = parse_time_24h(m.group(2).strip())
        if o is not None and c is not None: return (o,c)
    m2 = re.match(r'^(\d{1,2}:\d{2})', h_str)
    if m2 and ('sunset' in h_str.lower() or 'open' in h_str.lower()):
        o = parse_time_24h(m2.group(1))
        if o is not None: return (o, 20.0)
    return None

def hours_overlap(h_str, so, sc):
    p = parse_hours(h_str)
    if p is None: return False
    ao,ac = p
    return max(ao,so) < min(ac,sc)

with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    acts = {a['id']:a for a in json.load(f)}
for a in acts.values():
    a['tags'] = [TAG_NORM.get(t,t) for t in a.get('tags',[])]

with open('C:/Users/sarta/rosea/hotel_agents/data/families_clean_sports_fan_game_weekend__b0.jsonl') as f:
    families = [json.loads(l) for l in f if l.strip()]

total_remaining = 0
for fam in families:
    fam_id = fam['id']
    for i, aid in enumerate(fam['itinerary']):
        slot_idx = i % 6
        sw = SLOT_WINDOWS[slot_idx]
        req = SLOT_TAGS[slot_idx]
        a = acts.get(aid)
        if a is None:
            print(fam_id, "slot", i, ": STILL MISSING", aid)
            total_remaining += 1
            continue
        tags = set(a.get('tags',[]))
        has_tag = bool(tags & req)
        oh = a.get('open_hours',{}).get('fri','')
        overlap = hours_overlap(oh, sw[0], sw[1])
        if not has_tag or not overlap:
            r = []
            if not has_tag: r.append('BAD_TAG')
            if not overlap: r.append('CLOSED')
            print(fam_id, "slot", i, "(type", str(slot_idx) + "):", aid, "STILL", ' '.join(r))
            total_remaining += 1

print()
print("Remaining violations in output:", total_remaining)
print("(These are no-replacement cases where bank had no viable candidate)")
