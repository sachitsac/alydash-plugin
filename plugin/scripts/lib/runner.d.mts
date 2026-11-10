// Types for runner.mjs, so the TypeScript suites in tests/plugin can import it.

export const RUNNER_SCOPE: string;
export const CLAIM_HOLD_S: number;
export const CLAIM_TIMEOUT_MS: number;
export const CLAIM_MIN_MS: number;
export const CLAIM_BACKOFF_MS: number;
export const RUNNER_TICK_MS: number;
export const NOT_A_REPO: string;
export const NOT_CONNECTED: string;
export const RECONNECT: string;
export const RUNNER_STOPPED: string;
export const RUNNER_NOT_RUNNING: string;
export function runnerStarted(appName: string, desk: string): string;
export function runnerAlready(appName: string, desk: string): string;

export type TakenStart = { id: string; kind: 'start' | 'resume'; prompt: string; item: string; budgetUsd: number | null };

export function holdsRunnerScope(entry: { scopes?: unknown } | null | undefined): boolean;
export function repoKey(repo: string): string;
export function startOf(json: unknown): TakenStart | null;
export function worktreePath(root: string, startId: string): string;
export function claudeArgs(prompt: string, sessionId: string): string[];
export function claudeEnv(
  base: Readonly<Record<string, string | undefined>>,
  given: { credentials: string; start: TakenStart },
): Record<string, string | undefined>;
export function firstDelivery(seen: Readonly<Record<string, unknown>> | null | undefined, id: string): boolean;
export function runEnded(state: unknown): boolean;
export function claimEnds(reply: { status: number } | null | undefined): boolean;

// ALYDAS-127, S7 (Ask 79e3e335, runner-waits): waiting at a hold, then resuming.
export const PIPELINE_SCRIPT: string;
export function resumeArgs(prompt: string, sessionId: string): string[];
export type HeldPhase = { root: string; slug: string; n: number; attempt: number };
export function heldPhase(ledgers: ReadonlyArray<{ path: string; run: unknown }> | null | undefined): HeldPhase | null;
export type HoldOutcome = 'closed' | 'sent-back' | 'unanswered';
export function holdOutcome(phase: unknown): HoldOutcome;
export function lastDecision(events: ReadonlyArray<unknown> | null | undefined, n: number): string | null;
export function answerPrompt(outcome: HoldOutcome, n: number, decision: string | null): string;
export function outOfTime(n: number): string;
