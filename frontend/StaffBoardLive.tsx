import { useEffect, useMemo, useState } from "react";
import "./staff-board-live.css";

const API_BASE = import.meta.env.VITE_STAFF_API ?? "http://127.0.0.1:7879";
const POLL_MS = 15_000;

type Urgency = "info" | "soon" | "now";
type CardType = "housekeeping" | "room_service" | "arrival";

type Card = {
  room: string;
  type: CardType;
  urgency: Urgency;
  action_line: string;
  reasoning: string;
  deadline_ts: number | null;
};

type Health = {
  ok: boolean;
  apis: { aviationstack: string; google_maps: string };
  rooms_tracked: number;
};

const COLUMN_TITLES: Record<CardType, string> = {
  housekeeping: "Housekeeping",
  room_service: "Room Service",
  arrival: "Arrivals",
};

const COLUMN_ORDER: CardType[] = ["housekeeping", "room_service", "arrival"];

function fmtTime(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function fmtDelta(ts: number | null): string {
  if (!ts) return "";
  const delta = Math.round((ts - Date.now() / 1000) / 60);
  if (delta <= 0) return `${-delta} min ago`;
  if (delta < 60) return `in ${delta} min`;
  return `in ${Math.floor(delta / 60)}h ${delta % 60}m`;
}

export default function StaffBoardLive() {
  const [cards, setCards] = useState<Card[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [lastTick, setLastTick] = useState<number>(Date.now());

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const [feed, hp] = await Promise.all([
          fetch(`${API_BASE}/staff-feed`).then((r) => r.json()),
          fetch(`${API_BASE}/health`).then((r) => r.json()),
        ]);
        if (cancelled) return;
        setCards(feed as Card[]);
        setHealth(hp as Health);
        setErr(null);
        setLastTick(Date.now());
      } catch (e) {
        if (!cancelled) setErr(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const grouped = useMemo(() => {
    const out: Record<CardType, Card[]> = {
      housekeeping: [],
      room_service: [],
      arrival: [],
    };
    for (const c of cards) out[c.type]?.push(c);
    return out;
  }, [cards]);

  return (
    <div className="sb-shell">
      <header className="sb-header">
        <h1>Benney Prism · Staff Board</h1>
        <div className="sb-meta">
          <span>{health?.rooms_tracked ?? 0} rooms tracked</span>
          <span className="sb-dot" />
          <span>flights: {health?.apis.aviationstack ?? "?"}</span>
          <span className="sb-dot" />
          <span>traffic: {health?.apis.google_maps ?? "?"}</span>
          <span className="sb-dot" />
          <span>{new Date(lastTick).toLocaleTimeString()}</span>
        </div>
      </header>

      {err && <div className="sb-error">connection lost — {err}</div>}

      <div className="sb-board">
        {COLUMN_ORDER.map((col) => (
          <section key={col} className={`sb-col sb-col-${col}`}>
            <h2>
              {COLUMN_TITLES[col]}
              <span className="sb-count">{grouped[col].length}</span>
            </h2>
            <div className="sb-cards">
              {grouped[col].length === 0 && !loading && (
                <div className="sb-empty">all clear</div>
              )}
              {grouped[col].map((c, i) => (
                <article
                  key={`${c.room}-${c.type}-${i}`}
                  className={`sb-card sb-urg-${c.urgency}`}
                >
                  <header>
                    <span className="sb-room">{c.room}</span>
                    <span className="sb-deadline">
                      {c.deadline_ts ? fmtTime(c.deadline_ts) : "—"}
                      {c.deadline_ts && (
                        <em>· {fmtDelta(c.deadline_ts)}</em>
                      )}
                    </span>
                  </header>
                  <p className="sb-action">{c.action_line}</p>
                  <p className="sb-reasoning">{c.reasoning}</p>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
