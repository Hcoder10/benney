#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

// ── Slot-type rules ───────────────────────────────────────────────────────────
const SLOT_RULES = [
  { name: 'early-morning',    window: [7,  9],  tags: ['cafe','coffee','bakery','breakfast'] },
  { name: 'breakfast',        window: [8,  10], tags: ['cafe','coffee','bakery','breakfast','brunch'] },
  { name: 'late-morning',     window: [10, 13], tags: ['museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'] },
  { name: 'lunch+afternoon',  window: [12, 17], tags: ['restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'] },
  { name: 'evening',          window: [17, 20], tags: ['restaurant','dinner','fine-dining','fine dining','scenic','sunset','wine','casual'] },
  { name: 'night',            window: [20, 23], tags: ['bar','cocktails','nightlife','lounge','dinner','fine-dining','fine dining','speakeasy'] },
];

// ── Budget tier ordering ──────────────────────────────────────────────────────
const TIERS = ['shoestring', 'mid', 'premium', 'luxury'];

// ── Tag normalisation ─────────────────────────────────────────────────────────
// Normalise a tag: lower-case, collapse spaces to hyphens
function normTag(t) {
  return t.toLowerCase().replace(/\s+/g, '-');
}

function tagsMatch(actTags, requiredTags) {
  const normAct = (actTags || []).map(normTag);
  const normReq  = requiredTags.map(normTag);
  return normAct.some(t => normReq.includes(t));
}

// ── Hour parsing ──────────────────────────────────────────────────────────────
// Returns fractional 24-hour value, or null on failure.
function parseHour(s) {
  if (!s || typeof s !== 'string') return null;
  s = s.trim();

  // 12-hour with AM/PM  e.g. "5:30 PM", "9:00 AM", "11 AM"
  let m = s.match(/^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$/i);
  if (m) {
    let h   = parseInt(m[1], 10);
    let min = m[2] ? parseInt(m[2], 10) : 0;
    let mer = m[3].toUpperCase();
    if (mer === 'PM' && h !== 12) h += 12;
    if (mer === 'AM' && h === 12) h = 0;
    return h + min / 60;
  }

  // 24-hour HH:MM  e.g. "17:30", "09:00", "00:00"
  m = s.match(/^(\d{1,2}):(\d{2})$/);
  if (m) return parseInt(m[1], 10) + parseInt(m[2], 10) / 60;

  // Bare hour  e.g. "8", "17"
  m = s.match(/^(\d{1,2})$/);
  if (m) return parseInt(m[1], 10);

  return null;
}

// Returns a [startH, endH] pair from one session string like "17:30-22:30"
// or "5:30 PM - 9:00 PM", or null if unparseable.
function parseSession(session) {
  session = session.trim();

  // Whole-day tokens
  if (/^24h$/i.test(session) || session === '24/7') return [0, 24];
  if (/^closed$/i.test(session)) return null;

  // "Sunrise to sunset" and "sunrise-sunset"
  if (/sunrise\s+to\s+sunset/i.test(session)) return [6, 20];
  if (/^sunrise[-\s]+sunset$/i.test(session))  return [6, 20];

  // Strings ending in "sunset"
  const sunsetEnd = session.match(/^(.+?)(?:\s+-\s+|\s*-\s*)sunset$/i);
  if (sunsetEnd) {
    const sh = parseHour(sunsetEnd[1].trim());
    if (sh != null) return [sh, 20];
  }

  // Strings starting with "sunrise"
  const sunriseStart = session.match(/^sunrise[-\s]+(.+)$/i);
  if (sunriseStart) {
    const eh = parseHour(sunriseStart[1].trim());
    if (eh != null) return [6, eh];
  }

  // Split on " - " (with spaces) first
  let parts = session.split(/\s+-\s+/);
  if (parts.length === 2) {
    const sh = parseHour(parts[0].trim());
    const eh = parseHour(parts[1].trim());
    if (sh != null && eh != null) return [sh, eh];
  }

  // Split on bare "-" — match leading time, then hyphen, then trailing time
  const m = session.match(
    /^(\d{1,2}(?::\d{2})?(?:\s*[AP]M)?)\s*-\s*(\d{1,2}(?::\d{2})?(?:\s*[AP]M)?)$/i
  );
  if (m) {
    const sh = parseHour(m[1].trim());
    const eh = parseHour(m[2].trim());
    if (sh != null && eh != null) return [sh, eh];
  }

  return null;
}

// Returns true if the activity's open_hours string overlaps the slot window [winStart, winEnd).
function isOpenDuringSlot(openHoursStr, winStart, winEnd) {
  if (!openHoursStr || typeof openHoursStr !== 'string') return false;
  const s = openHoursStr.trim();

  // Special all-day tokens
  if (/^24h$/i.test(s) || s === '24/7') return true;
  if (/^closed$/i.test(s))              return false;
  if (/^varies$/i.test(s))              return true; // treat as open (conservative)

  // "Sunrise to sunset" and variants
  if (/sunrise\s+to\s+sunset/i.test(s)) return winStart < 20 && winEnd > 6;
  if (/^sunrise-sunset$/i.test(s))      return winStart < 20 && winEnd > 6;

  // Split on comma or semicolon to handle multi-session strings
  const sessions = s.split(/[,;]/).map(p => p.trim()).filter(Boolean);

  for (const session of sessions) {
    const parsed = parseSession(session);
    if (!parsed) continue;

    let [sh, eh] = parsed;
    // Handle midnight-crossing (e.g. 16:00-00:00 → 16:00-24:00)
    if (eh <= sh) eh += 24;

    // Overlap: [sh, eh) ∩ [winStart, winEnd) != empty iff sh < winEnd && eh > winStart
    if (sh < winEnd && eh > winStart) return true;
  }
  return false;
}

// ── Activity validity ─────────────────────────────────────────────────────────
function isCafe(act) {
  return (act.tags || []).map(normTag).some(t =>
    ['cafe','coffee','bakery','breakfast','brunch'].includes(t)
  );
}

function activityValid(act, rule, family) {
  if (!act) return false;
  // 1. Tag check
  if (!tagsMatch(act.tags, rule.tags)) return false;
  // 2. Open-hours check (use Friday as representative weekday)
  const oh = act.open_hours && act.open_hours.fri;
  if (oh == null) return false;
  if (!isOpenDuringSlot(oh, rule.window[0], rule.window[1])) return false;
  // 3. Kid-friendliness
  if (family.kid_ages !== 'none' && act.kid_ok === false) return false;
  // 4. Mobility
  if (family.mobility !== 'full' && act.mobility_ok === false) return false;
  // 5. Budget: activity tier must be <= family tier
  const famIdx = TIERS.indexOf(family.budget_tier);
  const actIdx = TIERS.indexOf(act.budget_tier);
  if (actIdx > famIdx) return false;
  return true;
}

// ── Replacement finder ────────────────────────────────────────────────────────
function findReplacement(rule, family, counts, prevAct, excludeId, allActivities) {
  let candidates = allActivities.filter(a => {
    if (a.id === excludeId) return false;
    if (!activityValid(a, rule, family)) return false;
    const used  = counts[a.id] || 0;
    const limit = isCafe(a) ? 5 : 3;
    if (used >= limit) return false;
    return true;
  });

  if (candidates.length === 0) return null;

  // Sort by proximity to previous slot's activity
  if (prevAct && prevAct.lat != null) {
    candidates.sort((a, b) => {
      const da = Math.hypot(a.lat - prevAct.lat, a.lng - prevAct.lng);
      const db = Math.hypot(b.lat - prevAct.lat, b.lng - prevAct.lng);
      return da - db;
    });
  }

  return candidates[0];
}

// ── Load data ─────────────────────────────────────────────────────────────────
const dataDir    = path.join(__dirname, '..', 'data');
const activities = JSON.parse(fs.readFileSync(path.join(dataDir, 'activities_bay.json'), 'utf8'));
const actMap     = Object.fromEntries(activities.map(a => [a.id, a]));

const inputPath  = path.join(dataDir, 'families_part_film_festival_critic__b0.jsonl');
const outputPath = path.join(dataDir, 'families_clean_film_festival_critic__b0.jsonl');
const inputLines = fs.readFileSync(inputPath, 'utf8').trim().split('\n').filter(Boolean);

// ── Per-family processing ─────────────────────────────────────────────────────
let totalSlotFixes = 0;
let totalRepFixes  = 0;
const noReplacementCases = [];
const outputLines = [];
const perFamilyReport = [];

for (const line of inputLines) {
  const record   = JSON.parse(line);
  const { family } = record;
  const itinerary  = [...record.itinerary]; // mutable copy
  const famReport  = { id: record.id, slotFixes: [], repFixes: [] };

  // ── Pass 1: slot-type + open-hours violations ──────────────────────────────
  const counts1 = {};
  for (const id of itinerary) counts1[id] = (counts1[id] || 0) + 1;

  for (let i = 0; i < 30; i++) {
    const actId  = itinerary[i];
    const act    = actMap[actId];
    const rule   = SLOT_RULES[i % 6];

    if (!act) {
      // Activity ID not found in bank — must replace
      const prevAct = i > 0 ? actMap[itinerary[i - 1]] : null;
      if (counts1[actId]) counts1[actId]--;
      const repl = findReplacement(rule, family, counts1, prevAct, actId, activities);
      if (repl) {
        itinerary[i]           = repl.id;
        counts1[repl.id]       = (counts1[repl.id] || 0) + 1;
        famReport.slotFixes.push({ slot: i, from: actId, to: repl.id, reason: 'unknown activity' });
      } else {
        if (counts1[actId] != null) counts1[actId]++;
        noReplacementCases.push({ fam: record.id, slot: i, actId, reason: 'unknown activity — no replacement' });
      }
      continue;
    }

    if (!activityValid(act, rule, family)) {
      const prevAct = i > 0 ? actMap[itinerary[i - 1]] : null;
      counts1[actId]--;
      const repl = findReplacement(rule, family, counts1, prevAct, actId, activities);
      if (repl) {
        itinerary[i]      = repl.id;
        counts1[repl.id]  = (counts1[repl.id] || 0) + 1;
        famReport.slotFixes.push({ slot: i, from: actId, to: repl.id, reason: 'tag/hours violation' });
      } else {
        counts1[actId]++;
        noReplacementCases.push({ fam: record.id, slot: i, actId, reason: 'tag/hours violation — no replacement in bank' });
      }
    }
  }

  // ── Pass 2: repetition cap ─────────────────────────────────────────────────
  const counts2 = {};
  for (const id of itinerary) counts2[id] = (counts2[id] || 0) + 1;

  for (let i = 0; i < 30; i++) {
    const actId = itinerary[i];
    const act   = actMap[actId];
    const limit = act && isCafe(act) ? 5 : 3;

    if ((counts2[actId] || 0) > limit) {
      const rule    = SLOT_RULES[i % 6];
      const prevAct = i > 0 ? actMap[itinerary[i - 1]] : null;
      counts2[actId]--;
      const repl = findReplacement(rule, family, counts2, prevAct, actId, activities);
      if (repl && repl.id !== actId) {
        itinerary[i]     = repl.id;
        counts2[repl.id] = (counts2[repl.id] || 0) + 1;
        famReport.repFixes.push({ slot: i, from: actId, to: repl.id });
      } else {
        counts2[actId]++;
        noReplacementCases.push({ fam: record.id, slot: i, actId, reason: 'repetition cap — no replacement' });
      }
    }
  }

  totalSlotFixes += famReport.slotFixes.length;
  totalRepFixes  += famReport.repFixes.length;
  perFamilyReport.push(famReport);
  outputLines.push(JSON.stringify({ ...record, itinerary }));
}

// ── Write output ──────────────────────────────────────────────────────────────
fs.writeFileSync(outputPath, outputLines.join('\n') + '\n', 'utf8');

// ── Report ────────────────────────────────────────────────────────────────────
console.log('\n=== AUDIT REPORT ===');
console.log(`Slot/open-hours violations fixed : ${totalSlotFixes}`);
console.log(`Repetition cap fixes             : ${totalRepFixes}`);
console.log(`Total replacements               : ${totalSlotFixes + totalRepFixes}`);

if (noReplacementCases.length > 0) {
  console.log('\nCould NOT find replacement for (bank gap):');
  for (const c of noReplacementCases) {
    console.log(`  fam=${c.fam}  slot=${c.slot}  act=${c.actId || '?'}  —  ${c.reason}`);
  }
} else {
  console.log('\nAll violations successfully replaced.');
}

console.log('\n=== PER-FAMILY BREAKDOWN ===');
for (const r of perFamilyReport) {
  const total = r.slotFixes.length + r.repFixes.length;
  console.log(`\n${r.id}  (${total} fix${total !== 1 ? 'es' : ''})`);
  for (const f of r.slotFixes) {
    const rule    = SLOT_RULES[f.slot % 6];
    const origAct = actMap[f.from];
    const replAct = actMap[f.to];
    console.log(`  slot ${String(f.slot).padStart(2)} (${rule.name}): ${f.from} -> ${f.to}   [${f.reason}]`);
    if (origAct) console.log(`    orig  tags=[${(origAct.tags||[]).join(', ')}]  fri="${origAct.open_hours && origAct.open_hours.fri}"`);
    if (replAct) console.log(`    repl  tags=[${(replAct.tags||[]).join(', ')}]  fri="${replAct.open_hours && replAct.open_hours.fri}"`);
  }
  for (const f of r.repFixes) {
    console.log(`  slot ${String(f.slot).padStart(2)} (rep-cap): ${f.from} -> ${f.to}`);
  }
}

console.log(`\nOutput written to: ${outputPath}`);
