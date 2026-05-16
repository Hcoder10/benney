"""FastAPI server — wraps PopulationAggregator + Haiku reasoning into HTTP
endpoints the prism frontend can hit.

Endpoints:
  GET  /health                       liveness
  GET  /activities                   list of activities (for client display)
  POST /next-slot                    { family, history } → per-slot probabilities
  POST /reasoning                    { activity_id, family, slot_idx } → 2-sentence Haiku narration

Run: uvicorn hotel_agents.trip_planner.server:app --host 127.0.0.1 --port 7878
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.encoder import load_encoder  # noqa: E402
from hotel_agents.shared.schema import Family, family_summary  # noqa: E402
from hotel_agents.shared.storage import (  # noqa: E402
    ACTIVITIES_PATH,
    CHECKPOINTS_DIR,
    DATA_DIR,
    read_json,
)
from hotel_agents.trip_planner.population_aggregator import (  # noqa: E402
    Cohort,
    PopulationAggregator,
    ProbabilityOption,
)

# ─────────────────────────────────────────────────────────────────────────────
# App + global state (loaded once at startup)
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Benney Trip Planner")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy globals so the import doesn't block test runs
_state: dict[str, Any] = {}


def _state_get() -> dict[str, Any]:
    if "aggregator" not in _state:
        cohort_path = DATA_DIR / "itineraries_cohort.npz"
        if not cohort_path.exists():
            raise RuntimeError(
                "missing itineraries_cohort.npz — run precompute_itineraries.py first"
            )
        cohort = Cohort.from_npz(cohort_path)
        encoder = load_encoder(CHECKPOINTS_DIR / "family_encoder.pt")
        agg = PopulationAggregator(cohort, encoder)
        activities = {a["id"]: a for a in read_json(ACTIVITIES_PATH)}
        _state["aggregator"] = agg
        _state["activities"] = activities
        _state["reasoning_cache"] = {}  # in-memory; could persist to disk
    return _state


# ─────────────────────────────────────────────────────────────────────────────
# Request/response models
# ─────────────────────────────────────────────────────────────────────────────

class JetlagInput(BaseModel):
    """Optional jet-lag context. Server computes the offset curve from
    origin/destination/timestamps if provided; or you can pass a pre-computed
    offset_h directly (e.g. from a /jetlag call cached client-side).
    """
    origin_iata: str | None = None
    dest_iata: str | None = "SFO"
    departure_iso: str | None = None
    arrival_iso: str | None = None
    # Pre-computed escape hatch (skips Forger99 if provided)
    offset_h: float | None = None
    trip_day: int = 0           # which day of the trip the next slot falls on


class NextSlotRequest(BaseModel):
    family: dict[str, Any] = Field(..., description="Family 15kw object")
    history: list[str] = Field(default_factory=list, description="activity_ids locked so far")
    jetlag: JetlagInput | None = None


class OptionPayload(BaseModel):
    activity_id: str
    name: str
    description: str
    tags: list[str]
    pct: float
    ci_low: float
    ci_high: float
    baseline_pct: float
    n: int
    of: int
    band: str          # popular | standard | niche | buried


class NextSlotResponse(BaseModel):
    slot_idx: int
    subpopulation_size: int
    jaccard_threshold_used: float
    options: list[OptionPayload]


class ReasoningRequest(BaseModel):
    activity_id: str
    family: dict[str, Any]
    slot_idx: int
    pct: float | None = None
    n: int | None = None
    of: int | None = None


class ReasoningResponse(BaseModel):
    activity_id: str
    text: str
    cached: bool


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

class JetlagResponse(BaseModel):
    origin_iata: str
    dest_iata: str
    tz_shift_h: float
    direction: str               # eastward | westward | none
    days_post: list[float]
    offset_h: list[float]
    recovery_day: float | None
    note: str


@app.post("/jetlag", response_model=JetlagResponse)
def jetlag(req: JetlagInput) -> JetlagResponse:
    """Predict the body-clock offset curve for a single flight.

    Used by the UI to render the offset trajectory chip on the prism. Free
    of the activity / family path — pure circadian math.
    """
    if not (req.origin_iata and req.dest_iata
            and req.departure_iso and req.arrival_iso):
        raise HTTPException(400, "origin_iata, dest_iata, departure_iso, arrival_iso required")
    try:
        from .jetlag import JetLagModel
        res = JetLagModel(
            req.origin_iata, req.dest_iata,
            req.departure_iso, req.arrival_iso,
        ).simulate()
    except KeyError as exc:
        raise HTTPException(400, f"unknown IATA: {exc}")
    except Exception as exc:
        raise HTTPException(500, str(exc))

    note = ""
    if abs(res.tz_shift_h) < 0.5:
        note = "No meaningful time-zone change — no jet-lag expected."
    elif res.recovery_day is None:
        note = f"{res.direction.title()} jet-lag; not fully recovered within {int(res.days_post[-1])} days."
    else:
        note = (f"{res.direction.title()} jet-lag; body re-entrains in "
                f"~{res.recovery_day:.1f} days.")

    return JetlagResponse(
        origin_iata=req.origin_iata,
        dest_iata=req.dest_iata,
        tz_shift_h=float(res.tz_shift_h),
        direction=res.direction,
        days_post=[float(d) for d in res.days_post],
        offset_h=[float(o) for o in res.offset_h],
        recovery_day=res.recovery_day,
        note=note,
    )


@app.get("/health")
def health() -> dict[str, Any]:
    s = _state_get()
    return {
        "ok": True,
        "cohort_size": s["aggregator"].cohort.itineraries.shape[0],
        "activities": len(s["activities"]),
        "device": str(s["aggregator"].device),
    }


@app.get("/activities")
def activities() -> list[dict[str, Any]]:
    s = _state_get()
    return list(s["activities"].values())


def _option_payload(opt: ProbabilityOption, activities: dict[str, dict]) -> OptionPayload:
    act = activities.get(opt.activity_id) or {}
    return OptionPayload(
        activity_id=opt.activity_id,
        name=act.get("name", opt.activity_id),
        description=act.get("description", ""),
        tags=act.get("tags", []),
        pct=opt.pct,
        ci_low=opt.ci_low,
        ci_high=opt.ci_high,
        baseline_pct=opt.baseline_pct,
        n=opt.n,
        of=opt.of,
        band=opt.band,
    )


def _resolve_jetlag_offset(req_jetlag: JetlagInput | None) -> float | None:
    """Compute (or pass through) the body-clock offset in hours for the
    guest's current trip day. Returns None if no jet-lag info supplied.
    """
    if req_jetlag is None:
        return None
    if req_jetlag.offset_h is not None:
        return float(req_jetlag.offset_h)
    if not (req_jetlag.origin_iata and req_jetlag.dest_iata
            and req_jetlag.departure_iso and req_jetlag.arrival_iso):
        return None
    try:
        # Lazy import — Forger99 + airportsdata only loaded if needed.
        from .jetlag import JetLagModel
        res = JetLagModel(
            req_jetlag.origin_iata,
            req_jetlag.dest_iata,
            req_jetlag.departure_iso,
            req_jetlag.arrival_iso,
        ).simulate()
        day = max(0, min(int(req_jetlag.trip_day), len(res.offset_h) - 1))
        return float(res.offset_h[day])
    except Exception as exc:
        # Don't fail the whole request if jet-lag inputs are malformed
        print(f"jetlag compute failed: {exc}")
        return None


def _activities_by_row(s: dict[str, Any]) -> dict[int, dict]:
    """Map cohort activity_row → activity metadata dict."""
    cached = s.get("activities_by_row")
    if cached is not None:
        return cached
    cohort_ids = s["aggregator"].cohort.activity_ids
    by_row = {i: s["activities"].get(aid, {}) for i, aid in enumerate(cohort_ids)}
    s["activities_by_row"] = by_row
    return by_row


@app.post("/next-slot", response_model=NextSlotResponse)
def next_slot(req: NextSlotRequest) -> NextSlotResponse:
    s = _state_get()
    offset_h = _resolve_jetlag_offset(req.jetlag)
    try:
        result = s["aggregator"].next_slot_probabilities(
            family=req.family,  # type: ignore[arg-type]
            guest_history=req.history,
            jetlag_offset_h=offset_h,
            activities_by_row=_activities_by_row(s) if offset_h is not None else None,
        )
    except Exception as exc:  # surface vocab/shape errors cleanly
        raise HTTPException(status_code=400, detail=str(exc))
    return NextSlotResponse(
        slot_idx=result.slot_idx,
        subpopulation_size=result.subpopulation_size,
        jaccard_threshold_used=result.jaccard_threshold_used,
        options=[_option_payload(o, s["activities"]) for o in result.options],
    )


@app.post("/reasoning", response_model=ReasoningResponse)
async def reasoning(req: ReasoningRequest) -> ReasoningResponse:
    """Two-sentence Haiku narration of why this activity fits the family.
    Cached on (activity_id, archetype-ish-hash, slot_idx) at first generation.
    """
    s = _state_get()
    cache = s["reasoning_cache"]
    # Compact archetype key — group_type + budget + primary interest is enough
    # signal for caching reasoning across guests with the same shape.
    fam = req.family
    arch_key = (
        f"{fam.get('group_type')}_"
        f"{fam.get('budget_tier')}_"
        f"{fam.get('primary_interest')}_"
        f"{fam.get('pace')}"
    )
    cache_key = (req.activity_id, arch_key, req.slot_idx)
    if cache_key in cache:
        return ReasoningResponse(activity_id=req.activity_id, text=cache[cache_key], cached=True)

    act = s["activities"].get(req.activity_id)
    if not act:
        raise HTTPException(status_code=404, detail=f"unknown activity {req.activity_id}")

    fam_summary = family_summary(fam)  # type: ignore[arg-type]
    pop_stat = ""
    if req.pct is not None and req.of is not None:
        pop_stat = f" {req.pct:.0f}% of comparable families ({req.n} of {req.of}) chose this for this time slot."

    prompt = f"""You're a hotel concierge explaining a trip suggestion to a guest. Be warm, specific, concrete. Two sentences max. No emojis.

Guest profile: {fam_summary}.
Activity: {act['name']} — {act['description']}
Tags: {", ".join(act['tags'])}.{pop_stat}

Explain why this fits THIS guest in particular. Mention one concrete detail about the activity and tie it to one specific trait from their profile."""

    text = await _call_haiku(prompt)
    cache[cache_key] = text
    return ReasoningResponse(activity_id=req.activity_id, text=text, cached=False)


# ─────────────────────────────────────────────────────────────────────────────
# Haiku 4.5 call (thin wrapper around the Anthropic SDK)
# ─────────────────────────────────────────────────────────────────────────────

_haiku_client_lock = asyncio.Lock()
_haiku_client = None


async def _call_haiku(prompt: str) -> str:
    global _haiku_client
    if _haiku_client is None:
        async with _haiku_client_lock:
            if _haiku_client is None:
                from anthropic import AsyncAnthropic
                _haiku_client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    try:
        resp = await _haiku_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        return f"(reasoning unavailable: {exc!s})"


if __name__ == "__main__":
    # Quick local run for sanity: `python -m hotel_agents.trip_planner.server`
    import uvicorn
    uvicorn.run(
        "hotel_agents.trip_planner.server:app",
        host="127.0.0.1",
        port=7878,
        reload=False,
    )
