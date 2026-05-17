import { useCallback, useState } from "react";
import "./landing-page-live.css";

const API_BASE = import.meta.env.VITE_BENNEY_API ?? "http://127.0.0.1:7878";

const itineraryDays = [
  {
    label: "Day 1 - Early morning",
    detail: "1000 similar families - trajectory match >= 50%",
  },
  { label: "Day 2" },
  { label: "Day 3" },
  { label: "Day 4" },
  { label: "Day 5" },
];

const staffStats = [
  { label: "Housekeeping", value: "0", detail: "rooms tracked" },
  { label: "Room Service", value: "0", detail: "requests" },
  { label: "Arrivals", value: "0", detail: "today" },
];

type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onstart: (() => void) | null;
  onresult: ((ev: { results?: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((ev: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
};

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type SpeechWindow = Window & typeof globalThis & {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
};

export default function LandingPageLive() {
  const [listening, setListening] = useState(false);
  const [pending, setPending] = useState(false);
  const [reply, setReply] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  const navigateOnReply = (nav?: string | null) => {
    const target = nav === "trip_planner" ? "?trip=1"
                 : nav === "staff_board"  ? "?staff=1"
                 : nav === "families"     ? "?families=1"
                 : nav === "home"         ? "?home=1"
                 : null;
    if (target) window.setTimeout(() => { window.location.search = target; }, 800);
  };

  const handleTranscript = useCallback(async (transcript: string) => {
    setPending(true);
    setError(null);
    setReply("");
    try {
      const r = await fetch(`${API_BASE}/voice`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript, context: {} }),
      });
      if (!r.ok) throw new Error(`/voice ${r.status}`);
      const data = await r.json();
      setReply(data.reply_text || "");
      if (data.audio_b64) {
        const audio = new Audio(`data:audio/mpeg;base64,${data.audio_b64}`);
        audio.play().catch(() => {});
      }
      navigateOnReply(data.nav);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }, []);

  const startListening = useCallback(() => {
    const w = window as SpeechWindow;
    const SR = w.SpeechRecognition ?? w.webkitSpeechRecognition;
    if (!SR) { setError("Browser speech recognition not supported - use Chrome/Edge"); return; }
    const rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.continuous = false;
    rec.onstart = () => { setListening(true); setError(null); };
    rec.onresult = (ev) => {
      const t = ev.results?.[0]?.[0]?.transcript;
      if (t) handleTranscript(t);
    };
    rec.onerror = (ev) => { setError(`mic: ${ev.error}`); setListening(false); };
    rec.onend = () => setListening(false);
    rec.start();
  }, [handleTranscript]);

  return (
    <main className="lp-shell" aria-labelledby="benney-title">
      <section className="lp-frame" aria-label="Benney Rosewood Sand Hill landing screen">
        <img
          className="lp-reference"
          src="/assets/rosewood-ui/landing-mockup.png"
          width="1672"
          height="941"
          alt=""
          aria-hidden="true"
          draggable={false}
        />

        <nav className="lp-actions" aria-label="Benney demo routes">
          <a className="lp-action lp-action-agent" href="/?home=1">
            <span>Talk to Benney</span>
            <small>Voice + cat + everything else</small>
          </a>
          <a className="lp-action lp-action-staff" href="/?staff=1">
            <span>Staff Board</span>
            <small>Live requests</small>
          </a>
        </nav>

        {(reply || error) && (
          <aside className={`lp-voice-bubble ${error ? "lp-voice-bubble-error" : ""}`}>
            {error ? <em>{error}</em> : reply}
          </aside>
        )}

        <div className="lp-semantic">
          <p>Rosewood Sand Hill</p>
          <h1 id="benney-title">Benney</h1>
          <p>A voice-first stay assistant</p>
          <p>Ask for plans, service, and calm answers.</p>
          <p>{listening ? "Listening..." : "How may I help you today?"}</p>

          <h2>Your itinerary</h2>
          <ol>
            {itineraryDays.map((day) => (
              <li key={day.label}>
                {day.label}
                {day.detail ? `: ${day.detail}` : ""}
              </li>
            ))}
          </ol>

          <h2>Staff board</h2>
          <dl>
            {staffStats.map((stat) => (
              <div key={stat.label}>
                <dt>{stat.label}</dt>
                <dd>
                  {stat.value} {stat.detail}
                </dd>
              </div>
            ))}
          </dl>
          <p>Updated just now</p>
        </div>
      </section>
    </main>
  );
}
