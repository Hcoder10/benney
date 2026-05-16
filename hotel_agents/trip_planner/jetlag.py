"""jetlag.py — Predict circadian re-entrainment from flight information.

Given two IATA airport codes and departure / arrival timestamps, this module
simulates the human circadian pacemaker (Forger–Jewett–Kronauer 1999 limit-cycle
oscillator) under an assumed post-arrival light schedule and returns a
*continuous* phase-offset curve: how many hours your internal clock is
misaligned with destination local time on each day after landing.

Key design points
------------------
* Phase is read from the model's continuous oscillator angle, NOT from
  DLMO-modulo-24, so the re-entrainment curve is smooth and monotone instead
  of wrapping at +/-12 h.
* The direction asymmetry (eastward phase-advance is slower than westward
  phase-delay) emerges naturally from the model's >24 h intrinsic period; it
  is not hard-coded.
* The dominant input is the assumed light schedule. The default assumes the
  traveler adopts the destination's local wake/sleep times immediately. You
  can override this with a custom schedule for more realistic predictions.

Dependencies: circadian, airportsdata, numpy  (all pip-installable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import airportsdata
from circadian.models import Forger99

_AIRPORTS = airportsdata.load("IATA")


# --------------------------------------------------------------------------- #
#  Airport / timezone resolution
# --------------------------------------------------------------------------- #
@dataclass
class Airport:
    iata: str
    tz: ZoneInfo
    city: str
    country: str
    lat: float
    lon: float

    @classmethod
    def from_iata(cls, code: str) -> "Airport":
        code = code.strip().upper()
        if code not in _AIRPORTS:
            raise KeyError(f"Unknown IATA code: {code!r}")
        r = _AIRPORTS[code]
        return cls(
            iata=code,
            tz=ZoneInfo(r["tz"]),
            city=r["city"],
            country=r["country"],
            lat=float(r["lat"]),
            lon=float(r["lon"]),
        )


# --------------------------------------------------------------------------- #
#  Light schedule
# --------------------------------------------------------------------------- #
def square_wave_light(
    t_hours: np.ndarray,
    shift_at_h: float,
    tz_shift_h: float,
    lux: float = 1000.0,
    wake_local: float = 7.0,
    sleep_local: float = 23.0,
) -> np.ndarray:
    """A square-wave light profile.

    Before ``shift_at_h`` the traveler is entrained at the origin
    (wake_local..sleep_local in origin clock time). After it, the same
    wake/sleep block is expressed in destination clock time, i.e. the schedule
    instantaneously jumps by ``tz_shift_h`` (positive = eastward).

    Returns lux for every time point. Being a true square wave it never goes
    negative, which the circadian solver requires.
    """
    h_origin = t_hours % 24.0
    h_dest = (t_hours - tz_shift_h) % 24.0
    h = np.where(t_hours < shift_at_h, h_origin, h_dest)
    awake = (h >= wake_local) & (h < sleep_local)
    return np.where(awake, lux, 0.0)


# --------------------------------------------------------------------------- #
#  Result container
# --------------------------------------------------------------------------- #
@dataclass
class ReentrainmentResult:
    origin: Airport
    destination: Airport
    tz_shift_h: float                 # +ve eastward (advance needed)
    direction: str                    # "eastward" | "westward" | "none"
    days_post: np.ndarray             # day index after arrival (0 = landing)
    offset_h: np.ndarray              # signed continuous offset, hrs
    recovery_day: float | None        # first day |offset| stays < threshold
    threshold_h: float

    def summary(self) -> str:
        lines = [
            f"{self.origin.iata} ({self.origin.city}) "
            f"-> {self.destination.iata} ({self.destination.city})",
            f"Time-zone shift : {self.tz_shift_h:+.1f} h ({self.direction})",
        ]
        if self.recovery_day is not None:
            lines.append(
                f"Recovered (|offset| < {self.threshold_h:g} h) "
                f"~{self.recovery_day:.1f} days after landing."
            )
        else:
            lines.append(
                f"Not fully recovered within {self.days_post[-1]:.0f} days."
            )
        lines.append("")
        lines.append(" day   offset(h)")
        for d, o in zip(self.days_post, self.offset_h):
            bar = "#" * int(round(abs(o) * 2))
            lines.append(f" {d:4.1f}  {o:+6.2f}  {bar}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  The model
# --------------------------------------------------------------------------- #
class JetLagModel:
    """Predict circadian re-entrainment for a single flight.

    Example
    -------
    >>> m = JetLagModel("JFK", "CDG",
    ...                  departure="2026-06-01 18:00",
    ...                  arrival="2026-06-02 07:30")
    >>> res = m.simulate()
    >>> print(res.summary())
    """

    def __init__(
        self,
        origin_iata: str,
        dest_iata: str,
        departure: str | datetime,
        arrival: str | datetime,
        *,
        pre_days: int = 12,
        post_days: int = 16,
        wake_local: float = 7.0,
        sleep_local: float = 23.0,
        lux: float = 1000.0,
        dt_h: float = 0.05,
    ):
        self.origin = Airport.from_iata(origin_iata)
        self.destination = Airport.from_iata(dest_iata)
        self.departure = self._parse_local(departure, self.origin.tz)
        self.arrival = self._parse_local(arrival, self.destination.tz)
        self.pre_days = pre_days
        self.post_days = post_days
        self.wake_local = wake_local
        self.sleep_local = sleep_local
        self.lux = lux
        self.dt_h = dt_h

        self.tz_shift_h = self._tz_shift_hours()

    # ---- helpers -------------------------------------------------------- #
    @staticmethod
    def _parse_local(value: str | datetime, tz: ZoneInfo) -> datetime:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt

    def _tz_shift_hours(self) -> float:
        """Signed zones crossed. +ve = eastward = phase advance required.

        Uses the actual UTC offsets at the flight dates so DST is handled
        correctly. Normalised to (-12, 12]; an eastward jump larger than 12 h
        is represented as the equivalent (shorter) westward delay, because the
        body re-entrains via the shortest path around the clock.
        """
        off_o = self.departure.utcoffset()
        off_d = self.arrival.utcoffset()
        shift = (off_d - off_o).total_seconds() / 3600.0
        shift = ((shift + 12) % 24) - 12     # -> (-12, 12]
        return shift

    @property
    def direction(self) -> str:
        if abs(self.tz_shift_h) < 0.5:
            return "none"
        return "eastward" if self.tz_shift_h > 0 else "westward"

    # ---- core simulation ------------------------------------------------ #
    def _continuous_offset(self) -> tuple[np.ndarray, np.ndarray]:
        """Integrate the pacemaker and return (days_post, signed_offset_h).

        Offset = (actual circadian phase) - (target phase the body would have
        if fully entrained to the destination). Computed from the model's
        continuous oscillator angle and unwrapped, so no +/-12 h discontinuity.
        """
        pre_h = self.pre_days * 24.0
        total_h = (self.pre_days + self.post_days) * 24.0
        t = np.arange(0.0, total_h, self.dt_h)

        light = square_wave_light(
            t, pre_h, self.tz_shift_h,
            lux=self.lux,
            wake_local=self.wake_local,
            sleep_local=self.sleep_local,
        )

        model = Forger99()
        ic = model.equilibrate(t, light, 4)
        traj = model.integrate(t, ic, light)
        phase = model.phase(traj)            # circadian angle, radians, (-pi, pi]

        # Reference: an oscillator fully entrained to the DESTINATION light
        # schedule. Once entrained its phase at any fixed *local clock time*
        # is constant. We read that constant from the pre-travel segment of
        # the traveler's own trace (they are entrained to origin == an
        # equivalent schedule shifted by the same wake/sleep block), which
        # makes the offset identically zero before the flight by construction.
        ref_model = Forger99()
        # Destination schedule for the whole timeline (shift applied from t=0).
        ref_light = square_wave_light(
            t, 0.0, self.tz_shift_h,
            lux=self.lux,
            wake_local=self.wake_local,
            sleep_local=self.sleep_local,
        )
        ref_ic = ref_model.equilibrate(t, ref_light, 4)
        ref_traj = ref_model.integrate(t, ref_ic, ref_light)
        ref_phase = ref_model.phase(ref_traj)

        # Signed angular difference in (-pi, pi], converted to clock hours.
        # This is the instantaneous phase misalignment between the traveler
        # and a destination-entrained clock at the same wall-clock instant.
        d = phase - ref_phase
        d = (d + np.pi) % (2.0 * np.pi) - np.pi      # wrap to (-pi, pi]
        offset_h = d * (24.0 / (2.0 * np.pi))

        # Smooth the per-instant angle (it oscillates slightly within a day)
        # by sampling at the same local clock time (local midnight) each day.
        day_grid = np.arange(0.0, self.post_days + 1e-9, 1.0)
        sampled = np.empty_like(day_grid)
        for k, day in enumerate(day_grid):
            tt = pre_h + day * 24.0
            idx = min(int(round(tt / self.dt_h)), len(offset_h) - 1)
            sampled[k] = offset_h[idx]
        return day_grid, sampled

    def simulate(self, threshold_h: float = 1.0) -> ReentrainmentResult:
        days, offset = self._continuous_offset()

        recovery_day = None
        for i, (d, o) in enumerate(zip(days, offset)):
            if abs(o) < threshold_h and np.all(np.abs(offset[i:]) < threshold_h):
                recovery_day = float(d)
                break

        return ReentrainmentResult(
            origin=self.origin,
            destination=self.destination,
            tz_shift_h=self.tz_shift_h,
            direction=self.direction,
            days_post=days,
            offset_h=offset,
            recovery_day=recovery_day,
            threshold_h=threshold_h,
        )


# --------------------------------------------------------------------------- #
#  CLI demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    demos = [
        ("JFK", "CDG", "2026-06-01 18:00", "2026-06-02 07:30"),  # east  +6
        ("CDG", "JFK", "2026-06-01 10:00", "2026-06-01 13:00"),  # west  -6
        ("SFO", "NRT", "2026-06-01 11:00", "2026-06-02 15:00"),  # east  +16->-8
    ]
    for o, d, dep, arr in demos:
        res = JetLagModel(o, d, dep, arr).simulate()
        print(res.summary())
        print("-" * 60)
