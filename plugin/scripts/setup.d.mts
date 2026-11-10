// Types for setup.mjs, so the TypeScript suites in tests/plugin can import it.
// The plugin runs the .mjs with no build step.

export const NOT_A_REPO: string;
export const NOT_CONNECTED: string;
export const NOTHING_TO_SET_UP: string;
export const OURS: string;

export type Outcome = { code: number; lines: string[] };

export function workflowName(text: string): string | null;
export function syncWorkflow(input: { desk: string; deployName: string | null }): string;
// ALYDAS-127, S6.
export const RUN_FILE: string;
export const PLUGIN_MARKETPLACE: string;
export const PLUGIN_NAME: string;
export function runWorkflow(input: { desk: string }): string;
export function deskFor(
  dataDir: string,
  repo: string,
  env?: Readonly<Record<string, string | undefined>>,
  now?: Date,
): string | null;
export function setup(input?: {
  env?: Readonly<Record<string, string | undefined>>;
  dataArg?: string;
  cwd?: string;
}): Promise<Outcome>;
export function status(input?: {
  env?: Readonly<Record<string, string | undefined>>;
  dataArg?: string;
  cwd?: string;
  now?: Date;
  newestMerged?: (root: string) => Promise<{ number: number; body: string } | null | undefined>;
  // ALYDAS-450: asks the desk whether a live token still works; lib/desk.mjs
  // probeConnection unless a test hands its own.
  probe?: (desk: string, token: string) => Promise<{ status: number; json: unknown }>;
}): Promise<Outcome>;
export function main(argv: string[]): Promise<number>;
