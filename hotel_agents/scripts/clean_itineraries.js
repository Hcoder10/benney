#!/usr/bin/env node
/**
 * Itinerary cleanup script — implements sonnet_cleanup_brief.md rules
 * Uses Node.js built-ins only.
 */

const fs = require('fs');
const path = require('path');

// ── Load data ──────────────────────────────────────────────────────────────
const DATA_DIR = path.join(__dirname, '..', 'data');
const activityBank = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'activities_bay.json'), 'utf8'));
const familiesRaw = fs.readFileSync(
  path.join(DATA_DIR, 'families_part_anniversary_wine_couple__b0.jsonl'), 'utf8'
).trim().split('\n').map(l => JSON.parse(l));

// Build a lookup map by id
const actMap = {};
for (const a of activityBank) actMap[a.id] = a;

// ── Slot rules ──────────────────────────────────────────────────────────────
// slot_idx % 6 → required tags (any-of) + time window [start, end) in hours
const SLOT_RULES = [
  { window: [7, 9],   tags: ['cafe','coffee','bakery','breakfast'] },            // 0 early-morning
  { window: [8, 10],  tags: ['cafe','coffee','bakery','breakfast','brunch'] },   // 1 breakfast
  { window: [10, 13], tags: ['museum','tour','outdoor','hiking','campus','tech','art','science','history','gardens','walking','shopping','viewpoint','landmark','architecture'] }, // 2 late-morning
  { window: [12, 17], tags: ['restaurant','lunch','casual','park','scenic','beach','outdoor','hiking','tour','shopping','winery','wine','tasting'] }, // 3 lunch+afternoon
  { window: [17, 20], tags: ['restaurant','dinner','fine-dining','scenic','sunset','wine','casual'] }, // 4 evening
  { window: [20, 23], tags: ['bar','cocktails','nightlife','lounge','dinner','fine-dining','speakeasy'] }, // 5 night
];

// ── Open-hours parser ───────────────────────────────────────────────────────
/**
 * Parse a time string like "10:00-17:00", "10:00 AM - 5:00 PM", "07:00-18:00"
 * Returns [startHour, endHour] in 24h floats, or null if unparseable/closed.
 */
function parseHours(str) {
  if (!str || str === 'closed' || str === 'Closed' || str.toLowerCase() === 'varies') return null;
  // Handle "sunrise-sunset" — treat as 6:00-20:00
  if (str.toLowerCase().includes('sunrise') || str.toLowerCase().includes('sunset')) return [6, 20];
  // May contain multiple windows like "11:30-14:00, 17:30-22:00" — use first
  const first = str.split(',')[0].trim();
  // Match patterns: "HH:MM" or "H:MM AM/PM" with separator " - " or "-"
  const m = first.match(/(\d{1,2}):(\d{2})\s*(AM|PM)?\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)?/i);
  if (!m) return null;
  let sh = parseInt(m[1]), sm = parseInt(m[2]);
  const sAmPm = (m[3] || '').toUpperCase();
  let eh = parseInt(m[4]), em = parseInt(m[5]);
  const eAmPm = (m[6] || '').toUpperCase();

  if (sAmPm === 'PM' && sh !== 12) sh += 12;
  if (sAmPm === 'AM' && sh === 12) sh = 0;
  if (eAmPm === 'PM' && eh !== 12) eh += 12;
  if (eAmPm === 'AM' && eh === 12) eh = 0;

  return [sh + sm/60, eh + em/60];
}

/** Check if activity open_hours.fri overlaps with slot window [wStart, wEnd] */
function isOpenDuring(act, wStart, wEnd) {
  const hours = parseHours(act.open_hours && act.open_hours.fri);
  if (!hours) return false; // closed or unparseable → not valid
  const [sh, eh] = hours;
  // Overlap: sh < wEnd AND eh > wStart
  return sh < wEnd && eh > wStart;
}

/** Check if activity tags overlap with required slot tags */
function matchesSlotTags(act, slotIdx) {
  const rule = SLOT_RULES[slotIdx % 6];
  const actTags = (act.tags || []).map(t => t.toLowerCase().replace(/_/g, '-'));
  return rule.tags.some(rt => actTags.includes(rt));
}

/** Check if activity satisfies family constraints */
function satisfiesFamily(act, family) {
  // budget: activity budget_tier must be ≤ family budget
  const TIERS = ['shoestring', 'mid', 'premium', 'luxury'];
  const actTierIdx = TIERS.indexOf(act.budget_tier);
  const famTierIdx = TIERS.indexOf(family.budget_tier);
  if (actTierIdx > famTierIdx) return false;
  // kid_ok
  if (!act.kid_ok && family.kid_ages !== 'none') return false;
  // mobility_ok
  if (!act.mobility_ok && family.mobility !== 'full') return false;
  return true;
}

/** Check if activity is valid for a slot (tags + open hours) */
function isValidForSlot(act, slotIdx) {
  const rule = SLOT_RULES[slotIdx % 6];
  return matchesSlotTags(act, slotIdx) && isOpenDuring(act, rule.window[0], rule.window[1]);
}

/** Is the activity a cafe (for repetition counting)? */
function isCafe(act) {
  const tags = (act.tags || []).map(t => t.toLowerCase().replace(/_/g, '-'));
  return tags.some(t => ['cafe','coffee','bakery','breakfast','brunch'].includes(t));
}

// ── Main audit+repair ───────────────────────────────────────────────────────
let totalSlotFixes = 0;
let totalRepFixes = 0;
const unfixable = [];
const outputLines = [];

for (const record of familiesRaw) {
  const { family } = record;
  const itinerary = [...record.itinerary]; // clone
  const slotFixes = [];
  const repFixes = [];

  // First pass: slot-type + open-hours violations
  const usedCounts = {}; // id → count
  for (const id of itinerary) {
    usedCounts[id] = (usedCounts[id] || 0) + 1;
  }

  // We'll do one pass for slot violations, then one for repetition
  // Slot-violation pass
  for (let i = 0; i < 30; i++) {
    const slotIdx = i % 6;
    const rule = SLOT_RULES[slotIdx];
    const currentId = itinerary[i];
    const currentAct = actMap[currentId];

    let needsReplacement = false;
    let reason = '';

    if (!currentAct) {
      needsReplacement = true;
      reason = 'unknown-activity';
    } else if (!matchesSlotTags(currentAct, slotIdx)) {
      needsReplacement = true;
      reason = 'wrong-tags';
    } else if (!isOpenDuring(currentAct, rule.window[0], rule.window[1])) {
      needsReplacement = true;
      reason = 'closed';
    }

    if (needsReplacement) {
      // Find replacement
      // Build current usage counts (updated as we go)
      const currentUsage = {};
      for (let j = 0; j < 30; j++) currentUsage[itinerary[j]] = (currentUsage[itinerary[j]] || 0) + 1;

      // Filter candidates
      const candidates = activityBank.filter(a => {
        if (!isValidForSlot(a, slotIdx)) return false;
        if (!satisfiesFamily(a, family)) return false;
        // Repetition: not already >2 (or >4 for cafes — leave room for cap enforcement)
        const cnt = currentUsage[a.id] || 0;
        const cafe = isCafe(a);
        if (cafe && cnt >= 5) return false;
        if (!cafe && cnt >= 3) return false; // brief says max 3× non-cafe
        return true;
      });

      if (candidates.length === 0) {
        unfixable.push({ family: record.id, slot: i, reason, id: currentId });
        // leave as-is
      } else {
        // Pick the best: prefer same activity if just re-scheduling isn't possible,
        // otherwise pick first valid. We prefer candidates not already in itinerary.
        const notUsed = candidates.filter(a => !(currentUsage[a.id] > 0));
        const pick = (notUsed.length > 0 ? notUsed : candidates)[0];
        // Update usage counts
        if (currentAct) {
          usedCounts[currentId] = (usedCounts[currentId] || 1) - 1;
          if (usedCounts[currentId] <= 0) delete usedCounts[currentId];
        }
        itinerary[i] = pick.id;
        usedCounts[pick.id] = (usedCounts[pick.id] || 0) + 1;
        slotFixes.push({ slot: i, from: currentId, to: pick.id, reason });
      }
    }
  }

  // Repetition cap pass
  // After slot fixes, recount
  const finalCounts = {};
  for (const id of itinerary) finalCounts[id] = (finalCounts[id] || 0) + 1;

  for (const [id, count] of Object.entries(finalCounts)) {
    const act = actMap[id];
    if (!act) continue;
    const cafe = isCafe(act);
    const maxAllowed = cafe ? 5 : 3;

    if (count > maxAllowed) {
      // Find all slots with this id and replace the extras
      let occurrences = [];
      for (let i = 0; i < 30; i++) {
        if (itinerary[i] === id) occurrences.push(i);
      }
      // Keep first maxAllowed, replace the rest
      const toReplace = occurrences.slice(maxAllowed);
      for (const slotIdx of toReplace) {
        const rule = SLOT_RULES[slotIdx % 6];
        // Rebuild current usage
        const currentUsage = {};
        for (const sid of itinerary) currentUsage[sid] = (currentUsage[sid] || 0) + 1;

        const candidates = activityBank.filter(a => {
          if (a.id === id) return false; // don't re-pick same
          if (!isValidForSlot(a, slotIdx % 6 === slotIdx ? slotIdx : slotIdx)) return false;
          if (!satisfiesFamily(a, family)) return false;
          const cnt = currentUsage[a.id] || 0;
          const c = isCafe(a);
          if (c && cnt >= 5) return false;
          if (!c && cnt >= 3) return false;
          return true;
        });

        if (candidates.length === 0) {
          unfixable.push({ family: record.id, slot: slotIdx, reason: 'repetition-cap', id });
        } else {
          const notUsed = candidates.filter(a => !(currentUsage[a.id] > 0));
          const pick = (notUsed.length > 0 ? notUsed : candidates)[0];
          itinerary[slotIdx] = pick.id;
          repFixes.push({ slot: slotIdx, from: id, to: pick.id });
        }
      }
    }
  }

  totalSlotFixes += slotFixes.length;
  totalRepFixes += repFixes.length;

  if (slotFixes.length + repFixes.length > 0) {
    console.log(`\n${record.id}:`);
    for (const f of slotFixes) {
      console.log(`  Slot ${f.slot} (day${Math.floor(f.slot/6)+1} pos${f.slot%6}): ${f.from} → ${f.to} [${f.reason}]`);
    }
    for (const r of repFixes) {
      console.log(`  Rep-cap slot ${r.slot}: ${r.from} → ${r.to}`);
    }
  }

  outputLines.push(JSON.stringify({ id: record.id, family, itinerary }));
}

// Write output
const outPath = path.join(DATA_DIR, 'families_clean_anniversary_wine_couple__b0.jsonl');
fs.writeFileSync(outPath, outputLines.join('\n') + '\n', 'utf8');

console.log('\n═══════════════════════════════════════');
console.log(`Slot-type / open-hours fixes: ${totalSlotFixes}`);
console.log(`Repetition-cap fixes:         ${totalRepFixes}`);
console.log(`Total fixes:                  ${totalSlotFixes + totalRepFixes}`);
if (unfixable.length > 0) {
  console.log(`\nUnfixable slots (left as-is):`);
  for (const u of unfixable) {
    console.log(`  ${u.family} slot ${u.slot}: ${u.id} (${u.reason})`);
  }
} else {
  console.log('No unfixable slots.');
}
console.log(`\nOutput written to: ${outPath}`);
