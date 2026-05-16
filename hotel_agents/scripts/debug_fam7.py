import json, re
from collections import defaultdict

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
BUDGET_ORDER = {'shoestring':0,'mid':1,'premium':2,'luxury':3}

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
    families = {json.loads(l)['id']:json.loads(l) for l in f if l.strip()}

fam = families['fam_sports_fan_game_weekend_7']
family = fam['family']
print("fam7 profile:", family)
print("budget:", family['budget_tier'])
print()

# Count usage in cleaned itinerary
used = defaultdict(int)
for aid in fam['itinerary']:
    used[aid] += 1

print("Night slot candidates with ignore_cap for shoestring budget, kid_ages=none:")
for aid, a in acts.items():
    tags = set(a.get('tags',[]))
    if not (tags & SLOT_TAGS[5]): continue
    oh = a.get('open_hours',{}).get('fri','')
    if not hours_overlap(oh, 20, 23): continue
    bt = BUDGET_ORDER.get(a.get('budget_tier','shoestring'),0)
    fb = BUDGET_ORDER.get(family.get('budget_tier','mid'),1)
    if bt > fb: continue
    print("  candidate:", aid, "budget="+a['budget_tier'], "kid_ok="+str(a.get('kid_ok')),
          "used="+str(used.get(aid,0)), "fri="+oh)

print()
print("Current itinerary night slots (type 5) in fam7:")
for i, aid in enumerate(fam['itinerary']):
    if i % 6 == 5:
        print("  slot", i, ":", aid, "used="+str(used.get(aid,0)))
