// Types for session.mjs, so the TypeScript suites in tests/plugin can import it.

import type { Usage } from './usage.mjs';

export type HookEvent = 'SessionStart' | 'UserPromptSubmit' | 'PostToolUse' | 'Stop' | 'SessionEnd';

// What Claude Code hands a hook on stdin, plus the branch hook.mjs reads.
export type HookInput = {
  session_id: string;
  hook_event_name?: string;
  source?: string;
  cwd?: string;
  branch?: string | null;
  // PostToolUse: the tool, and what it answered.
  tool_name?: string;
  tool_response?: unknown;
  // UserPromptSubmit: what the person typed, whole. Read by `promptLine` and
  // by nothing else.
  prompt?: string;
  // ALYDAS-265. Added by hook.mjs: the session's count as just read, and
  // whether this laptop had been told what is sent before this hook.
  usage?: Usage | null;
  told?: boolean;
  // ALYDAS-451, F11. Added by hook.mjs: false for an entry whose desk has no
  // Work, which names no work item.
  work?: boolean;
};

export type HeartbeatBody = {
  run: string;
  agent: string;
  statusLine: string;
  branch: string | null;
  workItem?: string;
  ended?: true;
  // ALYDAS-262.
  answers?: true;
  heard?: string[];
  // ALYDAS-263. Whether the session waits at its prompt; on every beat but
  // the ended one.
  idle?: boolean;
  // ALYDAS-265. The first line of the last prompt and the last tool's name,
  // blank to clear either; and the session's tokens and estimated cost.
  prompt?: string;
  tool?: string;
  tokens?: number;
  costUsd?: number;
  costExact?: false;
  // ALYDAS-127, S5. The desk's start, from ALYDASH_RUN_START.
  start?: string;
};

export type SessionState = {
  sessionId: string;
  run: string;
  startedAt: number;
  lastBeatAt: number | null;
  idle: boolean;
  stopped: boolean;
  capped: boolean;
  itemRefused: boolean;
  pending: string[];
  // ALYDAS-262. Answers not yet shown, worded; and the Asks this session
  // raised that no beat has named to the desk yet. Absent in a state file
  // written before.
  answers?: string[];
  heard?: string[];
  last: HeartbeatBody | null;
  // ALYDAS-266. What the last beat named and on whose word; the branch's
  // keys the desk refused; whether a beat that named an item ever landed; and
  // whether the session was told it is linked to nothing. Absent in a state
  // file written before.
  link?: { by: 'variable' | 'branch' | 'none'; key: string | null; spare: boolean };
  guessRefused?: string[];
  linked?: boolean;
  unlinkedSaid?: boolean;
  // Gate 8, COR-7: the 401 notice was already given this session.
  disconnectedSaid?: boolean;
  // ALYDAS-342. The desk said the 401 was the switch, and the session was
  // told so.
  switchedOffSaid?: boolean;
  // ALYDAS-265. The prompt line as it will be sent (null: none may be), the
  // last tool's name, the session's count, and whether the desk's last answer
  // was the budget stop. Absent in a state file written before.
  prompt?: string | null;
  tool?: string;
  usage?: Usage | null;
  held?: boolean;
  // ALYDAS-449. The desk last refused a beat because runs is switched off
  // for the app, and the session was told so.
  runsOff?: boolean;
};

// The desk's answer as desk.mjs reads it. `droppedItem` is set by hook.mjs on
// the answer to a retry without the work item: the item it dropped.
export type Reply = { status: number; json: unknown; droppedItem?: string };

export type HookOutput = {
  continue?: false;
  stopReason?: string;
  decision?: 'block';
  reason?: string;
  systemMessage?: string;
  hookSpecificOutput?: { hookEventName: string; additionalContext: string };
};

export const AGENT: string;
export const THROTTLE_MS: number;
export const STATUS_LINE_MAX: number;
export const BRANCH_MAX: number;
export const STOP_REASON: string;
export const CAP_NOTICE: string;
export const MESSAGE_PREFIX: string;
export const ANSWER_PREFIX: string;
export const HEARD_MAX: number;
export const HEARD_ID: RegExp;
export const SESSION_KEY: RegExp;
export const DISCONNECTED_NOTICE: string;
export const SWITCHED_OFF_REASON: string;
export const REFUSED_PREFIX: string;
export const REFUSED_SENTENCE_MAX: number;
export const RUNS_OFF_REASON: string;
export function runsOffNotice(app: string): string;
export function runsOnNotice(app: string): string;
// Gate 8 (W4): agent access switched off for the app, heard by the probe.
export function accessOffNotice(app: string): string;
export function isRunsOffRefusal(reply: Reply | null | undefined): boolean;
export const BRANCH_KEYS_MAX: number;
export const UNLINKED_NOTICE: string;
export const PROMPT_LINE_MAX: number;
export const TOOL_MAX: number;
export const WHAT_IS_SENT: string;
export const SENDS_NOTICE: string;
export const SENDS_VERSION: number;
export const HOOK_EVENTS: readonly HookEvent[];

export function cleanMessage(text: string): string | null;
export function switchedOffSentence(reply: Reply | null | undefined): string | null;
export function sendsPrompt(env: Readonly<Record<string, string | undefined>> | undefined): boolean;
export function promptLine(prompt: unknown): string;
export function toolName(input: { tool_name?: unknown } | null | undefined): string | null;
export function budgetStopReason(directive: unknown): string;
export function heardAsk(input: HookInput): string | null;
export function answerLine(answer: unknown): string | null;
export function branchItems(branch: string | null | undefined): string[];
export function itemRefusedNotice(item: string): string;
export function runName(sessionId: string, startMs: number): string;
export function statusLine(branch: string | null, idle: boolean): string;
export function runStartOf(env: Readonly<Record<string, string | undefined>> | null | undefined): string | null;
export function decide(
  event: HookEvent,
  input: HookInput,
  state: SessionState | null,
  now: number,
  env?: Readonly<Record<string, string | undefined>>,
): { beat: HeartbeatBody | null; state: SessionState | null };
export function endedBeat(state: SessionState): HeartbeatBody;
export function itemRetry(beat: HeartbeatBody | null, reply: Reply | null): HeartbeatBody | null;
export function isRunCapRefusal(reply: Reply | null): boolean;
export function applyReply(
  event: HookEvent,
  reply: Reply | null,
  state: SessionState | null,
  context?: { appName?: string },
): { output: HookOutput | null; state: SessionState | null; followUp: 'ended-beat' | null };
