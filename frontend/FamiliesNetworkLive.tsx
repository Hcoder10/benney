import { FormEvent, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Send, Sparkles } from "lucide-react";
import "./families-network-live.css";

const API_BASE = import.meta.env.VITE_BENNEY_API ?? "http://127.0.0.1:7878";

type CohortFamily = {
  family_id: string;
  archetype: string;
  family: Record<string, string | number> | null;
  sample_activities: string[];
  sample_names: string[];
};

type CohortPayload = {
  archetypes: string[];
  by_archetype: Record<string, CohortFamily[]>;
};

type SimFamily = CohortFamily & {
  name: string;
  members: string;
  keywords: string[];
  choices: string[];
  note: string;
  x: number;
  y: number;
};

type ChatLine = { role: "family" | "guest"; text: string; isFallback?: boolean };

function prettyArchetype(arch: string): string {
  return arch.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function familyName(arch: string, idx: number): string {
  // Pick a recognizable surname based on archetype + index
  const surnames = ["Alvarez","Kim","Whitaker","Patel","Okafor","Moreau","Bennett","Chen","Singh","Costa","Nakamura","Schmidt","Rossi","Andersson","Park","Garcia","Reyes","Yamamoto","Mensah","Diaz"];
  return `The ${surnames[idx % surnames.length]}s`;
}

function familyMembers(fam: CohortFamily): string {
  const f = fam.family;
  if (!f) return "Synthetic family";
  const group = String(f.group_type ?? "guests");
  const adults = Number(f.adult_count ?? 2);
  const kids = String(f.kid_ages ?? "none");
  if (kids === "none") return `${group}, ${adults} adult${adults === 1 ? "" : "s"}`;
  return `${group}, ${adults} adults, kids ${kids}`;
}

function deriveKeywords(fam: CohortFamily): string[] {
  const f = fam.family;
  if (!f) return [prettyArchetype(fam.archetype)];
  const keep = ["budget_tier","primary_interest","secondary_interest","pace","energy","mobility","dietary"]
    .map((k) => String(f[k] ?? "")).filter((v) => v && v !== "none" && v !== "full");
  return keep.slice(0, 5);
}

// Place archetypes on an oval ring; families jitter around their archetype center.
function layoutCohort(payload: CohortPayload): SimFamily[] {
  const archetypes = payload.archetypes;
  const out: SimFamily[] = [];
  const N = archetypes.length || 1;
  const cx = 50, cy = 50;
  const rx = 38, ry = 32;
  archetypes.forEach((arch, ai) => {
    const angle = (ai / N) * Math.PI * 2 - Math.PI / 2;
    const ax = cx + rx * Math.cos(angle);
    const ay = cy + ry * Math.sin(angle);
    const families = payload.by_archetype[arch] || [];
    families.forEach((fam, fi) => {
      const jitter = 3.5;
      const ja = ((ai * 7 + fi * 13) % 360) * (Math.PI / 180);
      const x = ax + jitter * Math.cos(ja);
      const y = ay + jitter * Math.sin(ja);
      out.push({
        ...fam,
        name: familyName(arch, fi),
        members: familyMembers(fam),
        keywords: deriveKeywords(fam),
        choices: fam.sample_names.slice(0, 3),
        note: `${prettyArchetype(arch)} - day 1 starts with ${fam.sample_names[0] ?? "?"}.`,
        x,
        y,
      });
    });
  });
  return out;
}

function fallbackReply(fam: SimFamily, message: string) {
  const choice = (fam.choices[0] ?? "their morning pick").toLowerCase();
  const keyword = (fam.keywords[0] ?? prettyArchetype(fam.archetype)).toLowerCase();
  const prompt = message.trim() || "your plan";
  return `${fam.name} (a ${prettyArchetype(fam.archetype)} family) would answer: for "${prompt}", they'd keep the ${keyword} lens and likely preserve ${choice}.`;
}

export default function FamiliesNetworkLive() {
  const [families, setFamilies] = useState<SimFamily[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<"idle" | "fallback">("idle");
  const [chat, setChat] = useState<Record<string, ChatLine[]>>({});
  const [visibleCount, setVisibleCount] = useState(0);

  // Fetch the real cohort sample on mount.
  useEffect(() => {
    fetch(`${API_BASE}/cohort-sample?per_archetype=3`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/cohort-sample ${r.status}`))))
      .then((data: CohortPayload) => {
        const laid = layoutCohort(data);
        setFamilies(laid);
        if (laid.length) {
          setActiveId(laid[0].family_id);
          const seed: Record<string, ChatLine[]> = {};
          laid.forEach((f) => {
            seed[f.family_id] = [{ role: "family",
              text: `Hi, we are ${f.name}. We're a ${prettyArchetype(f.archetype)} family — ask why we chose ${(f.choices[0] ?? "our day-1 pick").toLowerCase()}.` }];
          });
          setChat(seed);
        }
      })
      .catch((e) => setLoadError(String(e)));
  }, []);

  // Stagger reveal
  useEffect(() => {
    if (families.length === 0) return;
    setVisibleCount(0);
    let next = 0;
    const timer = window.setInterval(() => {
      next += 1;
      setVisibleCount(Math.min(next, families.length));
      if (next >= families.length) window.clearInterval(timer);
    }, 35);
    return () => window.clearInterval(timer);
  }, [families.length]);

  const activeFamily = useMemo(
    () => families.find((f) => f.family_id === activeId) ?? null,
    [families, activeId],
  );
  const visibleFamilies = families.slice(0, visibleCount);
  const activeChat = activeId ? (chat[activeId] ?? []) : [];

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || busy || !activeFamily) return;
    const outbound: ChatLine = { role: "guest", text: trimmed };
    setMessage("");
    setBusy(true);
    setStatus("idle");
    setChat((current) => ({
      ...current,
      [activeFamily.family_id]: [...(current[activeFamily.family_id] ?? []), outbound],
    }));

    try {
      const response = await fetch(`${API_BASE}/family-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          family_id: activeFamily.family_id,
          name: activeFamily.name,
          keywords: activeFamily.keywords,
          choices: activeFamily.choices,
          message: trimmed,
        }),
      });
      if (!response.ok) throw new Error(`family-chat ${response.status}`);
      const data = await response.json();
      const reply = String(data.reply ?? data.message ?? data.text ?? "").trim();
      if (!reply) throw new Error("empty reply");
      setChat((current) => ({
        ...current,
        [activeFamily.family_id]: [...(current[activeFamily.family_id] ?? []),
          { role: "family", text: reply }],
      }));
    } catch {
      setStatus("fallback");
      setChat((current) => ({
        ...current,
        [activeFamily.family_id]: [...(current[activeFamily.family_id] ?? []),
          { role: "family", text: fallbackReply(activeFamily, trimmed), isFallback: true }],
      }));
    } finally {
      setBusy(false);
    }
  }

  // Build links between adjacent families in same archetype + a few cross-archetype
  const links = useMemo(() => {
    const out: { from: string; to: string; label: string }[] = [];
    const byArch: Record<string, SimFamily[]> = {};
    families.forEach((f) => {
      (byArch[f.archetype] ??= []).push(f);
    });
    Object.values(byArch).forEach((arr) => {
      for (let i = 0; i < arr.length - 1; i++) {
        out.push({ from: arr[i].family_id, to: arr[i + 1].family_id, label: prettyArchetype(arr[i].archetype) });
      }
    });
    return out;
  }, [families]);

  return (
    <main className="fn-shell">
      <span className="fn-rose fn-rose-left" aria-hidden="true" />
      <span className="fn-rose fn-rose-right" aria-hidden="true" />
      <span className="fn-petal fn-petal-one" aria-hidden="true" />
      <span className="fn-petal fn-petal-two" aria-hidden="true" />

      <section className="fn-network" aria-label="simulated families network">
        <header className="fn-header">
          <span className="fn-eyebrow">Rosewood Sand Hill cohort</span>
          <h1>Families choosing together</h1>
          <p>
            {loadError ? `Cohort offline (${loadError}) — showing nothing yet.` :
              families.length === 0 ? "Loading the 9,867-family cohort…" :
              `${families.length} families from ${new Set(families.map((f) => f.archetype)).size} archetypes — click any to talk.`}
          </p>
        </header>

        <div className="fn-map">
          <svg className="fn-links" viewBox="0 0 100 100" aria-hidden="true" preserveAspectRatio="none">
            {links.map((l, index) => {
              const a = families.find((f) => f.family_id === l.from);
              const b = families.find((f) => f.family_id === l.to);
              if (!a || !b) return null;
              const ready = visibleFamilies.some((f) => f.family_id === l.from)
                         && visibleFamilies.some((f) => f.family_id === l.to);
              return (
                <line
                  key={`${l.from}-${l.to}`}
                  className={ready ? "fn-link fn-link-ready" : "fn-link"}
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  style={{ "--fn-delay": `${index * 8}ms` } as CSSProperties}
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
                "--fn-delay": `${index * 12}ms`,
              } as CSSProperties}
              onClick={() => setActiveId(family.family_id)}
              aria-label={`Chat with ${family.name}`}
              title={`${family.name} — ${prettyArchetype(family.archetype)}`}
            >
              <span className="fn-family-pin" aria-hidden="true" />
              <span className="fn-family-name">{family.name}</span>
              <span className="fn-family-members">{prettyArchetype(family.archetype)}</span>
            </button>
          ))}
        </div>
      </section>

      {activeFamily && (
        <aside className="fn-detail" aria-label="selected family details">
          <div className="fn-family-card">
            <span className="fn-card-sketch" aria-hidden="true" />
            <span className="fn-card-kicker">{prettyArchetype(activeFamily.archetype)}</span>
            <h2>{activeFamily.name}</h2>
            <p>{activeFamily.members}</p>
            <p>{activeFamily.note}</p>
            <div className="fn-keywords">
              {activeFamily.keywords.map((k) => (
                <span key={k}>{k.replace(/_/g, " ")}</span>
              ))}
            </div>
            <div className="fn-choice-list">
              {activeFamily.choices.map((c) => (
                <span key={c}>
                  <Sparkles size={14} aria-hidden="true" />
                  {c}
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
                  key={`${line.role}-${index}-${line.text.slice(0, 20)}`}
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
      )}
    </main>
  );
}
