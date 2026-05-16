"""Parallel regen of all 40 v4 archetype shards via Anthropic API.

Each archetype gets one Sonnet call asking for 15 diverse families. Key
rule: each non-cafe activity must appear in <= 7 of 15 families (no
anchor saturation). Achieves real within-persona diversity.

Runs 40 concurrent API calls via asyncio. Saves to v4_seeds/<archetype>.jsonl
with the same schema the build_v4_cohort script expects.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from anthropic import AsyncAnthropic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

DATA = ROOT / "data"
SEEDS_DIR = DATA / "v4_seeds"
PERSONAS_PATH = DATA / "v4_personas.json"
ACTIVITIES_PATH = DATA / "activities_bay.json"
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

BUDGET_RANK = {"shoestring": 0, "mid": 1, "premium": 2, "luxury": 3}


def build_prompt(archetype: str, persona: dict, bank_str: str) -> str:
    fam = persona["family"]
    guidance = persona["must_include_guidance"]
    return f"""You are generating 15 family itineraries for archetype "{archetype}".

PERSONA FIELDS (use EXACTLY these values for the family object in every record):
{json.dumps(fam, indent=2)}

PERSONA GUIDANCE: {guidance}

CRITICAL: real within-persona DIVERSITY. The 15 families share the persona
but each family is a DIFFERENT sub-type. Their itineraries must look obviously
different from each other. NO single non-cafe activity may appear in more
than 7 of the 15 families (<=47% saturation). Aim for 30+ distinct activities
used across the cohort.

For each family, build a 30-slot itinerary (5 days x 6 slots/day):
  slot 0 (7 AM):  cafe / breakfast
  slot 1 (9 AM):  cafe / bakery / brunch
  slot 2 (11 AM): museum / tour / outdoor / cultural
  slot 3 (2 PM):  lunch / restaurant / scenic
  slot 4 (7 PM):  dinner / restaurant
  slot 5 (9 PM):  bar / dessert / lounge / late activity

HARD CONSTRAINTS (every family, every activity):
- Pick activity_ids ONLY from the activity bank below
- activity.budget_tier rank <= family.budget_tier rank (shoestring<mid<premium<luxury)
- If kid_ages != "none": activity.kid_ok must be true
- If mobility in (limited, wheelchair): activity.mobility_ok must be true
- The activity must be open during its slot per open_hours.fri (use mid-point time)
- Max 4x repetition of any non-cafe activity per family (cafes 5x)
- No SF<->Palo Alto/Stanford ping-pong within a single day

Vary the families by:
- Different day-orderings (some peninsula days first, some SF first)
- Different sub-themes (e.g. for tech-curious: Apple-heavy, Stanford-heavy,
  hardware-tour, AI-VC-tour, art-tech crossover, science-museum-deep-dive,
  South-Bay-focused, etc.)
- Different specific picks for the same slot type

ACTIVITY BANK (id | budget_tier | kid_ok | mobility_ok | tags | open_hours.fri):
{bank_str}

OUTPUT FORMAT: 15 lines of JSONL. Each line:
{{"id":"v4_{archetype}_NNN","family":{{...exact persona fields...}},"activity_ids":["aid1","aid2",...30 items]}}

Output ONLY the 15 JSONL lines, no markdown, no explanation, no code blocks."""


def load_activity_bank_str() -> str:
    """Compact one-line summaries the model can scan quickly."""
    bank = json.load(open(ACTIVITIES_PATH))
    lines = []
    for a in bank:
        tags = ",".join(a.get("tags", [])[:4])
        hours = (a.get("open_hours") or {}).get("fri", "?")
        lines.append(
            f"{a['id']} | {a['budget_tier']} | "
            f"kid_ok={a['kid_ok']} | mob_ok={a['mobility_ok']} | "
            f"{tags} | fri={hours}"
        )
    return "\n".join(lines)


def parse_jsonl(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def validate_cohort(families: list[dict], bank: dict[str, dict],
                    persona_fam: dict) -> tuple[list[dict], dict]:
    """Lenient filter: keep families with <=3 bad slots; substitute bad
    activity IDs with a per-family cafe fallback (sightglass_coffee) so we
    never drop the whole itinerary for one violation.
    """
    has_kids = persona_fam.get("kid_ages", "none") != "none"
    is_limited = persona_fam.get("mobility") in ("limited", "wheelchair")
    fam_budget = BUDGET_RANK.get(persona_fam.get("budget_tier"), -1)
    kept = []
    stats = {"in": len(families), "bad_len": 0, "bad_id": 0,
             "kid_viol": 0, "mob_viol": 0, "budget_viol": 0,
             "slots_substituted": 0, "dropped_too_many_bad": 0}
    fallback = "sightglass_coffee" if "sightglass_coffee" in bank else next(iter(bank))
    for f in families:
        it = f.get("activity_ids") or f.get("itinerary") or []
        if not isinstance(it, list) or len(it) != 30:
            stats["bad_len"] += 1
            continue
        cleaned = []
        bad = 0
        for aid in it:
            a = bank.get(aid)
            why_bad = None
            if a is None: why_bad = "bad_id"
            elif has_kids and not a.get("kid_ok", True): why_bad = "kid_viol"
            elif is_limited and not a.get("mobility_ok", True): why_bad = "mob_viol"
            elif (bt := a.get("budget_tier")) and BUDGET_RANK.get(bt, 1) > fam_budget + 1:
                # Allow ONE tier of overrun — personas explicitly include
                # premium splurges (Bar Crenn, Madera, etc.) even at mid tier.
                why_bad = "budget_viol"
            if why_bad:
                bad += 1
                stats[why_bad] += 1
                cleaned.append(fallback)
            else:
                cleaned.append(aid)
        if bad > 3:
            stats["dropped_too_many_bad"] += 1
            continue
        if bad > 0:
            stats["slots_substituted"] += bad
        # Reassign cleaned itinerary
        f = dict(f)
        if "activity_ids" in f: f["activity_ids"] = cleaned
        else: f["itinerary"] = cleaned
        kept.append(f)
    return kept, stats


def diversity_score(families: list[dict]) -> dict:
    if not families:
        return {"unique": 0, "max_appearance_pct": 0}
    all_picks = []
    for f in families:
        all_picks.extend(f.get("activity_ids") or f.get("itinerary") or [])
    ctr = Counter(all_picks)
    unique = len(ctr)
    n = len(families)
    # most repeated non-cafe across-family
    in_family = Counter()
    for f in families:
        in_family.update(set(f.get("activity_ids") or f.get("itinerary") or []))
    max_pct = max(in_family.values()) / n if n else 0
    return {"unique": unique, "max_appearance_pct": round(max_pct * 100, 1)}


async def regen_one(client: AsyncAnthropic, archetype: str, persona: dict,
                     bank_str: str, bank: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            msg = await client.messages.create(
                model=MODEL,
                max_tokens=16000,
                messages=[{"role": "user", "content": build_prompt(archetype, persona, bank_str)}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            families = parse_jsonl(text)
            kept, stats = validate_cohort(families, bank, persona["family"])
            div = diversity_score(kept)
            out_path = SEEDS_DIR / f"{archetype}.jsonl"
            # CRITICAL: never overwrite a non-empty file with an empty one.
            # Preserves prior good shards if a regen returns no valid families.
            if not kept:
                return {"archetype": archetype, "wrote": 0, "stats": stats,
                        "diversity": div, "elapsed_s": round(time.time() - t0, 1),
                        "kept_existing": out_path.exists() and out_path.stat().st_size > 0}
            tmp = out_path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for fam in kept:
                    f.write(json.dumps(fam) + "\n")
            tmp.replace(out_path)
            return {"archetype": archetype, "wrote": len(kept), "stats": stats,
                    "diversity": div, "elapsed_s": round(time.time() - t0, 1)}
        except Exception as e:
            return {"archetype": archetype, "wrote": 0, "error": str(e),
                    "elapsed_s": round(time.time() - t0, 1)}


async def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not key.startswith("sk-ant-"):
        sys.exit("ANTHROPIC_API_KEY not set (or invalid). Source .env first.")
    client = AsyncAnthropic(api_key=key)
    personas = json.loads(open(PERSONAS_PATH).read())
    bank = {a["id"]: a for a in json.load(open(ACTIVITIES_PATH))}
    bank_str = load_activity_bank_str()
    print(f"model: {MODEL}")
    print(f"archetypes: {len(personas)}")
    SEEDS_DIR.mkdir(exist_ok=True)
    sem = asyncio.Semaphore(40)  # all parallel; Anthropic handles rate
    t0 = time.time()
    tasks = [regen_one(client, arch, p, bank_str, bank, sem)
             for arch, p in personas.items()]
    results = []
    for fut in asyncio.as_completed(tasks):
        r = await fut
        results.append(r)
        if "error" in r:
            print(f"  ERR  {r['archetype']:<35} {r['error'][:80]}  ({r['elapsed_s']}s)")
        else:
            stats = r.get("stats", {})
            stats_s = f"in={stats.get('in',0)} bad_id={stats.get('bad_id',0)} kid={stats.get('kid_viol',0)} mob={stats.get('mob_viol',0)} bud={stats.get('budget_viol',0)} len={stats.get('bad_len',0)}"
            print(f"  OK   {r['archetype']:<35} wrote={r['wrote']:>2}  "
                  f"unique={r['diversity']['unique']:>3}  "
                  f"max%={r['diversity']['max_appearance_pct']:>5.1f}  "
                  f"[{stats_s}]  ({r['elapsed_s']}s)")
    wall = time.time() - t0
    print(f"\nTotal wall: {wall:.0f}s for {len(personas)} archetypes")
    ok = [r for r in results if r.get("wrote", 0) >= 10]
    print(f"OK: {len(ok)}/{len(personas)} (with >=10 valid families)")
    overall_div = sum(r.get("diversity", {}).get("max_appearance_pct", 100) for r in ok) / max(1, len(ok))
    print(f"Mean max-appearance across cohort: {overall_div:.1f}% (target <50%)")


if __name__ == "__main__":
    asyncio.run(main())
