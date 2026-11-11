// Types for outcome.mjs, so the TypeScript suites in tests/plugin can import it.

export type GitFacts = { commit: string | null; ahead: number | null; pushed: boolean | null; onDefault: boolean | null };
export type GitRun = (cwd: string, args: string[]) => Promise<string | null>;

export const REPLY_READ_MAX_BYTES: number;
export const AHEAD_MAX: number;
export function lastReplyText(transcriptPath: unknown): string | null;
export function gitFacts(
  cwd: string,
  run: GitRun,
  options?: { startHead?: string | null; startOrigin?: string | null; since?: number | null },
): Promise<GitFacts>;
