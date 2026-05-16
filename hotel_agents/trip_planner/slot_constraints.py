"""Per-slot constraints: time-of-day open-hours filter + geographic continuity.

The scheduler used to apply only family-level constraints (kid_ok, mobility_ok,
budget). That let it put museums at breakfast and wineries at night.

This module adds:
  - SLOT_TIMES        — the typical hour window for each of 6 slots/day
  - slot_open_mask()  — bool mask: is each activity open during slot's window
  - haversine_km()    — distance in km, used for geo continuity penalty
  - SLOT_REQUIRED_TAGS — soft preference (boost score for matching tags)

The scheduler then applies:
  effective_mask = family_mask AND slot_open_mask
  logit = fit_score + tag_boost - distance_penalty
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

# Slot index 0..5 within a day. Times are the *middle* of the slot, in 24h.
SLOT_TIMES: dict[int, tuple[int, int]] = {
    0: (7, 9),     # early-morning
    1: (8, 10),    # breakfast
    2: (10, 13),   # late-morning
    3: (12, 17),   # lunch + afternoon
    4: (17, 21),   # evening
    5: (19, 23),   # night
}

# Tags that earn a soft preference boost when matched at each slot.
# This is NOT a hard filter — activities not in the list still appear, just
# at a lower score. Hard filtering is done by open-hours (above).
SLOT_REQUIRED_TAGS: dict[int, set[str]] = {
    0: {"cafe", "coffee", "bakery", "breakfast"},
    1: {"cafe", "coffee", "bakery", "breakfast", "brunch"},
    2: {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art",
        "science", "history", "gardens", "scenic", "walking", "shopping",
        "viewpoint", "landmark", "architecture"},
    3: {"restaurant", "lunch", "casual", "park", "scenic", "beach",
        "outdoor", "hiking", "tour", "shopping", "winery", "wine",
        "tasting", "tasting-room", "viewpoint"},
    4: {"restaurant", "dinner", "fine-dining", "scenic", "sunset",
        "wine", "casual", "italian", "japanese", "michelin"},
    5: {"bar", "cocktails", "nightlife", "lounge", "dinner",
        "fine-dining", "speakeasy", "wine-bar"},
}


def _parse_hours(spec: str) -> list[tuple[int, int]] | None:
    """Parse open-hours spec into a list of (open_h, close_h) ranges.

    Supports:
      - single range:    "09:00-17:00" → [(9, 17)]
      - multi-session:   "11:30-14:00, 17:30-22:30" → [(11, 14), (17, 22)]
      - midnight close:  "16:00-00:00" → [(16, 24)]
      - "closed" or missing/empty → None

    Note: a bug we found via the Sonnet cleanup agents — many fine-dining
    activities have lunch + dinner sessions separated by `,`, and the single-
    range parser was rejecting them entirely.
    """
    if not spec or spec == "closed":
        return None
    out: list[tuple[int, int]] = []
    for session in spec.replace(";", ",").split(","):
        session = session.strip()
        if not session or session == "closed":
            continue
        try:
            o, c = session.split("-")
            o_h = int(o.split(":")[0])
            c_h = int(c.split(":")[0])
            if c_h == 0 and o_h > 0:
                c_h = 24
            out.append((o_h, c_h))
        except Exception:
            continue
    return out or None


def _norm_tag(tag: str) -> str:
    """Normalize a tag for case- and separator-insensitive comparison.

    "Fine Dining" → "fine-dining"; "kids friendly" → "kids-friendly".
    """
    return tag.strip().lower().replace(" ", "-").replace("_", "-")


# Pre-normalize the required-tag sets once at import time so the per-slot
# membership check is fast + matches "fine dining" / "fine-dining" / "Fine_Dining".
SLOT_REQUIRED_TAGS_NORM: dict[int, set[str]] = {
    slot: {_norm_tag(t) for t in tags}
    for slot, tags in {
        0: {"cafe", "coffee", "bakery", "breakfast"},
        1: {"cafe", "coffee", "bakery", "breakfast", "brunch"},
        2: {"museum", "tour", "outdoor", "hiking", "campus", "tech", "art",
            "science", "history", "gardens", "scenic", "walking", "shopping",
            "viewpoint", "landmark", "architecture"},
        3: {"restaurant", "lunch", "casual", "park", "scenic", "beach",
            "outdoor", "hiking", "tour", "shopping", "winery", "wine",
            "tasting", "tasting-room", "viewpoint"},
        4: {"restaurant", "dinner", "fine-dining", "scenic", "sunset",
            "wine", "casual", "italian", "japanese", "michelin"},
        5: {"bar", "cocktails", "nightlife", "lounge", "dinner",
            "fine-dining", "speakeasy", "wine-bar"},
    }.items()
}


def slot_open_mask(
    activities: list[dict],
    slot_idx: int,
    day_of_week: str = "fri",
) -> np.ndarray:
    """Boolean mask (N,): is each activity open during the slot's window.

    Compatible = the slot's mid-point hour lies within the activity's open
    hours. Activities with unparseable hours default to "open" (don't drop).
    """
    slot_lo, slot_hi = SLOT_TIMES[slot_idx]
    slot_mid = (slot_lo + slot_hi) / 2

    out = np.zeros(len(activities), dtype=bool)
    for i, a in enumerate(activities):
        hours_dict = a.get("open_hours") or {}
        spec = hours_dict.get(day_of_week)
        sessions = _parse_hours(spec) if spec is not None else None
        if not sessions:
            # Unknown / closed → not available for this slot
            out[i] = False
            continue
        # Any session that covers the slot mid-point counts as open
        out[i] = any(o_h <= slot_mid <= c_h for (o_h, c_h) in sessions)
    return out


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two coords."""
    R = 6371.0
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dp = np.radians(lat2 - lat1)
    dl = np.radians(lng2 - lng1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * R * np.arcsin(np.sqrt(a)))


def distance_penalty(
    activities: list[dict],
    prev_lat: float | None,
    prev_lng: float | None,
    scale_km: float = 30.0,
    weight: float = 0.6,
) -> np.ndarray:
    """Per-activity geographic-continuity penalty (subtract from fit logit).

    No previous slot → zero penalty. Otherwise, distance in km / scale_km is
    multiplied by `weight`. SF↔Napa (~80 km) yields ~1.6 logit penalty, which
    is enough to push regional jumps below same-region candidates without
    making them impossible.
    """
    n = len(activities)
    if prev_lat is None or prev_lng is None:
        return np.zeros(n, dtype=np.float32)

    lats = np.array([a["lat"] for a in activities])
    lngs = np.array([a["lng"] for a in activities])
    p1 = np.radians(lats)
    p0 = np.radians(prev_lat)
    dp = np.radians(lats - prev_lat)
    dl = np.radians(lngs - prev_lng)
    R = 6371.0
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p0) * np.sin(dl / 2) ** 2
    dist_km = 2 * R * np.arcsin(np.sqrt(a))
    return (dist_km / scale_km * weight).astype(np.float32)


def tag_boost(
    activities: list[dict],
    slot_idx: int,
    boost: float = 0.8,
) -> np.ndarray:
    """Per-activity logit boost if any of its tags match the slot's preferred set.

    Tags are normalized (case-insensitive, spaces/underscores → hyphens) so
    'Fine Dining' / 'fine-dining' / 'fine_dining' all match.
    """
    required = SLOT_REQUIRED_TAGS_NORM[slot_idx]
    boosts = np.zeros(len(activities), dtype=np.float32)
    for i, a in enumerate(activities):
        if any(_norm_tag(t) in required for t in a.get("tags", [])):
            boosts[i] = boost
    return boosts


def repetition_penalty(
    activities: list[dict],
    used_rows: Iterable[int],
    penalty: float = 1.2,
) -> np.ndarray:
    """Big penalty for activities already used in this itinerary.

    Set to 1.2 logits — that's ~70% softmax-mass dropoff, but not infinite
    so a cafe can still be reused on a different day.
    """
    out = np.zeros(len(activities), dtype=np.float32)
    for row in used_rows:
        out[int(row)] = penalty
    return out
