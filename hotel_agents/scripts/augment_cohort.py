"""Augment the Haiku-generated seed families into a large synthetic cohort
via structured keyword noise, then run the scheduler on each augmented
family to produce its 30-slot itinerary.

Pipeline:
  seed_families.jsonl  →  (15kw structured)
       │
       │  For each seed × replicate_factor:
       │    for each of 15 fields, with probability `noise_p` (default 0.15),
       │    swap the value for a uniformly-sampled vocab member.
       │    (Lock trip_length_days at 5 to keep the slot count stable.)
       │
       ▼
  augmented_families (in memory)  →  FamilyEncoder  →  family_vec (N, 384)
       │                              │
       │                              ▼
       │                          schedule_population (FitScorer + greedy + T=0.6)
       │                              │
       ▼                              ▼
  augmented_cohort.npz  ←  itineraries (N, 30) + family_vecs + family_ids

Default 160× → 313 seeds × 160 = 50,080 augmented families.

Compute: on CPU ~30s per 100 families through the scheduler. For 50k,
plan on running on Vast Blackwell (~5 min) — see PLAN.md for the bootstrap.
Use --replicate small (e.g. 5) for a quick CPU check.
"""

from __future__ import annotations

import argparse
import sys
from copy import copy
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.encoder import (  # noqa: E402
    FIELD_VOCABS,
    family_to_indices,
    load_encoder,
)
from hotel_agents.shared.schema import FAMILY_FIELDS, Family  # noqa: E402
from hotel_agents.shared.storage import (  # noqa: E402
    ACTIVITIES_PATH,
    CHECKPOINTS_DIR,
    DATA_DIR,
    FAMILIES_PATH,
    read_json,
    read_jsonl,
)
from hotel_agents.trip_planner.fit_scorer import load_fit_scorer  # noqa: E402
from hotel_agents.trip_planner.scheduler import (  # noqa: E402
    ScheduleConfig,
    candidate_mask,
    schedule_population,
)


# Fields we always preserve from the seed (mutating these changes the
# structure of the family in ways the FitScorer can handle, but for a
# 5-day demo we want the trip length and group type stable).
LOCKED_FIELDS = {"trip_length_days", "group_type"}


def perturb_family(seed: Family, noise_p: float, rng: np.random.Generator) -> Family:
    """Return a copy of seed with each non-locked field flipped to a random
    vocab member with probability noise_p.
    """
    out: dict = dict(seed)
    for field in FAMILY_FIELDS:
        if field in LOCKED_FIELDS:
            continue
        if rng.random() < noise_p:
            vocab = FIELD_VOCABS[field]
            # Resample until we pick something different (rarely loops more than once)
            current = str(out[field])
            choices = [v for v in vocab if v != current]
            if choices:
                new_val = rng.choice(choices)
                if field in ("adult_count",):
                    # Vocab is ('1','2','3','4+'); coerce to numeric for downstream
                    out[field] = 4 if new_val == "4+" else int(new_val)
                elif field == "trip_length_days":
                    out[field] = 7 if new_val == "7+" else int(new_val)
                else:
                    out[field] = new_val
    return out  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replicate", type=int, default=160,
                        help="how many noisy variants per seed family")
    parser.add_argument("--noise-p", type=float, default=0.15,
                        help="per-field perturbation probability")
    parser.add_argument("--keep-seeds", action="store_true",
                        help="also include the original seed families verbatim")
    parser.add_argument("--temperature", type=float, default=0.6,
                        help="scheduler softmax temperature (for population spread)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str,
                        default=str(DATA_DIR / "itineraries_cohort.npz"))
    parser.add_argument("--batch", type=int, default=2000,
                        help="schedule this many families at a time to manage memory")
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device}")

    encoder = load_encoder(CHECKPOINTS_DIR / "family_encoder.pt", device=device)
    fit_scorer = load_fit_scorer(CHECKPOINTS_DIR / "fit_scorer.pt", device=device)
    bank = torch.load(CHECKPOINTS_DIR / "activity_bank.pt",
                      map_location=device, weights_only=False)
    activity_vecs: torch.Tensor = bank["vectors"].to(device)
    activity_ids: list[str] = bank["ids"]

    activities = read_json(ACTIVITIES_PATH)
    id_to_act = {a["id"]: a for a in activities}
    activities_in_bank_order = [id_to_act[aid] for aid in activity_ids]

    seeds_raw = read_jsonl(FAMILIES_PATH)
    print(f"loaded {len(seeds_raw)} seed families")
    if not seeds_raw:
        sys.exit("no seeds — generate via Haiku first")

    # Strip the id key so we can re-key the augmented copies
    seeds: list[Family] = []
    for s in seeds_raw:
        seeds.append({k: v for k, v in s.items() if k != "id"})  # type: ignore[arg-type]

    rng = np.random.default_rng(args.seed)

    # Build the full augmented family set
    augmented: list[Family] = []
    augmented_ids: list[str] = []
    if args.keep_seeds:
        for i, s in enumerate(seeds):
            augmented.append(s)
            augmented_ids.append(f"seed_{i:05d}")
    for i, s in enumerate(seeds):
        for k in range(args.replicate):
            augmented.append(perturb_family(s, args.noise_p, rng))
            augmented_ids.append(f"aug_{i:05d}_{k:04d}")
    print(f"augmented total: {len(augmented)} families "
          f"(seeds={len(seeds)}, replicate={args.replicate}, "
          f"keep_seeds={args.keep_seeds}, noise_p={args.noise_p})")

    # Encode all augmented families
    fam_indices: list[list[int]] = []
    masks_list: list[np.ndarray] = []
    valid_aug_idx: list[int] = []
    for i, fam in enumerate(augmented):
        try:
            fam_indices.append(family_to_indices(fam))
        except ValueError as e:
            print(f"  skip aug[{i}]: {e}")
            continue
        masks_list.append(candidate_mask(activities_in_bank_order, fam))
        valid_aug_idx.append(i)

    idx_tensor = torch.tensor(fam_indices, dtype=torch.long, device=device)
    with torch.no_grad():
        family_vecs = encoder(idx_tensor)
    masks_arr = np.stack(masks_list)
    print(f"encoded {family_vecs.size(0)} family vectors, "
          f"avg candidates/family: {masks_arr.sum(axis=1).mean():.0f}")

    # Schedule in batches to keep GPU memory bounded
    cfg = ScheduleConfig(temperature=args.temperature, top_k=10)
    n = family_vecs.size(0)
    itineraries = np.zeros((n, 30), dtype=np.int32)
    for start in range(0, n, args.batch):
        end = min(start + args.batch, n)
        chunk = schedule_population(
            fit_scorer=fit_scorer,
            family_vecs=family_vecs[start:end],
            candidate_masks=masks_arr[start:end],
            activity_vecs=activity_vecs,
            activities=activities_in_bank_order,
            cfg=cfg,
            seed=args.seed + start,
            device=device,
        )
        itineraries[start:end] = chunk
        print(f"  scheduled {end}/{n}")

    final_ids = [augmented_ids[i] for i in valid_aug_idx]
    out_path = Path(args.out)
    np.savez(
        out_path,
        itineraries=itineraries,
        family_ids=np.array(final_ids),
        activity_ids=np.array(activity_ids),
        family_vecs=family_vecs.cpu().numpy(),
    )
    print(f"\nsaved {n} families × 30 slots → {out_path}")

    # Sanity: slot-0 distribution should now spread across more activities
    from collections import Counter
    slot0 = Counter(itineraries[:, 0].tolist())
    print(f"\nslot-0 picks (top 8 of {len(slot0)} unique):")
    for row, count in slot0.most_common(8):
        print(f"  {count:>5}× ({count / n:>5.1%})  {activity_ids[row]}")


if __name__ == "__main__":
    main()
