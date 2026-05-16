import { useCallback, useEffect, useMemo, useState } from "react";
import "./trip-planner-live.css";

const API_BASE = import.meta.env.VITE_BENNEY_API ?? "http://127.0.0.1:7878";

type Band = "popular" | "standard" | "niche" | "buried";

type Option = {
  activity_id: string;
  name: string;
  description: string;
  tags: string[];
  pct: number;
  ci_low: number;
  ci_high: number;
  baseline_pct: number;
  n: number;
  of: number;
  band: Band;
};

type SlotResp = {
  slot_idx: number;
  subpopulation_size: number;
  jaccard_threshold_used: number;
  options: Option[];
};

const SLOT_LABELS = [
  "Day 1 · Early morning", "Day 1 · Breakfast", "Day 1 · Late morning",
  "Day 1 · Lunch + afternoon", "Day 1 · Evening", "Day 1 · Night",
  "Day 2 · Early morning", "Day 2 · Breakfast", "Day 2 · Late morning",
  "Day 2 · Lunch + afternoon", "Day 2 · Evening", "Day 2 · Night",
  "Day 3 · Early morning", "Day 3 · Breakfast", "Day 3 · Late morning",
  "Day 3 · Lunch + afternoon", "Day 3 · Evening", "Day 3 · Night",
  "Day 4 · Early morning", "Day 4 · Breakfast", "Day 4 · Late morning",
  "Day 4 · Lunch + afternoon", "Day 4 · Evening", "Day 4 · Night",
  "Day 5 · Early morning", "Day 5 · Breakfast", "Day 5 · Late morning",
  "Day 5 · Lunch + afternoon", "Day 5 · Evening", "Day 5 · Night",
];

const SAMPLE_FAMILY = {
  group_type: "couple",
  adult_count: 2,
  kid_ages: "none",
  trip_purpose: "leisure",
  budget_tier: "premium",
  trip_length_days: 5,
  pace: "balanced",
  primary_interest: "tech",
  secondary_interest: "food",
  crowd_tolerance: "okay",
  energy: "medium",
  local_interaction: "mixed",
  mobility: "full",
  dietary: "none",
  language_comfort: "english-only",
};

type LockedPick = {
  slot_idx: number;
  activity_id: string;
  name: string;
  pct: number;
  of: number;
  reasoning?: string;
};

type JetlagResp = {
  origin_iata: string;
  dest_iata: string;
  tz_shift_h: number;
  direction: "eastward" | "westward" | "none";
  days_post: number[];
  offset_h: number[];
  recovery_day: number | null;
  note: string;
};

// Demo flight — JFK to SFO. Easy to toggle to test other origins.
const DEMO_FLIGHT = {
  origin_iata: "JFK",
  dest_iata: "SFO",
  departure_iso: "2026-05-16 08:00",
  arrival_iso: "2026-05-16 11:30",
};

export default function TripPlannerLive() {
  const [family] = useState(SAMPLE_FAMILY);
  const [history, setHistory] = useState<LockedPick[]>([]);
  const [current, setCurrent] = useState<SlotResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reasoningMap, setReasoningMap] = useState<Record<string, string>>({});
  const [jetlag, setJetlag] = useState<JetlagResp | null>(null);

  const historyIds = useMemo(() => history.map((h) => h.activity_id), [history]);
  const isComplete = history.length >= SLOT_LABELS.length;

  // Trip day = how many filled "days" we're into (slots 0-5 = day 0, 6-11 = day 1, ...)
  const tripDay = Math.floor(history.length / 6);

  // Fetch the jet-lag curve once on mount.
  useEffect(() => {
    fetch(`${API_BASE}/jetlag`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(DEMO_FLIGHT),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((j: JetlagResp | null) => j && setJetlag(j))
      .catch(() => {});
  }, []);

  const fetchSlot = useCallback(async () => {
    if (isComplete) return;
    setLoading(true);
    setError(null);
    try {
      // Thread jet-lag into the /next-slot request so the aggregator can
      // re-weight by energy-vs-body-clock fit (offset curve from /jetlag).
      const jetlagBody = jetlag
        ? { offset_h: jetlag.offset_h[Math.min(tripDay, jetlag.offset_h.length - 1)], trip_day: tripDay }
        : null;
      const r = await fetch(`${API_BASE}/next-slot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ family, history: historyIds, jetlag: jetlagBody }),
      });
      if (!r.ok) throw new Error(`server returned ${r.status}`);
      const data: SlotResp = await r.json();
      setCurrent(data);
      // Pre-fetch reasoning for the top options (non-blocking)
      data.options.slice(0, 3).forEach((opt) => {
        if (!reasoningMap[opt.activity_id]) {
          fetch(`${API_BASE}/reasoning`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              activity_id: opt.activity_id,
              family,
              slot_idx: data.slot_idx,
              pct: opt.pct,
              n: opt.n,
              of: opt.of,
            }),
          })
            .then((res) => (res.ok ? res.json() : null))
            .then((j) => {
              if (j?.text) setReasoningMap((m) => ({ ...m, [opt.activity_id]: j.text }));
            })
            .catch(() => {});
        }
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [family, historyIds, isComplete, reasoningMap, jetlag, tripDay]);

  useEffect(() => {
    fetchSlot();
  }, [fetchSlot]);

  const pick = useCallback(
    (opt: Option) => {
      if (!current) return;
      setHistory((h) => [
        ...h,
        {
          slot_idx: current.slot_idx,
          activity_id: opt.activity_id,
          name: opt.name,
          pct: opt.pct,
          of: opt.of,
          reasoning: reasoningMap[opt.activity_id],
        },
      ]);
    },
    [current, reasoningMap],
  );

  const reset = () => {
    setHistory([]);
    setCurrent(null);
  };

  const slotLabel = current ? SLOT_LABELS[current.slot_idx] : null;
  // Group locked picks by day for the timeline column
  const picksByDay = useMemo(() => {
    const groups: LockedPick[][] = [[], [], [], [], []];
    history.forEach((p) => groups[Math.floor(p.slot_idx / 6)].push(p));
    return groups;
  }, [history]);

  return (
    <div className="tpl-shell">
      <header className="tpl-header">
        <div>
          <span className="tpl-eyebrow">Benney · Trip Planner</span>
          <h1>5-day Bay Area itinerary</h1>
          <p className="tpl-profile">
            {family.group_type}, {family.budget_tier} budget, loves {family.primary_interest} + {family.secondary_interest}
            {" — "}
            {family.pace} pace · {family.crowd_tolerance} crowds
          </p>
        </div>
        <button className="tpl-reset" type="button" onClick={reset} disabled={history.length === 0}>
          Start over
        </button>
      </header>

      {jetlag && (
        <section className="tpl-jetlag">
          <div className="tpl-jetlag-head">
            <span className="tpl-jetlag-label">Jet lag · Forger99 oscillator</span>
            <span className="tpl-jetlag-route">
              {jetlag.origin_iata} → {jetlag.dest_iata}{" "}
              <em>({jetlag.tz_shift_h >= 0 ? "+" : ""}{jetlag.tz_shift_h.toFixed(0)}h, {jetlag.direction})</em>
            </span>
          </div>
          <p className="tpl-jetlag-note">{jetlag.note}</p>
          <div className="tpl-jetlag-curve">
            {jetlag.offset_h.slice(0, 7).map((o, i) => {
              const isToday = i === tripDay;
              const mag = Math.min(1, Math.abs(o) / 6);
              return (
                <div key={i} className={`tpl-jetlag-day ${isToday ? "tpl-jetlag-day-today" : ""}`}>
                  <div className="tpl-jetlag-bar-track">
                    <div
                      className={`tpl-jetlag-bar ${o < 0 ? "tpl-jetlag-bar-neg" : "tpl-jetlag-bar-pos"}`}
                      style={{ width: `${mag * 100}%` }}
                    />
                  </div>
                  <span className="tpl-jetlag-bar-label">
                    Day {i + 1} · {o >= 0 ? "+" : ""}
                    {o.toFixed(1)}h
                  </span>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <div className="tpl-body">
        <section className="tpl-current">
          {isComplete ? (
            <div className="tpl-complete">
              <h2>Itinerary complete</h2>
              <p>30 slots locked across 5 days.</p>
            </div>
          ) : !current ? (
            <div className="tpl-loading">{error ? `error: ${error}` : "loading…"}</div>
          ) : (
            <>
              <div className="tpl-slot-head">
                <span className="tpl-slot-step">Slot {current.slot_idx + 1} / 30</span>
                <h2>{slotLabel}</h2>
                <small>
                  {current.subpopulation_size} similar families ·
                  trajectory match ≥ {(current.jaccard_threshold_used * 100).toFixed(0)}%
                </small>
              </div>
              <div className="tpl-bars">
                {current.options.map((opt) => {
                  const reasoning = reasoningMap[opt.activity_id];
                  const isPopular = opt.band === "popular";
                  const isBuried = opt.band === "buried";
                  return (
                    <button
                      key={opt.activity_id}
                      className={`tpl-bar tpl-band-${opt.band}`}
                      type="button"
                      onClick={() => pick(opt)}
                      disabled={loading}
                    >
                      <div className="tpl-bar-fill" style={{ width: `${Math.min(100, opt.pct)}%` }} />
                      <div className="tpl-bar-content">
                        <div className="tpl-bar-top">
                          <span className="tpl-bar-pct">
                            {isPopular ? "Most popular" : isBuried ? "Niche" : `${opt.pct.toFixed(0)}%`}
                          </span>
                          <span className="tpl-bar-name">{opt.name}</span>
                          {!isPopular && !isBuried && (
                            <span className="tpl-bar-ci">
                              {opt.ci_low.toFixed(0)}–{opt.ci_high.toFixed(0)}%
                            </span>
                          )}
                          <span className="tpl-bar-baseline">
                            base {opt.baseline_pct.toFixed(0)}%
                          </span>
                        </div>
                        <div className="tpl-bar-desc">{reasoning ?? opt.description}</div>
                        <div className="tpl-bar-tags">
                          {opt.tags.slice(0, 4).map((t) => (
                            <span key={t}>{t}</span>
                          ))}
                          <span className="tpl-bar-count">
                            {opt.n} of {opt.of} similar families
                          </span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </section>

        <aside className="tpl-timeline">
          <h3>Your itinerary</h3>
          {picksByDay.map((day, i) => (
            <div className="tpl-day" key={i}>
              <div className="tpl-day-label">Day {i + 1}</div>
              {day.length === 0 ? (
                <div className="tpl-day-empty">·</div>
              ) : (
                day.map((p) => (
                  <div className="tpl-day-pick" key={`${p.slot_idx}-${p.activity_id}`}>
                    <span className="tpl-day-time">
                      {SLOT_LABELS[p.slot_idx].split(" · ")[1]}
                    </span>
                    <span className="tpl-day-name">{p.name}</span>
                    <span className="tpl-day-pct">
                      {p.pct.toFixed(0)}% chose this
                    </span>
                  </div>
                ))
              )}
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}
