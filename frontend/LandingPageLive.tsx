import { useEffect, useState } from "react";
import "./landing-page-live.css";

const API_BASE = import.meta.env.VITE_BENNEY_API ?? "http://127.0.0.1:7878";

type PersonaFamily = {
  group_type: string; adult_count: number; kid_ages: string;
  trip_purpose: string; budget_tier: string; trip_length_days: number;
  pace: string; primary_interest: string; secondary_interest: string;
  crowd_tolerance: string; energy: string; local_interaction: string;
  mobility: string; dietary: string; language_comfort: string;
};
type PersonaEntry = { family: PersonaFamily; must_include_guidance: string };
type PersonasPayload = { personas: Record<string, PersonaEntry> };

function pretty(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function LandingPageLive() {
  const [personas, setPersonas] = useState<Record<string, PersonaEntry>>({});
  const [selected, setSelected] = useState<string | null>(() => {
    try { return localStorage.getItem("benney_persona_key"); } catch { return null; }
  });
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/personas`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/personas ${r.status}`))))
      .then((d: PersonasPayload) => setPersonas(d.personas || {}))
      .catch((e) => setLoadError(String(e)));
  }, []);

  const choosePersona = (key: string) => {
    const entry = personas[key];
    if (!entry) return;
    try {
      localStorage.setItem("benney_persona_key", key);
      localStorage.setItem("benney_family", JSON.stringify(entry.family));
    } catch {}
    setSelected(key);
  };

  const personaKeys = Object.keys(personas).sort();

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
            <small>{selected ? `as ${pretty(selected)}` : "voice + cat + everything else"}</small>
          </a>
          <a className="lp-action lp-action-staff" href="/?staff=1">
            <span>Staff Board</span>
            <small>Live requests</small>
          </a>
        </nav>

        <div className="lp-semantic">
          <p>Rosewood Sand Hill</p>
          <h1 id="benney-title">Benney</h1>
          <p>A voice-first stay assistant.</p>
          <p>Pick a guest persona below — your choice carries into the itinerary planner, the families network, and Benney's voice context.</p>
        </div>

        <section className="lp-persona-picker" aria-label="Choose a guest persona">
          <header>
            <h2>Choose a guest</h2>
            <p>
              {loadError ? `couldn't load personas: ${loadError}` :
                personaKeys.length === 0 ? "Loading 40 personas…" :
                selected ? `selected: ${pretty(selected)} — tap another to change` :
                `${personaKeys.length} personas — tap one`}
            </p>
          </header>
          <div className="lp-persona-grid">
            {personaKeys.map((key) => {
              const p = personas[key];
              const fam = p.family;
              return (
                <button
                  key={key}
                  type="button"
                  className={`lp-persona ${selected === key ? "lp-persona-selected" : ""}`}
                  onClick={() => choosePersona(key)}
                >
                  <span className="lp-persona-name">{pretty(key)}</span>
                  <span className="lp-persona-meta">
                    {fam.group_type} · {fam.budget_tier} · {fam.primary_interest}+{fam.secondary_interest}
                  </span>
                  <span className="lp-persona-extra">
                    {fam.kid_ages !== "none" ? `kids ${fam.kid_ages}` : "no kids"} ·{" "}
                    {fam.mobility !== "full" ? `mobility ${fam.mobility}` : `${fam.pace} pace`}
                    {fam.dietary !== "none" ? ` · ${fam.dietary}` : ""}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      </section>
    </main>
  );
}
