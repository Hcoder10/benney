import json, re

def parse_time_24h(s):
    s = s.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
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
    if h_str.lower() in ('closed', 'n/a', ''): return None
    for sep in (' - ', ' to '):
        if sep in h_str:
            parts = h_str.split(sep, 1)
            o = parse_time_24h(parts[0]); c = parse_time_24h(parts[1])
            if o is not None and c is not None: return (o, c)
    m = re.match(r'^(.+?)-(.+)$', h_str)
    if m:
        o = parse_time_24h(m.group(1).strip()); c = parse_time_24h(m.group(2).strip())
        if o is not None and c is not None: return (o, c)
    m2 = re.match(r'^(\d{1,2}:\d{2})', h_str)
    if m2 and ('sunset' in h_str.lower() or 'open' in h_str.lower()):
        o = parse_time_24h(m2.group(1))
        if o is not None: return (o, 20.0)
    return None

TAG_NORM = {'fine dining':'fine-dining','fine_dining':'fine-dining','bars':'bar','outdoors':'outdoor'}
SLOT_TAGS_5 = {'bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'}
BUDGET_ORDER = {'shoestring':0,'mid':1,'premium':2,'luxury':3}

with open('C:/Users/sarta/rosea/hotel_agents/data/activities_bay.json') as f:
    acts = json.load(f)

# For mid budget, kid_ages=none (kid_ok doesn't matter since no kids)
print("Night slot candidates for mid-or-below budget:")
for a in acts:
    tags = set(TAG_NORM.get(t,t) for t in a.get('tags',[]))
    if not (tags & SLOT_TAGS_5): continue
    oh = a.get('open_hours',{}).get('fri','')
    parsed = parse_hours(oh)
    if not parsed: continue
    if not (max(parsed[0],20) < min(parsed[1],23)): continue
    bt = BUDGET_ORDER.get(a.get('budget_tier','shoestring'),0)
    if bt <= BUDGET_ORDER['mid']:
        aid = a['id']
        budget = a['budget_tier']
        kid_ok = a['kid_ok']
        print("  mid-ok:", aid, "budget="+budget, "kid_ok="+str(kid_ok), "fri="+oh)

print()
# Also check slot 5 (night) for kid families with 0-5 kids (kid_ok must be True)
print("Night slot for kid family (kid_ok=True required), any budget:")
for a in acts:
    tags = set(TAG_NORM.get(t,t) for t in a.get('tags',[]))
    if not (tags & SLOT_TAGS_5): continue
    oh = a.get('open_hours',{}).get('fri','')
    parsed = parse_hours(oh)
    if not parsed: continue
    if not (max(parsed[0],20) < min(parsed[1],23)): continue
    if not a.get('kid_ok', False): continue
    aid = a['id']
    budget = a['budget_tier']
    print("  kid-ok:", aid, "budget="+budget, "fri="+oh)
