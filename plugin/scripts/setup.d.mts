// Types for setup.mjs, so the TypeScript suites in tests/plugin can import it.
// The plugin runs the .mjs with no build step.

export const NOT_A_REPO: string;
export const NOT_CONNECTED: string;
export const NOTHING_TO_SET_UP: string;
export const OURS: string;

export type Outcome = { code: number; lines: string[] };

export function workflowName(text: string): string | null;
// ALYDAS-518: both workflows read the desk's address from vars.DESK_URL.
export const DESK_VARIABLE: string;
export function syncWorkflow(input: { deployName: string | null }): string;
// ALYDAS-127, S6.
export const RUN_FILE: string;
export const PLUGIN_MARKETPLACE: string;
export const PLUGIN_NAME: string;
export function runWorkflow(): string;
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
  // ALYDAS-518: sets a repository variable, true when it was set; the GitHub
  // CLI unless a test hands its own.
  setVariable?: (root: string, repo: string, name: string, value: string) => Promise<boolean>;
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
