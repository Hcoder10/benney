"""Dump per-archetype family-generation prompts to disk so the
orchestrating Claude session can dispatch a Haiku subagent per file
without re-embedding the 6k-token prompt body in each Agent call.

Output: hotel_agents/data/prompts/fam_<archetype_id>__<batch_n>.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.scripts.family_gen_prompt import (  # noqa: E402
    build_family_batch_prompt,
    load_activity_ids,
    load_archetypes,
)

PROMPTS_DIR = ROOT / "data" / "prompts"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archetypes", type=int, default=10,
                        help="how many archetypes to dispatch in this wave")
    parser.add_argument("--batches", type=int, default=1,
                        help="how many batches per archetype")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="families per batch")
    parser.add_argument("--archetype-offset", type=int, default=0,
                        help="start from archetypes[offset:]")
    args = parser.parse_args()

    activities = load_activity_ids()
    archetypes = load_archetypes()
    print(f"loaded {len(activities)} activities, {len(archetypes)} archetypes")

    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    # Clear stale prompts so each wave is reproducible
    for old in PROMPTS_DIR.glob("fam_*.txt"):
        old.unlink()

    selected = archetypes[args.archetype_offset:args.archetype_offset + args.archetypes]
    written = []
    for ai, arch in enumerate(selected):
        for bi in range(args.batches):
            seed = 1000 * (args.archetype_offset + ai) + bi
            batch_label = f"{arch['id']}__b{bi}"
            prompt = build_family_batch_prompt(
                archetype=arch,
                activities=activities,
                batch_size=args.batch_size,
                batch_label=batch_label,
                seed=seed,
            )
            path = PROMPTS_DIR / f"fam_{batch_label}.txt"
            path.write_text(prompt, encoding="utf-8")
            written.append((str(path), arch["id"], bi))

    print(f"wrote {len(written)} prompts to {PROMPTS_DIR}")
    for p, aid, bi in written:
        print(f"  {p}  arch={aid} batch={bi}")
    print()
    print(f"total expected families if all succeed: {len(written) * args.batch_size}")


if __name__ == "__main__":
    main()
