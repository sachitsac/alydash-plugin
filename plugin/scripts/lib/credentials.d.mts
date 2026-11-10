// Types for credentials.mjs, so the TypeScript suites in tests/plugin can
// import it.

export type Credential = {
  desk: string;
  appId: string;
  appName: string;
  repo: string | null;
  token: string;
  expiresAt: string;
  // ALYDAS-379, S1: what connect asked for; absent on an entry filed before.
  scopes?: string[];
  // Gate 8 (W3): when the desk said it no longer takes the token.
  revokedAt?: string;
};

export const CREDENTIALS_FILE: string;
export const NO_DATA_DIR: string;
export const HEARTBEAT_SCOPE: string;
export const WORK_SCOPE: string;

export function pluginDataDir(env?: Readonly<Record<string, string | undefined>>, given?: string): string;
export function ensurePrivateDir(dir: string): void;
export function writePrivateJson(path: string, value: unknown): void;
export function sameRepo(a: string | null, b: string | null): boolean;
export function readCredentials(dataDir: string): { entries: Credential[] };
export function saveCredential(dataDir: string, entry: Credential): void;
export function markRevoked(dataDir: string, entry: Pick<Credential, 'desk' | 'repo' | 'token'>, at?: Date | null): void;
export function isRevoked(entry: { revokedAt?: unknown } | null | undefined): boolean;
export function sendsHeartbeat(entry: { scopes?: unknown } | null | undefined): boolean;
export function hasWork(entry: { scopes?: unknown } | null | undefined): boolean;
export function credentialFor(dataDir: string, desk: string, repo: string | null, now?: Date): Credential | null;
export function isLive(entry: { expiresAt: string } | null | undefined, now?: Date): boolean;
// ALYDAS-445: the one choice of a repository's entry, live first.
export function pickCredential(
  dataDir: string,
  repo: string | null,
  options?: { desk?: string | null; now?: Date },
): Credential | null;
