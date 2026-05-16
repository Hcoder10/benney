# hotel_agents

Backend for the **Benney Prism** — multi-agent recommendation framework with
a shared family encoder + synthetic cohort, plus per-agent fit-scorers and
item banks. First agent: 5-day Bay Area trip planner.

See `../PLAN.md` for full architecture (probability-aggregation layer +
Haiku narration + prism display integration).

## Quickstart (demo)

```bash
# 1. (one-time) install Python deps
pip install -r hotel_agents/requirements.txt

# 2. Generate data via Haiku subagent team (one wave at a time)
#    — see docs below; produces ~300 families across 30 archetypes.

# 3. Build derived artifacts
python hotel_agents/scripts/merge_activities.py
python hotel_agents/scripts/merge_families.py
python hotel_agents/scripts/train_family_encoder.py
python hotel_agents/scripts/build_activity_bank.py
python hotel_agents/scripts/train_fit_scorer.py
python hotel_agents/scripts/precompute_itineraries.py

# 4. Start the recommendation server
export ANTHROPIC_API_KEY=sk-ant-...      # for /reasoning Haiku narration
python -m hotel_agents.trip_planner.server
# → http://127.0.0.1:7878

# 5. In the rosea root, start the frontend
npm install && npm run dev
# Open: http://127.0.0.1:5173/?trip=1
```

## Layout

```
hotel_agents/
  shared/
    schema.py          # Family / Activity / Slot types + 15-field vocabs
    storage.py         # canonical data paths
    embedding.py       # sentence-transformers/all-MiniLM-L6-v2 wrapper
    encoder.py         # FamilyEncoder (ST2-style compression model)
  trip_planner/
    fit_scorer.py      # ResNet (family, activity, slot, history) → fit logit
    scheduler.py       # greedy + temperature-sampled chain builder
    population_aggregator.py  # KNN + Jaccard + bootstrap CIs + baseline
    server.py          # FastAPI: /next-slot + /reasoning
  data/
    activities_bay.json      # 190 Bay Area activities
    archetypes.json          # 50 traveler archetypes
    families.jsonl           # synthetic family seeds (Haiku-generated)
    itineraries.jsonl        # raw Haiku itineraries (training labels)
    itineraries_cohort.npz   # scheduler-produced cohort matrix
  checkpoints/
    family_encoder.pt        # ST2-style 15kw → 384-D
    activity_bank.pt         # 190 × 384 (L2-normalized)
    fit_scorer.pt            # ResNet MLP (~270k params)
  scripts/
    merge_activities.py
    merge_families.py        # also writes anchors.jsonl
    write_family_prompts.py  # dispatcher prep
    train_family_encoder.py
    build_activity_bank.py
    train_fit_scorer.py
    precompute_itineraries.py
```

## Phase status

- [x] Phase 0 — scaffolding
- [x] Phase 1 — data pipeline (Haiku subagent team)
  - Activity bank merged from 5 regional shards (Peninsula, South Bay,
    SF, Coast, Wine country): **190 activities**.
  - Archetype set: **50 traveler archetypes**.
  - Family generation: **313 families** across 30 archetypes (wave 1
    + 2 + 3). Pass rate ~70% with fuzzy ID-repair.
- [x] Phase 2 — FamilyEncoder
  - 15 categorical tokens → 384-D vector via 2-layer compression model.
  - Val cosine loss ~0.06, top-3 archetype retrieval 100% on val.
- [x] Phase 3 — FitScorer
  - ResNet MLP (~270k params, dropout 0.3). Best val BCE 0.37,
    val accuracy ~84% on 32k pos/neg pairs.
- [x] Phase 4 — offline itinerary precompute
  - 313 families × 30 slots via temperature-sampled greedy scheduler.
  - Output: `data/itineraries_cohort.npz`.
- [x] Phase 5 — online PopulationAggregator
  - KNN + Jaccard trajectory filter + bootstrap CIs + vs-baseline.
  - Sub-100ms per slot on CPU.
- [x] Phase 6 — Haiku reasoning endpoint
  - `/reasoning` cached in-memory by (activity × archetype-key × slot).
  - Falls through to a live Haiku 4.5 call on cache miss.
  - Set `ANTHROPIC_API_KEY` in env before launching the server.
- [x] Phase 7 — Prism display integration
  - `src/sides/TripPlannerLive.tsx` — probability-bar UI.
  - Query param `?trip=1` switches the React app to the live planner.
- [ ] Phase 8 — Polish + dogfood (deferred)
  - Voice input, multi-day reroll, real demo run-through.

## Haiku subagent team (Phase 1 data gen)

Three wave types, each dispatched as ~10 parallel `claude-haiku-4-5` subagents:

1. **Activity bank** (one-time): 5 regional shards
   (Peninsula / South Bay / SF / Coast+Redwoods / Wine country).
2. **Archetypes** (one-time): single subagent emits 60 traveler archetypes.
3. **Families** (iterative): one subagent per archetype × batch. Each
   produces 10 families with full 5-day itineraries. Files are
   `families_part_<archetype>__b<n>.jsonl`. Use
   `scripts/write_family_prompts.py` to emit per-archetype prompts, then
   dispatch subagents that `Read` each prompt file and `Write` the JSONL.

The merger handles ID typos via fuzzy match (`difflib.get_close_matches` with
0.78 cutoff), recovering ~30% of the dropped families across waves.

## Mitigations (mapped to PLAN.md ## Mitigations)

- **M1** (synthetic noise): bootstrap CIs + vs-baseline pct on every option,
  plus held-out validation against the original Haiku itineraries.
- **M2** (cold UI): the `/reasoning` endpoint provides per-option narration
  on hover. Voice input is planned for Phase 8.
- **M3** (low-pct backfire): `band` field on each option —
  `popular` (≥70%) / `standard` / `niche` (<15%) / `buried` (no signal vs
  baseline). Frontend treats each band differently.
- **M4** (saturated market): demo positioning leads with the
  probability-bar UI, not "AI concierge" framing.
