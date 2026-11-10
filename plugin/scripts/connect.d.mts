// Types for connect.mjs, so the TypeScript suites in tests/plugin can import
// it. The plugin runs the .mjs with no build step.

import type { DeskAnswer } from './lib/desk.mjs';

export const SCOPES: string;
export const RUNNER_SCOPE: string;
export const POLL_TIMEOUT_MS: number;
export const NOT_A_REPO: string;

export type Outcome = { code: number; lines: string[] };

export type WaitingState = {
  state: 'waiting';
  device_code: string;
  interval: number;
  expiresAt: string;
  desk: string;
  repo: string;
  pid?: number;
  // ALYDAS-379, S1: the scopes start asked for.
  scopes?: string[];
};

export type PollStep =
  | { kind: 'pending' }
  | { kind: 'slow_down' }
  | { kind: 'token'; token: string; expiresIn: number; app: { id: string; name: string }; sessionsOnNow?: boolean }
  | { kind: 'stop'; state: 'denied' | 'expired' | 'failed' };

export function hostName(raw: string): string;
export function whichDesk(known: readonly string[]): string[];
export function pollStep(answer: DeskAnswer | null): PollStep;

export function start(deps?: {
  env?: Readonly<Record<string, string | undefined>>;
  dataArg?: string;
  cwd?: string;
  hostname?: string;
  now?: () => number;
  post?: (url: string, fields: Record<string, string>, options?: { timeoutMs?: number }) => Promise<DeskAnswer>;
  get?: (url: string, options?: { timeoutMs?: number }) => Promise<DeskAnswer>;
  spawnPoller?: (stateFile: string, dataDir: string) => void;
}): Promise<Outcome>;

export function poll(
  stateFile: string,
  deps?: {
    post?: (url: string, fields: Record<string, string>, options?: { timeoutMs?: number }) => Promise<DeskAnswer>;
    sleep?: (ms: number) => Promise<void>;
    now?: () => number;
    pid?: number;
  },
): Promise<string>;

// The lines for every decided connect in `repo`, each removed once read.
export function takeDecidedLines(dataDir: string, repo: string): string[];

export function main(argv: readonly string[]): Promise<number>;
