// ALYDAS-265. What a plain session has spent, counted on the laptop. No hook
// is handed a token count or a cost (checked against the hooks reference on
// 2026-10-05), but every hook is handed `transcript_path`, the session's own
// JSONL, in which each answer from the model carries its `usage`. This file
// adds those numbers up and prices them. Only the sums ever leave it: no line
// of the transcript, and nothing of a message but its id, its model's name
// and its four token counts, is kept or sent.
//
// It reads as the pipeline does (`read_session` and `price_message` in
// skills/feature-pipeline/scripts/pipeline.py): each message id counted once,
// a `<synthetic>` line that spent nothing skipped, and the session's
// subagents, whose transcripts sit beside it in `<session>/subagents/`.
// Neither the transcript's shape nor that folder is documented, so a
// transcript that does not read this way counts as nothing spent and the page
// shows a dash.
//
// The cost is an estimate: list prices, and only the calls the transcripts
// hold. It is sent as a floor (`costExact: false`), which the desk says as
// "at least".
//
// Read a piece at a time: the state keeps, for each file, how far it has been
// read and the last few message ids, so a hook reads only what was appended
// since the one before.

import { closeSync, fstatSync, openSync, readSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

// Dollars per million tokens: input, cache write kept 5 minutes, cache write
// kept an hour, cache read, output. `PRICES` in pipeline.py, list prices of
// 2026-06-24; tests/plugin/usage.test.ts pins this table to that one, so a
// price changed there fails here.
export const PRICES = Object.freeze({
  'claude-opus-5-5': [4.0, 5.0, 8.0, 0.2, 20.0],
  'claude-sonnet-5': [2.0, 2.5, 4.0, 0.2, 10.0],
  'claude-fable-5-1': [10.0, 12.5, 20.0, 0.25, 50.0],
  'claude-haiku-4-5': [1.0, 1.25, 2.0, 0.1, 5.0],
  'claude-haiku-4-5-20251001': [1.0, 1.25, 2.0, 0.1, 5.0],
});

const SYNTHETIC_MODEL = '<synthetic>';
// The most of one file read by one hook; the rest is read by the next. A hook
// has ten seconds, and a resumed session's transcript can be very large.
export const READ_MAX_BYTES = 32 * 1024 * 1024;
// How many message ids a file remembers, so one answer written once per
// content block (the same id on lines a few apart) is counted once.
const IDS_KEPT = 16;
// How many subagent transcripts one session's count follows.
export const SUBAGENTS_MAX = 200;
// TOKENS_MAX in packages/modules/runs/src/heartbeat-body.ts and LEDGER_BOUNDS.costUsd in
// packages/modules/runs/src/ledger-bounds.ts.
// ALYDAS-543: the largest whole number a JS number holds exactly, as the
// desk's `bigint` columns allow, so a long session's real total is sent.
export const TOKENS_MAX = Number.MAX_SAFE_INTEGER;
export const COST_MAX = 99_999_999;

const count = (value) => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0);

// The id a price is listed under: `claude-opus-5-5[1m]` is `claude-opus-5-5`,
// and a dated id is its base when the table lists the base and not the date.
export function baseModel(model) {
  const plain = String(model ?? '').replace(/\[[^\]]*\]$/, '');
  if (Object.hasOwn(PRICES, plain)) return plain;
  const undated = plain.replace(/-\d{8}$/, '');
  return Object.hasOwn(PRICES, undated) ? undated : plain;
}

// Dollars for one message at list prices, or null for a model the table does
// not list. A cache write with no split, or the part its split does not
// account for, is priced as kept 5 minutes.
export function priceMessage(model, usage) {
  const base = baseModel(model);
  if (!Object.hasOwn(PRICES, base)) return null;
  const price = PRICES[base];
  const split = usage?.cache_creation !== null && typeof usage?.cache_creation === 'object' ? usage.cache_creation : {};
  const hour = count(split.ephemeral_1h_input_tokens);
  const minutes = count(split.ephemeral_5m_input_tokens);
  const rest = Math.max(0, count(usage?.cache_creation_input_tokens) - minutes - hour);
  return (
    (price[0] * count(usage?.input_tokens) +
      price[1] * (minutes + rest) +
      price[2] * hour +
      price[3] * count(usage?.cache_read_input_tokens) +
      price[4] * count(usage?.output_tokens)) /
    1_000_000
  );
}

// Every token of one message: input, both halves of the cache, and output,
// which is what the pipeline's `tokens` is.
export function tokensOf(usage) {
  return (
    count(usage?.input_tokens) +
    count(usage?.cache_creation_input_tokens) +
    count(usage?.cache_read_input_tokens) +
    count(usage?.output_tokens)
  );
}

const EMPTY = Object.freeze({ tokens: 0, costUsd: 0, priced: 0, files: {} });

// The count with its sums at nothing and its place in every file kept: a new
// run in the same session counts from here on.
export function restartUsage(usage) {
  return { ...EMPTY, files: { ...(usage?.files ?? {}) } };
}

// One transcript line's spend, added to `sums`, unless its message was
// counted already. `ids` is the file's remembered ids, newest last.
function addLine(text, sums, ids) {
  // Most lines are not an answer from the model, and are never parsed.
  if (!text.includes('"usage"')) return;
  let line;
  try {
    line = JSON.parse(text);
  } catch {
    return;
  }
  if (line?.type !== 'assistant') return;
  const message = line.message;
  const usage = message?.usage;
  if (usage === null || typeof usage !== 'object') return;
  const tokens = tokensOf(usage);
  if (message.model === SYNTHETIC_MODEL && tokens === 0) return;
  const id = message.id ?? line.requestId ?? line.uuid;
  if (typeof id === 'string') {
    if (ids.includes(id)) return;
    ids.push(id);
    if (ids.length > IDS_KEPT) ids.shift();
  }
  sums.tokens += tokens;
  const cost = priceMessage(message.model, usage);
  if (cost !== null) {
    sums.costUsd += cost;
    sums.priced += 1;
  }
}

// Reads what `path` gained since `place` (`{ at, ids }`) into `sums`, whole
// lines only, and answers the new place. A file that is not there, or shrank,
// is left where it was.
function readFrom(path, place, sums) {
  const from = Number.isInteger(place?.at) && place.at >= 0 ? place.at : 0;
  const ids = Array.isArray(place?.ids) ? place.ids.filter((id) => typeof id === 'string').slice(-IDS_KEPT) : [];
  let file;
  try {
    file = openSync(path, 'r');
  } catch {
    return { at: from, ids };
  }
  try {
    const size = fstatSync(file).size;
    if (size <= from) return { at: from, ids };
    const buffer = Buffer.alloc(Math.min(size - from, READ_MAX_BYTES));
    const read = readSync(file, buffer, 0, buffer.length, from);
    const end = buffer.lastIndexOf(0x0a, read - 1);
    // No whole line yet. A single line longer than the window is stepped
    // over, so the count is never stuck behind it.
    if (end === -1) return { at: read === READ_MAX_BYTES ? from + read : from, ids };
    for (const text of buffer.toString('utf8', 0, end).split('\n')) addLine(text, sums, ids);
    return { at: from + end + 1, ids };
  } finally {
    closeSync(file);
  }
}

// The session's spend so far: `previous` (a session's `usage` as its state
// file keeps it, or nothing) with whatever its transcripts gained since.
// `{ tokens, costUsd, priced, files }`: every token, the dollars of the
// messages that could be priced, how many those were, and each file's place.
export function readUsage(transcriptPath, previous) {
  const before = previous !== null && typeof previous === 'object' ? previous : EMPTY;
  const sums = { tokens: count(before.tokens), costUsd: count(before.costUsd), priced: count(before.priced) };
  const places = before.files !== null && typeof before.files === 'object' ? before.files : {};
  const files = {};
  if (typeof transcriptPath !== 'string' || !transcriptPath.endsWith('.jsonl')) return { ...sums, files: { ...places } };
  files[''] = readFrom(transcriptPath, places[''], sums);
  const folder = join(transcriptPath.slice(0, -'.jsonl'.length), 'subagents');
  let names = [];
  try {
    names = readdirSync(folder).filter((name) => /^agent-[A-Za-z0-9_-]{1,80}\.jsonl$/.test(name)).sort();
  } catch {
    // No subagents.
  }
  // The ones already followed first, so a file past the limit is never one
  // whose earlier lines were counted.
  const followed = names.filter((name) => Object.hasOwn(places, name));
  const fresh = names.filter((name) => !Object.hasOwn(places, name));
  for (const name of [...followed, ...fresh].slice(0, SUBAGENTS_MAX)) {
    files[name] = readFrom(join(folder, name), places[name], sums);
  }
  return { ...sums, files };
}

// What a beat says of a count: `{ tokens, costUsd }`, each within the desk's
// wall, the cost to four places and null when no message could be priced
// (the desk would draw a 0 as "$0.00"). Null when there is no count.
export function usageFigures(usage) {
  if (usage === null || typeof usage !== 'object') return null;
  // Nothing read (no transcript, or one in a shape this does not know) is
  // nothing known, not a session that spent nothing.
  if (count(usage.tokens) === 0) return null;
  const priced = count(usage.priced) > 0;
  return {
    tokens: Math.min(Math.round(count(usage.tokens)), TOKENS_MAX),
    costUsd: priced ? Math.min(Math.round(count(usage.costUsd) * 10_000) / 10_000, COST_MAX) : null,
  };
}
