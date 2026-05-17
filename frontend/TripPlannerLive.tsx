import { useCallback, useEffect, useMemo, useState } from "react";
import { BenneyCatRigManual, type BenneyManualState } from "../face/BenneyCatRigManual";
import { saveStaffTranscript } from "../services/staffRequests";
import "./trip-planner-live.css";

const API_BASE = import.meta.env.VITE_BENNEY_API ?? "http://127.0.0.1:7878";
const STAFF_API = import.meta.env.VITE_STAFF_API ?? "http://127.0.0.1:7879";
const STAFF_ROOM = "412";

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
  "Day 1 - Early morning", "Day 1 - Breakfast", "Day 1 - Late morning",
  "Day 1 - Lunch + afternoon", "Day 1 - Evening", "Day 1 - Night",
  "Day 2 - Early morning", "Day 2 - Breakfast", "Day 2 - Late morning",
  "Day 2 - Lunch + afternoon", "Day 2 - Evening", "Day 2 - Night",
  "Day 3 - Early morning", "Day 3 - Breakfast", "Day 3 - Late morning",
  "Day 3 - Lunch + afternoon", "Day 3 - Evening", "Day 3 - Night",
  "Day 4 - Early morning", "Day 4 - Breakfast", "Day 4 - Late morning",
  "Day 4 - Lunch + afternoon", "Day 4 - Evening", "Day 4 - Night",
  "Day 5 - Early morning", "Day 5 - Breakfast", "Day 5 - Late morning",
  "Day 5 - Lunch + afternoon", "Day 5 - Evening", "Day 5 - Night",
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

type SpeechRecognitionEventLike = {
  results?: ArrayLike<ArrayLike<{ transcript: string }>>;
};

type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((ev: SpeechRecognitionEventLike) => void) | null;
  onerror: ((ev: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
};

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type SpeechWindow = Window & typeof globalThis & {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
};

const DEMO_FLIGHT = {
  origin_iata: "JFK",
  dest_iata: "SFO",
  departure_iso: "2026-05-16 08:00",
  arrival_iso: "2026-05-16 11:30",
};

const FALLBACK_JETLAG: JetlagResp = {
  origin_iata: "JFK",
  dest_iata: "SFO",
  tz_shift_h: -3,
  direction: "westward",
  days_post: [1, 2, 3, 4, 5],
  offset_h: [-3, -2.1, -1.2, -0.4, 0],
  recovery_day: 4,
  note: "Westbound arrival favors morning light, gentle movement, and a quieter first evening.",
};

const FALLBACK_OPTIONS = [
  {
    name: "Rosewood garden breakfast",
    description: "A calm terrace start with fruit, espresso, and a short walk through the grounds.",
    tags: ["quiet", "hotel", "food", "low effort"],
  },
  {
    name: "Sightglass coffee run",
    description: "A polished city coffee stop with enough energy to feel local without crowding the day.",
    tags: ["coffee", "design", "city", "premium"],
  },
  {
    name: "Stanford sculpture walk",
    description: "Open-air art, shade, and architecture near campus before lunch plans pick up.",
    tags: ["art", "outdoors", "family", "nearby"],
  },
  {
    name: "Filoli garden afternoon",
    description: "Rose gardens, soft paths, and a photogenic estate pace that stays relaxed.",
    tags: ["roses", "garden", "calm", "scenic"],
  },
  {
    name: "Madera sunset dinner",
    description: "A warm, elegant dinner close to the room, timed for an easy first night.",
    tags: ["dinner", "hotel", "sunset", "chef"],
  },
] satisfies Array<Pick<Option, "name" | "description" | "tags">>;

function fallbackSlot(slotIdx: number): SlotResp {
  const safeSlot = Math.min(slotIdx, SLOT_LABELS.length - 1);
  return {
    slot_idx: safeSlot,
    subpopulation_size: 148,
    jaccard_threshold_used: 0.62,
    options: FALLBACK_OPTIONS.map((option, index) => ({
      ...option,
      activity_id: `fallback-${safeSlot}-${index}`,
      pct: 91 - index * 6,
      ci_low: 84 - index * 5,
      ci_high: 96 - index * 4,
      baseline_pct: 42 - index * 3,
      n: 48 - index * 5,
      of: 53,
      band: index === 0 ? "popular" : index < 3 ? "standard" : "niche",
    })),
  };
}

const SKETCHES = ["coffee", "resort", "tray", "coffee", "luggage"] as const;
const ITINERARY_PLACEHOLDERS = ["Early morning", "Morning", "Full day", "Relax & explore", "Departure"];
const CARE_STEPS = [
  ["Light exposure", "Morning"],
  ["Hydrate often", "All day"],
  ["Eat light, local", "Throughout"],
  ["Move gently", "Afternoon"],
  ["Sleep deeply", "Night"],
];

function slotTime(label: string) {
  return label.split(" - ")[1] ?? label;
}

function optionSketch(index: number) {
  return SKETCHES[index % SKETCHES.length];
}

export default function TripPlannerLive() {
  const [family] = useState(SAMPLE_FAMILY);
  const [history, setHistory] = useState<LockedPick[]>([]);
  // Start with null so the UI shows "loading…" until /next-slot returns.
  // Initializing with fallback static data made the page look "stuck" on
  // canned options before the real fetch finished.
  const [current, setCurrent] = useState<SlotResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reasoningMap, setReasoningMap] = useState<Record<string, string>>({});
  // Start null — chip hides until real /jetlag returns. Avoids the JFK->SFO
  // canned values flashing before the actual flight resolves.
  const [jetlag, setJetlag] = useState<JetlagResp | null>(null);
  const [flashHappy, setFlashHappy] = useState(false);

  const [voiceListening, setVoiceListening] = useState(false);
  const [voicePending, setVoicePending] = useState(false);
  const [voiceSpeaking, setVoiceSpeaking] = useState(false);
  const [voiceReply, setVoiceReply] = useState<string>("");
  const [voiceError, setVoiceError] = useState<string | null>(null);

  const historyIds = useMemo(() => history.map((h) => h.activity_id), [history]);
  const isComplete = history.length >= SLOT_LABELS.length;

  const benneyState: BenneyManualState = useMemo(() => {
    if (voiceSpeaking) return "speaking";
    if (voicePending) return "thinking";
    if (voiceListening) return "listening";
    if (error || voiceError) return "concerned";
    if (isComplete) return "celebrating";
    if (loading) return "thinking";
    if (flashHappy) return "happy";
    if (history.length === 0) return "greeting";
    return "curious";
  }, [voiceSpeaking, voicePending, voiceListening, error, voiceError,
       isComplete, loading, flashHappy, history.length]);

  const tripDay = Math.floor(history.length / 6);

  // Override global body { overflow: hidden } so this page can scroll.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "auto";
    document.documentElement.style.overflow = "auto";
    return () => {
      document.body.style.overflow = prev;
      document.documentElement.style.overflow = "";
    };
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/jetlag`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(DEMO_FLIGHT),
      })
      .then((r) => (r.ok ? r.json() : null))
      .then((j: JetlagResp | null) => j && setJetlag(j))
      .catch(() => setJetlag(null));
  }, []);

  const fetchSlot = useCallback(async () => {
    if (isComplete) return;
    setLoading(true);
    setError(null);
    try {
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
      // Surface the failure so we don't silently render canned data.
      setError(e instanceof Error ? e.message : String(e));
      setCurrent(null);
    } finally {
      setLoading(false);
    }
  }, [family, historyIds, isComplete, jetlag, tripDay]);
  // NOTE: deliberately NOT depending on reasoningMap — its setter is called
  // inside this callback, which would otherwise cause a refetch loop.

  useEffect(() => {
    fetchSlot();
  }, [fetchSlot]);

  const [staffSync, setStaffSync] = useState<string | null>(null);

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
      setFlashHappy(true);
      window.setTimeout(() => setFlashHappy(false), 1500);

      // Bridge to staff board: this picks the activity AND notifies staff
      // with an ETA back so housekeeping / room service can plan.
      fetch(`${STAFF_API}/lock-slot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          room: STAFF_ROOM,
          activity_id: opt.activity_id,
          slot_idx: current.slot_idx,
        }),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => {
          if (j?.expected_return_local) {
            setStaffSync(
              `Sent to staff (Room ${j.room}) — back ~${j.expected_return_local} (${j.travel_back_minutes} min drive)`,
            );
            window.setTimeout(() => setStaffSync(null), 4000);
          }
        })
        .catch(() => {});
    },
    [current, reasoningMap],
  );

  const speakReply = useCallback(async (audioB64: string) => {
    setVoiceSpeaking(true);
    const audio = new Audio(`data:audio/mpeg;base64,${audioB64}`);
    audio.onended = () => setVoiceSpeaking(false);
    audio.onerror = () => setVoiceSpeaking(false);
    try {
      await audio.play();
    } catch {
      setVoiceSpeaking(false);
    }
  }, []);

  const handleTranscript = useCallback(async (transcript: string) => {
    if (voicePending || voiceSpeaking) return;
    setVoiceReply("");
    setVoiceError(null);

    const staffCard = saveStaffTranscript(transcript);
    if (staffCard) {
      setVoiceReply(`Sent to staff: ${staffCard.action} for ${staffCard.room}.`);
      setFlashHappy(true);
      window.setTimeout(() => setFlashHappy(false), 1500);
      return;
    }

    setVoicePending(true);
    const offset_h = jetlag ? jetlag.offset_h[Math.min(tripDay, jetlag.offset_h.length - 1)] : null;
    try {
      const r = await fetch(`${API_BASE}/voice`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript,
          family,
          history: historyIds,
          context: {
            jetlag_offset_h: offset_h,
            current_slot: current?.slot_idx ?? null,
            top_options: current?.options.slice(0, 3).map((o) => ({
              activity_id: o.activity_id, name: o.name, pct: o.pct,
            })) ?? [],
          },
        }),
      });
      if (!r.ok) throw new Error(`/voice ${r.status}`);
      const data = await r.json();
      setVoiceReply(data.reply_text || "");
      if (data.audio_b64) await speakReply(data.audio_b64);
      // Benney can navigate the user between views by emitting <nav>...</nav>
      // tags. We honor those after the audio finishes for a smooth UX.
      if (data.nav && data.nav !== "trip_planner") {
        const target = data.nav === "staff_board" ? "?staff=1"
                     : data.nav === "families"    ? "?families=1"
                     : data.nav === "home"        ? "?home=1"
                     : data.nav === "landing"     ? "?landing=1"
                     : null;
        if (target) window.setTimeout(() => { window.location.search = target; }, 800);
      }
    } catch (e) {
      setVoiceError(e instanceof Error ? e.message : String(e));
    } finally {
      setVoicePending(false);
    }
  }, [family, historyIds, jetlag, tripDay, current, speakReply,
       voicePending, voiceSpeaking]);

  const startListening = useCallback(() => {
    const w = window as SpeechWindow;
    const SR = w.SpeechRecognition ?? w.webkitSpeechRecognition;
    if (!SR) {
      setVoiceError("Browser speech recognition is not available in this display.");
      return;
    }
    const rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    rec.onstart = () => { setVoiceListening(true); setVoiceError(null); };
    rec.onresult = (ev) => {
      const t = ev.results?.[0]?.[0]?.transcript;
      if (t) handleTranscript(t);
    };
    rec.onerror = (ev) => { setVoiceError(`mic: ${ev.error}`); setVoiceListening(false); };
    rec.onend = () => setVoiceListening(false);
    rec.start();
  }, [handleTranscript]);

  const slotLabel = current ? SLOT_LABELS[current.slot_idx] : null;
  const picksByDay = useMemo(() => {
    const groups: LockedPick[][] = [[], [], [], [], []];
    history.forEach((p) => groups[Math.floor(p.slot_idx / 6)].push(p));
    return groups;
  }, [history]);

  const voiceStatus = voiceListening
    ? "Listening for changes"
    : voicePending
      ? "Thinking through the stay"
      : voiceSpeaking
        ? "Speaking softly"
        : "Listening for changes";

  return (
    <main className="tpl-shell">
      <span className="tpl-rose tpl-rose-left" aria-hidden="true" />
      <span className="tpl-rose tpl-rose-bottom-left" aria-hidden="true" />
      <span className="tpl-rose tpl-rose-right" aria-hidden="true" />
      <span className="tpl-petal tpl-petal-one" aria-hidden="true" />
      <span className="tpl-petal tpl-petal-two" aria-hidden="true" />
      <span className="tpl-petal tpl-petal-three" aria-hidden="true" />

      <header className="tpl-header">
        <div className="tpl-header-cat">
          <div className="tpl-title-block">
            <span className="tpl-eyebrow">
              <span className="tpl-brand-name">Benney</span>
              <span className="tpl-brand-diamond" aria-hidden="true" />
              <span>Rosewood Sand Hill</span>
            </span>
            <h1>5-day Bay Area itinerary</h1>
            <p className="tpl-profile">
              {family.group_type}, {family.budget_tier} stay, loves {family.primary_interest} and {family.secondary_interest}.
              {" "}
              {family.pace} pace, {family.crowd_tolerance} with crowds.
            </p>
          </div>
        </div>
        <div className="tpl-resort-sketch" aria-hidden="true" />
      </header>

      {(voiceReply || voiceError) && (
        <aside className={`tpl-voice-bubble ${voiceError ? "tpl-voice-bubble-error" : ""}`}>
          {voiceError ? <em>{voiceError}</em> : voiceReply}
        </aside>
      )}
      {staffSync && (
        <aside className="tpl-staff-toast">{staffSync}</aside>
      )}

      <div className="tpl-layout">
        <aside className="tpl-quote">
          <span className="tpl-quote-mark">"</span>
          <p>Craft, clarity, and calm - that's how we travel best.</p>
          <small>- Benney</small>
          <div className="tpl-side-benney">
            <BenneyCatRigManual state={benneyState} className="tpl-cat" />
          </div>
          <button
            className={`tpl-ask-benney ${voiceListening ? "tpl-voice-on" : ""} ${voicePending ? "tpl-voice-thinking" : ""} ${voiceSpeaking ? "tpl-voice-speaking" : ""}`}
            type="button"
            onClick={startListening}
            disabled={voiceListening || voiceSpeaking || voicePending}
          >
            <span className="tpl-wave" aria-hidden="true" />
            <span>Ask Benney</span>
          </button>
        </aside>

        <section className="tpl-current" aria-live="polite">
          {isComplete ? (
            <div className="tpl-complete">
              <h2>Itinerary complete</h2>
              <p>30 slots locked across 5 days.</p>
            </div>
          ) : !current ? (
            <div className="tpl-loading">{error ? `error: ${error}` : "assembling the folio"}</div>
          ) : (
            <>
              <div className="tpl-slot-head">
                <h2>{slotLabel}</h2>
              </div>
              <div className="tpl-bars">
                {current.options.map((opt, index) => {
                  const reasoning = reasoningMap[opt.activity_id];
                  const sketch = optionSketch(index);
                  return (
                    <button
                      key={opt.activity_id}
                      type="button"
                      className={`tpl-bar tpl-band-${opt.band}`}
                      aria-label={`Lock in ${opt.name}`}
                      onClick={() => pick(opt)}
                    >
                      <span className={`tpl-option-sketch tpl-sketch-${sketch}`} aria-hidden="true" />
                      <span className="tpl-card-rose" aria-hidden="true" />
                      <span className="tpl-bar-content">
                        <span className="tpl-bar-copy">
                          <span className="tpl-bar-name">{opt.name}</span>
                          <span className="tpl-bar-desc">{reasoning ?? opt.description}</span>
                          <span className="tpl-bar-tags">
                            {opt.tags.slice(0, 4).map((t) => (
                              <span key={t}>{t}</span>
                            ))}
                            <span className="tpl-bar-count">
                              {opt.n} of {opt.of} similar families
                            </span>
                          </span>
                        </span>
                        <span className="tpl-match">
                          <span className="tpl-bar-pct">{opt.pct.toFixed(0)}%</span>
                          <span className="tpl-match-label">probability match</span>
                          <span className="tpl-match-track" aria-hidden="true">
                            <span className="tpl-match-fill" style={{ width: `${Math.min(100, opt.pct)}%` }} />
                          </span>
                          <span className="tpl-bar-ci">
                            {opt.ci_low.toFixed(0)}-{opt.ci_high.toFixed(0)}% base {opt.baseline_pct.toFixed(0)}%
                          </span>
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </section>

        <aside className="tpl-side-panel">
          {jetlag && (
            <section className="tpl-jetlag">
              <div className="tpl-jetlag-head">
                <span>Jet lag care</span>
                <em>
                  {jetlag.origin_iata} to {jetlag.dest_iata}
                </em>
              </div>
              <div className="tpl-care-row">
                {CARE_STEPS.map(([label, timing], i) => (
                  <span key={label} className={`tpl-care-step tpl-care-step-${i + 1}`}>
                    <strong>{label}</strong>
                    <em>{timing}</em>
                  </span>
                ))}
              </div>
              <p>{jetlag.note}</p>
              <div className="tpl-jetlag-curve">
                {jetlag.offset_h.slice(0, 5).map((o, i) => {
                  const isToday = i === tripDay;
                  const mag = Math.min(1, Math.abs(o) / 6);
                  return (
                    <div key={i} className={`tpl-jetlag-day ${isToday ? "tpl-jetlag-day-today" : ""}`}>
                      <span>Day {i + 1}</span>
                      <span className="tpl-jetlag-bar-track">
                        <span
                          className={`tpl-jetlag-bar ${o < 0 ? "tpl-jetlag-bar-neg" : "tpl-jetlag-bar-pos"}`}
                          style={{ width: `${Math.max(9, mag * 100)}%` }}
                        />
                      </span>
                      <em>{o >= 0 ? "+" : ""}{o.toFixed(1)}h</em>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          <section className="tpl-timeline">
            <h3>Your itinerary</h3>
            {picksByDay.map((day, i) => (
              <div className="tpl-day" key={i}>
                <div className="tpl-day-label">Day {i + 1}</div>
                <span className={`tpl-itin-sketch tpl-itin-sketch-${i + 1}`} aria-hidden="true" />
                {day.length === 0 ? (
                  <div className="tpl-day-empty">
                    <span aria-hidden="true" />
                    {i === tripDay ? slotTime(slotLabel ?? SLOT_LABELS[i * 6]) : ITINERARY_PLACEHOLDERS[i]}
                  </div>
                ) : (
                  day.map((p) => (
                    <div className="tpl-day-pick" key={`${p.slot_idx}-${p.activity_id}`}>
                      <span className="tpl-day-time">{slotTime(SLOT_LABELS[p.slot_idx])}</span>
                      <span className="tpl-day-name">{p.name}</span>
                      <span className="tpl-day-pct">{p.pct.toFixed(0)}% chose this</span>
                    </div>
                  ))
                )}
              </div>
            ))}

            <div className="tpl-voice-status" aria-live="polite">
              <span className="tpl-wave" aria-hidden="true" />
              <span>{voiceStatus}</span>
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}
