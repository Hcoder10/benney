import "./landing-page-live.css";

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

export default function LandingPageLive() {
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
            <span>Voice Agent</span>
            <small>Talk to Benney</small>
          </a>
          <a className="lp-action lp-action-staff" href="/?staff=1">
            <span>Staff Board</span>
            <small>Live requests</small>
          </a>
        </nav>

        <div className="lp-semantic">
          <p>Rosewood Sand Hill</p>
          <h1 id="benney-title">Benney</h1>
          <p>A voice-first stay assistant</p>
          <p>Ask for plans, service, and calm answers.</p>
          <p>Listening...</p>

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
          <p>How may I help you today?</p>
        </div>
      </section>
    </main>
  );
}
