"""Shared types + constants for the Benney hotel-agent framework.

All agents (trip planner, restaurant picker, room service, concierge chat)
share the family encoder + 50k cohort defined here. Per-agent banks
(activities, restaurants, menus) plug into the same `Bank` protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal, TypedDict

# ─────────────────────────────────────────────────────────────────────────────
# Family — 15-keyword profile assembled across signup, room cost, guest, staff.
# Field order is load-bearing: the FamilyEncoder tokenises in this order.
# ─────────────────────────────────────────────────────────────────────────────

GroupType = Literal["solo", "couple", "family", "friends", "business", "event"]
KidAges = Literal["none", "0-5", "6-12", "13-17", "mixed"]
TripPurpose = Literal["leisure", "business", "mixed", "event", "honeymoon"]
BudgetTier = Literal["shoestring", "mid", "premium", "luxury"]
Pace = Literal["relaxed", "balanced", "packed"]
Interest = Literal["food", "culture", "nature", "adventure", "tech", "wine", "shopping"]
CrowdTolerance = Literal["avoid", "okay", "love"]
Energy = Literal["low", "medium", "high"]
LocalInteraction = Literal["touristy", "mixed", "off-the-beaten-path"]
Mobility = Literal["full", "limited", "wheelchair"]
Dietary = Literal["none", "veg", "vegan", "kosher", "halal", "gf", "nut-free", "other"]
LanguageComfort = Literal["english-only", "english-plus", "non-english"]


class Family(TypedDict):
    """The 15-keyword profile. ID is stable across cohort regens."""
    id: str
    # Signup (set on booking)
    group_type: GroupType
    adult_count: int            # 1..4+
    kid_ages: KidAges
    trip_purpose: TripPurpose
    # Room cost (interpolated)
    budget_tier: BudgetTier
    # Booking
    trip_length_days: int       # 1..7+
    # Guest dropdown (filled on prism)
    pace: Pace
    primary_interest: Interest
    secondary_interest: Interest
    crowd_tolerance: CrowdTolerance
    energy: Energy
    local_interaction: LocalInteraction
    # Staff (filled on arrival)
    mobility: Mobility
    dietary: Dietary
    language_comfort: LanguageComfort


# Ordered list of field names, matches FamilyEncoder token order.
FAMILY_FIELDS: tuple[str, ...] = (
    "group_type", "adult_count", "kid_ages", "trip_purpose",
    "budget_tier", "trip_length_days",
    "pace", "primary_interest", "secondary_interest",
    "crowd_tolerance", "energy", "local_interaction",
    "mobility", "dietary", "language_comfort",
)
assert len(FAMILY_FIELDS) == 15, "must be exactly 15 keywords"


def family_to_tokens(family: Family) -> list[str]:
    """Serialise a Family into the 15 string tokens fed to the encoder."""
    return [f"{field}={family[field]}" for field in FAMILY_FIELDS]  # type: ignore[literal-required]


def family_summary(family: Family) -> str:
    """Human-readable summary used in Haiku prompts."""
    parts = [
        f"{family['group_type']}",
        f"adults={family['adult_count']}",
    ]
    if family["kid_ages"] != "none":
        parts.append(f"kids={family['kid_ages']}")
    parts += [
        f"purpose={family['trip_purpose']}",
        f"budget={family['budget_tier']}",
        f"pace={family['pace']}",
        f"loves {family['primary_interest']}+{family['secondary_interest']}",
        f"energy={family['energy']}",
        f"crowds={family['crowd_tolerance']}",
        f"style={family['local_interaction']}",
    ]
    if family["mobility"] != "full":
        parts.append(f"mobility={family['mobility']}")
    if family["dietary"] != "none":
        parts.append(f"dietary={family['dietary']}")
    return " · ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Activity — one row in the Trip Planner's activity bank.
# ─────────────────────────────────────────────────────────────────────────────

IndoorOutdoor = Literal["indoor", "outdoor", "mixed"]
ActivityEnergy = Literal["low", "medium", "high"]


class Activity(TypedDict):
    id: str
    name: str
    description: str            # 1-2 sentences, used for embedding
    lat: float
    lng: float
    address: str
    open_hours: dict[str, str]  # {"mon": "09:00-17:00", ...}
    duration_min: int
    budget_tier: BudgetTier
    energy: ActivityEnergy
    indoor_outdoor: IndoorOutdoor
    kid_ok: bool
    mobility_ok: bool
    tags: list[str]             # ["museum", "tech", "campus", ...]
    photo_url: str | None


ACTIVITY_BUDGET_RANK = {"shoestring": 0, "mid": 1, "premium": 2, "luxury": 3}


def activity_compatible_with_budget(activity: Activity, family_budget: BudgetTier) -> bool:
    """True if the activity is at or below the family's budget tier."""
    return ACTIVITY_BUDGET_RANK[activity["budget_tier"]] <= ACTIVITY_BUDGET_RANK[family_budget]


# ─────────────────────────────────────────────────────────────────────────────
# Itinerary — chain of (slot, activity_id) over a 5-day trip.
# 30 slots: 5 days × 6 events (early-morning, breakfast, late-morning,
# lunch+afternoon, evening, night).
# ─────────────────────────────────────────────────────────────────────────────

SlotName = Literal[
    "early-morning",
    "breakfast",
    "late-morning",
    "lunch-afternoon",
    "evening",
    "night",
]

SLOT_NAMES: tuple[SlotName, ...] = (
    "early-morning",
    "breakfast",
    "late-morning",
    "lunch-afternoon",
    "evening",
    "night",
)

SLOTS_PER_DAY = len(SLOT_NAMES)  # 6
DEFAULT_TRIP_DAYS = 5
TOTAL_SLOTS = SLOTS_PER_DAY * DEFAULT_TRIP_DAYS  # 30


@dataclass(frozen=True)
class Slot:
    day: int          # 1..N
    slot: SlotName
    index: int        # 0..29 for a 5-day trip

    @classmethod
    def all_slots(cls, trip_days: int = DEFAULT_TRIP_DAYS) -> list["Slot"]:
        return [
            cls(day=d + 1, slot=s, index=d * SLOTS_PER_DAY + i)
            for d in range(trip_days)
            for i, s in enumerate(SLOT_NAMES)
        ]


class ItineraryEvent(TypedDict):
    slot_index: int             # 0..29
    activity_id: str            # references Activity.id


class Itinerary(TypedDict):
    family_id: str
    events: list[ItineraryEvent]   # length = TOTAL_SLOTS, in slot order


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def slot_label(slot: Slot) -> str:
    """Human-readable slot label for prompts + UI."""
    return f"Day {slot.day} {slot.slot.replace('-', ' ')}"
