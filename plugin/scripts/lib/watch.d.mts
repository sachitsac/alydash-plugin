import type { SessionState } from './session.mjs';

export const WATCH_TICK_MS: number;
export const PULSE_MS: number;
export const STARTING_MS: number;

export type WatchJob = {
  dataDir: string;
  desk: string;
  repo: string;
  stateFile: string;
  pid: number;
  started: string;
  nonce: string;
  at: number;
  watcher: number | null;
};

export function isAlive(pid: number): boolean;
export function startedOf(pid: number): Promise<string | null>;
export function parseProcess(line: string | null): { ppid: number; name: string } | null;
export function sessionProcess(
  from?: number,
  lookup?: (pid: number) => Promise<string | null>,
): Promise<{ pid: number; started: string } | null>;
export function needsWatcher(current: Partial<WatchJob> | null, now: number, alive?: (pid: number) => boolean): boolean;
export function watchStep(input: {
  job: Partial<WatchJob> | null;
  nonce: string;
  state: Partial<SessionState> | null;
  now: number;
  lastPulseAt: number | null;
  alive?: (pid: number) => boolean;
}): 'exit' | 'pulse' | 'wait';
export function pulseBody(state: Pick<SessionState, 'run'> & Partial<SessionState>): {
  run: string;
  agent: string;
  statusLine: string;
  pulse: true;
};
export function pulseEnds(reply: { status: number; json: unknown } | null | undefined): boolean;
