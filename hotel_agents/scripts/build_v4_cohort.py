"""Build cohort_v4.npz from the 40 Sonnet-generated persona shards.

Pipeline:
  1. Read every data/v4_seeds/*.jsonl shard.
  2. Validate each family:
       - 30 activity_ids
       - All IDs in activity bank
       - Hard constraints: kid_ok / mobility_ok / budget_tier rank
  3. Encode each family's 15kw via the FamilyEncoder.
  4. Optionally noise-augment to grow the cohort (default replicate=10).
  5. Save itineraries_cohort_v4.npz with same schema as the v1 cohort.

Why no scheduler augment: the agents already produced persona-deep itineraries
with the must_include guidance — running them through the FitScorer scheduler
would *replace* those persona-specific picks with the model's preferred picks
(which is what gave us the SFMOMA-everywhere bias in v1). The agents' picks
ARE the supervision; we just encode them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.encoder import family_to_indices, load_encoder  # noqa: E402
from hotel_agents.shared.schema import FAMILY_FIELDS, TOTAL_SLOTS  # noqa: E402
from hotel_agents.shared.storage import (  # noqa: E402
    ACTIVITIES_PATH, CHECKPOINTS_DIR, DATA_DIR, read_json,
)

V4_SEEDS_DIR = DATA_DIR / "v4_seeds"
V4_COHORT_PATH = DATA_DIR / "itineraries_cohort_v4.npz"
BUDGET_RANK = {"shoestring": 0, "mid": 1, "premium": 2, "luxury": 3}


def validate(family, activity_ids: list[str], bank: dict[str, dict]) -> str | None:
    """Return None if valid, or error string."""
    if isinstance(family, str):
        try:
            family = json.loads(family)
        except Exception:
            return "family field is a non-JSON string"
    if not isinstance(family, dict):
        return f"family must be a dict (got {type(family).__name__})"
    if not isinstance(activity_ids, list) or len(activity_ids) != TOTAL_SLOTS:
        return f"need {TOTAL_SLOTS} activity_ids, got {len(activity_ids) if isinstance(activity_ids, list) else 'non-list'}"
    has_kids = family.get("kid_ages", "none") != "none"
    is_limited = family.get("mobility") in ("limited", "wheelchair")
    fam_budget = BUDGET_RANK.get(family.get("budget_tier"), -1)
    if fam_budget < 0:
        return f"bad budget_tier {family.get('budget_tier')!r}"
    for aid in activity_ids:
        a = bank.get(aid)
        if a is None:
            return f"unknown activity_id {aid!r}"
        if has_kids and not a.get("kid_ok", True):
            return f"{aid} not kid_ok for family with kids"
        if is_limited and not a.get("mobility_ok", True):
            return f"{aid} not mobility_ok for limited-mobility family"
        bt = a.get("budget_tier")
        # Allow ONE tier of overrun (e.g. mid family with premium splurge —
        # the personas explicitly include premium spots like Bar Crenn).
        if bt and BUDGET_RANK.get(bt, 1) > fam_budget + 1:
            return f"{aid} budget {bt} exceeds family {family['budget_tier']}"
    # Ensure 15-keyword fields are present
    for f in FAMILY_FIELDS:
        if f not in family:
            return f"family missing field {f!r}"
    return None


def perturb(family: dict, rng: np.random.Generator, p: float = 0.10) -> dict:
    """Light per-field noise for replication. Far less aggressive than v1's
    0.15 since our seeds are already persona-tight — we just want a bit of
    coverage variation without breaking the archetype."""
    from hotel_agents.shared.encoder import FIELD_VOCABS
    LOCKED = {"trip_length_days", "group_type", "primary_interest",
              "secondary_interest", "budget_tier", "mobility", "dietary"}
    out = dict(family)
    for f in FAMILY_FIELDS:
        if f in LOCKED:
            continue
        if rng.random() < p:
            vocab = FIELD_VOCABS[f]
            current = str(out[f])
            choices = [v for v in vocab if v != current]
            if not choices:
                continue
            new_val = rng.choice(choices)
            if f == "adult_count":
                out[f] = 4 if new_val == "4+" else int(new_val)
            elif f == "trip_length_days":
                out[f] = 7 if new_val == "7+" else int(new_val)
            else:
                out[f] = new_val
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replicate", type=int, default=10,
                        help="noise-replicate each seed N times (0 = no augmentation)")
    parser.add_argument("--noise-p", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default=str(V4_COHORT_PATH))
    parser.add_argument("--include-clean", action="store_true",
                        help="also include the original cleaned families")
    args = parser.parse_args()

    activities = read_json(ACTIVITIES_PATH)
    bank = {a["id"]: a for a in activities}
    bank_ids = [a["id"] for a in activities]
    id_to_row = {aid: i for i, aid in enumerate(bank_ids)}
    print(f"activity bank: {len(activities)}")

    shards = sorted(V4_SEEDS_DIR.glob("*.jsonl"))
    print(f"found {len(shards)} v4 shards in {V4_SEEDS_DIR}")

    families: list[dict] = []
    itineraries: list[list[str]] = []
    family_ids: list[str] = []
    seed_archetype: list[str] = []
    stats = {"total": 0, "ok": 0, "drop_bad_family": 0,
             "drop_bad_itinerary": 0, "drop_unknown_id": 0,
             "drop_kid_viol": 0, "drop_mob_viol": 0, "drop_budget_viol": 0}

    for shard in shards:
        archetype = shard.stem
        with shard.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                stats["total"] += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    stats["drop_bad_itinerary"] += 1
                    continue
                fam = rec.get("family", {})
                if isinstance(fam, str):
                    try: fam = json.loads(fam)
                    except Exception: pass
                it = rec.get("activity_ids") or rec.get("itinerary") or []
                err = validate(fam, it, bank)
                if err:
                    if "unknown" in err: stats["drop_unknown_id"] += 1
                    elif "kid_ok" in err: stats["drop_kid_viol"] += 1
                    elif "mobility_ok" in err: stats["drop_mob_viol"] += 1
                    elif "budget" in err: stats["drop_budget_viol"] += 1
                    else: stats["drop_bad_family"] += 1
                    continue
                fid = rec.get("id") or f"{archetype}_{stats['ok']:03d}"
                families.append(fam)
                itineraries.append(it)
                family_ids.append(fid)
                seed_archetype.append(archetype)
                stats["ok"] += 1

    print(f"\n=== seed merge stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if stats["ok"] == 0:
        sys.exit("no valid families — abort")

    # Optionally include the existing cleaned families
    if args.include_clean:
        from hotel_agents.shared.storage import FAMILIES_PATH, ITINERARIES_PATH, read_jsonl
        fam_by_id = {f["id"]: f for f in read_jsonl(FAMILIES_PATH)}
        for it_rec in read_jsonl(ITINERARIES_PATH):
            fid = it_rec["family_id"]
            if fid not in fam_by_id:
                continue
            fam = {k: v for k, v in fam_by_id[fid].items() if k != "id"}
            if validate(fam, it_rec["activity_ids"], bank) is None:
                families.append(fam)
                itineraries.append(it_rec["activity_ids"])
                family_ids.append("clean_" + fid)
                seed_archetype.append("v1_clean")
        print(f"  added clean v1 families: now {len(families)} total")

    # Augment with light noise
    rng = np.random.default_rng(args.seed)
    aug_families = list(families)
    aug_itineraries = list(itineraries)
    aug_ids = list(family_ids)
    aug_arch = list(seed_archetype)
    for i, (fam, it, fid, arch) in enumerate(zip(families, itineraries, family_ids, seed_archetype)):
        for k in range(args.replicate):
            aug_families.append(perturb(fam, rng, args.noise_p))
            aug_itineraries.append(it)        # keep itinerary, vary family attrs
            aug_ids.append(f"{fid}_aug{k:03d}")
            aug_arch.append(arch)
    print(f"after replicate={args.replicate} noise={args.noise_p}: {len(aug_families)} families")

    # Encode
    device = "cuda" if torch.cuda.is_available() else "cpu"
    encoder = load_encoder(CHECKPOINTS_DIR / "family_encoder.pt", device=device)
    encoder.eval()
    indices: list[list[int]] = []
    keep: list[int] = []
    for i, fam in enumerate(aug_families):
        try:
            indices.append(family_to_indices(fam))
            keep.append(i)
        except ValueError as e:
            print(f"  skip aug[{i}] {aug_ids[i]}: {e}")
    if not indices:
        sys.exit("no encodable families")
    idx_tensor = torch.tensor(indices, dtype=torch.long, device=device)
    with torch.no_grad():
        fam_vecs = encoder(idx_tensor)
    print(f"encoded {fam_vecs.size(0)} family vectors")

    # Build itinerary row matrix
    itin_rows = np.zeros((len(keep), TOTAL_SLOTS), dtype=np.int32)
    final_ids = []
    final_arch = []
    for new_i, src_i in enumerate(keep):
        itin_rows[new_i] = [id_to_row[aid] for aid in aug_itineraries[src_i]]
        final_ids.append(aug_ids[src_i])
        final_arch.append(aug_arch[src_i])

    np.savez(
        args.out,
        itineraries=itin_rows,
        family_ids=np.array(final_ids),
        activity_ids=np.array(bank_ids),
        family_vecs=fam_vecs.cpu().numpy(),
        archetypes=np.array(final_arch),
    )
    print(f"\nsaved {len(keep)} families x 30 slots -> {args.out}")

    # Quick stats: per-archetype count
    from collections import Counter
    arch_counts = Counter(final_arch)
    print(f"\nper-archetype:")
    for arch, n in sorted(arch_counts.items()):
        print(f"  {arch:<35} {n:>5}")

    # Slot-0 spread
    s0 = Counter(itin_rows[:, 0].tolist())
    print(f"\nslot-0 unique picks: {len(s0)} (vs 9 in v1)")
    for row, count in s0.most_common(10):
        print(f"  {count:>5} ({count/len(keep):>5.1%}) {bank_ids[row]}")


if __name__ == "__main__":
    main()
