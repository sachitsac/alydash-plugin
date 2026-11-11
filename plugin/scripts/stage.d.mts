// Types for stage.mjs, so the TypeScript suites in tests/plugin can import it.
// The plugin itself runs the .mjs with no build step.

export type StageMark = 'start' | 'done' | 'failed' | 'skipped';
export type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';
export type Stage = { n: number; status: StageStatus; reason: string | null; startedAt: string | null; endedAt: string | null };
export type StageCall = { n: number; mark: StageMark; reason: string | null };

export const STAGES: readonly string[];
export const PHASE_RANGE: readonly [number, number];
export const MARKS: readonly StageMark[];
export const REASON_MAX: number;
export const USAGE: string;

export function reasonOf(words: readonly string[]): string | null;
export function parseArgs(argv: readonly string[]): StageCall | null;
export function nextStages(before: readonly Stage[] | null | undefined, call: StageCall, now: Date): Stage[];
export function ledgerOf(stages: readonly Stage[], options?: { reasons?: boolean }): {
  phases: {
    n: number;
    name: string;
    kind: 'phase';
    status: StageStatus;
    summary: string | null;
    skipReason: string | null;
    startedAt: string | null;
    endedAt: string | null;
  }[];
};
export function stageBeat(
  state: { run: string; sessionId?: string; desk?: string; repo?: string; last?: Record<string, unknown> | null },
  stages: readonly Stage[],
  env?: Readonly<Record<string, string | undefined>>,
  options?: { reasons?: boolean },
): Record<string, unknown>;
export function report(
  argv: readonly string[],
  env?: Readonly<Record<string, string | undefined>>,
  cwd?: string,
  now?: Date,
): Promise<void>;
