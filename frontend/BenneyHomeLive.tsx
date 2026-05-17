import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BenneyCatRigManual, type BenneyManualState } from "../face/BenneyCatRigManual";
import { saveStaffTranscript } from "../services/staffRequests";
import TripPlannerLive from "./TripPlannerLive";
import "./benney-home-live.css";

const API_BASE = import.meta.env.VITE_BENNEY_API ?? "http://127.0.0.1:7878";

type HomeMode = "center" | "itinerary";
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

const HOME_FAMILY_CONTEXT = {
  group_type: "family",
  adult_count: 2,
  kid_ages: "8, 12",
  trip_purpose: "leisure",
  budget_tier: "premium",
  trip_length_days: 5,
  pace: "balanced",
  primary_interest: "food",
  secondary_interest: "gardens",
  crowd_tolerance: "okay",
  energy: "medium",
  local_interaction: "mixed",
  mobility: "full",
  dietary: "none",
  language_comfort: "english-only",
};

const HOME_TOP_OPTIONS = [
  { activity_id: "rosewood-breakfast", name: "Rosewood garden breakfast", pct: 91 },
  { activity_id: "stanford-sculpture", name: "Stanford sculpture walk", pct: 82 },
  { activity_id: "madera-dinner", name: "Madera sunset dinner", pct: 76 },
];

function getInitialMode(): HomeMode {
  const params = new URLSearchParams(window.location.search);
  const show = params.get("show") ?? params.get("mode");
  return show === "itinerary" || show === "trip" ? "itinerary" : "center";
}

declare global {
  interface Window {
    benneyOpenItinerary?: () => void;
    benneyCenter?: () => void;
    benneyHear?: (transcript: string) => void;
  }
}

export default function BenneyHomeLive() {
  const [mode, setMode] = useState<HomeMode>(getInitialMode);
  const [voiceState, setVoiceState] = useState<"idle" | "listening" | "thinking" | "happy" | "concerned" | "speaking">("idle");
  const [voiceReply, setVoiceReply] = useState("Say \"show itinerary\", or ask for cleaning, food, or flight tracking.");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const benneyState: BenneyManualState = useMemo(() => {
    if (mode === "itinerary") return "focused";
    if (voiceState === "listening") return "listening";
    if (voiceState === "thinking") return "thinking";
    if (voiceState === "speaking") return "speaking";
    if (voiceState === "happy") return "happy";
    if (voiceState === "concerned") return "concerned";
    return "curious";
  }, [mode, voiceState]);

  const handleTranscript = useCallback(async (transcript: string) => {
    const cleanTranscript = transcript.trim();
    if (!cleanTranscript) {
      setVoiceReply("I did not catch words yet. Try asking for the itinerary, cleaning, food, or flight tracking.");
      setVoiceState("concerned");
      return;
    }

    const lower = cleanTranscript.toLowerCase();
    setVoiceState("thinking");
    setVoiceReply(`I heard: "${cleanTranscript}"`);

    if (/\b(itinerary|trip|plan|plans|recommend|recommendations|show.+screen|open.+screen)\b/.test(lower)) {
      setVoiceReply("Opening your itinerary…");
      setVoiceState("happy");
      // Navigate to the full trip planner page (live cohort, /next-slot,
      // probability bars + jet-lag chip + voice mic). The embedded preview
      // mode was kept stale; this gets the full surface every time.
      window.setTimeout(() => { window.location.search = "?trip=1"; }, 400);
      return;
    }
    if (/\b(famil|cohort|network|persona|agent)\b/.test(lower)) {
      setVoiceReply("Opening the families network…");
      setVoiceState("happy");
      window.setTimeout(() => { window.location.search = "?families=1"; }, 400);
      return;
    }
    if (/\b(staff|housekeeping|room service|cleaning queue|board)\b/.test(lower)) {
      setVoiceReply("Opening the staff board…");
      setVoiceState("happy");
      window.setTimeout(() => { window.location.search = "?staff=1"; }, 400);
      return;
    }

    const staffCard = saveStaffTranscript(cleanTranscript);
    if (staffCard) {
      setVoiceReply(`I passed that to the staff board: ${staffCard.action} for ${staffCard.room}.`);
      setVoiceState("happy");
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/voice`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript: cleanTranscript,
          family: HOME_FAMILY_CONTEXT,
          history: [],
          context: {
            current_surface: mode,
            hotel: "Rosewood Sand Hill",
            room: "Room 304",
            can_show_itinerary: true,
            can_pass_staff_requests: true,
            cannot_book_or_reserve: true,
            top_options: HOME_TOP_OPTIONS,
            staff_request_hint: "For cleaning, food, return-time, or flight tracking, pass the request to staff rather than claiming to schedule or reserve.",
          },
        }),
      });
      if (!response.ok) throw new Error(`voice ${response.status}`);
      const data = await response.json() as { reply_text?: string; audio_b64?: string | null; emotion?: BenneyManualState; nav?: string | null };
      setVoiceReply(data.reply_text || "I can help with the itinerary or pass requests to staff.");
      setVoiceState(data.emotion === "concerned" ? "concerned" : "happy");
      // Honor Benney's <nav> tag — route to the matching page after speech.
      if (data.nav) {
        const target = data.nav === "trip_planner" ? "?trip=1"
                     : data.nav === "families"    ? "?families=1"
                     : data.nav === "staff_board" ? "?staff=1"
                     : data.nav === "landing"     ? "?landing=1"
                     : null;
        if (target) window.setTimeout(() => { window.location.search = target; }, 900);
      }
      if (data.audio_b64) {
        setVoiceState("speaking");
        const audio = new Audio(`data:audio/mpeg;base64,${data.audio_b64}`);
        audio.onended = () => setVoiceState("happy");
        audio.onerror = () => setVoiceState("happy");
        await audio.play().catch(() => setVoiceState("happy"));
      }
    } catch {
      setVoiceReply("I can help with the itinerary, or pass cleaning, food, return-time, and flight details to staff.");
      setVoiceState("concerned");
    }
  }, [mode]);

  useEffect(() => {
    // Any caller asking to "open itinerary" routes to the real ?trip=1 page
    // — embedding TripPlannerLive in the home shell shadowed the real /next-slot
    // and surfaced as 'static'. Navigation is the single source of truth.
    window.benneyOpenItinerary = () => { window.location.search = "?trip=1"; };
    window.benneyCenter = () => setMode("center");
    window.benneyHear = (transcript: string) => handleTranscript(transcript);

    const onShow = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail;
      if (detail === "itinerary" || detail === "trip") {
        window.location.search = "?trip=1"; return;
      }
      if (detail === "families") { window.location.search = "?families=1"; return; }
      if (detail === "staff") { window.location.search = "?staff=1"; return; }
      if (detail === "center" || detail === "home") setMode("center");
    };

    window.addEventListener("benney:show", onShow);
    return () => {
      window.removeEventListener("benney:show", onShow);
      delete window.benneyOpenItinerary;
      delete window.benneyCenter;
      delete window.benneyHear;
    };
  }, [handleTranscript]);

  const startListening = async () => {
    const w = window as SpeechWindow;
    const SR = w.SpeechRecognition ?? w.webkitSpeechRecognition;
    if (!SR) {
      setVoiceReply("This browser display does not expose speech recognition. The voice-agent bridge can still send me text through window.benneyHear(...).");
      setVoiceState("concerned");
      return;
    }
    const rec = new SR();
    recognitionRef.current = rec;
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    rec.onstart = () => {
      setVoiceReply("Listening. Ask for the itinerary, room help, food, or flight tracking.");
      setVoiceState("listening");
    };
    rec.onresult = (ev) => {
      const transcript = ev.results?.[0]?.[0]?.transcript;
      if (transcript) void handleTranscript(transcript);
    };
    rec.onerror = () => {
      setVoiceReply("I could not hear that clearly. Check microphone permission, then try again.");
      setVoiceState("concerned");
    };
    rec.onend = () => {
      setVoiceState((state) => (state === "listening" ? "idle" : state));
      recognitionRef.current = null;
    };
    try {
      rec.start();
    } catch {
      setVoiceReply("The microphone listener did not start. Please try again once the browser grants mic access.");
      setVoiceState("concerned");
      recognitionRef.current = null;
    }
  };

  const shellClass = useMemo(
    () => `bh-shell ${mode === "itinerary" ? "bh-show-itinerary" : "bh-show-center"}`,
    [mode],
  );

  return (
    <main className={shellClass} aria-live="polite">
      <span className="bh-rose bh-rose-left" aria-hidden="true" />
      <span className="bh-rose bh-rose-right" aria-hidden="true" />

      <section className="bh-center" aria-label="Benney assistant home screen">
        <BenneyCatRigManual state={benneyState} className="bh-cat" ariaLabel="Benney listening" />
        <button className="bh-listening" type="button" onClick={startListening}>
          <span className="bh-wave" aria-hidden="true" />
          <span>{voiceState === "listening" ? "Listening" : "Ask Benney"}</span>
        </button>
        <p className="bh-reply">{voiceReply}</p>
      </section>

      {/* Itinerary surface removed from the embedded shell — it now lives
          at /?trip=1 as a real page. Asking Benney for the itinerary
          navigates there, where the live /next-slot data renders. */}
    </main>
  );
}
