import { FormEvent, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Send, Sparkles } from "lucide-react";
import "./families-network-live.css";

const API_BASE = import.meta.env.VITE_BENNEY_API ?? "http://127.0.0.1:7878";

type SimFamily = {
  family_id: string;
  name: string;
  members: string;
  keywords: string[];
  choices: string[];
  note: string;
  x: number;
  y: number;
};

type ChatLine = {
  role: "family" | "guest";
  text: string;
  isFallback?: boolean;
};

const families: SimFamily[] = [
  {
    family_id: "fam-rose-01",
    name: "The Alvarez Family",
    members: "Two parents, ages 8 and 11",
    keywords: ["garden calm", "pool", "kid-friendly food"],
    choices: ["Filoli morning walk", "Cabanas after lunch", "Early Madera dinner"],
    note: "Chose a gentle first day after a red-eye and kept dinner close to the room.",
    x: 16,
    y: 20,
  },
  {
    family_id: "fam-rose-02",
    name: "The Kims",
    members: "Grandparents, parents, toddler",
    keywords: ["shade", "stroller", "multigenerational"],
    choices: ["Terrace breakfast", "Palo Alto art stroll", "In-room dessert"],
    note: "Optimized for short walks, shaded pauses, and easy exits.",
    x: 44,
    y: 13,
  },
  {
    family_id: "fam-rose-03",
    name: "The Whitakers",
    members: "Parents and teen siblings",
    keywords: ["tennis", "design", "coffee"],
    choices: ["Morning tennis clinic", "Sightglass coffee", "Stanford Cantor Center"],
    note: "Balanced activity for the teens with polished, design-forward stops.",
    x: 76,
    y: 25,
  },
  {
    family_id: "fam-rose-04",
    name: "The Patels",
    members: "Parents, aunt, ages 6 and 9",
    keywords: ["vegetarian", "photos", "low crowds"],
    choices: ["Garden portraits", "Vegetarian tasting notes", "Quiet pool hour"],
    note: "Picked scenic moments that would not overfill the day.",
    x: 29,
    y: 57,
  },
  {
    family_id: "fam-rose-05",
    name: "The Okafors",
    members: "Parent, uncle, ages 10 and 13",
    keywords: ["science", "outdoors", "curious kids"],
    choices: ["Computer History Museum", "Baylands boardwalk", "Hot chocolate at dusk"],
    note: "Followed the kids' curiosity, then softened the evening.",
    x: 61,
    y: 55,
  },
  {
    family_id: "fam-rose-06",
    name: "The Moreaus",
    members: "Parents and infant twins",
    keywords: ["nap windows", "room service", "quiet"],
    choices: ["Breakfast in-room", "Courtyard stroll", "Chef's picnic basket"],
    note: "Protected nap windows and let the hotel do the heavy lifting.",
    x: 83,
    y: 70,
  },
];

const links = [
  ["fam-rose-01", "fam-rose-02", "shared first-day recovery"],
  ["fam-rose-01", "fam-rose-04", "kid-friendly dining"],
  ["fam-rose-02", "fam-rose-06", "quiet pacing"],
  ["fam-rose-03", "fam-rose-05", "curiosity-led afternoon"],
  ["fam-rose-04", "fam-rose-05", "outdoor photo stops"],
  ["fam-rose-05", "fam-rose-06", "low-friction evening"],
] as const;

function familyById(id: string) {
  return families.find((family) => family.family_id === id) ?? families[0];
}

function fallbackReply(family: SimFamily, message: string) {
  const choice = family.choices[0].toLowerCase();
  const keyword = family.keywords[0].toLowerCase();
  const prompt = message.trim() || "your plan";
  return `${family.name} would answer from the simulated profile: for "${prompt}", they would keep the ${keyword} lens and likely preserve ${choice}.`;
}

export default function FamiliesNetworkLive() {
  const [visibleCount, setVisibleCount] = useState(0);
  const [activeId, setActiveId] = useState(families[0].family_id);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<"idle" | "fallback">("idle");
  const [chat, setChat] = useState<Record<string, ChatLine[]>>(() =>
    Object.fromEntries(
      families.map((family) => [
        family.family_id,
        [{ role: "family", text: `Hi, we are ${family.name}. Ask why we chose ${family.choices[0].toLowerCase()}.` }],
      ]),
    ),
  );

  const activeFamily = useMemo(() => familyById(activeId), [activeId]);
  const visibleFamilies = families.slice(0, visibleCount);
  const activeChat = chat[activeId] ?? [];

  useEffect(() => {
    let next = 0;
    const timer = window.setInterval(() => {
      next += 1;
      setVisibleCount(Math.min(next, families.length));
      if (next >= families.length) window.clearInterval(timer);
    }, 520);

    return () => window.clearInterval(timer);
  }, []);

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || busy) return;

    const outbound: ChatLine = { role: "guest", text: trimmed };
    setMessage("");
    setBusy(true);
    setStatus("idle");
    setChat((current) => ({
      ...current,
      [activeId]: [...(current[activeId] ?? []), outbound],
    }));

    const body = {
      family_id: activeFamily.family_id,
      name: activeFamily.name,
      keywords: activeFamily.keywords,
      choices: activeFamily.choices,
      message: trimmed,
    };

    try {
      const response = await fetch(`${API_BASE}/family-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(`family-chat ${response.status}`);
      const data = await response.json();
      const reply = String(data.reply ?? data.message ?? data.text ?? "").trim();
      if (!reply) throw new Error("family-chat empty reply");
      setChat((current) => ({
        ...current,
        [activeId]: [...(current[activeId] ?? []), { role: "family", text: reply }],
      }));
    } catch {
      setStatus("fallback");
      setChat((current) => ({
        ...current,
        [activeId]: [
          ...(current[activeId] ?? []),
          { role: "family", text: fallbackReply(activeFamily, trimmed), isFallback: true },
        ],
      }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="fn-shell">
      <span className="fn-rose fn-rose-left" aria-hidden="true" />
      <span className="fn-rose fn-rose-right" aria-hidden="true" />
      <span className="fn-petal fn-petal-one" aria-hidden="true" />
      <span className="fn-petal fn-petal-two" aria-hidden="true" />

      <section className="fn-network" aria-label="simulated families network">
        <header className="fn-header">
          <span className="fn-eyebrow">Rosewood Sand Hill simulation</span>
          <h1>Families choosing together</h1>
          <p>
            Each folio appears as Benney discovers an adjacent family pattern: pace, needs, and what they actually chose to do.
          </p>
        </header>

        <div className="fn-map">
          <svg className="fn-links" viewBox="0 0 100 100" aria-hidden="true" preserveAspectRatio="none">
            {links.map(([from, to, label], index) => {
              const a = familyById(from);
              const b = familyById(to);
              const ready = visibleFamilies.some((family) => family.family_id === from) &&
                visibleFamilies.some((family) => family.family_id === to);
              return (
                <line
                  key={label}
                  className={ready ? "fn-link fn-link-ready" : "fn-link"}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  style={{ "--fn-delay": `${index * 95}ms` } as CSSProperties}
                />
              );
            })}
          </svg>

          {visibleFamilies.map((family, index) => (
            <button
              key={family.family_id}
              type="button"
              className={`fn-family ${family.family_id === activeId ? "fn-family-active" : ""}`}
              style={{
                left: `${family.x}%`,
                top: `${family.y}%`,
                "--fn-delay": `${index * 90}ms`,
              } as CSSProperties}
              onClick={() => setActiveId(family.family_id)}
              aria-label={`Chat with ${family.name}`}
            >
              <span className="fn-family-pin" aria-hidden="true" />
              <span className="fn-family-name">{family.name}</span>
              <span className="fn-family-members">{family.members}</span>
              <span className="fn-family-choice">{family.choices[0]}</span>
            </button>
          ))}
        </div>
      </section>

      <aside className="fn-detail" aria-label="selected family details">
        <div className="fn-family-card">
          <span className="fn-card-sketch" aria-hidden="true" />
          <span className="fn-card-kicker">Selected family</span>
          <h2>{activeFamily.name}</h2>
          <p>{activeFamily.note}</p>
          <div className="fn-keywords">
            {activeFamily.keywords.map((keyword) => (
              <span key={keyword}>{keyword}</span>
            ))}
          </div>
          <div className="fn-choice-list">
            {activeFamily.choices.map((choice) => (
              <span key={choice}>
                <Sparkles size={14} aria-hidden="true" />
                {choice}
              </span>
            ))}
          </div>
        </div>

        <section className="fn-chat" aria-label={`Chat with ${activeFamily.name}`}>
          <div className="fn-chat-head">
            <div>
              <span>Family chat</span>
              <strong>{activeFamily.name}</strong>
            </div>
            {status === "fallback" && <em>offline stub</em>}
          </div>

          <div className="fn-chat-log" aria-live="polite">
            {activeChat.map((line, index) => (
              <p
                key={`${line.role}-${index}-${line.text}`}
                className={`fn-chat-line fn-chat-${line.role} ${line.isFallback ? "fn-chat-fallback" : ""}`}
              >
                {line.text}
              </p>
            ))}
          </div>

          <form className="fn-chat-form" onSubmit={sendMessage}>
            <input
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder={`Ask ${activeFamily.name.split(" ")[1] ?? "them"} about their choices`}
              aria-label="Message to selected family"
            />
            <button type="submit" disabled={busy || !message.trim()} aria-label="Send message">
              <Send size={18} aria-hidden="true" />
            </button>
          </form>
        </section>
      </aside>
    </main>
  );
}
