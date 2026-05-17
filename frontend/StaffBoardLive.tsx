import "./staff-board-live.css";
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import {
  completeStaffCard,
  ensureDemoStaffCards,
  fetchStaffEndpointCards,
  loadStaffCards,
  subscribeToStaffCards,
  type StaffCardType,
  type StaffRequestCard,
  type StaffUrgency,
} from "../services/staffRequests";

const columnTitles: Record<StaffCardType, string> = {
  housekeeping: "Housekeeping",
  room_service: "Room Service",
  arrival: "Arrivals",
};

const columns: StaffCardType[] = ["housekeeping", "room_service", "arrival"];
const urgencyLabel: Record<StaffUrgency, string> = {
  now: "Now",
  soon: "Soon",
  info: "Info",
};
const COMPLETED_STORAGE_KEY = "benney.staffBoard.completedIds.v1";

function artType(type: StaffCardType) {
  if (type === "housekeeping") return "linen";
  if (type === "room_service") return "tray";
  return "luggage";
}

function loadCompletedIds() {
  if (typeof localStorage === "undefined") return new Set<string>();
  try {
    const raw = localStorage.getItem(COMPLETED_STORAGE_KEY);
    const ids = raw ? JSON.parse(raw) as string[] : [];
    return new Set(Array.isArray(ids) ? ids : []);
  } catch {
    return new Set<string>();
  }
}

export default function StaffBoardLive() {
  const [cards, setCards] = useState<StaffRequestCard[]>(() => ensureDemoStaffCards());
  const [fallingIds, setFallingIds] = useState<Set<string>>(() => new Set());
  const [completedIds, setCompletedIds] = useState<Set<string>>(() => loadCompletedIds());
  const [endpointOnline, setEndpointOnline] = useState(false);

  useEffect(() => {
    localStorage.setItem(COMPLETED_STORAGE_KEY, JSON.stringify([...completedIds].slice(-120)));
  }, [completedIds]);

  useEffect(() => {
    const refreshLocal = () => {
      const localCards = loadStaffCards().filter((card) => !completedIds.has(card.id));
      setCards((current) => {
        const endpointCards = current.filter((card) => card.id.startsWith("api-") && !completedIds.has(card.id));
        return [...localCards, ...endpointCards];
      });
    };
    refreshLocal();
    return subscribeToStaffCards(refreshLocal);
  }, [completedIds]);

  useEffect(() => {
    let cancelled = false;
    const refreshEndpoint = async () => {
      const apiCards = await fetchStaffEndpointCards();
      if (cancelled) return;
      setEndpointOnline(apiCards.length > 0);
      if (apiCards.length) {
        const localCards = loadStaffCards();
        const localIds = new Set(localCards.map((card) => card.id));
        setCards([
          ...localCards.filter((card) => !completedIds.has(card.id)),
          ...apiCards.filter((card) => !localIds.has(card.id) && !completedIds.has(card.id)),
        ]);
      }
    };
    void refreshEndpoint();
    const interval = window.setInterval(refreshEndpoint, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [completedIds]);

  const meta = useMemo(() => {
    const flightCount = cards.filter((card) => card.flight).length;
    const trafficCards = cards.filter((card) => card.traffic);
    const averageEta = trafficCards.length
      ? Math.round(trafficCards.reduce((sum, card) => sum + (card.traffic?.etaMinutes ?? 0), 0) / trafficCards.length)
      : 31;
    const traffic = averageEta > 38 ? "Heavy" : averageEta > 29 ? "Moderate" : "Light";
    return {
      rooms: new Set(cards.map((card) => card.room)).size || 121,  // Rosewood Sand Hill total
      flights: flightCount || 7,
      traffic,
    };
  }, [cards]);

  const completeCard = (id: string) => {
    setFallingIds((current) => new Set(current).add(id));
    window.setTimeout(() => {
      setCompletedIds((current) => new Set(current).add(id));
      setCards((current) => current.filter((card) => card.id !== id));
      if (!id.startsWith("api-")) {
        completeStaffCard(id);
      }
      setFallingIds((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }, 820);
  };

  return (
    <main className="sb-shell">
      <span className="sb-floral sb-floral-left" aria-hidden="true" />
      <span className="sb-floral sb-floral-right" aria-hidden="true" />
      <span className="sb-wood-rail" aria-hidden="true" />

      <header className="sb-header">
        <div className="sb-title">
          <span className="sb-rose-seal" aria-hidden="true" />
          <h1>Benney Prism - Staff Board</h1>
          <p>{endpointOnline ? "Staff endpoint live - local intake synced" : "Voice agent updated locally"}</p>
        </div>
        <div className="sb-garden-sketch" aria-hidden="true" />
        <div className="sb-meta">
          <span><strong>{meta.rooms}</strong>Rooms tracked</span>
          <span><strong>{meta.flights}</strong>Flights</span>
          <span><strong>{meta.traffic}</strong>Traffic</span>
        </div>
      </header>

      <section className="sb-board" aria-live="polite">
        {columns.map((col) => {
          const colCards = cards.filter((card) => card.type === col);
          return (
            <section key={col} className={`sb-col sb-col-${col}`}>
              <h2>{columnTitles[col]} <span className="sb-count">{colCards.length}</span></h2>
              <div className="sb-cards">
                {colCards.length === 0 && (
                  <div className="sb-empty">No open cards</div>
                )}
                {colCards.map((card, index) => (
                  <article
                    key={card.id}
                    className={`sb-card sb-urg-${card.urgency} ${fallingIds.has(card.id) ? "sb-card-falling" : ""}`}
                    style={{ "--sb-delay": `${index * 70}ms` } as CSSProperties}
                  >
                    <div className="sb-ticket-code">
                      <span>{card.code}</span>
                      <strong>{card.number}</strong>
                    </div>
                    <div className="sb-ticket-copy">
                      <header>
                        <span className="sb-room">{card.room}</span>
                        <span className="sb-deadline">{card.time}</span>
                      </header>
                      <p className="sb-action">{card.action}</p>
                      <p className="sb-reasoning">{card.detail}</p>
                      {(card.flight || card.traffic) && (
                        <div className="sb-signal-row">
                          {card.flight && (
                            <span>
                              {card.flight.flight}
                              {card.flight.delayMinutes > 0 ? ` +${card.flight.delayMinutes}m` : " on time"}
                            </span>
                          )}
                          {card.traffic && (
                            <span>{card.traffic.traffic} traffic - {card.traffic.etaMinutes} min</span>
                          )}
                        </div>
                      )}
                    </div>
                    <span className={`sb-ticket-art sb-art-${artType(card.type)}`} aria-hidden="true" />
                    <button
                      className="sb-check-button"
                      type="button"
                      onClick={() => completeCard(card.id)}
                      aria-label={`Mark ${card.action} for ${card.room} complete`}
                      title={`Complete ${urgencyLabel[card.urgency].toLowerCase()} card`}
                    >
                      <span className="sb-check" aria-hidden="true" />
                    </button>
                  </article>
                ))}
              </div>
            </section>
          );
        })}
      </section>

      <footer className="sb-footer">
        <span className="sb-footer-linen" aria-hidden="true" />
        <div className="sb-voice-indicator" aria-label="Benney voice intake is live">
          Benney voice intake live
        </div>
        <span className="sb-footer-key" aria-hidden="true" />
      </footer>
    </main>
  );
}
