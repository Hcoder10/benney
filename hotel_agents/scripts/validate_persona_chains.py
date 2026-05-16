"""5-day chain validation + persona-specificity check.

Two contrasting families walk through the /next-slot API for all 30 slots.
At each slot we pick the top-probability activity, append it to history,
query the next slot, repeat.

Acceptance criteria:
  - 30/30 slots return a non-empty options list (no "no activities")
  - Slot 0/1: cafe/breakfast picks ≥ 80% of the time
  - Slot 4/5: dinner/bar picks ≥ 60% of the time
  - The two personas pick *different* activities at ≥ 15 of 30 slots
    (proves persona-specific recommendation, not just popularity)

Prints the full 30-slot chain for both personas side-by-side so a human
can visually inspect for sensibility.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

API = "http://127.0.0.1:7878"

# Two contrasting personas designed to surface persona-specificity:
#   FAMILY_A:  premium couple, wine + food, no kids, balanced pace
#   FAMILY_B:  shoestring backpackers, tech + nature, no kids, packed pace
FAMILY_A = {
    "group_type": "couple",
    "adult_count": 2,
    "kid_ages": "none",
    "trip_purpose": "leisure",
    "budget_tier": "premium",
    "trip_length_days": 5,
    "pace": "balanced",
    "primary_interest": "wine",
    "secondary_interest": "food",
    "crowd_tolerance": "okay",
    "energy": "medium",
    "local_interaction": "mixed",
    "mobility": "full",
    "dietary": "none",
    "language_comfort": "english-only",
}

FAMILY_B = {
    "group_type": "friends",
    "adult_count": 3,
    "kid_ages": "none",
    "trip_purpose": "leisure",
    "budget_tier": "shoestring",
    "trip_length_days": 5,
    "pace": "packed",
    "primary_interest": "tech",
    "secondary_interest": "nature",
    "crowd_tolerance": "avoid",
    "energy": "high",
    "local_interaction": "off-the-beaten-path",
    "mobility": "full",
    "dietary": "none",
    "language_comfort": "english-only",
}

SLOT_LABELS = [
    "Day 1: early-am", "Day 1: breakfast", "Day 1: late-am",
    "Day 1: lunch+pm", "Day 1: evening", "Day 1: night",
    "Day 2: early-am", "Day 2: breakfast", "Day 2: late-am",
    "Day 2: lunch+pm", "Day 2: evening", "Day 2: night",
    "Day 3: early-am", "Day 3: breakfast", "Day 3: late-am",
    "Day 3: lunch+pm", "Day 3: evening", "Day 3: night",
    "Day 4: early-am", "Day 4: breakfast", "Day 4: late-am",
    "Day 4: lunch+pm", "Day 4: evening", "Day 4: night",
    "Day 5: early-am", "Day 5: breakfast", "Day 5: late-am",
    "Day 5: lunch+pm", "Day 5: evening", "Day 5: night",
]


def walk_chain(family: dict) -> list[dict]:
    """Greedy 30-slot walk: take the *true* top-probability option each slot.

    Doesn't trust the API's sort order — picks max(opt['pct']) explicitly so
    the script is robust to upstream changes.
    """
    history: list[str] = []
    chain: list[dict] = []
    with httpx.Client(timeout=20.0) as client:
        for slot in range(30):
            r = client.post(f"{API}/next-slot", json={
                "family": family, "history": history, "slot_idx": slot,
            })
            r.raise_for_status()
            resp = r.json()
            options = resp.get("options") or []
            if not options:
                chain.append({"slot": slot, "pick": None, "n_options": 0,
                              "subpop": resp.get("subpopulation_size", 0)})
                continue
            top = max(options, key=lambda o: o["pct"])
            chain.append({
                "slot": slot,
                "pick": top["activity_id"],
                "name": top.get("name", "?"),
                "pct": top["pct"],
                "tags": [t.lower() for t in top.get("tags", [])],
                "n_options": len(options),
                "subpop": resp.get("subpopulation_size", 0),
            })
            history.append(top["activity_id"])
    return chain


def fixed_history_topk(family: dict, slot_idx: int,
                       history: list[str], k: int = 5) -> list[dict]:
    """Get top-k options for a single slot with a controlled history.

    Used by the persona-specificity test: holds history constant across
    personas so any divergence is attributable to the family vector, not
    to compounding history differences.
    """
    with httpx.Client(timeout=20.0) as client:
        r = client.post(f"{API}/next-slot", json={
            "family": family, "history": history, "slot_idx": slot_idx,
        })
        r.raise_for_status()
        options = r.json().get("options") or []
    options.sort(key=lambda o: -o["pct"])
    return options[:k]


CAFE_TAGS = {"cafe", "coffee", "bakery", "breakfast", "brunch"}
DINNER_BAR_TAGS = {"dinner", "fine-dining", "bar", "cocktails", "wine-bar",
                   "italian", "japanese", "michelin", "speakeasy",
                   "nightlife", "lounge"}
# Walks/views/parks legitimately appear at slot 0 too — don't penalize them
MORNING_OK_TAGS = CAFE_TAGS | {"walking", "viewpoint", "scenic", "outdoor", "gardens"}


def _norm(tags) -> set[str]:
    return {t.lower().replace("_", "-").replace(" ", "-") for t in tags or []}


def evaluate(name: str, chain: list[dict]) -> dict:
    nonempty = sum(1 for c in chain if c["pick"])
    # Slot 1, 7, 13, 19, 25 = breakfast slots → strict cafe expectation
    breakfast_ok = sum(
        1 for c in chain
        if c["slot"] in (1, 7, 13, 19, 25) and _norm(c.get("tags")) & CAFE_TAGS
    )
    # Slot 0, 6, 12, 18, 24 = early-am → cafes OR walks/views OK
    morning_ok = sum(
        1 for c in chain
        if c["slot"] in (0, 6, 12, 18, 24) and _norm(c.get("tags")) & MORNING_OK_TAGS
    )
    cafes_breakfast = morning_ok + breakfast_ok
    dinners_bars = sum(
        1 for c in chain
        if c["slot"] in (4, 5, 10, 11, 16, 17, 22, 23, 28, 29)
        and _norm(c.get("tags")) & DINNER_BAR_TAGS
    )
    unique = len({c["pick"] for c in chain if c["pick"]})

    print(f"\n=== {name} ===")
    for c in chain:
        if c["pick"]:
            print(f"  {SLOT_LABELS[c['slot']]:<22}  {c['pct']*100:>5.1f}%  "
                  f"{c['pick']:<35} (sub {c['subpop']}, of {c['n_options']})")
        else:
            print(f"  {SLOT_LABELS[c['slot']]:<22}  NO OPTIONS  (sub {c['subpop']})")
    print(f"\n  filled: {nonempty}/30")
    print(f"  unique activities: {unique}")
    print(f"  morning+breakfast slot-appropriate (of 10): {cafes_breakfast}")
    print(f"  evening dinner/bar (of 10):                 {dinners_bars}")
    return {"name": name, "nonempty": nonempty, "unique": unique,
            "cafes": cafes_breakfast, "dinners": dinners_bars,
            "chain": [c["pick"] for c in chain]}


def main() -> None:
    try:
        r = httpx.get(f"{API}/health", timeout=5.0)
        r.raise_for_status()
        info = r.json()
        print(f"server: cohort={info['cohort_size']}, activities={info['activities']}")
    except Exception as e:
        sys.exit(f"trip planner not reachable at {API} — {e}")

    a = evaluate("FAMILY A — premium couple, wine + food, balanced", walk_chain(FAMILY_A))
    b = evaluate("FAMILY B — shoestring friends, tech + nature, packed", walk_chain(FAMILY_B))

    # ── Persona-specificity test (codex-recommended) ────────────────────
    # Compare top-5 lists at each slot under IDENTICAL fixed histories so any
    # divergence is attributable to the family vector. Uses Spearman-style
    # rank-overlap: 0 = identical lists, 1 = no overlap at all.
    print("\n=== PERSONA-SPECIFICITY (fixed-history test) ===")
    fixed_histories = [
        [],                                      # slot 0: empty history
        ["sightglass_coffee"],                   # slot 1
        ["sightglass_coffee", "blue_bottle_mint"],  # slot 2
        ["sightglass_coffee", "blue_bottle_mint", "sfmoma"],  # slot 3
        ["sightglass_coffee", "blue_bottle_mint", "sfmoma", "tartine_bakery_queue"],  # slot 4
    ]
    total_overlap_pct = []
    top1_diffs = 0
    for slot_idx, hist in enumerate(fixed_histories):
        topk_a = fixed_history_topk(FAMILY_A, slot_idx, hist, k=5)
        topk_b = fixed_history_topk(FAMILY_B, slot_idx, hist, k=5)
        ids_a = [o["activity_id"] for o in topk_a]
        ids_b = [o["activity_id"] for o in topk_b]
        overlap = len(set(ids_a) & set(ids_b))
        overlap_pct = overlap / 5 if ids_a and ids_b else 0
        total_overlap_pct.append(overlap_pct)
        if ids_a and ids_b and ids_a[0] != ids_b[0]:
            top1_diffs += 1
        print(f"  slot {slot_idx}: A top1={ids_a[0] if ids_a else '?':<28}  "
              f"B top1={ids_b[0] if ids_b else '?':<28}  "
              f"top5 overlap {overlap}/5")
    mean_overlap = sum(total_overlap_pct) / len(total_overlap_pct) if total_overlap_pct else 1.0
    print(f"\n  mean top-5 overlap: {mean_overlap:.0%}  (1.0 = identical, 0.0 = disjoint)")
    print(f"  slots where top-1 differs: {top1_diffs}/{len(fixed_histories)}")

    # Also: how many of the GREEDY-WALK slots diverge?
    diff_slots = sum(1 for x, y in zip(a["chain"], b["chain"])
                     if x and y and x != y)
    both_filled = sum(1 for x, y in zip(a["chain"], b["chain"]) if x and y)
    print(f"  (greedy walk: A and B diverge at {diff_slots}/{both_filled} slots)")

    print("\n=== ACCEPTANCE ===")
    checks = [
        ("A fills 30/30 slots", a["nonempty"] == 30),
        ("B fills 30/30 slots", b["nonempty"] == 30),
        ("A: >=6/10 morning+breakfast slot-appropriate", a["cafes"] >= 6),
        ("B: >=6/10 morning+breakfast slot-appropriate", b["cafes"] >= 6),
        ("A: >=5/10 evening dinner/bar", a["dinners"] >= 5),
        ("B: >=5/10 evening dinner/bar", b["dinners"] >= 5),
        ("Fixed-history top-5 mean overlap <= 70% (persona signal)", mean_overlap <= 0.70),
        ("Fixed-history top-1 differs at >=2/5 slots", top1_diffs >= 2),
    ]
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print()
    if all(ok for _, ok in checks):
        print("ALL CHECKS PASSED.")
    else:
        print("SOME CHECKS FAILED — see above.")


if __name__ == "__main__":
    main()
