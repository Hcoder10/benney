export type StaffCardType = "housekeeping" | "room_service" | "arrival";
export type StaffUrgency = "info" | "soon" | "now";

export type StaffTrafficInfo = {
  source: "maps" | "demo";
  from: string;
  to: string;
  etaMinutes: number;
  traffic: "Light" | "Moderate" | "Heavy";
};

export type StaffFlightInfo = {
  source: "api" | "demo";
  flight: string;
  status: "scheduled" | "active" | "landed" | "delayed";
  scheduledArrival: string;
  estimatedArrival: string;
  delayMinutes: number;
  airport: string;
};

export type StaffRequestCard = {
  id: string;
  room: string;
  type: StaffCardType;
  urgency: StaffUrgency;
  code: string;
  number: string;
  action: string;
  detail: string;
  time: string;
  createdAt: number;
  deadlineTs?: number;
  completedAt?: number;
  source: "benney-voice" | "benney-intake" | "staff-api" | "demo";
  traffic?: StaffTrafficInfo;
  flight?: StaffFlightInfo;
  guestPersona?: string;
};

export type StaffRequestInput = {
  room?: string;
  transcript?: string;
  kind?: StaffCardType | "return_time" | "flight";
  action?: string;
  detail?: string;
  foodItems?: string[];
  returnTime?: string;
  flight?: string;
  source?: StaffRequestCard["source"];
  guestPersona?: string;
};

function prettyPersona(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function readSelectedPersonaKey(): string | undefined {
  try {
    return localStorage.getItem("benney_persona_key") || undefined;
  } catch {
    return undefined;
  }
}

function roomForPersona(key: string): string {
  const h = hashText(key);
  const floor = 2 + (h % 4);
  const unit = String(1 + (h % 18)).padStart(2, "0");
  return `Room ${floor}${unit}`;
}

const STORAGE_KEY = "benney.staffBoard.cards.v1";
const CHANNEL_NAME = "benney-staff-board";
const STAFF_API_BASE = import.meta.env.VITE_STAFF_API ?? "http://127.0.0.1:7879";
const DEMO_HOTEL_NAME = "Rosewood Sand Hill";
const DEMO_AIRPORT_NAME = "SFO";

type StaffBroadcastMessage = {
  type: "changed";
  cards: StaffRequestCard[];
};

const memoryListeners = new Set<() => void>();
const channel = typeof BroadcastChannel !== "undefined"
  ? new BroadcastChannel(CHANNEL_NAME)
  : null;

function safeNow() {
  return Date.now();
}

function hashText(value: string) {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function formatClock(ts: number) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(ts));
}

function parseClock(value: string | undefined, now = new Date()) {
  if (!value) return undefined;
  const trimmed = value.trim().toLowerCase();
  const match = trimmed.match(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/);
  if (!match) return undefined;

  let hour = Number(match[1]);
  const minute = Number(match[2] ?? "0");
  const suffix = match[3];
  if (suffix === "pm" && hour < 12) hour += 12;
  if (suffix === "am" && hour === 12) hour = 0;
  if (!suffix && hour < 7) hour += 12;

  const date = new Date(now);
  date.setHours(hour, minute, 0, 0);
  if (date.getTime() < now.getTime() - 10 * 60 * 1000) {
    date.setDate(date.getDate() + 1);
  }
  return date.getTime();
}

function normalizeRoom(room: string | undefined, transcript = "") {
  const raw = room ?? transcript.match(/\b(?:room|suite)\s*([a-z]?\d{2,4})\b/i)?.[1];
  if (!raw) return "Room 304";
  const clean = raw.toUpperCase().replace(/^ROOM\s+/i, "");
  return `Room ${clean}`;
}

function urgencyFor(deadlineTs: number | undefined, createdAt: number): StaffUrgency {
  if (!deadlineTs) return "info";
  const minutes = (deadlineTs - createdAt) / 60000;
  if (minutes <= 15) return "now";
  if (minutes <= 60) return "soon";
  return "info";
}

function nextNumber(type: StaffCardType, existing: StaffRequestCard[]) {
  const base: Record<StaffCardType, number> = {
    housekeeping: 10,
    room_service: 20,
    arrival: 30,
  };
  const max = existing
    .filter((card) => card.type === type)
    .reduce((value, card) => Math.max(value, Number(card.number) || base[type]), base[type]);
  return String(max + 1);
}

function codeFor(type: StaffCardType) {
  if (type === "housekeeping") return "HK";
  if (type === "room_service") return "RS";
  return "AR";
}

function demoTraffic(seed: string): StaffTrafficInfo {
  const hash = hashText(seed);
  const etaMinutes = 24 + (hash % 19);
  const traffic: StaffTrafficInfo["traffic"] = etaMinutes > 38
    ? "Heavy"
    : etaMinutes > 29
      ? "Moderate"
      : "Light";
  return {
    source: "demo",
    from: DEMO_AIRPORT_NAME,
    to: DEMO_HOTEL_NAME,
    etaMinutes,
    traffic,
  };
}

function demoFlight(flight: string, now = safeNow()): StaffFlightInfo {
  const clean = flight.toUpperCase().replace(/\s+/g, "");
  const hash = hashText(clean);
  const scheduled = now + (45 + (hash % 95)) * 60000;
  const delayMinutes = [0, 0, 8, 14, 22, 35][hash % 6];
  const estimated = scheduled + delayMinutes * 60000;
  return {
    source: "demo",
    flight: clean,
    status: delayMinutes > 0 ? "delayed" : "active",
    scheduledArrival: new Date(scheduled).toISOString(),
    estimatedArrival: new Date(estimated).toISOString(),
    delayMinutes,
    airport: DEMO_AIRPORT_NAME,
  };
}

function extractFoodItems(text: string) {
  const match = text.match(/\b(?:order|bring|send|get|request)\s+(.+?)(?:\s+(?:to|for)\s+(?:room|suite)\b|$)/i);
  if (!match) return undefined;
  return match[1]
    .replace(/\b(?:food|room service|please)\b/gi, "")
    .split(/\s*(?:,| and )\s*/i)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 4);
}

function inferInputsFromTranscript(transcript: string): StaffRequestInput[] {
  const text = transcript.trim();
  if (!text) return [];
  const lower = text.toLowerCase();
  const room = normalizeRoom(undefined, text);
  const flightMatch = text.match(/\b([A-Z]{2}|[A-Z]\d|\d[A-Z])\s?(\d{2,4})\b/);
  const returnMatch = lower.match(/\b(?:return|back|arrive back|back at|returning)\b.*?\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b/i);
  const inputs: StaffRequestInput[] = [];

  if (flightMatch && /\b(flight|arriv|landing|delay|eta)\b/i.test(text)) {
    inputs.push({
      kind: "flight",
      room,
      flight: `${flightMatch[1]}${flightMatch[2]}`,
      transcript: text,
      source: "benney-voice",
    });
  }

  if (/\b(clean|cleaning|housekeeping|turndown|turn down|towels|linen|restock)\b/i.test(text)) {
    inputs.push({
      kind: "housekeeping",
      room,
      transcript: text,
      returnTime: returnMatch?.[1],
      source: "benney-voice",
    });
  }

  if (/\b(food|room service|breakfast|dinner|lunch|coffee|espresso|pastr|sandwich|juice|toast|tea)\b/i.test(text)) {
    inputs.push({
      kind: "room_service",
      room,
      foodItems: extractFoodItems(text),
      transcript: text,
      returnTime: returnMatch?.[1],
      source: "benney-voice",
    });
  }

  if (!inputs.length && returnMatch) {
    inputs.push({
      kind: "return_time",
      room,
      returnTime: returnMatch[1],
      transcript: text,
      source: "benney-voice",
    });
  }

  return inputs;
}

function inferInputFromTranscript(transcript: string): StaffRequestInput | null {
  return inferInputsFromTranscript(transcript)[0] ?? null;
}

function readRawCards(): StaffRequestCard[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const cards = JSON.parse(raw) as StaffRequestCard[];
    return Array.isArray(cards) ? cards.filter((card) => !card.completedAt) : [];
  } catch {
    return [];
  }
}

function writeRawCards(cards: StaffRequestCard[]) {
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cards));
  }
  memoryListeners.forEach((listener) => listener());
  channel?.postMessage({ type: "changed", cards } satisfies StaffBroadcastMessage);
}

function toCard(input: StaffRequestInput, existing: StaffRequestCard[]): StaffRequestCard {
  const createdAt = safeNow();
  const transcript = input.transcript ?? "";
  const source = input.source ?? "benney-intake";
  const personaKey = input.guestPersona ?? readSelectedPersonaKey();
  const room = input.room
    ? normalizeRoom(input.room, transcript)
    : transcript && /\b(?:room|suite)\s*([a-z]?\d{2,4})\b/i.test(transcript)
      ? normalizeRoom(undefined, transcript)
      : personaKey
        ? roomForPersona(personaKey)
        : normalizeRoom(input.room, transcript);
  const kind = input.kind ?? inferInputFromTranscript(transcript)?.kind ?? "housekeeping";
  const type: StaffCardType = kind === "flight"
    ? "arrival"
    : kind === "return_time"
      ? "housekeeping"
      : kind;
  const returnTs = parseClock(input.returnTime, new Date(createdAt));
  const flight = input.flight ? demoFlight(input.flight, createdAt) : undefined;
  const traffic = kind === "flight" || kind === "return_time" || returnTs
    ? demoTraffic(`${room}-${input.flight ?? input.returnTime ?? transcript}`)
    : undefined;
  const flightEta = flight ? new Date(flight.estimatedArrival).getTime() + (traffic?.etaMinutes ?? 30) * 60000 : undefined;
  const deadlineTs = returnTs ?? flightEta;
  const urgency = urgencyFor(deadlineTs, createdAt);

  let action = input.action ?? "Refresh room";
  let detail = input.detail ?? "Benney shared a guest request.";

  if (type === "housekeeping") {
    if (kind === "return_time") {
      action = "Clean while guest is out";
      detail = `Guest expects to return ${returnTs ? formatClock(returnTs) : "soon"}. ${traffic ? `${traffic.traffic} traffic, ${traffic.etaMinutes} min ETA to hotel.` : ""}`;
    } else {
      action = /\b(towels|linen|restock)\b/i.test(transcript)
        ? "Refresh towels and amenities"
        : "Housekeeping request from Benney";
      detail = returnTs
        ? `Guest asked before return at ${formatClock(returnTs)}.`
        : "Voice intake flagged room refresh.";
    }
  }

  if (type === "room_service") {
    const items = input.foodItems?.length ? input.foodItems : extractFoodItems(transcript);
    action = items?.length ? `Prepare ${items.join(", ")}` : "Room service request";
    detail = returnTs
      ? `Deliver near guest return at ${formatClock(returnTs)}.`
      : "Benney captured food request from guest.";
  }

  if (type === "arrival") {
    action = flight ? `Track ${flight.flight} arrival` : "Guest arrival update";
    detail = flight
      ? `${flight.delayMinutes ? `${flight.delayMinutes} min delay. ` : "On schedule. "}ETA hotel ${flightEta ? formatClock(flightEta) : "soon"} with ${traffic?.traffic.toLowerCase() ?? "moderate"} traffic.`
      : "Flight details shared by Benney.";
  }

  return {
    id: `staff-${createdAt}-${Math.random().toString(36).slice(2, 8)}`,
    room,
    type,
    urgency,
    code: codeFor(type),
    number: nextNumber(type, existing),
    action,
    detail: detail.trim(),
    time: deadlineTs ? formatClock(deadlineTs) : formatClock(createdAt),
    createdAt,
    deadlineTs,
    source,
    traffic,
    flight,
    guestPersona: personaKey ? prettyPersona(personaKey) : undefined,
  };
}

export function loadStaffCards() {
  return readRawCards().sort((a, b) => {
    const rank: Record<StaffUrgency, number> = { now: 0, soon: 1, info: 2 };
    return rank[a.urgency] - rank[b.urgency] || (a.deadlineTs ?? a.createdAt) - (b.deadlineTs ?? b.createdAt);
  });
}

export function saveStaffCard(input: StaffRequestInput): StaffRequestCard {
  const existing = loadStaffCards();
  const card = toCard(input, existing);
  writeRawCards([card, ...existing].slice(0, 30));
  void mirrorToStaffEndpoint(card);
  return card;
}

export function saveStaffTranscript(transcript: string): StaffRequestCard | null {
  const inputs = inferInputsFromTranscript(transcript);
  if (!inputs.length) return null;
  let firstCard: StaffRequestCard | null = null;
  for (const input of inputs) {
    const card = saveStaffCard(input);
    if (!firstCard) firstCard = card;
  }
  return firstCard;
}

export function completeStaffCard(id: string) {
  const cards = loadStaffCards().filter((card) => card.id !== id);
  writeRawCards(cards);
}

export function subscribeToStaffCards(listener: () => void) {
  memoryListeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) listener();
  };
  const onMessage = (event: MessageEvent<StaffBroadcastMessage>) => {
    if (event.data?.type === "changed") listener();
  };
  window.addEventListener("storage", onStorage);
  channel?.addEventListener("message", onMessage);
  return () => {
    memoryListeners.delete(listener);
    window.removeEventListener("storage", onStorage);
    channel?.removeEventListener("message", onMessage);
  };
}

export async function fetchStaffEndpointCards() {
  try {
    const response = await fetch(`${STAFF_API_BASE}/staff-feed`, { cache: "no-store" });
    if (!response.ok) return [];
    const rows = await response.json() as Array<{
      room: string;
      type: StaffCardType;
      urgency: StaffUrgency;
      action_line: string;
      reasoning: string;
      deadline_ts: number | null;
    }>;
    return rows.map((row, index) => {
      const deadlineTs = row.deadline_ts ? row.deadline_ts * 1000 : undefined;
      return {
        id: `api-${row.room}-${row.type}-${deadlineTs ?? index}`,
        room: row.room,
        type: row.type,
        urgency: row.urgency,
        code: codeFor(row.type),
        number: String(index + 1).padStart(2, "0"),
        action: row.action_line,
        detail: row.reasoning,
        time: deadlineTs ? formatClock(deadlineTs) : "-",
        createdAt: safeNow(),
        deadlineTs,
        source: "staff-api" as const,
      };
    });
  } catch {
    return [];
  }
}

export function ensureDemoStaffCards() {
  const existing = loadStaffCards();
  if (existing.length) return existing;
  const seeds: StaffRequestInput[] = [
    {
      room: "304",
      kind: "housekeeping",
      action: "Prepare turn-down",
      detail: "Guest requested late turndown through Benney.",
      returnTime: "7:20 PM",
      source: "demo",
    },
    {
      room: "217",
      kind: "room_service",
      foodItems: ["espresso", "warm pastries"],
      source: "demo",
    },
    {
      room: "510",
      kind: "flight",
      flight: "UA837",
      source: "demo",
    },
  ];
  const cards = seeds.reduce<StaffRequestCard[]>((acc, seed) => [toCard(seed, acc), ...acc], []);
  writeRawCards(cards);
  return cards;
}

async function mirrorToStaffEndpoint(card: StaffRequestCard) {
  const payload = card.type === "room_service"
    ? { items: [card.action.replace(/^Prepare\s+/i, "")], target_ts: card.deadlineTs ? card.deadlineTs / 1000 : undefined }
    : card.type === "arrival"
      ? { flight: card.flight?.flight, eta_ts: card.deadlineTs ? card.deadlineTs / 1000 : undefined }
      : { expected_return_ts: card.deadlineTs ? card.deadlineTs / 1000 : undefined, note: card.detail };
  const eventType = card.type === "room_service"
    ? "food_order"
    : card.type === "arrival"
      ? "flight_update"
      : "override";
  try {
    await fetch(`${STAFF_API_BASE}/event`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        room: card.room.replace(/^Room\s+/i, ""),
        type: eventType,
        ts: card.createdAt / 1000,
        payload,
      }),
    });
  } catch {
    // Local storage is the source of truth for the demo; endpoint mirroring is best effort.
  }
}

declare global {
  interface Window {
    benneyStaffRequest?: (input: StaffRequestInput | string) => StaffRequestCard | null;
    benneyStaffCards?: () => StaffRequestCard[];
  }
}

if (typeof window !== "undefined") {
  window.benneyStaffRequest = (input) => (
    typeof input === "string" ? saveStaffTranscript(input) : saveStaffCard(input)
  );
  window.benneyStaffCards = loadStaffCards;
}
